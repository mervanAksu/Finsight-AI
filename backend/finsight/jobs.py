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
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from collections.abc import Callable
from datetime import date

from sqlalchemy.orm import Session, sessionmaker

from . import repository as repo
from .analyze import (TIMING_STEPS, analyze_sections, compare_risk, compute_text_signals,
                      default_chunk_words, embed_sections, latest_tone_summaries,
                      latest_two_annual, score_company, select_filings_for_analysis,
                      sentiment_stats)
from .config import (ConfigError, analyze_quarterly_enabled, get_cache_dir,
                     get_documents_dir, get_max_concurrent_jobs, get_max_queued_jobs,
                     get_max_rps, get_sec_json_ttl, get_user_agent)
from .edgar import build_ticker_map
from .embeddings import DEFAULT_ST_MODEL, HASHING_NAME, Embedder, get_embedder
from .llm import LLMClient, LLMError, get_llm_client
from .models import utcnow
from .pipeline import collect
from .rate_limiter import FileRateLimiter, shared_file_limiter
from .sec_client import FileCache, SecClient, SecError
from .sentiment import (LEXICON_NAME, LM_LEXICON_NAME, SentimentModel,
                        configured_finbert_name, get_sentiment_model)
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
        sentiment_name = configured_finbert_name()
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

_limiter: FileRateLimiter | None = None


def shared_limiter() -> FileRateLimiter:
    """Tüm SEC istekleri tek hız bütçesini paylaşır (NFR-02): süreç içindeki eşzamanlı işler
    VE aynı önbellek klasörünü kullanan diğer süreçler (``finsight.refresh``, ikinci bir
    uvicorn) dahil (SEC-07; durum ``<SEC_CACHE_DIR>/.ratelimit``)."""
    global _limiter
    with _limiter_lock:
        if _limiter is None:
            _limiter = shared_file_limiter(get_cache_dir(), get_max_rps())
        return _limiter


def make_sec_client() -> SecClient:
    """Yapılandırmadan SecClient (User-Agent zorunlu, önbellekli, paylaşılan hız sınırı).

    Önbellek süresi URL sınıfına göredir (Archives süresiz, submissions/companyfacts
    FINSIGHT_SEC_JSON_TTL, company_tickers 24 saat; docs/asama4-sozlesme.md §5)."""
    return SecClient(get_user_agent(), limiter=shared_limiter(),
                     cache=FileCache(get_cache_dir(), json_ttl_seconds=get_sec_json_ttl()))


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


_CIK_RE = re.compile(r"\d{1,10}")
_ACCESSION_RE = re.compile(r"\d{18}")  # 0000320193-25-000001 → tiresiz 18 rakam


def store_document(body: bytes, cik: str, accession_no: str, primary_document: str,
                   documents_dir: str | None = None) -> dict:
    """Belgeyi diske yazar; ``{"local_path", "sha256", "size_bytes"}``. Yazılamazsa
    local_path None (analiz yine de devam eder)."""
    meta: dict = {"local_path": None, "sha256": hashlib.sha256(body).hexdigest(),
                  "size_bytes": len(body)}
    base = documents_dir or get_documents_dir()
    name = os.path.basename(str(primary_document or "").replace("\\", "/")) or "document.htm"
    # RT-02: CIK ve accession SEC JSON'undan gelir ama yola eklenmeden önce biçimleri
    # doğrulanır; ayrıca nihai yolun belge klasörünün içinde kaldığı denetlenir.
    cik_part = str(cik or "").strip()
    acc_part = str(accession_no or "").strip().replace("-", "")
    path = os.path.join(base, cik_part, acc_part, name)
    root = os.path.realpath(base)
    if not (_CIK_RE.fullmatch(cik_part) and _ACCESSION_RE.fullmatch(acc_part)
            and name not in (".", "..")
            and os.path.commonpath([root, os.path.realpath(path)]) == root):
        log.warning("Belge diske yazılmadı: geçersiz CIK / accession / dosya adı.")
        return meta
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(body)
        meta["local_path"] = path
    except OSError as exc:
        log.warning("Belge diske yazılamadı (%s): %s", type(exc).__name__, path)
    return meta


# --------------------------------------------------------------------------- skor


