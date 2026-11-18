"""Arka plan veri çekme + analiz işleri (FR-15) ve süreç düzeyinde model önbelleği.

Akış (docs/entegrasyon-plani.md): collect → save → her başvuru için belge indir → bölümler +
duygu → gömme → risk karşılaştırması → skor. Her adım ve ilerleme (0–1) ``ingestion_job``
tablosuna yazılır. Bir başvurudaki hata işi durdurmaz (işin ``warnings`` listesine yazılır);
genel hata ``status=error`` ve gizli bilgi içermeyen bir mesajla kaydedilir.

Ağır modeller (FinBERT, sentence-transformers) süreç başına bir kez, ilk ihtiyaçta yüklenir.
"""

from __future__ import annotations

import hashlib
import importlib.util
import logging
import os
import threading
from collections.abc import Callable
from datetime import date

from sqlalchemy.orm import Session, sessionmaker

from . import repository as repo
from .analyze import (analyze_sections, compare_risk, compute_text_signals,
                      default_chunk_words, embed_sections, latest_annual_summaries,
                      latest_two_annual, score_company, select_filings_for_analysis)
from .config import (ConfigError, get_cache_dir, get_documents_dir, get_max_rps,
                     get_user_agent)
from .edgar import build_ticker_map
from .embeddings import DEFAULT_ST_MODEL, HASHING_NAME, Embedder, get_embedder
from .llm import LLMClient, LLMError, get_llm_client
from .models import utcnow
from .pipeline import collect
from .rate_limiter import RateLimiter
from .sec_client import FileCache, SecClient, SecError
from .sentiment import (FINBERT_NAME, LEXICON_NAME, LM_LEXICON_NAME, SentimentModel,
                        get_sentiment_model)
from .views import current_sentiment_model, sentiment_sections

log = logging.getLogger(__name__)

DEFAULT_YEARS = 3  # başvuru listesi; finansal kalemler years+1 (bkz. pipeline.collect)
ProgressFn = Callable[[str, float], None]

# --------------------------------------------------------------------------- model önbelleği

# Her model kendi kilidini kullanır: FinBERT yüklenirken /health ve SEC istekleri beklemesin.
_sentiment_lock = threading.Lock()
_embedder_lock = threading.Lock()
_llm_lock = threading.Lock()
_limiter_lock = threading.Lock()


class _ModelCache:
    """Süreç düzeyinde model önbelleği (FinBERT her istekte yeniden yüklenmesin)."""

    def __init__(self) -> None:
        self.sentiment: SentimentModel | None = None
        self.embedder: Embedder | None = None
        self.llm: LLMClient | None = None
        self.llm_ready = False


_cache = _ModelCache()
_UNSET = object()


def get_sentiment() -> SentimentModel:
    """Süreç başına tek duygu modeli (FinBERT yoksa sözlük yedeği)."""
    with _sentiment_lock:
        if _cache.sentiment is None:
            _cache.sentiment = get_sentiment_model()
            log.info("Duygu modeli yüklendi: %s", _cache.sentiment.name)
        return _cache.sentiment


def get_embedder_cached() -> Embedder:
    """Süreç başına tek gömücü (sentence-transformers yoksa hashing yedeği)."""
    with _embedder_lock:
        if _cache.embedder is None:
            _cache.embedder = get_embedder()
            log.info("Gömme modeli yüklendi: %s", _cache.embedder.name)
        return _cache.embedder


def get_llm() -> LLMClient | None:
    """Ortam değişkenlerinden LLM istemcisi (yoksa None); süreç başına bir kez kurulur."""
    with _llm_lock:
        if not _cache.llm_ready:
            _cache.llm = get_llm_client()
            _cache.llm_ready = True
        return _cache.llm


def override_models(*, sentiment: SentimentModel | None = None, embedder: Embedder | None = None,
                    llm: object = _UNSET) -> None:
    """Testler ve CLI için önbelleğe hazır model yerleştirir (llm=None → LLM kapalı)."""
    if sentiment is not None:
        with _sentiment_lock:
            _cache.sentiment = sentiment
    if embedder is not None:
        with _embedder_lock:
            _cache.embedder = embedder
    if llm is not _UNSET:
        with _llm_lock:
            _cache.llm = llm  # type: ignore[assignment]
            _cache.llm_ready = True


