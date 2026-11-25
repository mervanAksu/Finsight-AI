"""SEC'e tek erişim noktası: User-Agent, hız sınırı, yeniden deneme, önbellek.

Kaynak kurallar:
- https://www.sec.gov/about/developer-resources (en fazla 10 istek/sn)
- https://www.sec.gov/search-filings/edgar-application-programming-interfaces
"""

import hashlib
import json
import logging
import os
import re
import tempfile
import time
from typing import Any, Callable, Protocol

from .edgar import document_url, pad_cik
from .rate_limiter import RateLimiter, shared_file_limiter

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}
# SEC hız sınırı aşımında 429 veya gövdesinde "Request Rate Threshold Exceeded" yazan 403
# döndürür. Diğer 403'ler (ör. yasak yol) küresel duraklama tetiklemez (O5).
RATE_LIMIT_STATUSES = {429, 403}
RATE_LIMIT_MARKER = b"request rate threshold"


def _is_rate_limited(status: int, body: bytes) -> bool:
    if status == 429:
        return True
    return status == 403 and RATE_LIMIT_MARKER in (body or b"")[:20_000].lower()
RATE_LIMIT_PAUSE_SECONDS = 10.0  # SEC-07: küresel duraklama (SEC penceresi 1 sn)


def _parse_json(body: bytes):
    """JSON ayrıştırma; her hata ValueError'dur (UnicodeDecodeError da ValueError alt sınıfı)."""
    return json.loads(body)


class SecError(RuntimeError):
    """SEC'ten başarılı yanıt alınamadı. ``status``: son HTTP durum kodu (varsa)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


MAX_REDIRECTS = 5


def is_allowed_sec_url(url: str) -> bool:
    """Yalnızca https ile sec.gov ve alt alan adları (www.sec.gov, data.sec.gov) — SEC-17:
    yönlendirme User-Agent'taki e-postayı başka bir siteye taşımasın."""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    return parts.scheme == "https" and (host == "sec.gov" or host.endswith(".sec.gov"))


class Fetcher(Protocol):
    def __call__(self, url: str, headers: dict[str, str]) -> tuple[int, bytes]: ...


class HttpxFetcher:
    """Gerçek HTTP istemcisi. httpx gzip yanıtını kendisi çözer.

    Yönlendirmeler otomatik izlenmez (SEC-17): en fazla 5 adım, yalnızca https://*.sec.gov
    hedeflerine elle izlenir; başka bir ana makineye yönlendirme SecError'dur.
    """

    def __init__(self, timeout: float = 30.0) -> None:
        import httpx  # yalnızca gerçek kullanımda gerekir

        self._client = httpx.Client(timeout=timeout, follow_redirects=False)
        # SecClient bağlar: yönlendirme adımları da hız sınırından ve sayaçtan geçer.
        self.before_hop: Callable[[], None] | None = None

    def __call__(self, url: str, headers: dict[str, str]) -> tuple[int, bytes]:
        return follow_sec_redirects(lambda u: self._get(u, headers), url,
                                    before_hop=self.before_hop)

    def _get(self, url: str, headers: dict[str, str]) -> tuple[int, bytes, str | None]:
        response = self._client.get(url, headers=headers)
        return response.status_code, response.content, response.headers.get("location")


def follow_sec_redirects(get: Callable[[str], tuple[int, bytes, str | None]],
                         url: str, *, before_hop: Callable[[], None] | None = None
                         ) -> tuple[int, bytes]:
    """get(url) → (durum, gövde, Location). 3xx yalnızca sec.gov içine izlenir (saf, test
    edilebilir). before_hop: her YÖNLENDİRME adımından önce çağrılır (ilk istek çağıranın
    sorumluluğundadır) — SecClient burada hız sınırını uygular ve istek sayacını artırır."""
    from urllib.parse import urljoin

    if not is_allowed_sec_url(url):
        raise SecError(f"SEC dışı adrese istek reddedildi: {url}")
    for hop in range(MAX_REDIRECTS + 1):
        if hop and before_hop is not None:
            before_hop()
        status, body, location = get(url)
        if status not in (301, 302, 303, 307, 308) or not location:
            return status, body
        target = urljoin(url, location)
        if not is_allowed_sec_url(target):
            raise SecError(f"SEC dışı adrese yönlendirme reddedildi ({status})", status)
        url = target
    raise SecError("SEC isteği: çok fazla yönlendirme")