def text_signals_for(sections: list[dict], risk: dict | None) -> dict:
    """Skorun metin sinyalleri (saf): MD&A tonu en son analiz edilmiş başvurudan (10-K Item 7
    veya daha yeni 10-Q Item 2), risk tonu en son 10-K'dan; risk farkı iki son 10-K arasında
    (docs/asama4-sozlesme.md §3). Kaynak başvurular ``mdna_filing`` / ``risk_filing``."""
    model = current_sentiment_model(sections)
    tone = latest_tone_summaries(sentiment_sections(sections, model))
    signals = compute_text_signals(tone["by_code"], risk)
    signals["mdna_filing"] = tone["mdna_filing"]
    signals["risk_filing"] = tone["risk_filing"]
    return signals


def compute_and_save_score(session: Session, company_id: int) -> dict:
    """Dinamik seri (TTM varsa) + metin sinyalleri + son risk farkından skoru hesaplar, yazar."""
    facts = repo.load_facts(session, company_id)
    dynamic = repo.load_dynamic(session, company_id, facts)
    signals = text_signals_for(repo.load_sentiment_sections(session, company_id),
                               repo.load_risk(session, company_id))
    _, score = score_company(facts, signals, dynamic=dynamic)
    repo.save_score(session, company_id, score)
    return score


# --------------------------------------------------------------------------- analiz


