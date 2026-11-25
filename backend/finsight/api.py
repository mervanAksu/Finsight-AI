"""FastAPI uygulaması: docs/api-contract.md'deki 14 uç nokta (/api öneki) + derlenmiş arayüz.

Çalıştırma: ``uvicorn finsight.api:app --reload`` → http://127.0.0.1:8000 (arayüz derlenmişse),
http://127.0.0.1:8000/docs (Swagger). Bu modül yalnızca HTTP ↔ servis çevirisi yapar: veri
``repository``, JSON şekli ``views``, hesaplar saf modüllerdedir. Uç noktalar senkron
(``def``) tanımlıdır; FastAPI bunları iş parçacığı havuzunda çalıştırır.

``app`` modül özniteliği tembel oluşturulur (``create_app``): içe aktarmak veritabanı açmaz.
"""

from __future__ import annotations

import asyncio
import base64
import collections
import contextlib
import datetime as _dt
import hashlib
import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import Future
from urllib.parse import quote

from fastapi import (APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException, Query,
                     Request)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from . import DISCLAIMER, __version__, jobs, views
from . import repository as repo
from .config import (ConfigError, get_allowed_hosts, get_ask_per_minute,
                     get_auto_refresh_minutes, get_cache_dir, get_cors_origins,
                     get_frontend_dist, get_llm_daily_limit, get_sec_json_ttl, get_user_agent,
                     qa_log_enabled)
from .edgar import build_ticker_map, search_companies
from .rag import EMBEDDING_MISMATCH_WARNING, answer_question
from .ratios import compute_ratios
from .refresh import (AUTO_REFRESH_BUDGET_PER_MINUTE, LOCK_FILE_NAME, AutoRefresher,
                      FreshnessService, ProcessLock, RequestBudget, check_company,
                      compute_freshness, db_company_entries, sec_fetcher, start_refresh_job)
from .sec_client import SecClient
from .xbrl import METRICS

log = logging.getLogger(__name__)

# SEC-18 + RT-01: harf/rakamla başlar ("..", "-" geçersiz); fullmatch → sondaki "\n" de
# reddedilir (``re.match`` + ``$`` satır sonundan önce de eşleşirdi).
TICKER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-]{0,9}")
RATIO_PERIODS = 3
TICKER_MAP_TTL = 6 * 3600       # başarılı company_tickers listesi süreç içinde 6 saat tutulur
TICKER_MAP_RETRY = 300          # başarısız denemeden sonra 5 dk yalnızca yerel arama
DISCLAIMER_HEADER = "X-FinSight-Disclaimer"  # CSV'de uyarı metni (URL kodlu, ASCII)
SEARCH_SCOPE_HEADER = "X-FinSight-Search-Scope"    # FR-01: "sec" veya "local"
SEARCH_REASON_HEADER = "X-FinSight-Search-Reason"  # scope=local ise neden kodu
INGEST_RETRY_AFTER = 30  # sn; SEC-02 kapasite dolu yanıtında Retry-After
DB_BUSY_RETRY_AFTER = 5
DB_BUSY_MESSAGE = ("Veritabanı şu anda meşgul (başka bir işlem yazıyor). Birkaç saniye sonra "
                   "tekrar deneyin.")
LLM_BUDGET_WARNING = ("Günlük LLM soru sınırına ulaşıldı (FINSIGHT_LLM_DAILY_LIMIT); cevap LLM "
                      "kullanılmadan, rapor alıntılarıyla üretildi.")

# O1: "çalışan iş var mı?" kontrolü ile iş oluşturma arasında başka istek araya girmesin
# (aynı şirket için iki iş açılmasın). Tek süreç içindir; çok süreçli dağıtımda Aşama 4.
_ingest_lock = threading.Lock()
# POST /ingest için şirket bazında şeritli kilitler (sınırlı bellek: 64 kilit).
_ingest_stripes = [threading.Lock() for _ in range(64)]


def _ticker_lock(ticker: str) -> threading.Lock:
    digest = hashlib.sha256(ticker.upper().encode()).digest()
    return _ingest_stripes[digest[0] % len(_ingest_stripes)]


# --------------------------------------------------------------------------- istek gövdeleri


class IngestRequest(BaseModel):
    analyze: bool = True


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500,
                          description="Rapor hakkında soru (1–500 karakter)")


# --------------------------------------------------------------------------- bağımlılıklar


def get_session(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] = request.app.state.session_factory
    session = factory()
    try:
        yield session
    finally:
        session.close()