DEFAULT_JSON_TTL = 3600            # submissions / companyfacts (FINSIGHT_SEC_JSON_TTL)
TICKERS_TTL = 24 * 3600            # company_tickers.json
ARCHIVES_MARKER = "/Archives/edgar/data/"
# Archives altında DEĞİŞEBİLEN dizin sayfaları (yeni başvuru eklenince güncellenir): süresiz
# önbelleğe alınmaz, JSON süresi uygulanır.
_MUTABLE_ARCHIVE_RE = re.compile(r"(?:/index\.json|/index\.xml|-index\.html?|"
                                 r"-index-headers\.html?|/)$", re.I)


def cache_ttl_for(url: str, json_ttl: float = DEFAULT_JSON_TTL,
                  tickers_ttl: float = TICKERS_TTL) -> float | None:
    """URL sınıfına göre önbellek süresi (sn); None = süresiz (docs/asama4-sozlesme.md §5).

    - ``https://*.sec.gov/Archives/edgar/data/...`` (YOL öneki; sorgu parametresindeki eşleşme
      sayılmaz): yayımlanmış belge değişmez → süresiz. Dizin sayfaları (``index.json``,
      ``-index.htm``, ``-index-headers.html``, klasör) hariç: onlara ``json_ttl``.
    - ``company_tickers.json``: 24 saat.
    - ``submissions/*.json``, ``companyfacts/*.json`` ve diğer JSON: ``json_ttl``
      (yeni başvurular saatler içinde görünsün).
    """
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
    except ValueError:
        return json_ttl
    host = (parts.hostname or "").lower().rstrip(".")
    path = parts.path or ""
    on_sec = host == "sec.gov" or host.endswith(".sec.gov")
    if on_sec and path.startswith(ARCHIVES_MARKER) and not _MUTABLE_ARCHIVE_RE.search(path):
        return None
    if on_sec and path.endswith("/company_tickers.json"):
        return tickers_ttl
    return json_ttl


class FileCache:
    """URL -> yanıt gövdesi; süre URL sınıfına göredir (bkz. ``cache_ttl_for``).

    ttl_seconds verilirse (eski kullanım) TÜM URL'lere aynı süre uygulanır.
    """

    def __init__(self, directory: str, ttl_seconds: float | None = None,
                 clock: Callable[[], float] = time.time, *,
                 json_ttl_seconds: float = DEFAULT_JSON_TTL,
                 tickers_ttl_seconds: float = TICKERS_TTL) -> None:
        self.directory = directory
        self.ttl = ttl_seconds
        self.json_ttl = json_ttl_seconds
        self.tickers_ttl = tickers_ttl_seconds
        self._clock = clock
        os.makedirs(directory, exist_ok=True)

    def _path(self, url: str) -> str:
        return os.path.join(self.directory, hashlib.sha256(url.encode()).hexdigest())

    def ttl_for(self, url: str) -> float | None:
        if self.ttl is not None:
            return self.ttl
        return cache_ttl_for(url, self.json_ttl, self.tickers_ttl)

    def get(self, url: str) -> bytes | None:
        path = self._path(url)
        if not os.path.exists(path):
            return None
        ttl = self.ttl_for(url)
        if ttl is not None and self._clock() - os.path.getmtime(path) > ttl:
            return None
        with open(path, "rb") as f:
            return f.read()

    def set(self, url: str, body: bytes) -> None:
        """Atomik yazım (O5): önce aynı klasörde geçici dosya, sonra ``os.replace``. Yarıda
        kesilen yazım (çökme, disk dolu) bozuk bir önbellek dosyası bırakmaz; eşzamanlı
        okuyucu ya eski ya yeni içeriği görür."""
        path = self._path(url)
        fd, tmp = tempfile.mkstemp(dir=self.directory, prefix=".tmp-", suffix=".part")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(body)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