def run_analysis_for_company(session: Session, client: SecClient, company_id: int, *,
                             progress: ProgressFn | None = None,
                             warn: Callable[[str], None] | None = None,
                             sentiment_model: SentimentModel | None = None,
                             embedder: Embedder | None = None,
                             documents_dir: str | None = None,
                             force: bool = False,
                             sentence_cache: bool = True,
                             analyze_quarterly: bool | None = None) -> dict:
    """Seçilen başvuruları analiz eder, risk farkını ve skoru yazar; özet döndürür.

    progress(step, frac): frac 0–1 (analiz bölümünün içindeki oran).
    Aynı başvuru aynı duygu ve gömme modeliyle analiz edildiyse atlanır (force=False).
    Hiçbir başvuru analiz edilemezse (ör. tüm belgeler 404) risk karşılaştırması, skor ve
    ``analysis_updated_at`` güncellenmez; uyarı eklenir (K2).

    sentence_cache=True (varsayılan): şirketin aynı duygu modeliyle önceden kaydedilmiş cümle
    sonuçları ve bu çalıştırmada önce analiz edilen başvuruların sonuçları yeniden kullanılır
    (eski/yeni 10-K risk bölümlerinin çoğu aynıdır). force=True iken de geçerlidir; modelin
    gerçek hızını ölçmek için kapatın (CLI: --no-sentence-cache).
    analyze_quarterly: None → ``FINSIGHT_ANALYZE_10Q`` (varsayılan açık).

    Özet ``timings`` (adım → sn, bkz. TIMING_STEPS), ``total_seconds`` ve ``sentiment_stats``
    (cümle, modele giden, önbellek isabeti, yinelenen, sn, cümle/sn) içerir.
    """
    started = time.perf_counter()
    timings = {key: 0.0 for key, _ in TIMING_STEPS}
    totals: dict = {}

    def timed(key: str, since: float) -> float:
        now = time.perf_counter()
        timings[key] += now - since
        return now

    report = progress or (lambda _step, _frac: None)
    company = repo.load_company_by_id(session, company_id)
    if company is None:
        raise KeyError(f"Şirket bulunamadı (id={company_id})")
    # Seçim model yüklemesinden ÖNCE (docs/performans-asama4.md §6.1): ilk belge modeller
    # yüklenirken iner. Session yalnızca bu (çağıran) iş parçacığında kullanılır.
    all_filings = repo.load_filings(session, company_id)
    include_quarterly = analyze_quarterly_enabled() if analyze_quarterly is None \
        else analyze_quarterly
    selected = select_filings_for_analysis(all_filings, include_quarterly=include_quarterly)
    # Tek işçili havuz: aynı anda en fazla bir belge indirmesi; tüm SEC istekleri yine
    # SecClient'tan (paylaşılan hız sınırlayıcı + önbellek) geçer.
    prefetch_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sec-prefetch")
    fetches: dict[int, Future] = {}

    def prefetch(filing: dict) -> None:
        if filing["id"] not in fetches:
            fetches[filing["id"]] = prefetch_pool.submit(
                client.document, company["cik"], filing["accession_no"],
                filing["primary_document"])

    def needs_analysis(filing: dict, sentiment_name: str, embedding_name: str) -> bool:
        return force or not repo.is_filing_analyzed(session, filing["id"], sentiment_name,
                                                    embedding_name)

    # Model adları yüklemeden bilinir; tahmin yanlışsa (ör. FinBERT yüklenemedi) en kötü
    # ihtimalle bir belge gereksiz iner (sonucu kullanılmaz).
    if sentiment_model is not None and embedder is not None:
        expected = (sentiment_model.name, embedder.name)
    else:
        status = model_status()
        expected = (sentiment_model.name if sentiment_model else status["sentiment"],
                    embedder.name if embedder else status["embedding"])
    first = next((f for f in selected if needs_analysis(f, *expected)), None)
    if first is not None:
        prefetch(first)
    try:
        # O2: ilk çalıştırmada FinBERT / sentence-transformers yüklemesi dakikalar sürebilir;
        # kullanıcı işin takıldığını sanmasın.
        report("Modeller yükleniyor", 0.0)
        t = time.perf_counter()
        sm = sentiment_model or get_sentiment()
        emb = embedder or get_embedder_cached()
        llm = get_llm()
        timed("model_load", t)
        report(f"Modeller hazır ({sm.name}, {emb.name})", 0.0)
        summary: dict = {
            "sentiment_model": sm.name, "embedding_model": emb.name,
            "llm": {"provider": llm.provider, "model": llm.model} if llm else None,
            "filings": [], "warnings": [], "notes": [], "risk": None, "score": None,
            "quarterly_analysis": include_quarterly,
            "sentiment_device": getattr(sm, "device_info", None),
        }

        def warning(message: str) -> None:
            log.warning(message)
            summary["warnings"].append(message)
            if warn is not None:
                warn(message)

        def finish() -> dict:
            summary["timings"] = {k: round(v, 3) for k, v in timings.items()}
            summary["total_seconds"] = round(time.perf_counter() - started, 3)
            summary["sentiment_stats"] = sentiment_stats(
                totals, cache_enabled=sentence_cache, cache_preloaded=preloaded)
            return summary

        if not include_quarterly and select_filings_for_analysis(all_filings) != selected:
            summary["notes"].append("Hızlı mod (FINSIGHT_ANALYZE_10Q=0): en son 10-Q listelendi "
                                    "ama analiz edilmedi.")
        if not selected:
            warning("Analiz edilecek 10-K / 10-Q başvurusu bulunamadı.")
        cache: dict[str, dict] | None = None
        preloaded = 0
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
            if sentence_cache and cache is None:
                t = time.perf_counter()
                try:
                    cache = repo.load_sentence_predictions(session, company_id, sm.name)
                except Exception as exc:  # önbellek yalnızca hızlandırır; okunamazsa boş başla
                    session.rollback()
                    log.warning("Cümle önbelleği okunamadı (%s); boş önbellekle devam ediliyor.",
                                type(exc).__name__)
                    cache = {}
                preloaded = len(cache)
                timed("cache_load", t)
            try:
                report(f"Belge indiriliyor ({label})", i / n * 0.8)
                t = time.perf_counter()
                prefetch(filing)
                # "download" süresi = yalnızca bekleme (belge çoğu zaman önceki başvurunun
                # çıkarımı sırasında inmiştir). Hata aynı istisna olarak yükselir.
                body = fetches.pop(filing["id"]).result()
                nxt = next((f for f in selected[i + 1:]
                            if needs_analysis(f, sm.name, emb.name)), None)
                if nxt is not None:
                    prefetch(nxt)  # FinBERT / gömme çalışırken arka planda iner
                document = store_document(body, company["cik"], filing["accession_no"],
                                          filing["primary_document"], documents_dir)
                timed("download", t)
                report(f"Bölümler ve duygu analizi ({label})", (i + 0.3) / n * 0.8)
                stats: dict = {}
                sections = analyze_sections(body, filing["form"], sentiment_model=sm,
                                            prediction_cache=cache, stats=stats)
                for key in ("html_to_text", "sections", "sentiment"):
                    timings[key] += float(stats.get(f"{key}_s", 0.0))
                for key in ("sentences", "predicted", "cache_hits", "duplicates", "sentiment_s"):
                    totals[key] = totals.get(key, 0) + stats.get(key, 0)
                entry["sentiment"] = sentiment_stats(stats, cache_enabled=sentence_cache)
                report(f"Gömme ({label})", (i + 0.7) / n * 0.8)
                t = time.perf_counter()
                embed_sections(sections, embedder=emb)
                t = timed("embedding", t)
                saved = repo.save_analysis_document(session, filing["id"], {
                    "sections": sections, "sentiment_model": sm.name, "embedding_model": emb.name,
                    "embedding_dim": emb.dim, "chunk_words": default_chunk_words(emb),
                }, document=document)
                timed("save", t)
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
            return finish()

        report("Risk karşılaştırması", 0.85)
        t = time.perf_counter()
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
        timed("risk", t)

        report("Skor hesaplanıyor", 0.95)
        t = time.perf_counter()
        score = compute_and_save_score(session, company_id)
        repo.touch_analysis(session, company_id)
        timed("score", t)
        summary["score"] = {"total": score.get("total"), "band": score.get("band"),
                            "as_of_period": score.get("as_of_period"),
                            "basis": score.get("basis"), "as_of_filing": score.get("as_of_filing")}
        return finish()
    finally:
        prefetch_pool.shutdown(wait=True, cancel_futures=True)


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
                   today: date | None = None, documents_dir: str | None = None,
                   fresh: bool = False) -> None:
    """POST /ingest arka plan işi. Kendi oturumunu açar; FastAPI senkron görevi iş
    parçacığı havuzunda çalıştırır. Hata yükseltmez, sonucu işe yazar.

    fresh=True: submissions / companyfacts önbellek okunmadan SEC'ten alınır (yenileme ve
    "Güncelle" akışı; yeni 10-Q önbellekteki eski JSON yüzünden kaçmasın). Artımlıdır: önceden
    aynı modellerle analiz edilmiş başvurular yeniden analiz edilmez."""
    factory = session_factory or default_session_factory()
    with factory() as session:
        client: SecClient | None = None

        def update(**fields) -> None:
            if client is not None:
                fields.setdefault("request_count", client.request_count)
            repo.update_job(session, job_id, **fields)

        # O2: iş hâlâ kuyruktaysa atomik olarak sahiplenilir; değilse (eskime ile kapatıldı veya
        # başka süreç başlattı) hiçbir şey yapılmaz — aynı şirket için iki iş koşmaz.
        try:
            claimed = repo.claim_job(session, job_id)
        except Exception:
            log.exception("İş sahiplenilemedi: %s", job_id)
            return
        if not claimed:
            log.warning("İş %s artık kuyrukta değil; çalıştırılmadı.", job_id)
            return
        try:
            update(status="running", step="SEC verisi çekiliyor", progress=0.02)
            client = (client_factory or make_sec_client)()
            ticker_map = build_ticker_map(client.company_tickers())
            data = collect(client, ticker, ticker_map, years=DEFAULT_YEARS, today=today,
                           fresh=fresh)
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


