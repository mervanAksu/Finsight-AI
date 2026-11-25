"""Savunma testleri (security-engineer) — bulutta koşabilen kanıtlar.

HTTP katmanı düzeltmeleri ``api.py`` içinde; ``api.py`` FastAPI ve SQLAlchemy'yi içe aktardığı
için bulutta yüklenemez. Bu dosya ilgili sınıf/fonksiyonları ``api.py`` / ``jobs.py``
KAYNAĞINDAN (AST) alır ve yalnızca Starlette + httpx ile (kuruluysa) çalıştırır. Böylece
saldırı testlerinin (``test_attack_csrf_host.py``, ``test_attack_dos.py``) kullanıcının
bilgisayarında doğrulayacağı mantık burada da gerçekten koşar. Ağa çıkılmaz.

Kapsam: SEC-01/03 (Host + Origin ara katmanı), SEC-12 (başlıklar, CSP özeti), SEC-03 ek
(/ask hız sınırı, günlük LLM bütçesi), SEC-02 (JobRunner: istek havuzunu tüketmeme), SEC-10
(tek uçuş), SEC-07 (süreçler arası hız sınırı, CLI yolu, duraklama, yönlendirme adımları),
SEC-09 (önbellek TTL sınıfları, bozuk önbellek), SEC-04/05 yanlış pozitif kontrolleri.
"""

from __future__ import annotations

import __future__
import ast
import asyncio
import base64
import collections
import concurrent.futures
import contextlib
import datetime as _dt
import hashlib
import json
import logging
import os
import pathlib
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable

import pytest

from finsight import config
from finsight.config import ConfigError
from finsight.edgar import build_ticker_map
from finsight.llm import LLMError
from finsight.rate_limiter import FileRateLimiter, RateLimiter
from finsight.sec_client import FileCache, SecClient, cache_ttl_for, follow_sec_redirects

BACKEND = pathlib.Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
UA = "FinSight-AI Savunma test@example.com"
ATTACKER = "https://kotu.example"


def _load(module: str, names: set[str], extra: dict | None = None) -> dict:
    """``finsight/<module>.py`` içinden adı verilen üst düzey tanımları ve BÜYÜK_HARF
    sabitleri çalıştırır (FastAPI / SQLAlchemy içe aktarılmadan)."""
    tree = ast.parse((BACKEND / "finsight" / f"{module}.py").read_text(encoding="utf-8"))
    body = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) and t.id.isupper()
                                                and not t.id.startswith("_")
                                                for t in node.targets):
            body.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and node.name in names:
            body.append(node)
    ns: dict = {"os": os, "re": re, "json": json, "base64": base64, "hashlib": hashlib,
                "collections": collections, "threading": threading, "time": time,
                "_dt": _dt, "asyncio": asyncio, "contextlib": contextlib,
                "Iterable": Iterable, "Callable": Callable, "Future": concurrent.futures.Future,
                "ThreadPoolExecutor": concurrent.futures.ThreadPoolExecutor,
                "log": logging.getLogger("test"), "ConfigError": ConfigError,
                "build_ticker_map": build_ticker_map, "__name__": f"ast_{module}",
                "get_max_concurrent_jobs": config.get_max_concurrent_jobs,
                "get_max_queued_jobs": config.get_max_queued_jobs, **(extra or {})}
    for n in body:  # ifade değerlendirmesi isim bulamazsa sabiti atla (ör. modül nesneleri)
        try:
            code = compile(ast.Module(body=[n], type_ignores=[]), f"{module}.py", "exec",
                           flags=__future__.annotations.compiler_flag)
            exec(code, ns)  # noqa: S102
        except NameError:
            if isinstance(n, ast.Assign):
                continue
            raise
    missing = names - ns.keys()
    assert not missing, f"{module}.py içinde bulunamadı: {missing}"
    return ns


API = _load("api", {"_host_name", "build_app_csp", "inline_script_hashes",
                    "LocalGuardMiddleware", "SlidingWindowLimiter", "DailyBudget",
                    "_TickerMapCache", "_await_job", "BudgetedLLM"},
           {"LLMError": LLMError})


