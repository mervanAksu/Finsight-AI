"""Tazelik kontrolü ve eskimiş şirketleri artımlı güncelleme (docs/asama4-sozlesme.md §4).

Kullanım (CLI)::

    python -m finsight.refresh --all          # kayıtlı tüm şirketlerden yalnızca eskimişler
    python -m finsight.refresh MSFT AAPL      # yalnızca verilen (kayıtlı) şirketler
    python -m finsight.refresh --all --dry-run

Windows Görev Zamanlayıcı ile periyodik çalıştırılabilir. "Eskimiş" = SEC'te veritabanında
olmayan, en son kayıtlı 10-K/10-Q'dan yeni (veya aynı gün) bir 10-K / 10-Q (/A) başvurusu
var; 8-K listelenir ama eskitmez.

Bu modülün üst düzeyi fastapi/sqlalchemy içe aktarmaz: tazelik hesabı (``compute_freshness``),
süreç içi önbellek + küresel istek bütçesi (``FreshnessService``), tek sahip kilidi
(``ProcessLock``) ve otomatik yenileme döngüsü (``AutoRefresher``) saf / bağımlılık enjekte
edilebilir sınıflardır ve bulutta test edilir. Veritabanı yalnızca CLI ve ``api`` bağlarken
tembel içe aktarılır.

Güvenlik ön koşulları (docs/guvenlik/denetim-raporu.md SEC-16): önbellek hata sonuçlarını da
tutar (15 dk), boyutu sınırlıdır (LRU), şirketten bağımsız küresel "fresh" istek bütçesi vardır;
iş açma ``repository.create_job_if_idle`` ile veritabanı düzeyinde atomiktir; otomatik
yenileme 429/403/5xx'te üstel bekler, tek şirketin sürekli hatası döngüyü kilitlemez,
durdurulabilir ve birden çok worker'da tek sahip (kilit dosyası) çalışır.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Iterable, Sequence
from datetime import date, datetime, timezone
from typing import Any

from .edgar import parse_filings

log = logging.getLogger(__name__)

PERIODIC_BASE_FORMS = ("10-K", "10-Q")
FRESHNESS_TTL_SECONDS = 15 * 60
FRESHNESS_CACHE_MAX = 500
FRESH_BUDGET_PER_MINUTE = 10
THROTTLE_STATUSES = frozenset({403, 429, 500, 502, 503, 504})
AUTO_REFRESH_PACE_SECONDS = 6.0    # otomatik yenileyicide şirketler arası bekleme
AUTO_REFRESH_BUDGET_PER_MINUTE = 10
MAX_BACKOFF_SECONDS = 24 * 3600
LOCK_FILE_NAME = "auto-refresh.lock"

ERROR_BUDGET = ("SEC tazelik kontrolü için kısa süreli istek sınırına ulaşıldı; "
                "bir dakika sonra tekrar deneyin.")


# --------------------------------------------------------------------------- tazelik hesabı


def _as_date(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _base(form: str | None) -> str:
    form = (form or "").strip().upper()
    return form[:-2] if form.endswith("/A") else form


def is_periodic(filing: dict) -> bool:
    return _base(filing.get("form")) in PERIODIC_BASE_FORMS


def _ref(filing: dict | None) -> dict | None:
    if not filing:
        return None
    return {"form": filing.get("form"),
            "filing_date": _iso(_as_date(filing.get("filing_date"))),
            "report_date": _iso(_as_date(filing.get("report_date"))),
            "accession_no": filing.get("accession_no")}


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _key(filing: dict) -> tuple:
    return (_as_date(filing.get("filing_date")) or date.min, str(filing.get("accession_no") or ""))


def utc_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(microsecond=0) \
        .isoformat().replace("+00:00", "Z")


XBRL_PENDING_NOTE = ("En son 10-Q kayıtlı ancak XBRL değerleri (companyfacts) henüz TTM'ye "
                     "yansımadı; SEC companyfacts birkaç saat ile birkaç gün gecikebilir. "
                     "Skor ve oranlar şimdilik önceki döneme dayanıyor; daha sonra "
                     "Güncelle'yi tekrar deneyin.")


def xbrl_pending(periodic_db: Sequence[dict]) -> dict:
    """En son kayıtlı 10-Q (son 10-K'dan yeni) TTM noktalarında görünmüyorsa
    ``{"xbrl_pending": True, "xbrl_note": ...}``; ``stale`` hesabını etkilemez.

    periodic_db: yeniden eskiye 10-K/10-Q; öğelerde ``in_ttm`` (repository.load_filings)
    yoksa kontrol yapılmaz (False)."""
    latest_q = next((f for f in periodic_db if _base(f.get("form")) == "10-Q"), None)
    latest_k = next((f for f in periodic_db if _base(f.get("form")) == "10-K"), None)
    pending = (latest_q is not None and "in_ttm" in latest_q and not latest_q["in_ttm"]
               and (latest_k is None or _key(latest_q) > _key(latest_k)))
    return {"xbrl_pending": bool(pending), "xbrl_note": XBRL_PENDING_NOTE if pending else None}


def compute_freshness(sec_state: dict, db_filings: Sequence[dict]) -> dict:
    """``/freshness`` yanıtı (disclaimer hariç).

    sec_state: ``FreshnessService.sec_state`` çıktısı — ``{"checked_at", "filings": [...] |
    None, "error": str | None}`` (filings ``edgar.parse_filings`` biçiminde).
    db_filings: veritabanındaki başvurular (``repository.filing_dict``; analyzed alanıyla).

    - analyzed_latest: analiz edilmiş en son 10-K/10-Q; yoksa kayıtlı en son 10-K/10-Q.
    - new_filings: SEC'te olup veritabanında olmayan ve en son kayıtlı başvurudan eski olmayan
      başvurular (8-K dahil), yeniden eskiye.
    - stale: new_filings içinde 10-K/10-Q (/A) varsa True; SEC'e ulaşılamadıysa None.
    """
    known = {f.get("accession_no") for f in db_filings}
    periodic_db = sorted((f for f in db_filings if is_periodic(f)), key=_key, reverse=True)
    analyzed = [f for f in periodic_db if f.get("analyzed")]
    analyzed_latest = _ref(analyzed[0] if analyzed else (periodic_db[0] if periodic_db else None))
    out = {"checked_at": sec_state.get("checked_at"), "sec_latest": None,
           "analyzed_latest": analyzed_latest, "new_filings": [], "stale": None,
           "error": sec_state.get("error"), **xbrl_pending(periodic_db)}
    if sec_state.get("error") or sec_state.get("filings") is None:
        out["error"] = out["error"] or "SEC başvuru listesi alınamadı."
        return out
    sec_filings = sorted(sec_state["filings"], key=_key, reverse=True)
    sec_periodic = [f for f in sec_filings if is_periodic(f)]
    out["sec_latest"] = _ref(sec_periodic[0] if sec_periodic else None)
    # Taban: en son kayıtlı 10-K/10-Q (yoksa herhangi bir başvuru) tarihi; daha eski SEC
    # başvuruları (kayıt penceresi dışında kalanlar) "yeni" sayılmaz.
    floor_src = periodic_db or sorted(db_filings, key=_key, reverse=True)
    floor = _as_date(floor_src[0].get("filing_date")) if floor_src else None
    new = [f for f in sec_filings if f.get("accession_no") not in known
           and (floor is None or (_as_date(f.get("filing_date")) or date.min) >= floor)]
    out["new_filings"] = [_ref(f) for f in new]
    out["stale"] = any(is_periodic(f) for f in new)
    return out


# --------------------------------------------------------------------------- önbellek + bütçe


class RequestBudget:
    """Küresel kayan pencere bütçesi: ``limit`` istek / ``window`` sn (şirketten bağımsız).
    limit None → sınırsız (CLI)."""

    def __init__(self, limit: int | None = FRESH_BUDGET_PER_MINUTE, window: float = 60.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.window = window
        self._clock = clock
        self._times: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        if self.limit is None:
            return True
        with self._lock:
            now = self._clock()
            while self._times and now - self._times[0] >= self.window:
                self._times.popleft()
            if len(self._times) >= self.limit:
                return False
            self._times.append(now)
            return True


def _default_error_fn(exc: BaseException) -> str:
    try:  # jobs sqlalchemy içe aktarır; bulunmazsa genel ve güvenli metin
        from .jobs import safe_error
    except ImportError:  # pragma: no cover - yalnızca bağımlılıksız ortam
        return f"SEC'e ulaşılamadı ({type(exc).__name__})"
    return safe_error(exc)


class FreshnessService:
    """SEC başvuru listesini (submissions, önbelleği atlayarak) şirket başına 15 dk tutar.

    fetch(cik) → submissions JSON (SEC'e EN FAZLA bir istek; çağıran ``fresh=True`` ve
    yeniden denemesiz istemci verir). Hata sonuçları da önbelleğe alınır (negatif önbellek);
    bütçe aşımı SEC'e gitmediği için önbelleğe alınmaz. Önbellek en fazla ``max_entries``
    şirket tutar (LRU).
    """

    def __init__(self, fetch: Callable[[str], dict], *, ttl: float = FRESHNESS_TTL_SECONDS,
                 max_entries: int = FRESHNESS_CACHE_MAX, budget: RequestBudget | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 wall_clock: Callable[[], float] = time.time,
                 error_fn: Callable[[BaseException], str] | None = None) -> None:
        self._fetch = fetch
        self.ttl = ttl
        self.max_entries = max_entries
        self.budget = budget if budget is not None else RequestBudget(clock=clock)
        self._clock = clock
        self._wall = wall_clock
        self._error_fn = error_fn or _default_error_fn
        self._cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self._lock = threading.Lock()
        self.fetch_count = 0

    def cached(self, cik: str) -> dict | None:
        with self._lock:
            entry = self._cache.get(cik)
            if entry is None or self._clock() - entry[0] >= self.ttl:
                return None
            self._cache.move_to_end(cik)
            return entry[1]

    def invalidate(self, cik: str | None = None) -> None:
        with self._lock:
            if cik is None:
                self._cache.clear()
            else:
                self._cache.pop(cik, None)

    def sec_state(self, cik: str) -> dict:
        """``{"checked_at", "filings", "error", "status"}`` (status: SEC HTTP kodu, varsa)."""
        hit = self.cached(cik)
        if hit is not None:
            return hit
        if not self.budget.allow():
            return {"checked_at": utc_iso(self._wall()), "filings": None,
                    "error": ERROR_BUDGET, "status": None, "budget_exhausted": True}
        self.fetch_count += 1
        try:
            subs = self._fetch(cik)
            filings = parse_filings((subs.get("filings") or {}).get("recent") or {})
            state = {"checked_at": utc_iso(self._wall()), "filings": filings, "error": None,
                     "status": None}
        except Exception as exc:  # ağ / SEC / yapılandırma: HTTP 200 + Türkçe neden
            log.warning("Tazelik kontrolü başarısız (%s)", type(exc).__name__)
            state = {"checked_at": utc_iso(self._wall()), "filings": None,
                     "error": self._error_fn(exc), "status": getattr(exc, "status", None)}
        with self._lock:
            self._cache[cik] = (self._clock(), state)
            self._cache.move_to_end(cik)
            while len(self._cache) > self.max_entries:
                self._cache.popitem(last=False)
        return state


def sec_fetcher(client_factory: Callable[[], Any]) -> Callable[[str], dict]:
    """FreshnessService için: her çağrıda yeni istemci, yeniden deneme yok, önbellek okunmaz."""

    def fetch(cik: str) -> dict:
        client = client_factory()
        if hasattr(client, "max_retries"):
            client.max_retries = 0  # "SEC'e en fazla bir istek" (sözleşme §4)
        return client.submissions(cik, fresh=True)

    return fetch


# --------------------------------------------------------------------------- tek sahip kilidi


class ProcessLock:
    """Süreçler arası, engellemeyen dosya kilidi (``uvicorn --workers N`` ile tek sahip).

    POSIX'te ``fcntl.flock``, Windows'ta ``msvcrt.locking``. Kilit süreç ölünce işletim
    sistemi tarafından bırakılır (artık kilit dosyası sorun olmaz).
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._fh = None

    def acquire(self) -> bool:
        if self._fh is not None:
            return True
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        fh = open(self.path, "a+b")
        try:
            if os.name == "nt":  # pragma: no cover - Windows
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if os.name == "nt":  # pragma: no cover - Windows
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None


# --------------------------------------------------------------------------- otomatik yenileme


class AutoRefresher:
    """Eskimiş şirketleri sırayla güncelleyen arka plan döngüsü (tek iş parçacığı).

    list_fn() → [{"ticker", "cik", "analyzed"}]; check_fn(company) → (sec_state, freshness);
    start_fn(company) → iş başlatır ve bitene kadar çalıştırır (veya None: etkin iş var).
    Testler ``run_once`` / ``next_delay`` çağırır (iş parçacığı yok, deterministik).

    - 429/403/5xx: tur kesilir, sonraki bekleme 2^n katına çıkar (en fazla 24 saat).
    - Tek şirketin ardışık hatası: o şirket 2^k tur atlanır (döngüyü kilitlemez).
    - ``stop()`` döngüyü durdurur; ``start()`` kilit dosyasını alamazsa çalışmaz.
    """

    def __init__(self, interval_minutes: float, *, list_fn: Callable[[], list[dict]],
                 check_fn: Callable[[dict], tuple[dict, dict]],
                 start_fn: Callable[[dict], object], lock: ProcessLock | None = None,
                 pace_seconds: float = AUTO_REFRESH_PACE_SECONDS) -> None:
        self.interval = float(interval_minutes) * 60.0
        self.pace = pace_seconds
        self.cursor: str | None = None  # bütçe aşımında kalınan şirket (sonraki tur buradan)
        self._list = list_fn
        self._check = check_fn
        self._start = start_fn
        self._lock = lock
        self.backoff_level = 0
        self.cycle = 0
        self.failures: dict[str, int] = {}
        self.skip_until: dict[str, int] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def next_delay(self) -> float:
        return min(self.interval * (2 ** self.backoff_level), max(self.interval,
                                                                   MAX_BACKOFF_SECONDS))

    def _fail(self, key: str) -> None:
        n = self.failures.get(key, 0) + 1
        self.failures[key] = n
        self.skip_until[key] = self.cycle + 2 ** min(n, 6)

    def _ordered(self, companies: list[dict]) -> list[dict]:
        """Önceki tur bütçe yüzünden kesildiyse kalınan şirketten başlayan döngüsel sıra."""
        keys = [self._key(c) for c in companies]
        if self.cursor in keys:
            i = keys.index(self.cursor)
            return companies[i:] + companies[:i]
        return companies

    @staticmethod
    def _key(company: dict) -> str:
        return str(company.get("cik") or company.get("ticker"))

    def run_once(self) -> dict:
        """Bir tur: {"checked", "started", "skipped", "errors", "throttled", "budget"}.

        Şirketler ``pace`` sn arayla kontrol edilir. Tazelik bütçesi dolarsa (kullanıcı
        isteklerinden ayrı, yenileyicinin kendi bütçesi) tur kesilir, hata sayılmaz ve sonraki
        tur aynı şirketten devam eder.
        """
        self.cycle += 1
        summary = {"checked": [], "started": [], "skipped": [], "errors": [],
                   "throttled": False, "budget": False}
        try:
            companies = self._ordered(list(self._list()))
        except Exception as exc:
            log.warning("Otomatik yenileme: şirket listesi okunamadı (%s)", type(exc).__name__)
            summary["errors"].append("liste")
            return summary
        self.cursor = None
        for n, company in enumerate(companies):
            if self._stop.is_set():
                break
            if n and self.pace > 0 and self._stop.wait(self.pace):
                break
            key = self._key(company)
            if self.skip_until.get(key, 0) > self.cycle:
                summary["skipped"].append(key)
                continue
            try:
                state, fresh = self._check(company)
            except Exception as exc:
                log.warning("Otomatik yenileme: %s kontrol edilemedi (%s)", key,
                            type(exc).__name__)
                self._fail(key)
                summary["errors"].append(key)
                continue
            if state.get("budget_exhausted"):
                summary["budget"] = True
                self.cursor = key
                break
            summary["checked"].append(key)
            if state.get("status") in THROTTLE_STATUSES:
                summary["throttled"] = True
                break
            if fresh.get("error"):
                self._fail(key)
                summary["errors"].append(key)
                continue
            if not fresh.get("stale"):
                self.failures.pop(key, None)
                continue
            try:
                job = self._start(company)
            except Exception as exc:
                log.warning("Otomatik yenileme: %s güncellenemedi (%s)", key,
                            type(exc).__name__)
                self._fail(key)
                summary["errors"].append(key)
                if getattr(exc, "status", None) in THROTTLE_STATUSES:
                    summary["throttled"] = True  # işte SEC 429/403/5xx → küresel geri çekilme
                    break
                continue
            self.failures.pop(key, None)
            if job is not None:
                summary["started"].append(key)
        self.backoff_level = min(self.backoff_level + 1, 10) if summary["throttled"] else 0
        return summary

    def _loop(self) -> None:
        while not self._stop.wait(self.next_delay()):
            try:
                self.run_once()
            except Exception:  # pragma: no cover - döngü asla ölmesin
                log.exception("Otomatik yenileme turu başarısız")

    def start(self) -> bool:
        if self._thread is not None:
            return True
        if self._lock is not None and not self._lock.acquire():
            log.info("Otomatik yenileme başka bir süreçte çalışıyor; bu süreçte başlatılmadı.")
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="finsight-auto-refresh",
                                        daemon=True)
        self._thread.start()
        return True

    def stop(self, timeout: float | None = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
        if self._lock is not None:
            self._lock.release()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()


# --------------------------------------------------------------------------- veritabanı bağları


def db_company_entries(session_factory) -> list[dict]:
    """Kayıtlı şirketler: [{"ticker", "cik", "analyzed"}] (repository tembel içe aktarılır)."""
    from . import repository as repo

    with session_factory() as session:
        return [{"ticker": e["company"]["ticker"], "cik": e["company"]["cik"],
                 "id": e["company"]["id"], "analyzed": bool(e["analyzed"])}
                for e in repo.list_companies(session)]


def check_company(service: FreshnessService, session_factory, company: dict
                  ) -> tuple[dict, dict]:
    """(sec_state, freshness) — veritabanındaki başvurulara karşı."""
    from . import repository as repo

    state = service.sec_state(company["cik"])
    with session_factory() as session:
        db_filings = repo.load_filings(session, company["id"])
    return state, compute_freshness(state, db_filings)


class RefreshJobError(RuntimeError):
    """Yenileme işi ``error`` durumunda bitti. ``status``: hata metnindeki SEC HTTP kodu."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


_STATUS_RE = re.compile(r"\((\d{3})\)")


def job_error_status(message: str | None) -> int | None:
    """"SEC isteği başarısız (429): ..." → 429 (safe_error SecError metnini korur)."""
    m = _STATUS_RE.search(message or "")
    return int(m.group(1)) if m else None


def start_refresh_job(company: dict, *, session_factory, client_factory,
                      lock: threading.Lock | None = None, run: bool = True) -> dict | None:
    """Şirket için atomik iş açar (repository.create_job_if_idle) ve çalıştırır (artımlı:
    önceden analiz edilmiş başvurular atlanır). Etkin iş varsa None.

    İş "error" ile biterse RefreshJobError (status: SEC kodu) yükseltir → otomatik
    yenileyicide _fail / geri çekilme, CLI'de hata ve sıfırdan farklı çıkış kodu."""
    from . import jobs
    from . import repository as repo

    name = company.get("ticker") or company["cik"]  # ticker yoksa CIK ile (resolve_company)
    guard = lock if lock is not None else threading.Lock()
    with guard, session_factory() as session:
        job, created = repo.create_job_if_idle(session, name,
                                               analyze=company.get("analyzed", True),
                                               cik=company.get("cik"))
    if not created:
        return None
    if run:
        jobs.run_ingest_job(job["id"], name, company.get("analyzed", True),
                            session_factory=session_factory, client_factory=client_factory,
                            fresh=True)
        with session_factory() as session:
            job = repo.get_job(session, job["id"]) or job
        if job.get("status") == "error":
            raise RefreshJobError(job.get("error") or "Yenileme işi başarısız",
                                  job_error_status(job.get("error")))
    return job


def refresh_companies(companies: Iterable[dict], *, check: Callable[[dict], tuple[dict, dict]],
                      start: Callable[[dict], Any], dry_run: bool = False,
                      out=print) -> dict:
    """CLI çekirdeği: yalnızca eskimiş şirketleri günceller. Dönüş: {"stale", "updated",
    "busy", "errors", "fresh"} (ticker listeleri)."""
    result: dict[str, list] = {"stale": [], "updated": [], "busy": [], "errors": [], "fresh": []}
    for company in companies:
        ticker = company.get("ticker") or company.get("cik")
        try:
            _state, fresh = check(company)
        except Exception as exc:
            out(f"{ticker}: kontrol edilemedi ({type(exc).__name__})")
            result["errors"].append(ticker)
            continue
        if fresh.get("error"):
            out(f"{ticker}: kontrol edilemedi — {fresh['error']}")
            result["errors"].append(ticker)
            continue
        if not fresh.get("stale"):
            out(f"{ticker}: güncel")
            result["fresh"].append(ticker)
            continue
        news = ", ".join(f"{f['form']} ({f['filing_date']})" for f in fresh["new_filings"]
                         if is_periodic(f))
        out(f"{ticker}: yeni rapor var — {news}")
        result["stale"].append(ticker)
        if dry_run:
            continue
        try:
            job = start(company)
        except RefreshJobError as exc:
            out(f"{ticker}: güncellenemedi — {exc}")
            result["errors"].append(ticker)
            continue
        except Exception as exc:
            out(f"{ticker}: güncellenemedi — {_default_error_fn(exc)}")
            result["errors"].append(ticker)
            continue
        if job is None:
            out(f"{ticker}: zaten çalışan bir iş var, atlandı")
            result["busy"].append(ticker)
        else:
            out(f"{ticker}: güncellendi (iş {job.get('id')})")
            result["updated"].append(ticker)
    return result


def _masked_db_url(url: str) -> str:
    """Parolayı gizler (postgresql://kullanici:***@host/db)."""
    return re.sub(r"(://[^:/@]+:)[^@]*@", r"\1***@", url)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Kayıtlı şirketlerden yalnızca eskimiş olanları SEC'ten günceller.")
    parser.add_argument("companies", nargs="*", help="Ticker (kayıtlı şirket)")
    parser.add_argument("--all", action="store_true", help="Tüm kayıtlı şirketler")
    parser.add_argument("--dry-run", action="store_true",
                        help="Yalnızca eskimiş şirketleri listele, güncelleme")
    args = parser.parse_args(argv)
    if not args.all and not args.companies:
        parser.error("Ticker verin veya --all kullanın.")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from . import DISCLAIMER, jobs
    from .config import ConfigError

    try:
        jobs.make_sec_client()  # User-Agent ve yapılandırma doğrulaması
    except ConfigError as exc:
        print(f"Yapılandırma hatası: {exc}", file=sys.stderr)
        return 2
    from .config import get_cache_dir, get_database_url

    db_url = get_database_url()
    print(f"Veritabanı: {_masked_db_url(db_url)} (çalışma klasörü: {os.getcwd()})")
    print(f"SEC önbelleği: {os.path.abspath(get_cache_dir())}")
    if db_url.startswith("sqlite:///") and not os.path.isabs(db_url[len("sqlite:///"):]):
        print("Not: göreli SQLite yolu çalışma klasörüne göredir; Görev Zamanlayıcı'da "
              "'Başlangıç konumu' = backend klasörü verin veya DATABASE_URL'yi mutlak yol "
              "yapın.")
    factory = jobs.default_session_factory()
    entries = db_company_entries(factory)
    if not args.all:
        wanted = [t.strip().upper() for t in args.companies]
        by_ticker = {}
        from . import repository as repo

        with factory() as session:
            for t in wanted:
                row = repo.load_company(session, t)
                if row is None:
                    print(f"{t}: kayıtlı değil (önce: python -m finsight.ingest {t})",
                          file=sys.stderr)
                    continue
                by_ticker[row["cik"]] = row
        entries = [e for e in entries if e["cik"] in by_ticker]
    service = FreshnessService(sec_fetcher(jobs.make_sec_client),
                               budget=RequestBudget(limit=None), error_fn=jobs.safe_error)
    result = refresh_companies(
        entries,
        check=lambda c: check_company(service, factory, c),
        start=lambda c: start_refresh_job(c, session_factory=factory,
                                          client_factory=jobs.make_sec_client),
        dry_run=args.dry_run)
    print(f"\nEskimiş: {len(result['stale'])}, güncellenen: {len(result['updated'])}, "
          f"hata: {len(result['errors'])}")
    print(DISCLAIMER)
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
