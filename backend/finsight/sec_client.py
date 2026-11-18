"""SEC'e tek erişim noktası: User-Agent, hız sınırı, yeniden deneme, önbellek.

Kaynak kurallar:
- https://www.sec.gov/about/developer-resources (en fazla 10 istek/sn)
- https://www.sec.gov/search-filings/edgar-application-programming-interfaces
"""

import hashlib
import json
import logging
import os
import time
from typing import Callable, Protocol

from .edgar import document_url, pad_cik
from .rate_limiter import RateLimiter

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}


class SecError(RuntimeError):
    """SEC'ten başarılı yanıt alınamadı."""


class Fetcher(Protocol):
    def __call__(self, url: str, headers: dict[str, str]) -> tuple[int, bytes]: ...


class HttpxFetcher:
    """Gerçek HTTP istemcisi. httpx gzip yanıtını kendisi çözer."""

    def __init__(self, timeout: float = 30.0) -> None:
        import httpx  # yalnızca gerçek kullanımda gerekir

        self._client = httpx.Client(timeout=timeout, follow_redirects=True)

    def __call__(self, url: str, headers: dict[str, str]) -> tuple[int, bytes]:
        response = self._client.get(url, headers=headers)
        return response.status_code, response.content


class FileCache:
    """URL -> yanıt gövdesi. Aynı gün aynı JSON'un tekrar indirilmesini önler."""

    def __init__(self, directory: str, ttl_seconds: int = 24 * 3600,
                 clock: Callable[[], float] = time.time) -> None:
        self.directory = directory
        self.ttl = ttl_seconds
        self._clock = clock
        os.makedirs(directory, exist_ok=True)

    def _path(self, url: str) -> str:
        return os.path.join(self.directory, hashlib.sha256(url.encode()).hexdigest())

    def get(self, url: str) -> bytes | None:
        path = self._path(url)
        if not os.path.exists(path):
            return None
        if self._clock() - os.path.getmtime(path) > self.ttl:
            return None
        with open(path, "rb") as f:
            return f.read()

    def set(self, url: str, body: bytes) -> None:
        with open(self._path(url), "wb") as f:
            f.write(body)


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
        self.limiter = limiter or RateLimiter()
        self.cache = cache
        self.fetcher = fetcher or HttpxFetcher()
        self.max_retries = max_retries
        self.backoff = backoff_seconds
        self._sleep = sleep
        self.request_count = 0

    def get_bytes(self, url: str) -> bytes:
        if self.cache is not None:
            cached = self.cache.get(url)
            if cached is not None:
                return cached

        for attempt in range(self.max_retries + 1):
            self.limiter.acquire()
            status, body = self.fetcher(url, self.headers)
            self.request_count += 1
            if status == 200:
                if self.cache is not None:
                    self.cache.set(url, body)
                return body
            if status in RETRY_STATUSES and attempt < self.max_retries:
                wait = self.backoff * (2 ** attempt)
                log.warning("SEC %s döndü, %.1f sn sonra yeniden denenecek: %s", status, wait, url)
                self._sleep(wait)
                continue
            raise SecError(f"SEC isteği başarısız ({status}): {url}")
        raise SecError(f"SEC isteği başarısız: {url}")  # pragma: no cover

    def get_json(self, url: str) -> dict:
        return json.loads(self.get_bytes(url))

    # --- Uç noktalar -------------------------------------------------------

    def company_tickers(self) -> dict:
        return self.get_json(f"{self.WWW}/files/company_tickers.json")

    def submissions(self, cik: str | int) -> dict:
        return self.get_json(f"{self.DATA}/submissions/CIK{pad_cik(cik)}.json")

    def submissions_file(self, name: str) -> dict:
        """filings.files altındaki eski başvuru dosyaları."""
        return self.get_json(f"{self.DATA}/submissions/{name}")

    def companyfacts(self, cik: str | int) -> dict:
        return self.get_json(f"{self.DATA}/api/xbrl/companyfacts/CIK{pad_cik(cik)}.json")

    def document(self, cik: str | int, accession: str, primary_document: str) -> bytes:
        return self.get_bytes(document_url(cik, accession, primary_document))