def check_ticker(ticker: str) -> str:
    if not TICKER_RE.fullmatch(ticker or ""):
        raise HTTPException(400, "Geçersiz ticker: yalnızca harf, rakam, nokta ve tire "
                                 "kullanılabilir (en fazla 10 karakter).")
    return ticker.upper()


def company_or_404(session: Session, ticker: str) -> dict:
    t = check_ticker(ticker)
    company = repo.load_company(session, t)
    if company is None:
        raise HTTPException(404, f"{t} henüz işlenmedi. Önce verisini çekin: "
                                 f"POST /api/companies/{t}/ingest")
    return company


class _TickerMapCache:
    """SEC company_tickers.json'dan kurulan ticker haritası (süreç içi önbellek)."""

    def __init__(self, client_factory: Callable[[], SecClient]) -> None:
        self._factory = client_factory
        self._lock = threading.Lock()
        self._map: dict[str, dict] | None = None
        self._fetched_at = 0.0
        self._failed_at: float | None = None
        self._refreshing = False
        self.failure_reason: str | None = None  # "sec_user_agent_missing" | "sec_unavailable"

    def get(self) -> dict[str, dict] | None:
        """SEC-10: ağ isteği kilidin DIŞINDA yapılır ve aynı anda yalnızca bir iş parçacığı
        yeniler (tek uçuş); diğer çağrılar beklemeden eski haritayı (veya None → yerel arama)
        alır. SEC yavaşken arama istekleri kilitte birikip iş parçacığı havuzunu tüketmez."""
        now = time.monotonic()
        with self._lock:
            if self._map is not None and now - self._fetched_at < TICKER_MAP_TTL:
                return self._map
            if self._failed_at is not None and now - self._failed_at < TICKER_MAP_RETRY:
                return self._map  # son başarılı liste (varsa) veya None
            if self._refreshing:
                return self._map
            self._refreshing = True
        new_map: dict[str, dict] | None = None
        reason: str | None = None
        try:
            new_map = build_ticker_map(self._factory().company_tickers())
        except ConfigError as exc:  # SEC_USER_AGENT yok: mesaj gizli bilgi içermez
            log.warning("SEC şirket listesi alınamadı: %s Arama yalnızca analiz edilmiş "
                        "şirketlerde yapılacak.", exc)
            reason = "sec_user_agent_missing"
        except Exception as exc:  # ağ yok, SEC hatası → yerel arama
            log.warning("company_tickers alınamadı (%s); yalnızca yerel arama.",
                        type(exc).__name__)
            reason = "sec_unavailable"
        finally:
            with self._lock:
                self._refreshing = False
                if new_map is not None:
                    self._map = new_map
                    self._fetched_at = time.monotonic()
                    self._failed_at = None
                    self.failure_reason = None
                elif reason is not None:
                    self._failed_at = time.monotonic()
                    self.failure_reason = reason
        with self._lock:
            return self._map

    def peek(self) -> dict[str, dict] | None:
        """Ağa çıkmadan, varsa önbellekteki haritayı döndürür."""
        with self._lock:
            return self._map


def same_cik_tickers(ticker_map: dict[str, dict] | None, ticker: str) -> list[str]:
    """SEC ticker listesinde ``ticker`` ile aynı CIK'e bağlı ticker'lar (kendisi dahil)."""
    info = (ticker_map or {}).get(ticker)
    if not info:
        return []
    cik = info.get("cik")
    return [t for t, other in (ticker_map or {}).items() if other.get("cik") == cik]


# --------------------------------------------------------------------------- uç noktalar

router = APIRouter(prefix="/api")


def sec_user_agent_configured() -> bool:
    try:
        get_user_agent()
    except ConfigError:
        return False
    return True


def _config_value(getter, fallback):
    try:
        return getter()
    except ConfigError:
        return fallback


@router.get("/health")
def health(request: Request) -> dict:
    # v1.4: sec.json_ttl_seconds ve auto_refresh_minutes — uygulamanın gerçekten kullandığı
    # değer (create_app parametresi veya ortam değişkeni; kapalıysa 0).
    return {"status": "ok", "version": __version__, "models": jobs.model_status(),
            "sec": {"user_agent_configured": sec_user_agent_configured(),
                    "json_ttl_seconds": _config_value(get_sec_json_ttl, None)},
            "auto_refresh_minutes": getattr(request.app.state, "auto_refresh_minutes", 0)}


@router.get("/companies")
def list_companies(session: Session = Depends(get_session)) -> list[dict]:
    return [views.company_list_item(e["company"], e["analyzed"], e["last_filing_date"],
                                    e["score"]) for e in repo.list_companies(session)]


