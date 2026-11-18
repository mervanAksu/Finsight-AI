"""SEC adil erişim kuralı için hız sınırlayıcı (NFR-02).

İstekler arasında en az 1 / max_per_second saniye bırakır. Bu yöntem,
herhangi bir 1 saniyelik pencerede max_per_second'dan fazla istek
gitmemesini garanti eder. Saat ve uyku fonksiyonları testte sahtelenebilir.
"""

import threading
import time
from typing import Callable


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
        self._clock = clock
        self._sleep = sleep
        self._next_allowed: float | None = None
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Bir istek hakkı alır; gerekirse bekler."""
        with self._lock:
            now = self._clock()
            if self._next_allowed is not None and now < self._next_allowed:
                self._sleep(self._next_allowed - now)
                now = self._next_allowed
            self._next_allowed = now + self.interval