# --------------------------------------------------------------------------- SEC-01 / 03 / 12


def _guard_app(**kw):
    pytest.importorskip("starlette")
    pytest.importorskip("httpx")
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    hits: list[str] = []

    async def endpoint(request):
        hits.append(request.method + " " + request.url.path)
        return JSONResponse({"ok": True}, status_code=202 if request.method == "POST" else 200)

    app = Starlette(routes=[Route("/api/health", endpoint),
                            Route("/api/companies/EXMP/ingest", endpoint, methods=["POST"]),
                            Route("/docs", endpoint)])
    app.add_middleware(API["LocalGuardMiddleware"],
                       allowed_hosts=kw.get("hosts", ["127.0.0.1", "localhost", "::1"]),
                       allowed_origins=["http://127.0.0.1:5173", "http://localhost:5173",
                                        *API["SELF_ORIGINS"]],
                       app_csp=API["build_app_csp"](["abc="]))
    return app, hits


def _client(app, base="http://127.0.0.1:8000"):
    from starlette.testclient import TestClient

    return TestClient(app, base_url=base)


@pytest.mark.parametrize("base", ["http://rebind.kotu.example:8000", "http://testserver",
                                  "http://192.168.1.20:8000", "http://127.0.0.1.nip.io:8000"])
def test_guard_rejects_foreign_host(base):
    """SEC-03 (A09/A10 mantığı): yerel olmayan Host → 400, uç noktaya ulaşılmaz."""
    app, hits = _guard_app()
    res = _client(app, base).get("/api/health")
    assert res.status_code == 400 and "DNS rebinding" in res.json()["detail"]
    assert hits == []
    assert res.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize("base", ["http://127.0.0.1:8000", "http://localhost:8000",
                                  "http://[::1]:8000", "http://LOCALHOST.:9000"])
def test_guard_allows_loopback_hosts(base):
    """SEC-03 yanlış pozitif kontrolü (A11)."""
    app, _ = _guard_app()
    assert _client(app, base).get("/api/health").status_code == 200


def test_guard_extra_host_configurable():
    app, _ = _guard_app(hosts=["127.0.0.1", "testserver"])
    assert _client(app, "http://testserver").get("/api/health").status_code == 200


@pytest.mark.parametrize("headers", [{"Origin": ATTACKER}, {"Origin": "null"},
                                     {"Sec-Fetch-Site": "cross-site"},
                                     {"Origin": "http://localhost:3000"},
                                     {"Origin": ATTACKER, "Content-Type": "text/plain"}])
def test_guard_rejects_cross_site_post(headers):
    """SEC-01 (A01, A02, A04): başka siteden durum değiştiren istek → 403, uç nokta çalışmaz."""
    app, hits = _guard_app()
    res = _client(app).post("/api/companies/EXMP/ingest", headers=headers, content=b"{}")
    assert res.status_code == 403 and "CSRF" in res.json()["detail"]
    assert hits == []


@pytest.mark.parametrize("base,headers", [
    ("http://127.0.0.1:8000", {}),                                     # curl / betik
    ("http://127.0.0.1:8000", {"Origin": "http://127.0.0.1:5173"}),     # geliştirme arayüzü
    ("http://127.0.0.1:8000", {"Origin": "http://localhost:5173"}),
    ("http://127.0.0.1:8000", {"Origin": "http://127.0.0.1:8000"}),     # aynı köken
    ("http://localhost:9000", {"Origin": "http://localhost:9000",
                               "Sec-Fetch-Site": "same-origin"}),       # başka portta aynı köken
    ("http://127.0.0.1:8000", {"Sec-Fetch-Site": "same-site"}),
])
def test_guard_allows_legit_post(base, headers):
    """SEC-01 yanlış pozitif kontrolü (A05): arayüz, aynı köken ve Origin'siz istemciler."""
    app, hits = _guard_app()
    res = _client(app, base).post("/api/companies/EXMP/ingest", json={"analyze": False},
                                  headers=headers)
    assert res.status_code == 202, res.text
    assert hits == ["POST /api/companies/EXMP/ingest"]