@router.get("/companies/search")
def search(request: Request, response: Response,
           q: str = Query(..., min_length=1, max_length=100),
           limit: int = Query(10, ge=1, le=50),
           session: Session = Depends(get_session)) -> list[dict]:
    """FR-01: SEC ticker listesinde arama; liste alınamazsa yalnızca işlenmiş şirketler.

    Hangi kapsamda arandığı yanıt başlığında bildirilir (sessiz düşüş olmasın): arayüz
    "yalnızca analiz edilmiş şirketler" uyarısını ve nedenini gösterir.
    """
    ingested = repo.all_tickers(session)
    maps = request.app.state.ticker_maps
    ticker_map = maps.get()
    if ticker_map:
        response.headers[SEARCH_SCOPE_HEADER] = "sec"
    else:
        response.headers[SEARCH_SCOPE_HEADER] = "local"
        response.headers[SEARCH_REASON_HEADER] = maps.failure_reason or "sec_unavailable"
    results = search_companies(ticker_map, q, limit) if ticker_map else []
    if len(results) < limit:
        seen = {r["ticker"] for r in results}
        for r in repo.search_local(session, q, limit):
            if r["ticker"] and r["ticker"].upper() not in seen and len(results) < limit:
                results.append(r)
                seen.add(r["ticker"].upper())
    return [{"ticker": r["ticker"], "cik": r["cik"], "name": r["name"],
             "ingested": (r["ticker"] or "").upper() in ingested} for r in results]


@router.post("/companies/{ticker}/ingest", status_code=202)
def ingest(ticker: str, request: Request, background_tasks: BackgroundTasks,
           body: IngestRequest | None = None,
           session: Session = Depends(get_session)) -> dict:
    """FR-15: arka planda veri çekme + analiz. Çalışan iş varsa onun kimliği döner."""
    t = check_ticker(ticker)
    # K3: aynı CIK'in diğer ticker'larıyla başlatılmış iş de "çalışan iş" sayılır (GOOG/GOOGL).
    # SEC listesi yalnızca önbellekteyse kullanılır (bu istek ağa çıkmaz).
    ticker_map = request.app.state.ticker_maps.peek()
    aliases = same_cik_tickers(ticker_map, t)
    # Kayıtsız şirkette de CIK tabanlı iş kilidi (GOOG/GOOGL aynı anda iki iş açmasın).
    sec_cik = ((ticker_map or {}).get(t) or {}).get("cik")
    analyze = True if body is None else body.analyze
    runner: jobs.JobRunner = request.app.state.job_runner
    if request.app.state.check_sec_config:
        try:
            get_user_agent()
        except ConfigError as exc:
            raise HTTPException(503, str(exc)) from None
    # Kod incelemesi: kilit şirket bazında (şeritli) — SQLite busy_timeout (15 sn) sırasında
    # diğer şirketlerin ingest istekleri beklemez. Süreçler arası tekillik create_job_if_idle'da.
    reserved = False
    try:
        with _ticker_lock(t):
            running = repo.find_running_job(session, t, aliases=aliases)
            if running is not None:
                return {"job_id": running["id"], "status": running["status"]}
            # SEC-02: eşzamanlı iş kapasitesi (çalışan + kuyruk) doluysa 429.
            if not runner.try_reserve():
                raise HTTPException(
                    429, f"Şu anda çok fazla veri çekme işi var (en fazla {runner.max_running} "
                         f"çalışan, toplam {runner.capacity} iş). Mevcut işler bitince tekrar "
                         "deneyin.", headers={"Retry-After": str(INGEST_RETRY_AFTER)})
            reserved = True
            # Aşama 4 (SEC-16 madde 3): süreçler arası atomik (refresh CLI / otomatik
            # yenileyici ile aynı şirket için iki iş açılmaz).
            try:
                job, created = repo.create_job_if_idle(session, t, analyze, aliases=aliases,
                                                       cik=sec_cik)
            except repo.JobBusyError as exc:
                raise HTTPException(409, str(exc)) from None
    except OperationalError:
        # SQLite "database is locked" (busy_timeout doldu) vb.: genel 500 yerine 503.
        session.rollback()
        if reserved:
            runner.release()
        raise HTTPException(503, DB_BUSY_MESSAGE,
                            headers={"Retry-After": str(DB_BUSY_RETRY_AFTER)}) from None
    except BaseException:
        if reserved:
            runner.release()
        raise
    if not created:
        runner.release()
    if created:
        # SEC-02: iş, istek iş parçacığı havuzunda DEĞİL, sınırlı JobRunner havuzunda çalışır.
        # fresh=True: "Güncelle" akışında yeni 10-Q önbellekteki eski JSON yüzünden kaçmasın.
        future = runner.submit(jobs.run_ingest_job, job["id"], t, analyze,
                               session_factory=request.app.state.session_factory,
                               client_factory=request.app.state.client_factory, fresh=True)
        # Yanıt gönderildikten sonra iş bitene kadar (iş parçacığı TUTMADAN) beklenir; TestClient
        # yanıtı işin sonunda döndürür (önceki BackgroundTasks davranışıyla aynı).
        background_tasks.add_task(_await_job, future)
    return {"job_id": job["id"], "status": job["status"]}


