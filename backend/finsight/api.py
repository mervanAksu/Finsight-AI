"""FastAPI uygulaması: docs/api-contract.md'deki 14 uç nokta (/api öneki) + derlenmiş arayüz.

Çalıştırma: ``uvicorn finsight.api:app --reload`` → http://127.0.0.1:8000 (arayüz derlenmişse),
http://127.0.0.1:8000/docs (Swagger). Bu modül yalnızca HTTP ↔ servis çevirisi yapar: veri
``repository``, JSON şekli ``views``, hesaplar saf modüllerdedir. Uç noktalar senkron
(``def``) tanımlıdır; FastAPI bunları iş parçacığı havuzunda çalıştırır.

``app`` modül özniteliği tembel oluşturulur (``create_app``): içe aktarmak veritabanı açmaz.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Iterator
from urllib.parse import quote

from fastapi import (APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException, Query,
                     Request)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from . import DISCLAIMER, __version__, jobs, views
from . import repository as repo
from .config import ConfigError, get_cors_origins, get_frontend_dist, get_user_agent
from .edgar import build_ticker_map, search_companies
from .rag import EMBEDDING_MISMATCH_WARNING, answer_question
from .ratios import compute_ratios
from .sec_client import SecClient
from .xbrl import METRICS

log = logging.getLogger(__name__)

TICKER_RE = re.compile(r"^[A-Za-z0-9.\-]{1,10}$")
RATIO_PERIODS = 3
TICKER_MAP_TTL = 6 * 3600       # başarılı company_tickers listesi süreç içinde 6 saat tutulur
TICKER_MAP_RETRY = 300          # başarısız denemeden sonra 5 dk yalnızca yerel arama
DISCLAIMER_HEADER = "X-FinSight-Disclaimer"  # CSV'de uyarı metni (URL kodlu, ASCII)

# O1: "çalışan iş var mı?" kontrolü ile iş oluşturma arasında başka istek araya girmesin
# (aynı şirket için iki iş açılmasın). Tek süreç içindir; çok süreçli dağıtımda Aşama 4.
_ingest_lock = threading.Lock()


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
    if not TICKER_RE.match(ticker or ""):
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

    def get(self) -> dict[str, dict] | None:
        now = time.monotonic()
        with self._lock:
            if self._map is not None and now - self._fetched_at < TICKER_MAP_TTL:
                return self._map
            if self._failed_at is not None and now - self._failed_at < TICKER_MAP_RETRY:
                return self._map  # son başarılı liste (varsa) veya None
            try:
                self._map = build_ticker_map(self._factory().company_tickers())
                self._fetched_at = now
                self._failed_at = None
            except Exception as exc:  # User-Agent yok, ağ yok, SEC hatası → yerel arama
                log.warning("company_tickers alınamadı (%s); yalnızca yerel arama.",
                            type(exc).__name__)
                self._failed_at = now
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


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__, "models": jobs.model_status()}


@router.get("/companies")
def list_companies(session: Session = Depends(get_session)) -> list[dict]:
    return [views.company_list_item(e["company"], e["analyzed"], e["last_filing_date"],
                                    e["score"]) for e in repo.list_companies(session)]


@router.get("/companies/search")
def search(request: Request, q: str = Query(..., min_length=1, max_length=100),
           limit: int = Query(10, ge=1, le=50),
           session: Session = Depends(get_session)) -> list[dict]:
    """FR-01: SEC ticker listesinde arama; liste alınamazsa yalnızca işlenmiş şirketler."""
    ingested = repo.all_tickers(session)
    ticker_map = request.app.state.ticker_maps.get()
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
    aliases = same_cik_tickers(request.app.state.ticker_maps.peek(), t)
    analyze = True if body is None else body.analyze
    with _ingest_lock:
        running = repo.find_running_job(session, t, aliases=aliases)
        if running is not None:
            return {"job_id": running["id"], "status": running["status"]}
        if request.app.state.check_sec_config:
            try:
                get_user_agent()
            except ConfigError as exc:
                raise HTTPException(503, str(exc)) from None
        job = repo.create_job(session, t, analyze)
    background_tasks.add_task(jobs.run_ingest_job, job["id"], t, analyze,
                              session_factory=request.app.state.session_factory,
                              client_factory=request.app.state.client_factory)
    return {"job_id": job["id"], "status": job["status"]}


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
    return views.overview_view(
        company, repo.load_facts(session, cid), repo.load_latest_score(session, cid),
        repo.load_filings(session, cid, limit=views.LATEST_FILINGS),
        repo.load_sentiment_sections(session, cid), repo.load_risk(session, cid),
        analyzed=repo.is_company_analyzed(session, cid))


@router.get("/companies/{ticker}/facts")
def facts(ticker: str, metric: str | None = Query(None, description=", ".join(METRICS)),
          session: Session = Depends(get_session)) -> dict:
    company = company_or_404(session, ticker)
    if metric and metric not in METRICS:
        raise HTTPException(400, f"Bilinmeyen kalem: {metric}. Seçenekler: {', '.join(METRICS)}")
    return views.facts_view(company, repo.load_facts(session, company["id"], metric), metric)


@router.get("/companies/{ticker}/ratios")
def ratios(ticker: str, session: Session = Depends(get_session)) -> dict:
    company = company_or_404(session, ticker)
    computed = compute_ratios(repo.load_facts(session, company["id"]), periods=RATIO_PERIODS)
    return views.ratios_view(company, computed)


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
def ask(ticker: str, body: AskRequest, session: Session = Depends(get_session)) -> dict:
    """FR-10: kaynak gösteren soru-cevap. LLM yoksa çıkarımsal (extractive) mod."""
    company = company_or_404(session, ticker)
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "Soru boş olamaz.")
    embedder = jobs.get_embedder_cached()
    llm = jobs.get_llm()
    chunks = repo.load_chunks_for_rag(session, company["id"], embedder.name)
    result = answer_question(question, chunks, embedder, llm)
    if not chunks and "warning" not in result and \
            repo.count_chunks_other_models(session, company["id"], embedder.name) > 0:
        # K4: parçalar başka bir gömme modeliyle oluşturulmuş → yeniden analiz uyarısı.
        result["warning"] = EMBEDDING_MISMATCH_WARNING
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
        facts_ = repo.load_facts(session, company["id"])
        entries.append({"company": company,
                        "ratios": compute_ratios(facts_, periods=RATIO_PERIODS),
                        "score": repo.load_latest_score(session, company["id"])})
    return views.compare_view(entries)


@router.get("/companies/{ticker}/export.csv")
def export_csv(ticker: str, session: Session = Depends(get_session)) -> Response:
    """FR-16: kalemler, oranlar ve skor; UTF-8 BOM'lu CSV (Excel uyumu)."""
    company = company_or_404(session, ticker)
    facts_ = repo.load_facts(session, company["id"])
    rows = views.export_rows(company, facts_, compute_ratios(facts_, periods=RATIO_PERIODS),
                             repo.load_latest_score(session, company["id"]))
    filename = f"finsight-{company['ticker'] or company['cik']}.csv"
    # O12: CSV'nin içine yorum satırı eklenmez (Excel'de ilk satır başlık olmalı); uyarı
    # metni yanıt başlığındadır. HTTP başlıkları ASCII olmalı → URL kodlaması.
    return Response(content=views.export_csv(rows).encode("utf-8"),
                    media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"',
                             DISCLAIMER_HEADER: quote(DISCLAIMER, safe="")})


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
               serve_frontend: bool = True) -> FastAPI:
    """Uygulama fabrikası. Testler session_factory ve sahte SEC client_factory verir.

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

    app = FastAPI(title="FinSight AI", version=__version__,
                  description="SEC raporlarından finansal analiz. " + DISCLAIMER)
    app.state.session_factory = session_factory
    app.state.client_factory = client_factory or jobs.make_sec_client
    app.state.check_sec_config = client_factory is None
    app.state.ticker_maps = _TickerMapCache(app.state.client_factory)

    app.add_middleware(CORSMiddleware, allow_origins=get_cors_origins(),
                       allow_credentials=False, allow_methods=["*"], allow_headers=["*"],
                       expose_headers=[DISCLAIMER_HEADER, "Content-Disposition"])
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