def test_guard_get_from_other_origin_not_blocked():
    """GET durum değiştirmez; yanıtın okunmasını CORS engeller (ara katman 403 vermez)."""
    app, _ = _guard_app()
    assert _client(app).get("/api/health", headers={"Origin": ATTACKER}).status_code == 200


def test_security_headers_and_docs_csp():
    """SEC-12 (A31): tüm yanıtlarda CSP frame-ancestors 'none', XFO DENY, nosniff,
    Referrer-Policy; /docs için Swagger CDN'ine izin veren ayrı politika."""
    app, _ = _guard_app()
    c = _client(app)
    h = c.get("/api/health").headers
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert "'unsafe-inline'" not in h["content-security-policy"].split("script-src")[1] \
        .split(";")[0]
    assert "'sha256-abc='" in h["content-security-policy"]
    assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff"
    assert h["referrer-policy"] == "no-referrer"
    docs = c.get("/docs").headers["content-security-policy"]
    assert "cdn.jsdelivr.net" in docs and "frame-ancestors 'none'" in docs


def test_inline_script_hash_matches_built_index(tmp_path):
    """SEC-12: CSP, index.html'deki satır içi tema betiğine yalnızca SHA-256 özetiyle izin
    verir; özet tarayıcının hesapladığı biçimde (betik içeriğinin base64 SHA-256'sı)."""
    script = "\n  (function () { document.documentElement.dataset.x = 1; })();\n"
    (tmp_path / "index.html").write_text(
        f"<html><head><script>{script}</script><script type=\"module\" src=\"./a.js\">"
        "</script></head></html>", encoding="utf-8")
    expected = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    assert API["inline_script_hashes"](str(tmp_path)) == [expected]
    assert API["inline_script_hashes"](str(tmp_path / "yok")) == []
    real = REPO / "frontend" / "dist"
    if (real / "index.html").exists():
        assert len(API["inline_script_hashes"](str(real))) >= 1


def test_host_name_parsing():
    f = API["_host_name"]
    assert f("127.0.0.1:8000") == "127.0.0.1"
    assert f("[::1]:8000") == "::1"
    assert f("LocalHost.") == "localhost"
    assert f("rebind.kotu.example:8000") == "rebind.kotu.example"
    assert f("") == ""


# --------------------------------------------------------------------------- SEC-03 ek


def test_ask_sliding_window_limit():
    """SEC-03 ek önlem (A12): dakikada 20 soru; 21. istek Retry-After ile reddedilir, pencere
    kayınca yeniden izin verilir."""
    now = [0.0]
    lim = API["SlidingWindowLimiter"](20, 60.0, clock=lambda: now[0])
    assert all(lim.hit() is None for _ in range(20))
    retry = lim.hit()
    assert retry is not None and 1 <= retry <= 61
    now[0] = 60.5
    assert lim.hit() is None


def test_llm_daily_budget():
    day = [_dt.date(2026, 11, 25)]
    budget = API["DailyBudget"](3, today=lambda: day[0])
    assert [budget.take() for _ in range(4)] == [True, True, True, False]
    day[0] = _dt.date(2026, 11, 26)
    assert budget.take() is True
    assert all(API["DailyBudget"](0).take() for _ in range(1000))  # 0 = sınırsız


@pytest.mark.parametrize("name,value", [("FINSIGHT_ASK_PER_MINUTE", "0"),
                                        ("FINSIGHT_MAX_CONCURRENT_JOBS", "50"),
                                        ("FINSIGHT_MAX_QUEUED_JOBS", "-1"),
                                        ("FINSIGHT_LLM_DAILY_LIMIT", "1.5")])