async def _await_job(future: Future) -> None:
    with contextlib.suppress(Exception):
        await asyncio.wrap_future(future)


@router.get("/jobs/{job_id}")
def get_job(job_id: int, session: Session = Depends(get_session)) -> dict:
    job = repo.get_job(session, job_id)
    if job is None:
        raise HTTPException(404, f"İş bulunamadı: {job_id}")
    return views.job_view(job)


@router.get("/companies/{ticker}/overview")
def overview(ticker: str, session: Session = Depends(get_session)) -> dict:
    company = company_or_404(session, ticker)
    cid = company["id"]
    facts_ = repo.load_facts(session, cid)
    latest_analyzed = next((f for f in repo.latest_periodic_filings(session, cid)
                            if f.get("analyzed")), None)
    return views.overview_view(
        company, facts_, repo.load_latest_score(session, cid),
        repo.load_filings(session, cid, limit=views.LATEST_FILINGS),
        repo.load_sentiment_sections(session, cid), repo.load_risk(session, cid),
        analyzed=repo.is_company_analyzed(session, cid),
        dynamic=repo.load_dynamic(session, cid, facts_), latest_filing=latest_analyzed)


@router.get("/companies/{ticker}/facts")
def facts(ticker: str, metric: str | None = Query(None, description=", ".join(METRICS)),
          session: Session = Depends(get_session)) -> dict:
    company = company_or_404(session, ticker)
    if metric and metric not in METRICS:
        raise HTTPException(400, f"Bilinmeyen kalem: {metric}. Seçenekler: {', '.join(METRICS)}")
    # v1.4: `latest` alanı (TTM / son dönem) dinamik seriden gelir.
    return views.facts_view(company, repo.load_facts(session, company["id"], metric), metric,
                            dynamic=repo.load_dynamic(session, company["id"]))


@router.get("/companies/{ticker}/ratios")
def ratios(ticker: str, session: Session = Depends(get_session)) -> dict:
    company = company_or_404(session, ticker)
    return views.ratios_view(company, _ratios_for(session, company["id"]))


def _ratios_for(session: Session, company_id: int) -> dict:
    """Yıllık dönemler + (varsa) TTM dönemi (sözleşme v1.4)."""
    facts_ = repo.load_facts(session, company_id)
    return compute_ratios(facts_, periods=RATIO_PERIODS,
                          dynamic=repo.load_dynamic(session, company_id, facts_))


@router.get("/companies/{ticker}/freshness")
def freshness(ticker: str, request: Request, session: Session = Depends(get_session)) -> dict:
    """v1.4: SEC'te veritabanında olmayan yeni 10-K/10-Q var mı (stale). Yalnızca kayıtlı
    şirketler (kayıtsızda 404, SEC'e istek yok). SEC sonucu şirket başına 15 dk süreç içinde
    tutulur (hatalar dahil); küresel istek bütçesi vardır (SEC-16). SEC'e ulaşılamazsa HTTP 200,
    stale null, error Türkçe neden."""
    company = company_or_404(session, ticker)
    state = request.app.state.freshness.sec_state(company["cik"])
    return views.freshness_view(compute_freshness(state, repo.load_filings(session,
                                                                          company["id"])))


@router.get("/companies/{ticker}/score")
def score(ticker: str, session: Session = Depends(get_session)) -> dict:
    company = company_or_404(session, ticker)
    return views.score_view(repo.load_latest_score(session, company["id"]))


@router.get("/companies/{ticker}/filings")
def filings(ticker: str,
            form: str | None = Query(None, max_length=12, description="10-K, 10-Q veya 8-K"),
            limit: int = Query(50, ge=1, le=500),
            session: Session = Depends(get_session)) -> dict:
    company = company_or_404(session, ticker)
    rows = repo.load_filings(session, company["id"], form=form, limit=limit)
    return views.filings_view(company, rows)


@router.get("/companies/{ticker}/sentiment")
def sentiment(ticker: str, session: Session = Depends(get_session)) -> dict:
    company = company_or_404(session, ticker)
    return views.sentiment_view(repo.load_sentiment_sections(session, company["id"]),
                                default_model=jobs.model_status()["sentiment"])