def reset_models() -> None:
    """Önbelleği boşaltır (testler; ortam değişkeni değişikliğinden sonra)."""
    with _sentiment_lock:
        _cache.sentiment = None
    with _embedder_lock:
        _cache.embedder = None
    with _llm_lock:
        _cache.llm = None
        _cache.llm_ready = False


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def model_status() -> dict:
    """Kullanılan / kullanılacak model adları — ağır modelleri YÜKLEMEDEN (GET /health)."""
    sentiment = _cache.sentiment  # kilitsiz okuma: yüklenmekte olan modeli beklemez
    embedder = _cache.embedder
    if sentiment is not None:
        sentiment_name = sentiment.name
    elif os.getenv("FINSIGHT_SENTIMENT_MODEL", "").strip().lower() != "lexicon" \
            and _has_module("transformers"):
        sentiment_name = FINBERT_NAME
    else:
        sentiment_name = LM_LEXICON_NAME if os.getenv("LM_DICTIONARY_PATH") else LEXICON_NAME
    if embedder is not None:
        embedding_name = embedder.name
    elif os.getenv("FINSIGHT_EMBEDDER", "").strip().lower() != "hashing" \
            and _has_module("sentence_transformers"):
        embedding_name = os.getenv("FINSIGHT_EMBEDDING_MODEL") or DEFAULT_ST_MODEL
    else:
        embedding_name = HASHING_NAME
    llm = get_llm()
    return {
        "sentiment": sentiment_name,
        "embedding": embedding_name,
        "llm": ({"provider": llm.provider, "model": llm.model, "enabled": True} if llm
                else {"provider": "none", "model": None, "enabled": False}),
        "loaded": {"sentiment": sentiment is not None, "embedding": embedder is not None},
    }


# --------------------------------------------------------------------------- SEC istemcisi

_limiter: RateLimiter | None = None


def shared_limiter() -> RateLimiter:
    """Süreçteki tüm SEC istekleri tek hız sınırlayıcıyı paylaşır (eşzamanlı işler dahil,
    NFR-02)."""
    global _limiter
    with _limiter_lock:
        if _limiter is None:
            _limiter = RateLimiter(get_max_rps())
        return _limiter


def make_sec_client() -> SecClient:
    """Yapılandırmadan SecClient (User-Agent zorunlu, önbellekli, paylaşılan hız sınırı)."""
    return SecClient(get_user_agent(), limiter=shared_limiter(), cache=FileCache(get_cache_dir()))


# --------------------------------------------------------------------------- yardımcılar

_SECRET_ENV = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "POSTGRES_PASSWORD")


def safe_error(exc: BaseException) -> str:
    """Kullanıcıya gösterilecek hata metni; API anahtarları ve veritabanı parolası maskelenir."""
    if isinstance(exc, KeyError):
        message = str(exc.args[0]) if exc.args else "Kayıt bulunamadı"
    elif isinstance(exc, ConfigError):
        message = f"Yapılandırma hatası: {exc}"
    elif isinstance(exc, (SecError, LLMError, ValueError)):
        message = str(exc)
    else:
        message = f"Beklenmeyen hata ({type(exc).__name__})"
    for name in _SECRET_ENV:
        secret = os.getenv(name, "").strip()
        if secret and len(secret) >= 4:
            message = message.replace(secret, "***")
    return message[:500]


def _filing_label(filing: dict) -> str:
    filed = filing.get("filing_date")
    filed_text = filed.isoformat() if isinstance(filed, date) else str(filed or "")
    return f"{filing.get('form')} {filed_text}".strip()


def store_document(body: bytes, cik: str, accession_no: str, primary_document: str,
                   documents_dir: str | None = None) -> dict:
    """Belgeyi diske yazar; ``{"local_path", "sha256", "size_bytes"}``. Yazılamazsa
    local_path None (analiz yine de devam eder)."""
    meta: dict = {"local_path": None, "sha256": hashlib.sha256(body).hexdigest(),
                  "size_bytes": len(body)}
    base = documents_dir or get_documents_dir()
    name = os.path.basename(primary_document or "") or "document.htm"
    path = os.path.join(base, str(cik), accession_no.replace("-", ""), name)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(body)
        meta["local_path"] = path
    except OSError as exc:
        log.warning("Belge diske yazılamadı (%s): %s", type(exc).__name__, path)
    return meta


# --------------------------------------------------------------------------- skor