def test_limit_env_validation(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    getter = {"FINSIGHT_ASK_PER_MINUTE": config.get_ask_per_minute,
              "FINSIGHT_MAX_CONCURRENT_JOBS": config.get_max_concurrent_jobs,
              "FINSIGHT_MAX_QUEUED_JOBS": config.get_max_queued_jobs,
              "FINSIGHT_LLM_DAILY_LIMIT": config.get_llm_daily_limit}[name]
    with pytest.raises(ConfigError):
        getter()


def test_limit_env_defaults(monkeypatch):
    for n in ("FINSIGHT_ASK_PER_MINUTE", "FINSIGHT_MAX_CONCURRENT_JOBS",
              "FINSIGHT_MAX_QUEUED_JOBS", "FINSIGHT_LLM_DAILY_LIMIT", "FINSIGHT_QA_LOG",
              "FINSIGHT_ALLOWED_HOSTS"):
        monkeypatch.delenv(n, raising=False)
    assert (config.get_ask_per_minute(), config.get_max_concurrent_jobs(),
            config.get_max_queued_jobs(), config.get_llm_daily_limit()) == (20, 2, 8, 300)
    assert config.qa_log_enabled() is True
    monkeypatch.setenv("FINSIGHT_QA_LOG", "0")
    assert config.qa_log_enabled() is False
    monkeypatch.setenv("FINSIGHT_ALLOWED_HOSTS", "finsight.local, *, bad host")
    assert config.get_allowed_hosts() == ["127.0.0.1", "localhost", "::1", "finsight.local"]


# --------------------------------------------------------------------------- SEC-02


JOBS = _load("jobs", {"JobRunner"})


def test_job_runner_capacity_and_release():
    runner = JOBS["JobRunner"](max_running=2, max_queued=1)
    assert runner.capacity == 3
    gate = threading.Event()
    futures = []
    for _ in range(3):
        assert runner.try_reserve()
        futures.append(runner.submit(gate.wait, 10))
    assert runner.try_reserve() is False  # 4. iş → API 429
    gate.set()
    concurrent.futures.wait(futures, timeout=10)
    deadline = time.time() + 5
    while runner.active and time.time() < deadline:
        time.sleep(0.01)
    assert runner.active == 0
    runner.shutdown()


def test_job_flood_does_not_starve_request_pool():
    """SEC-02 (A08 mantığı, bulutta): 42 eşzamanlı ingest benzeri istek; iş ``JobRunner``'da
    çalışır ve yanıt sonrası iş parçacığı TUTMADAN beklenir (``_await_job``). Beklenen: en
    fazla 2 iş aynı anda çalışır, fazlası 429, /health < 1 sn."""
    pytest.importorskip("starlette")
    from starlette.applications import Starlette
    from starlette.background import BackgroundTask
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    runner = JOBS["JobRunner"](max_running=2, max_queued=8)
    release = threading.Event()
    running = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def slow_job():
        with lock:
            running["now"] += 1
            running["peak"] = max(running["peak"], running["now"])
        release.wait(30)
        with lock:
            running["now"] -= 1

    def ingest(_request):  # senkron uç nokta (FastAPI'deki gibi havuzda)
        if not runner.try_reserve():
            return JSONResponse({"detail": "dolu"}, status_code=429)
        future = runner.submit(slow_job)
        return JSONResponse({"status": "queued"}, status_code=202,
                            background=BackgroundTask(API["_await_job"], future))

    def health(_request):
        return JSONResponse({"status": "ok"})

    app = Starlette(routes=[Route("/ingest", ingest, methods=["POST"]),
                            Route("/health", health)])
    codes: list[int] = []
    with TestClient(app) as client:
        threads = [threading.Thread(target=lambda: codes.append(client.post("/ingest")
                                                                .status_code), daemon=True)
                   for _ in range(42)]
        for t in threads:
            t.start()
        time.sleep(1.0)
        t0 = time.perf_counter()
        assert client.get("/health").status_code == 200
        latency = time.perf_counter() - t0
        release.set()
        for t in threads:
            t.join(timeout=30)
    runner.shutdown()
    assert latency < 1.0, f"/health {latency:.2f} sn"
    assert running["peak"] <= 2
    assert codes.count(202) == 10 and codes.count(429) == 32, codes


# --------------------------------------------------------------------------- SEC-10


def test_ticker_map_single_flight():
    """SEC-10 (A29 mantığı): SEC yavaşken ikinci arama kilitte beklemez."""
    calls = []

    class SlowClient:
        def company_tickers(self):
            calls.append(1)
            time.sleep(1.0)
            return {"0": {"cik_str": 1, "ticker": "EXMP", "title": "Example"}}

    cache = API["_TickerMapCache"](lambda: SlowClient())
    first = threading.Thread(target=cache.get, daemon=True)
    first.start()
    time.sleep(0.2)
    t0 = time.perf_counter()
    assert cache.get() is None  # henüz harita yok → yerel arama
    assert time.perf_counter() - t0 < 0.1
    first.join(timeout=5)
    assert "EXMP" in cache.get() and len(calls) == 1


def test_ticker_map_failure_recorded():
    class Broken:
        def company_tickers(self):
            raise RuntimeError("ağ yok")

    cache = API["_TickerMapCache"](lambda: Broken())
    assert cache.get() is None and cache.failure_reason == "sec_unavailable"


# --------------------------------------------------------------------------- SEC-07


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.now += max(s, 0.0)


def test_file_limiter_spacing_and_pause_shared_between_instances(tmp_path):
    """SEC-07: aynı klasördeki iki ``FileRateLimiter`` (iki süreci temsilen) tek bütçeyi ve
    429 duraklamasını paylaşır."""
    clock = Clock()
    a = FileRateLimiter(str(tmp_path), 8, clock=clock, sleep=clock.sleep)
    b = FileRateLimiter(str(tmp_path), 8, clock=clock, sleep=clock.sleep)
    stamps = []
    for i in range(16):
        (a if i % 2 else b).acquire()
        stamps.append(clock.now)
    assert all(y - x >= 0.125 - 1e-9 for x, y in zip(stamps, stamps[1:], strict=False))
    a.pause(10)
    before = clock.now
    b.acquire()
    assert clock.now - before >= 10 - 1e-6


def test_file_limiter_corrupt_state_is_bounded(tmp_path):
    clock = Clock()
    (tmp_path / ".ratelimit").write_text('{"next": 1e18, "paused_until": "x"}')
    lim = FileRateLimiter(str(tmp_path), 8, clock=clock, sleep=clock.sleep)
    before = clock.now
    lim.acquire()
    assert clock.now - before <= 0.125 + 1e-9


def test_cli_sec_client_upgraded_to_shared_limiter(tmp_path):
    """SEC-07: ``finsight.ingest`` CLI kendi ``RateLimiter(get_max_rps())``'ını verir; disk
    önbelleği ile kurulan istemci aynı klasörün paylaşılan sınırlayıcısına yükseltilir (API ve
    refresh ile ortak bütçe). Sahte saatli test sınırlayıcıları yükseltilmez."""
    from finsight.rate_limiter import shared_file_limiter

    cache = FileCache(str(tmp_path / "c"))
    cli = SecClient(UA, limiter=RateLimiter(8), cache=cache, fetcher=lambda u, h: (200, b"{}"))
    assert cli.limiter is shared_file_limiter(str(tmp_path / "c"), 8)
    clock = Clock()
    fake = RateLimiter(8, clock=clock, sleep=clock.sleep)
    assert SecClient(UA, limiter=fake, cache=cache, fetcher=lambda u, h: (200, b"")).limiter \
        is fake


_PROC = """
import sys, time, json
sys.path.insert(0, {backend!r})
from finsight.rate_limiter import RateLimiter, shared_file_limiter
from finsight.sec_client import FileCache, SecClient
if {cli!r}:
    # CLI yolu (finsight.ingest): kendi RateLimiter'ı + disk önbelleği
    client = SecClient("FinSight test t@example.com", limiter=RateLimiter(8),
                       cache=FileCache({cache!r}), fetcher=lambda u, h: (200, b"{{}}"))
    acquire = client.limiter.acquire
else:
    acquire = shared_file_limiter({cache!r}, 8).acquire  # API / refresh yolu
start = {start!r}
while time.time() < start:
    time.sleep(0.005)
stamps = []
for _ in range({n}):
    acquire()
    stamps.append(time.time())
print(json.dumps(stamps))
"""


def test_api_and_cli_processes_share_rate_limit(tmp_path):
    """SEC-07 (A25 mantığı, bulutta): API süreci + CLI süreci aynı anda 16'şar istek hakkı
    ister. Beklenen: herhangi bir 1 sn'lik pencerede toplam ≤ 9 (8 rps + zamanlama)."""
    start = time.time() + 2.0
    procs = [subprocess.Popen([sys.executable, "-c", _PROC.format(
        backend=str(BACKEND), cache=str(tmp_path / "cache"), start=start, n=16, cli=cli)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=dict(os.environ, FINSIGHT_DISABLE_DOTENV="1")) for cli in (False, True)]
    stamps: list[float] = []
    for p in procs:
        out, err = p.communicate(timeout=60)
        assert p.returncode == 0, err[-500:]
        stamps += json.loads(out.strip().splitlines()[-1])
    stamps.sort()
    peak = max(sum(1 for s in stamps if t <= s < t + 1.0) for t in stamps)
    assert peak <= 9, f"1 sn'de {peak} istek"


def test_redirect_hops_are_rate_limited_and_counted():
    """Kod incelemesi: yönlendirme adımları da hız sınırından ve sayaçtan geçer (tek
    get_bytes en fazla 6 isteği sınırsız yapamaz)."""
    clock = Clock()
    acquires = []

    class CountingLimiter(RateLimiter):
        def acquire(self):
            acquires.append(clock.now)
            super().acquire()

    class RedirectingFetcher:
        def __init__(self):
            self.before_hop = None
            self.urls = []

        def __call__(self, url, headers):
            def get(u):
                self.urls.append(u)
                n = len(self.urls)
                if n < 4:
                    return 302, b"", f"https://www.sec.gov/r{n}"
                return 200, b"{}", None
            return follow_sec_redirects(get, url, before_hop=self.before_hop)

    fetcher = RedirectingFetcher()
    client = SecClient(UA, fetcher=fetcher, sleep=clock.sleep,
                       limiter=CountingLimiter(8, clock=clock, sleep=clock.sleep))
    assert client.get_json("https://data.sec.gov/x.json") == {}
    assert len(fetcher.urls) == 4 and client.request_count == 4 and len(acquires) == 4
    assert clock.now - 1_000_000.0 >= 3 * 0.125 - 1e-9


# --------------------------------------------------------------------------- SEC-09


@pytest.mark.parametrize("url,ttl", [
    ("https://www.sec.gov/Archives/edgar/data/1/000000000125000001/a.htm", None),
    ("https://data.sec.gov/submissions/CIK0000000001.json?x=/Archives/edgar/data/", 3600),
    ("https://kotu.example/Archives/edgar/data/1/a.htm", 3600),
    ("https://www.sec.gov/Archives/edgar/data/1/000000000125000001/index.json", 3600),
    ("https://www.sec.gov/Archives/edgar/data/1/000000000125000001/"
     "0000000001-25-000001-index.htm", 3600),
    ("https://www.sec.gov/Archives/edgar/data/1/000000000125000001/"
     "0000000001-25-000001-index-headers.html", 3600),
    ("https://www.sec.gov/Archives/edgar/data/1/000000000125000001/", 3600),
    ("https://www.sec.gov/files/company_tickers.json", 86400),
])
def test_cache_ttl_classes(url, ttl):
    """Kod incelemesi: yalnızca https://*.sec.gov YOLU /Archives/edgar/data/ ile başlayan ve
    dizin sayfası olmayan belgeler süresiz önbelleğe alınır."""
    assert cache_ttl_for(url, 3600, 86400) == ttl


def test_poisoned_cache_entry_is_ignored(tmp_path):
    """SEC-09: önceki sürümden kalan bozuk (HTML) önbellek kaydı okunmaz; SEC'ten yeniden
    alınıp düzeltilir."""
    url = "https://data.sec.gov/submissions/CIK0000000001.json"
    cache = FileCache(str(tmp_path))
    cache.set(url, b"<html>bakim</html>")
    clock = Clock()
    calls = []

    def fetch(u, h):
        calls.append(u)
        return 200, b'{"cik": "1"}'

    client = SecClient(UA, fetcher=fetch, cache=cache, sleep=clock.sleep,
                       limiter=RateLimiter(8, clock=clock, sleep=clock.sleep))
    assert client.submissions(1) == {"cik": "1"} and len(calls) == 1
    assert cache.get(url) == b'{"cik": "1"}'


# --------------------------------------------------------------------------- SEC-04/05 kontrol


@pytest.mark.parametrize("question", [
    "Şirketin yatırım harcamaları ne kadar?", "Şirket hisse geri alımı yaptı mı?",
    "What is the long-term debt?", "Does the company hold short-term investments?",
    "Şirketin portföyünde hangi ürünler var?", "Şirket yeni bir pazara girmek için hazırlanıyor mu?",
])
def test_advice_question_false_positives(question):
    from finsight.rag import is_advice_question

    assert is_advice_question(question) is False


@pytest.mark.parametrize("sentence", [
    "Şirket ürün portföyünü genişletti [1].", "Şirket uzun vadeli borçlanma yaptı [1].",
    "The company may go short on supply contracts [1].",
    "Şirket 2025'te hisse geri alımı yaptı [1].",
])
def test_advice_sentence_false_positives(sentence):
    from finsight.rag import _sentence_allowed

    assert _sentence_allowed(sentence) is True


@pytest.mark.parametrize("answer", [
    "Hasılat 412,5 milyar dolar oldu [1].", "Brüt marj %46,2 idi [1].",
    "Şirketin 2 segmenti var [1].", "1.234.567 hisse geri alındı [1].",
    "Hasılat 2025 mali yılında 412.500 milyon dolar [1].",
])
def test_number_check_normalizes_formats(answer):
    """SEC-05 yanlış pozitif kontrolü: biçim farkı (TR/EN ondalık, milyar/billion, %, sayı
    kelimesi) olan ama kaynakta geçen sayı korunur."""
    from finsight.rag import filter_answer

    src = ("Total revenue was $412.5 billion in fiscal 2025. Gross margin was 46.2%. "
           "We have two segments. The company repurchased 1,234,567 shares.")
    assert filter_answer(answer, [src])[0] == answer


def test_number_check_rejects_wrong_source():
    """SEC-05: sayı başka bir kaynakta geçse bile ATIF YAPILAN kaynakta yoksa çıkarılır."""
    from finsight.rag import filter_answer

    text, used = filter_answer("Hasılat 999 milyar oldu [1].", ["Revenue grew.", "999 billion"])
    assert text == "" and used == set()


# --------------------------------------------------------------------------- O7


def test_o7_budget_charged_only_on_real_llm_call():
    """O7: günlük LLM bütçesi yalnızca LLM gerçekten çağrıldığında (soru başına bir kez) düşer;
    LLM'e gitmeyen soru (ilgili parça yok) bütçe harcamaz; bütçe dolunca LLMError → rag
    çıkarımsal moda düşer ve ``exhausted`` uyarı için işaretlenir."""
    from finsight.rag import answer_question

    from .attack_support import FakeLLM, KeywordEmbedder, make_chunk

    budget = API["DailyBudget"](1)
    llm = FakeLLM("Revenue risk discussed [1].")
    unused = API["BudgetedLLM"](llm, budget)
    out = answer_question("weather football", [make_chunk("supplier risk")], KeywordEmbedder(),
                          unused)
    assert out["mode"] == "not_found" and not unused.charged and llm.calls == []
    first = API["BudgetedLLM"](llm, budget)
    out = answer_question("supplier risk?", [make_chunk("Supplier risk is high.")],
                          KeywordEmbedder(), first)
    assert out["mode"] == "llm" and first.charged and not first.exhausted
    first.complete("s", "u")  # aynı soru içinde ikinci çağrı ek birim harcamaz
    second = API["BudgetedLLM"](llm, budget)
    out = answer_question("supplier risk?", [make_chunk("Supplier risk is high.")],
                          KeywordEmbedder(), second)
    assert second.exhausted and out["mode"] == "extractive"