@router.get("/companies/{ticker}/risk-changes")
def risk_changes(ticker: str, session: Session = Depends(get_session)) -> dict:
    company = company_or_404(session, ticker)
    return views.risk_view(repo.load_risk(session, company["id"]))


@router.post("/companies/{ticker}/ask")
def ask(ticker: str, body: AskRequest, request: Request,
        session: Session = Depends(get_session)) -> dict:
    """FR-10: kaynak gösteren soru-cevap. LLM yoksa çıkarımsal (extractive) mod.

    SEC-03 ek önlemleri: dakikada en fazla ``FINSIGHT_ASK_PER_MINUTE`` soru (aşılırsa 429);
    LLM'li günlük soru sınırı ``FINSIGHT_LLM_DAILY_LIMIT`` dolunca cevap LLM'siz üretilir.
    SEC-19: ``FINSIGHT_QA_LOG=0`` iken soru veritabanına yazılmaz."""
    retry_after = request.app.state.ask_limiter.hit()
    if retry_after is not None:
        raise HTTPException(429, "Çok fazla soru gönderildi; lütfen biraz bekleyip tekrar "
                                 "deneyin.", headers={"Retry-After": str(retry_after)})
    company = company_or_404(session, ticker)
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "Soru boş olamaz.")
    embedder = jobs.get_embedder_cached()
    llm = jobs.get_llm()
    budget_warning = None
    if llm is not None and not request.app.state.llm_budget.take():
        llm = None
        budget_warning = LLM_BUDGET_WARNING
    chunks = repo.load_chunks_for_rag(session, company["id"], embedder.name)
    result = answer_question(question, chunks, embedder, llm)
    if not chunks and "warning" not in result and \
            repo.count_chunks_other_models(session, company["id"], embedder.name) > 0:
        # K4: parçalar başka bir gömme modeliyle oluşturulmuş → yeniden analiz uyarısı.
        result["warning"] = EMBEDDING_MISMATCH_WARNING
    if budget_warning and "warning" not in result:
        result["warning"] = budget_warning
    if qa_log_enabled():
        repo.log_qa(session, company["id"], question, result)
    return views.json_safe({"question": question, **result, "disclaimer": DISCLAIMER})


@router.get("/compare")
def compare(tickers: str = Query(..., max_length=100, description="Virgülle 2–4 ticker"),
            session: Session = Depends(get_session)) -> dict:
    """FR-13: 2–4 şirketin son dönem oranları ve skoru."""
    requested: list[str] = []
    for raw in tickers.split(","):
        raw = raw.strip()
        if not raw:
            continue
        t = check_ticker(raw)
        if t not in requested:
            requested.append(t)
    if not 2 <= len(requested) <= 4:
        raise HTTPException(400, "Karşılaştırma için 2 ile 4 arasında farklı şirket seçin.")
    entries = []
    for t in requested:
        company = company_or_404(session, t)
        entries.append({"company": company,
                        "ratios": _ratios_for(session, company["id"]),
                        "score": repo.load_latest_score(session, company["id"])})
    return views.compare_view(entries)


@router.get("/companies/{ticker}/export.csv")
def export_csv(ticker: str, session: Session = Depends(get_session)) -> Response:
    """FR-16: kalemler, oranlar ve skor; UTF-8 BOM'lu CSV (Excel uyumu)."""
    company = company_or_404(session, ticker)
    facts_ = repo.load_facts(session, company["id"])
    rows = views.export_rows(company, facts_, _ratios_for(session, company["id"]),
                             repo.load_latest_score(session, company["id"]))
    filename = f"finsight-{company['ticker'] or company['cik']}.csv"
    # O12: CSV'nin içine yorum satırı eklenmez (Excel'de ilk satır başlık olmalı); uyarı
    # metni yanıt başlığındadır. HTTP başlıkları ASCII olmalı → URL kodlaması.
    return Response(content=views.export_csv(rows).encode("utf-8"),
                    media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"',
                             DISCLAIMER_HEADER: quote(DISCLAIMER, safe="")})


# --------------------------------------------------------------------------- güvenlik katmanı
# docs/guvenlik/duzeltmeler.md. Yerel, tek kullanıcılı uygulama: kimlik doğrulama yerine
# orantılı önlemler — yalnızca yerel Host, başka siteden gelen durum değiştiren isteğin reddi,
# güvenlik başlıkları ve kaynak sınırları.

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
# API'nin kendi kökeni (uvicorn varsayılanı); Host'tan türetilen aynı köken ayrıca kabul edilir.
SELF_ORIGINS = ("http://127.0.0.1:8000", "http://localhost:8000")
DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")
# /docs (Swagger) ve /redoc JS/CSS'i CDN'den yükler; yalnızca bu sayfalar için gevşek politika.
DOCS_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
            "font-src 'self' data: https://fonts.gstatic.com; "
            "img-src 'self' data: https://fastapi.tiangolo.com https://cdn.redoc.ly; "
            "worker-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'; object-src 'none'")