class SecClient:
    DATA = "https://data.sec.gov"
    WWW = "https://www.sec.gov"

    def __init__(
        self,
        user_agent: str,
        *,
        limiter: RateLimiter | None = None,
        cache: FileCache | None = None,
        fetcher: Fetcher | None = None,
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not user_agent or "@" not in user_agent:
            raise ValueError("Tanımlayıcı bir User-Agent (e-posta içeren) zorunludur.")
        self.headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
        limiter = limiter or RateLimiter()
        if isinstance(limiter, RateLimiter) and limiter.uses_real_clock \
                and isinstance(cache, FileCache):
            # SEC-07: gerçek saatli süreç içi sınırlayıcı + disk önbelleği → aynı önbellek
            # klasörünü kullanan TÜM süreçlerle paylaşılan sınırlayıcıya yükseltilir (CLI
            # ``finsight.ingest`` kendi RateLimiter'ını verse de API ile aynı bütçeyi kullanır).
            limiter = shared_file_limiter(cache.directory, limiter.max_per_second)
        self.limiter = limiter
        self.cache = cache
        self.fetcher = fetcher or HttpxFetcher()
        self.max_retries = max_retries
        self.backoff = backoff_seconds
        self._sleep = sleep
        self.request_count = 0
        if hasattr(self.fetcher, "before_hop"):
            self.fetcher.before_hop = self._before_hop

    def _before_hop(self) -> None:
        """Yönlendirme adımı da ayrı bir SEC isteğidir: hız sınırı + sayaç."""
        self.limiter.acquire()
        self.request_count += 1

    def get_bytes(self, url: str, *, fresh: bool = False) -> bytes:
        """URL gövdesi. fresh=True: önbellek OKUNMAZ (SEC'ten taze yanıt) ama başarılı yanıt
        önbelleğe yazılır (tazelik kontrolü, docs/asama4-sozlesme.md §4–5)."""
        return self._fetch(url, fresh=fresh, parse=None)

    def get_json(self, url: str, *, fresh: bool = False) -> dict:
        """JSON gövde. SEC-09: gövde JSON olarak ayrıştırılamazsa (ör. 200 + HTML bakım sayfası)
        önbelleğe YAZILMAZ ve SecError yükselir; önbellekteki bozuk kayıt da yok sayılır
        (eski zehirlenmiş önbellek kendiliğinden iyileşir)."""
        return self._fetch(url, fresh=fresh, parse=_parse_json)

    def _fetch(self, url: str, *, fresh: bool, parse: Callable[[bytes], Any] | None):
        if self.cache is not None and not fresh:
            cached = self.cache.get(url)
            if cached is not None:
                if parse is None:
                    return cached
                try:
                    return parse(cached)
                except ValueError:
                    log.warning("Önbellekteki yanıt bozuk (JSON değil); SEC'ten yeniden "
                                "alınacak: %s", url)

        for attempt in range(self.max_retries + 1):
            self.limiter.acquire()
            status, body = self.fetcher(url, self.headers)
            self.request_count += 1
            if status == 200:
                result = body
                if parse is not None:
                    try:
                        result = parse(body)
                    except ValueError:
                        raise SecError(f"SEC yanıtı geçerli JSON değil (önbelleğe "
                                       f"yazılmadı): {url}", status) from None
                if self.cache is not None:
                    self.cache.set(url, body)
                return result
            if _is_rate_limited(status, body):
                # SEC-07: küresel devre kesici — sınırlayıcıyı paylaşan tüm istekler (ve dosya
                # tabanlı sınırlayıcıda tüm süreçler) bekler.
                pause = getattr(self.limiter, "pause", None)
                if callable(pause):
                    pause(RATE_LIMIT_PAUSE_SECONDS)
                log.warning("SEC %s döndü (hız sınırı); tüm SEC istekleri %.0f sn "
                            "duraklatıldı.", status, RATE_LIMIT_PAUSE_SECONDS)
            if status in RETRY_STATUSES and attempt < self.max_retries:
                wait = self.backoff * (2 ** attempt)
                log.warning("SEC %s döndü, %.1f sn sonra yeniden denenecek: %s", status, wait, url)
                self._sleep(wait)
                continue
            raise SecError(f"SEC isteği başarısız ({status}): {url}", status)
        raise SecError(f"SEC isteği başarısız: {url}")  # pragma: no cover

    # --- Uç noktalar -------------------------------------------------------

    def company_tickers(self) -> dict:
        return self.get_json(f"{self.WWW}/files/company_tickers.json")

    def submissions(self, cik: str | int, *, fresh: bool = False) -> dict:
        return self.get_json(f"{self.DATA}/submissions/CIK{pad_cik(cik)}.json", fresh=fresh)

    def submissions_file(self, name: str) -> dict:
        """filings.files altındaki eski başvuru dosyaları."""
        return self.get_json(f"{self.DATA}/submissions/{name}")

    def companyfacts(self, cik: str | int, *, fresh: bool = False) -> dict:
        return self.get_json(f"{self.DATA}/api/xbrl/companyfacts/CIK{pad_cik(cik)}.json",
                             fresh=fresh)

    def document(self, cik: str | int, accession: str, primary_document: str) -> bytes:
        return self.get_bytes(document_url(cik, accession, primary_document))