def compute_and_save_score(session: Session, company_id: int) -> dict:
    """Kayıtlı kalemler + son 10-K tonları + son risk farkından skoru hesaplar ve yazar."""
    facts = repo.load_facts(session, company_id)
    sections = repo.load_sentiment_sections(session, company_id)
    model = current_sentiment_model(sections)
    _, by_code = latest_annual_summaries(sentiment_sections(sections, model))
    risk = repo.load_risk(session, company_id)
    signals = compute_text_signals(by_code, risk)
    _, score = score_company(facts, signals)
    repo.save_score(session, company_id, score)
    return score


# --------------------------------------------------------------------------- analiz


def run_analysis_for_company(session: Session, client: SecClient, company_id: int, *,
                             progress: ProgressFn | None = None,
                             warn: Callable[[str], None] | None = None,
                             sentiment_model: SentimentModel | None = None,
                             embedder: Embedder | None = None,
                             documents_dir: str | None = None,
                             force: bool = False) -> dict:
    """Seçilen başvuruları analiz eder, risk farkını ve skoru yazar; özet döndürür.

    progress(step, frac): frac 0–1 (analiz bölümünün içindeki oran).
    Aynı başvuru aynı duygu ve gömme modeliyle analiz edildiyse atlanır (force=False).
    Hiçbir başvuru analiz edilemezse (ör. tüm belgeler 404) risk karşılaştırması, skor ve
    ``analysis_updated_at`` güncellenmez; uyarı eklenir (K2).
    """
    report = progress or (lambda _step, _frac: None)
    company = repo.load_company_by_id(session, company_id)
    if company is None:
        raise KeyError(f"Şirket bulunamadı (id={company_id})")
    # O2: ilk çalıştırmada FinBERT / sentence-transformers yüklemesi dakikalar sürebilir;
    # kullanıcı işin takıldığını sanmasın.
    report("Modeller yükleniyor", 0.0)
    sm = sentiment_model or get_sentiment()
    emb = embedder or get_embedder_cached()
    llm = get_llm()
    report(f"Modeller hazır ({sm.name}, {emb.name})", 0.0)
    summary: dict = {
        "sentiment_model": sm.name, "embedding_model": emb.name,
        "llm": {"provider": llm.provider, "model": llm.model} if llm else None,
        "filings": [], "warnings": [], "risk": None, "score": None,
    }

    def warning(message: str) -> None:
        log.warning(message)
        summary["warnings"].append(message)
        if warn is not None:
            warn(message)

    selected = select_filings_for_analysis(repo.load_filings(session, company_id))
    if not selected:
        warning("Analiz edilecek 10-K / 10-Q başvurusu bulunamadı.")
    n = max(len(selected), 1)
    for i, filing in enumerate(selected):
        label = _filing_label(filing)
        entry = {"accession_no": filing["accession_no"], "form": filing["form"],
                 "filing_date": filing["filing_date"], "status": "analyzed", "sections": [],
                 "sentences": 0, "chunks": 0}
        summary["filings"].append(entry)
        if not force and repo.is_filing_analyzed(session, filing["id"], sm.name, emb.name):
            entry["status"] = "skipped"
            report(f"Önceden analiz edilmiş, atlandı ({label})", (i + 1) / n * 0.8)
            continue
        try:
            report(f"Belge indiriliyor ({label})", i / n * 0.8)
            body = client.document(company["cik"], filing["accession_no"],
                                   filing["primary_document"])
            document = store_document(body, company["cik"], filing["accession_no"],
                                      filing["primary_document"], documents_dir)
            report(f"Bölümler ve duygu analizi ({label})", (i + 0.3) / n * 0.8)
            sections = analyze_sections(body, filing["form"], sentiment_model=sm)
            report(f"Gömme ({label})", (i + 0.7) / n * 0.8)
            embed_sections(sections, embedder=emb)
            saved = repo.save_analysis_document(session, filing["id"], {
                "sections": sections, "sentiment_model": sm.name, "embedding_model": emb.name,
                "embedding_dim": emb.dim, "chunk_words": default_chunk_words(emb),
            }, document=document)
            entry.update(saved)
            if not saved["sections"]:
                warning(f"{label}: risk / MD&A bölümü bulunamadı.")
        except Exception as exc:  # bir başvurunun hatası diğerlerini durdurmaz
            session.rollback()
            entry["status"] = "error"
            entry["error"] = safe_error(exc)
            warning(f"{label}: {entry['error']}")
            log.debug("Başvuru analizi hatası", exc_info=True)

    if not any(f["status"] in ("analyzed", "skipped") for f in summary["filings"]):
        # K2: analiz edilen başvuru yoksa eski/eksik verilerle skor yazılmaz.
        warning("Hiçbir başvuru analiz edilemedi; risk karşılaştırması ve skor güncellenmedi.")
        return summary

    report("Risk karşılaştırması", 0.85)
    old, new = latest_two_annual(selected)
    if old is not None and new is not None:
        try:
            comparison = compare_risk(repo.load_section_text(session, old["id"], "1A"),
                                      repo.load_section_text(session, new["id"], "1A"))
            if comparison is None:
                warning("Risk karşılaştırması yapılamadı: iki 10-K'nın birinde Item 1A yok.")
            else:
                repo.save_risk_comparison(session, company_id, old["id"], new["id"], comparison)
                summary["risk"] = comparison["counts"]
        except Exception as exc:
            session.rollback()
            warning(f"Risk karşılaştırması: {safe_error(exc)}")
    else:
        warning("Risk karşılaştırması için iki 10-K yok.")

    report("Skor hesaplanıyor", 0.95)
    score = compute_and_save_score(session, company_id)
    repo.touch_analysis(session, company_id)
    summary["score"] = {"total": score.get("total"), "band": score.get("band"),
                        "as_of_period": score.get("as_of_period")}
    return summary