HOST_REJECTED = ("Geçersiz Host başlığı: FinSight yalnızca 127.0.0.1 / localhost üzerinden "
                 "kullanılabilir (DNS rebinding koruması).")
ORIGIN_REJECTED = "Başka bir siteden gelen istek reddedildi (CSRF koruması)."


def build_app_csp(script_hashes: Iterable[str] = ()) -> str:
    """SEC-12: arayüz ve API için Content-Security-Policy. index.html'deki satır içi tema
    betiğine yalnızca SHA-256 özetiyle izin verilir ('unsafe-inline' yok)."""
    scripts = " ".join(["'self'", *(f"'sha256-{h}'" for h in script_hashes)])
    return (f"default-src 'self'; script-src {scripts}; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


def inline_script_hashes(dist: str | None) -> list[str]:
    """Derlenmiş index.html'deki satır içi <script> bloklarının base64 SHA-256 özetleri."""
    if not dist:
        return []
    try:
        with open(os.path.join(dist, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
    except OSError:
        return []
    out = []
    for m in re.finditer(r"<script\b(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script\s*>", html,
                         re.S | re.I):
        out.append(base64.b64encode(hashlib.sha256(m.group(1).encode("utf-8")).digest())
                   .decode("ascii"))
    return out


def _host_name(host: str) -> str:
    """"127.0.0.1:8000" → "127.0.0.1"; "[::1]:8000" → "::1"; küçük harf, sondaki nokta yok."""
    host = host.strip().lower()
    if host.startswith("["):
        return host[1:host.find("]")] if "]" in host else ""
    if host.count(":") == 1:
        host = host.split(":", 1)[0]
    return host.rstrip(".")


class LocalGuardMiddleware:
    """Saf ASGI ara katmanı (en dışta):

    - SEC-03: Host başlığı izinli listede değilse 400 (DNS rebinding).
    - SEC-01: durum değiştiren yöntemlerde (POST/PUT/PATCH/DELETE) ``Sec-Fetch-Site:
      cross-site`` veya izinli olmayan ``Origin`` (``null`` dahil) → 403. Origin'siz istekler
      (curl, betikler, bench_api.py) etkilenmez.
    - SEC-12: tüm yanıtlara CSP, X-Frame-Options, nosniff, Referrer-Policy.
    """

    def __init__(self, app, *, allowed_hosts: Iterable[str], allowed_origins: Iterable[str],
                 app_csp: str) -> None:
        self.app = app
        self.allowed_hosts = {h.strip().strip("[]").lower().rstrip(".") for h in allowed_hosts}
        self.allowed_origins = {o.rstrip("/").lower() for o in allowed_origins}
        self.app_csp = app_csp

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers: dict[str, str] = {}
        for k, v in scope.get("headers") or ():
            headers.setdefault(k.decode("latin-1").lower(), v.decode("latin-1"))
        host = headers.get("host", "")
        path = scope.get("path") or ""
        csp = DOCS_CSP if path.startswith(DOCS_PATHS) else self.app_csp
        if _host_name(host) not in self.allowed_hosts:
            await self._reject(send, 400, HOST_REJECTED, csp)
            return
        if scope.get("method", "GET").upper() not in SAFE_METHODS:
            origin = headers.get("origin")
            site = headers.get("sec-fetch-site", "").strip().lower()
            same = f"{scope.get('scheme', 'http')}://{host}".lower()
            if site == "cross-site" or (origin is not None and origin.rstrip("/").lower()
                                        not in self.allowed_origins | {same}):
                await self._reject(send, 403, ORIGIN_REJECTED, csp)
                return

        async def send_with_headers(message) -> None:
            if message.get("type") == "http.response.start":
                raw = list(message.get("headers") or [])
                present = {k.lower() for k, _ in raw}
                for name, value in self._security_headers(csp):
                    if name not in present:
                        raw.append((name, value))
                message = {**message, "headers": raw}
            await send(message)

        await self.app(scope, receive, send_with_headers)

    @staticmethod
    def _security_headers(csp: str) -> list[tuple[bytes, bytes]]:
        return [(b"content-security-policy", csp.encode("latin-1")),
                (b"x-frame-options", b"DENY"),
                (b"x-content-type-options", b"nosniff"),
                (b"referrer-policy", b"no-referrer"),
                (b"cross-origin-opener-policy", b"same-origin")]

    async def _reject(self, send, status: int, detail: str, csp: str) -> None:
        body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json; charset=utf-8"),
                                (b"content-length", str(len(body)).encode()),
                                *self._security_headers(csp)]})
        await send({"type": "http.response.body", "body": body})