# --------------------------------------------------------------------------- iş kuyruğu (SEC-02)


class JobRunner:
    """Ingest işleri için ayrı, sınırlı iş parçacığı havuzu (SEC-02, CWE-770).

    - Aynı anda en fazla ``max_running`` iş ÇALIŞIR (varsayılan 2); ``max_queued`` kadarı
      kuyrukta (``status=queued``) bekler. Kapasite doluysa ``try_reserve`` False döner ve API
      429 verir.
    - İşler FastAPI/AnyIO'nun istek iş parçacığı havuzunu KULLANMAZ: uzun süren ingest işleri
      ``/api/health`` gibi uç noktaları bekletemez.
    """

    def __init__(self, max_running: int | None = None, max_queued: int | None = None) -> None:
        self.max_running = max_running if max_running is not None else get_max_concurrent_jobs()
        queued = max_queued if max_queued is not None else get_max_queued_jobs()
        self.capacity = self.max_running + queued
        self._executor = ThreadPoolExecutor(max_workers=self.max_running,
                                            thread_name_prefix="finsight-job")
        self._lock = threading.Lock()
        self._active = 0

    @property
    def active(self) -> int:
        with self._lock:
            return self._active

    def try_reserve(self) -> bool:
        """Kapasite varsa bir yer ayırır (True). Ayrılan yer ``submit`` ile kullanılmalı veya
        ``release`` ile bırakılmalı."""
        with self._lock:
            if self._active >= self.capacity:
                return False
            self._active += 1
            return True

    def release(self) -> None:
        with self._lock:
            self._active = max(self._active - 1, 0)

    def submit(self, fn: Callable, *args, **kwargs) -> Future:
        """Önceden ayrılmış yerle işi kuyruğa koyar; iş bitince yer bırakılır."""
        def run() -> None:
            try:
                fn(*args, **kwargs)
            finally:
                self.release()

        try:
            return self._executor.submit(run)
        except BaseException:
            self.release()
            raise

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
