"""SEC adil erişim kuralı için hız sınırlayıcı (NFR-02).

İstekler arasında en az 1 / max_per_second saniye bırakır. Bu yöntem,
herhangi bir 1 saniyelik pencerede max_per_second'dan fazla istek
gitmemesini garanti eder. Saat ve uyku fonksiyonları testte sahtelenebilir.

SEC-07 (docs/guvenlik/duzeltmeler.md):
- ``pause(seconds)``: SEC 429/403 döndürdüğünde sınırlayıcıyı paylaşan TÜM istekler bu süre
  boyunca bekletilir (küresel devre kesici).
- ``FileRateLimiter``: aynı önbellek klasörünü kullanan SÜREÇLER (uvicorn, ``finsight.ingest``
  CLI, ``finsight.refresh``) tek bir hız bütçesini paylaşır. Durum ``.ratelimit`` dosyasındadır;
  okuma-yazma ``.ratelimit.lock`` üzerindeki işletim sistemi kilidiyle (POSIX ``fcntl.flock``,
  Windows ``msvcrt.locking``) korunur. Kilit süreç ölünce işletim sistemince bırakılır.
"""

import json
import logging
import os
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)

MAX_PAUSE_SECONDS = 600.0  # bozuk durum dosyası / saat sıçraması sonsuz bekletmesin


class RateLimiter:
    def __init__(
        self,
        max_per_second: float = 8.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 0 < max_per_second < 10:
            raise ValueError("SEC sınırı nedeniyle max_per_second 0-10 aralığında olmalı.")
        self.interval = 1.0 / max_per_second
        self.max_per_second = max_per_second
        # Gerçek saatli sınırlayıcı (sahte saatli test sınırlayıcısı değil): SecClient bunu
        # önbellek klasöründeki süreçler arası sınırlayıcıya yükseltebilir (SEC-07).
        self.uses_real_clock = clock is time.monotonic and sleep is time.sleep
        self._clock = clock
        self._sleep = sleep
        self._next_allowed: float | None = None
        self._paused_until: float | None = None
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Bir istek hakkı alır; gerekirse bekler."""
        with self._lock:
            now = self._clock()
            target = max(self._next_allowed or now, self._paused_until or now)
            if now < target:
                self._sleep(target - now)
                now = target
            self._next_allowed = now + self.interval

    def pause(self, seconds: float) -> None:
        """SEC-07: sonraki tüm istekleri en az ``seconds`` saniye ertele (429/403 sonrası)."""
        seconds = min(max(float(seconds), 0.0), MAX_PAUSE_SECONDS)
        with self._lock:
            until = self._clock() + seconds
            self._paused_until = max(self._paused_until or until, until)


class _FileLock:
    """Süreçler arası engelleyen dosya kilidi (refresh.ProcessLock kalıbı; burada bekler)."""

    def __init__(self, path: str, sleep: Callable[[float], None]) -> None:
        self.path = path
        self._sleep = sleep
        self._fh = None

    def __enter__(self):
        fh = open(self.path, "a+b")
        try:
            if os.name == "nt":  # pragma: no cover - Windows
                import msvcrt

                while True:
                    try:
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        self._sleep(0.005)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except BaseException:
            fh.close()
            raise
        self._fh = fh
        return self

    def __exit__(self, *_exc) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            if os.name == "nt":  # pragma: no cover - Windows
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


class FileRateLimiter:
    """Süreçler arası paylaşılan hız sınırlayıcı (SEC-07). Arayüzü ``RateLimiter`` ile aynıdır.

    Durum dosyası: ``{"next": <unix zamanı>, "paused_until": <unix zamanı>}``. Saat duvar
    saatidir (``time.time``; süreçler arası ortak tek saat). Dosyaya erişilemezse süreç içi
    ``RateLimiter``'a düşülür (uyarıyla) — istek hiçbir durumda sınırsız gitmez.
    """

    def __init__(self, directory: str, max_per_second: float = 8.0,
                 clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        if not 0 < max_per_second < 10:
            raise ValueError("SEC sınırı nedeniyle max_per_second 0-10 aralığında olmalı.")
        self.interval = 1.0 / max_per_second
        self.state_path = os.path.join(directory, ".ratelimit")
        self.lock_path = self.state_path + ".lock"
        self._directory = directory
        self._clock = clock
        self._sleep = sleep
        self._thread_lock = threading.Lock()
        self._fallback = RateLimiter(max_per_second, clock=clock, sleep=sleep)
        self._broken = False

    def _read(self) -> dict:
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, state: dict) -> None:
        tmp = self.state_path + f".{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp, self.state_path)

    def _number(self, state: dict, key: str, now: float, cap: float) -> float:
        value = state.get(key)
        if not isinstance(value, (int, float)) or value != value or value > now + cap + 1e-6:
            return now  # bozuk dosya / saat geri gitti: geçersiz değer yok sayılır
        return float(value)

    def _locked(self, fn: Callable[[], float]) -> float:
        os.makedirs(self._directory, exist_ok=True)
        with _FileLock(self.lock_path, self._sleep):
            return fn()

    def acquire(self) -> None:
        """Bir istek hakkı alır; gerekirse bekler (tüm süreçler toplamında max_per_second)."""
        with self._thread_lock:
            if self._broken:
                self._fallback.acquire()
                return
            while True:
                def step() -> float:
                    state = self._read()
                    now = self._clock()
                    target = max(self._number(state, "next", now, self.interval),
                                 self._number(state, "paused_until", now, MAX_PAUSE_SECONDS))
                    if target > now:
                        return target - now
                    state["next"] = now + self.interval
                    self._write(state)
                    return 0.0

                try:
                    wait = self._locked(step)
                except OSError as exc:
                    log.warning("Paylaşılan hız sınırı dosyası kullanılamıyor (%s); süreç içi "
                                "sınırlayıcıya geçildi.", type(exc).__name__)
                    self._broken = True
                    self._fallback.acquire()
                    return
                if wait <= 0:
                    return
                self._sleep(min(wait, MAX_PAUSE_SECONDS))

    def pause(self, seconds: float) -> None:
        """SEC-07: tüm süreçlerdeki sonraki SEC isteklerini ``seconds`` saniye ertele."""
        seconds = min(max(float(seconds), 0.0), MAX_PAUSE_SECONDS)
        self._fallback.pause(seconds)
        if self._broken:
            return

        def step() -> float:
            state = self._read()
            until = self._clock() + seconds
            current = state.get("paused_until")
            if not isinstance(current, (int, float)) or current < until:
                state["paused_until"] = until
                self._write(state)
            return 0.0

        try:
            with self._thread_lock:
                self._locked(step)
        except OSError as exc:
            log.warning("Hız sınırı duraklaması dosyaya yazılamadı (%s).", type(exc).__name__)


_shared: dict[tuple[str, float], FileRateLimiter] = {}
_shared_lock = threading.Lock()


def shared_file_limiter(directory: str, max_per_second: float) -> FileRateLimiter:
    """Önbellek klasörü başına süreç içinde TEK ``FileRateLimiter`` (SEC-07). Aynı klasörü
    kullanan tüm süreçler (API, ``finsight.ingest`` CLI, ``finsight.refresh``) durum dosyası
    üzerinden aynı bütçeyi paylaşır."""
    key = (os.path.realpath(directory), float(max_per_second))
    with _shared_lock:
        limiter = _shared.get(key)
        if limiter is None:
            limiter = _shared[key] = FileRateLimiter(directory, max_per_second)
        return limiter