class SlidingWindowLimiter:
    """SEC-03 ek önlem: ``window`` saniyede en fazla ``limit`` olay (süreç içi, iş parçacığı
    güvenli). ``hit()`` izin verirse None, aksi halde Retry-After saniyesi döndürür."""

    def __init__(self, limit: int, window: float,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.window = window
        self._clock = clock
        self._events: collections.deque[float] = collections.deque()
        self._lock = threading.Lock()

    def hit(self) -> int | None:
        with self._lock:
            now = self._clock()
            while self._events and now - self._events[0] >= self.window:
                self._events.popleft()
            if len(self._events) >= self.limit:
                return max(1, int(self.window - (now - self._events[0])) + 1)
            self._events.append(now)
            return None


class DailyBudget:
    """SEC-03 ek önlem: günlük LLM'li soru bütçesi (0 = sınırsız; gün yerel saate göre)."""

    def __init__(self, limit: int, today: Callable[[], _dt.date] = _dt.date.today) -> None:
        self.limit = limit
        self._today = today
        self._day: _dt.date | None = None
        self._used = 0
        self._lock = threading.Lock()

    def take(self) -> bool:
        if self.limit <= 0:
            return True
        with self._lock:
            day = self._today()
            if day != self._day:
                self._day, self._used = day, 0
            if self._used >= self.limit:
                return False
            self._used += 1
            return True


# --------------------------------------------------------------------------- uygulama


async def _validation_error(_request: Request, exc: Exception) -> JSONResponse:
    """422 yanıtında da detail Türkçe metin olsun (sözleşme); ayrıntılar errors altında."""
    errors_fn = getattr(exc, "errors", None)
    raw = errors_fn() if callable(errors_fn) else []
    errors: list[dict] = ([e for e in raw if isinstance(e, dict)]
                          if isinstance(raw, (list, tuple)) else [])
    parts = []
    for e in errors:
        loc = ".".join(str(p) for p in e.get("loc", ()) if p not in ("body", "query", "path"))
        parts.append(f"{loc}: {e.get('msg')}" if loc else str(e.get("msg")))
    detail = "Geçersiz istek" + (": " + "; ".join(parts) if parts else ".")
    return JSONResponse(status_code=422,
                        content={"detail": detail, "errors": views.json_safe(
                            [{"loc": list(e.get("loc", ())), "msg": e.get("msg")}
                             for e in errors])})


async def _unhandled_error(_request: Request, exc: Exception) -> JSONResponse:
    log.error("Beklenmeyen hata: %s", type(exc).__name__, exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Beklenmeyen sunucu hatası."})


def create_app(*, database_url: str | None = None,
               session_factory: sessionmaker[Session] | None = None,
               client_factory: Callable[[], SecClient] | None = None,
               frontend_dist: str | None = None,
               serve_frontend: bool = True,
               auto_refresh_minutes: int | None = None,
               allowed_hosts: Iterable[str] | None = None) -> FastAPI:
    """Uygulama fabrikası. Testler session_factory ve sahte SEC client_factory verir.

    allowed_hosts: kabul edilen Host adları (SEC-03); verilmezse 127.0.0.1, localhost, ::1 +
    ``FINSIGHT_ALLOWED_HOSTS``.

    session_factory verilmezse DATABASE_URL (veya database_url) ile motor kurulur; her iki
    durumda da tablolar oluşturulur / eksik sütunlar eklenir (init_db).
    """
    from .db import init_db, make_engine, make_session_factory

    if session_factory is None:
        engine = make_engine(database_url)
        init_db(engine)
        session_factory = make_session_factory(engine)
    else:
        bind = session_factory.kw.get("bind")
        if bind is not None:
            init_db(bind)

    # Geçersiz FINSIGHT_SEC_JSON_TTL / FINSIGHT_AUTO_REFRESH_MINUTES → ConfigError (SEC-16/5).
    if auto_refresh_minutes is None:
        auto_refresh_minutes = get_auto_refresh_minutes()
    get_sec_json_ttl()
    refresher_box: dict = {}

    @contextlib.asynccontextmanager
    async def lifespan(app_: FastAPI):
        refresher = refresher_box.get("refresher")
        if refresher is not None:
            refresher.start()
        try:
            yield
        finally:
            if refresher is not None:
                refresher.stop()
            app_.state.job_runner.shutdown()  # SEC-02: kuyruktaki işler iptal

    app = FastAPI(title="FinSight AI", version=__version__,
                  description="SEC raporlarından finansal analiz. " + DISCLAIMER,
                  lifespan=lifespan)
    app.state.session_factory = session_factory
    app.state.client_factory = client_factory or jobs.make_sec_client
    app.state.check_sec_config = client_factory is None
    # Güvenlik (docs/guvenlik/duzeltmeler.md): SEC-02 iş kuyruğu, SEC-03 /ask sınırları.
    app.state.job_runner = jobs.JobRunner()
    app.state.ask_limiter = SlidingWindowLimiter(get_ask_per_minute(), 60.0)
    app.state.llm_budget = DailyBudget(get_llm_daily_limit())
    app.state.ticker_maps = _TickerMapCache(app.state.client_factory)
    app.state.freshness = FreshnessService(sec_fetcher(app.state.client_factory),
                                           error_fn=jobs.safe_error)
    app.state.auto_refresher = None
    app.state.auto_refresh_minutes = int(auto_refresh_minutes or 0)
    if auto_refresh_minutes:
        factory = session_factory
        # Yenileyici kullanıcı isteklerinin tazelik bütçesini paylaşmaz: kendi servisi ve
        # bütçesi vardır (bütçe dolunca tur kesilir, sonraki tur kalınan yerden sürer).
        app.state.refresh_freshness = FreshnessService(
            sec_fetcher(app.state.client_factory), error_fn=jobs.safe_error,
            budget=RequestBudget(limit=AUTO_REFRESH_BUDGET_PER_MINUTE))
        app.state.auto_refresher = refresher_box["refresher"] = AutoRefresher(
            auto_refresh_minutes,
            list_fn=lambda: db_company_entries(factory),
            check_fn=lambda c: check_company(app.state.refresh_freshness, factory, c),
            start_fn=lambda c: start_refresh_job(c, session_factory=factory,
                                                 client_factory=app.state.client_factory,
                                                 lock=_ingest_lock),
            lock=ProcessLock(os.path.join(get_cache_dir(), LOCK_FILE_NAME)))
    if client_factory is None and not sec_user_agent_configured():
        log.warning("SEC_USER_AGENT tanımlı değil: arama yalnızca analiz edilmiş şirketlerde "
                    "çalışır, yeni şirket çekilemez. Depo kökündeki .env.example dosyasını .env "
                    "olarak kopyalayıp SEC_USER_AGENT satırını doldurun.")

    app.add_middleware(CORSMiddleware, allow_origins=get_cors_origins(),
                       allow_credentials=False, allow_methods=["*"], allow_headers=["*"],
                       expose_headers=[DISCLAIMER_HEADER, SEARCH_SCOPE_HEADER,
                                       SEARCH_REASON_HEADER, "Content-Disposition"])
    # En dışta (son eklenen): Host / Origin denetimi + güvenlik başlıkları (SEC-01/03/12).
    dist = frontend_dist or (get_frontend_dist() if serve_frontend else None)
    app.add_middleware(
        LocalGuardMiddleware,
        allowed_hosts=list(allowed_hosts) if allowed_hosts is not None else get_allowed_hosts(),
        allowed_origins=[*get_cors_origins(), *SELF_ORIGINS],
        app_csp=build_app_csp(inline_script_hashes(dist) if dist and serve_frontend else []))
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _unhandled_error)
    app.include_router(router)

    dist = frontend_dist or (get_frontend_dist() if serve_frontend else None)
    if dist and serve_frontend:
        # API yönlendiricisinden SONRA: /api/* ve /docs önce eşleşir.
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    else:
        @app.get("/", include_in_schema=False)
        def root() -> dict:
            return {"name": "FinSight AI", "version": __version__, "api": "/api/health",
                    "docs": "/docs",
                    "frontend": "Arayüz derlenmemiş: cd frontend && npm install && npm run build",
                    "disclaimer": DISCLAIMER}
    return app


_app: FastAPI | None = None
_app_lock = threading.Lock()


def __getattr__(name: str):
    """``uvicorn finsight.api:app`` için tembel uygulama (içe aktarma DB açmaz)."""
    if name == "app":
        global _app
        with _app_lock:
            if _app is None:
                try:
                    _app = create_app()
                except AttributeError as exc:
                    # K7: modül __getattr__ içinden sızan AttributeError, Python tarafından
                    # "app özniteliği yok" diye yorumlanır ve asıl hata gizlenir.
                    raise RuntimeError(f"FinSight uygulaması kurulamadı: {exc}") from exc
            return _app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