# --------------------------------------------------------------------------- arka plan işi

_factory_lock = threading.Lock()
_default_factory: sessionmaker[Session] | None = None


def default_session_factory() -> sessionmaker[Session]:
    """DATABASE_URL'den oturum fabrikası (ilk çağrıda tablolar oluşturulur)."""
    global _default_factory
    with _factory_lock:
        if _default_factory is None:
            from .db import init_db, make_engine, make_session_factory

            engine = make_engine()
            init_db(engine)
            _default_factory = make_session_factory(engine)
        return _default_factory


def run_ingest_job(job_id: int, ticker: str, analyze: bool = True, *,
                   session_factory: sessionmaker[Session] | None = None,
                   client_factory: Callable[[], SecClient] | None = None,
                   today: date | None = None, documents_dir: str | None = None) -> None:
    """POST /ingest arka plan işi. Kendi oturumunu açar; FastAPI senkron görevi iş
    parçacığı havuzunda çalıştırır. Hata yükseltmez, sonucu işe yazar."""
    factory = session_factory or default_session_factory()
    with factory() as session:
        client: SecClient | None = None

        def update(**fields) -> None:
            if client is not None:
                fields.setdefault("request_count", client.request_count)
            repo.update_job(session, job_id, **fields)

        try:
            update(status="running", started_at=utcnow(), step="SEC verisi çekiliyor",
                   progress=0.02)
            client = (client_factory or make_sec_client)()
            ticker_map = build_ticker_map(client.company_tickers())
            data = collect(client, ticker, ticker_map, years=DEFAULT_YEARS, today=today)
            update(step="Finansal veriler kaydediliyor", progress=0.15)
            company = repo.save_collected(session, data)
            company_id = company.id

            if analyze:
                def progress(step: str, frac: float) -> None:
                    update(step=step, progress=0.2 + 0.78 * frac)

                def warn(message: str) -> None:
                    repo.add_job_warning(session, job_id, message)

                run_analysis_for_company(session, client, company_id, progress=progress,
                                         warn=warn, documents_dir=documents_dir)
            elif repo.is_company_analyzed(session, company_id):
                # Analiz yoksa skor üretilmez (sözleşme: skor analiz yoksa null); önceden
                # analiz edilmiş şirkette yeni finansal verilerle skor güncellenir.
                update(step="Skor hesaplanıyor", progress=0.9)
                compute_and_save_score(session, company_id)
            update(status="done", step="Tamamlandı", progress=1.0, finished_at=utcnow())
        except Exception as exc:
            log.error("İş %s (%s) başarısız: %s", job_id, ticker, type(exc).__name__,
                      exc_info=True)
            session.rollback()
            try:
                update(status="error", error=safe_error(exc), finished_at=utcnow())
            except Exception:  # pragma: no cover - veritabanı tamamen erişilemezse
                log.exception("İş durumu yazılamadı: %s", job_id)
