"""Saldırı testleri — hizmet dışı bırakma ve kaynak tüketimi (SEC-02, SEC-10, SEC-16).

FastAPI/SQLAlchemy gerekir (bulutta atlanır; kullanıcının bilgisayarında koşar). SEC sahte;
yavaş SEC ``threading.Event`` ile taklit edilir, gerçek bekleme süreleri kısadır (≤ ~5 sn).
"""

from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from integration_support import SUBMISSIONS_URL, TICKER  # noqa: E402

from .attack_support import LOCAL_BASE  # noqa: E402

FLOOD = 42          # anyio iş parçacığı havuzu varsayılanı 40 belirteç
MAX_RUNNING = 4     # denetim önerisi FINSIGHT_MAX_CONCURRENT_JOBS=2; tolerans 4


def _running_jobs(session_factory) -> int:
    from sqlalchemy import func, select

    from finsight.models import IngestionJob

    with session_factory() as s:
        return s.scalar(select(func.count()).select_from(IngestionJob)
                        .where(IngestionJob.status == "running"))


# --------------------------------------------------------------------------- SEC-02


def test_sec02_ingest_flood_exhausts_threadpool(light_models, file_session_factory, sec):
    """SEC-02 (CWE-770) — Saldırgan A1 (SEC-01 ile uzaktan): 42 farklı ticker için ingest.
    Her iş, SEC yavaşken iş parçacığı havuzundan bir belirteç tutar (burada sahte istemci
    ``release`` olayını bekler). Beklenen savunma: aynı anda en fazla ``MAX_RUNNING`` iş
    ``running`` (fazlası kuyrukta / 429) ve bu sırada ``GET /api/health`` < 1 sn yanıt verir."""
    from fastapi.testclient import TestClient

    from finsight import api

    release = threading.Event()

    def slow_factory():
        release.wait(30)
        return sec()

    app = api.create_app(session_factory=file_session_factory, client_factory=slow_factory,
                         serve_frontend=False)
    statuses: list[int] = []
    health: dict = {}
    with TestClient(app, base_url=LOCAL_BASE) as client:
        def flood(i: int) -> None:
            try:
                statuses.append(client.post(f"/api/companies/T{i:03d}/ingest",
                                            json={"analyze": False}).status_code)
            except Exception as exc:  # noqa: BLE001 - kanıt olarak kaydedilir
                statuses.append(-1)
                health.setdefault("flood_errors", []).append(type(exc).__name__)

        def probe() -> None:
            t0 = time.perf_counter()
            code = client.get("/api/health").status_code
            health["latency"] = time.perf_counter() - t0
            health["code"] = code

        threads = [threading.Thread(target=flood, args=(i,), daemon=True) for i in range(FLOOD)]
        p = threading.Thread(target=probe, daemon=True)
        running = -1
        try:
            for t in threads:
                t.start()
            time.sleep(2.0)  # işlerin başlaması için
            running = _running_jobs(file_session_factory)
            p.start()
            p.join(timeout=3.0)
        finally:
            release.set()
            for t in threads:
                t.join(timeout=60)
        p.join(timeout=30)
    latency = health.get("latency", float("inf"))
    assert latency < 1.0 and health.get("code") == 200, \
        f"/api/health {latency:.2f} sn (havuz tükendi), running={running}"
    assert running <= MAX_RUNNING, f"aynı anda {running} iş çalışıyor"


# --------------------------------------------------------------------------- SEC-10


def test_sec10_slow_sec_does_not_block_other_searches():
    """SEC-10 (CWE-667) — SEC yavaşken (burada 2 sn) arama kutusunun her tuş vuruşu kilitte
    bekler ve havuz belirteci tutar. Yük: iki eşzamanlı ``_TickerMapCache.get()``.
    Beklenen savunma: ikinci çağrı < 0.1 sn içinde (eski harita veya ``None`` ile) döner."""
    from finsight import api

    class SlowClient:
        def company_tickers(self):
            time.sleep(2.0)
            return {"0": {"cik_str": 1, "ticker": "EXMP", "title": "Example"}}

    cache = api._TickerMapCache(lambda: SlowClient())
    first = threading.Thread(target=cache.get, daemon=True)
    first.start()
    time.sleep(0.2)
    t0 = time.perf_counter()
    cache.get()
    elapsed = time.perf_counter() - t0
    first.join(timeout=10)
    assert elapsed < 0.1, f"ikinci arama {elapsed:.2f} sn kilitte bekledi"


# --------------------------------------------------------------------------- SEC-16


def _submissions_calls(sec) -> int:
    return sum(1 for u in sec.urls() if u == SUBMISSIONS_URL)


def test_sec16_freshness_unknown_company_no_sec_request(client, sec):
    """SEC-16/1 — Saldırgan A1: ``<img src=".../api/companies/X/freshness">`` dizisiyle kayıtsız
    yüzlerce şirket için önbelleği atlayan SEC isteği ürettirmek. Yük: kayıtsız ``ZZZZQ``.
    Beklenen savunma: 404 ve SEC'e hiç istek gitmez. (Aşama 4 kodunda bulunuyor.)"""
    res = client.get("/api/companies/ZZZZQ/freshness")
    assert res.status_code == 404
    assert sec.urls() == []


@pytest.mark.parametrize("sec_status", [200, 503])
def test_sec16_freshness_flood_hits_sec_once(client, ingested, sec, sec_status):
    """SEC-16/1 — Kayıtlı şirket için 20 ardışık ``/freshness`` (``<img>`` seli). SEC başarılı
    ya da hatalıyken (negatif önbellek) SEC'e en fazla 1 istek gitmeli."""
    if sec_status != 200:
        sec.fetcher.routes[SUBMISSIONS_URL] = (sec_status, b"")
    before = _submissions_calls(sec)
    codes = {client.get(f"/api/companies/{ingested}/freshness").status_code for _ in range(20)}
    assert codes <= {200, 404}, codes  # 404: freshness uç noktası olmayan eski sürüm
    assert _submissions_calls(sec) - before <= 1, sec.urls()[-5:]


def test_sec16_concurrent_job_creation_is_atomic(light_models, file_session_factory):
    """SEC-16/3 (CWE-362) — API ile ``finsight.refresh`` (ayrı süreç, ``_ingest_lock``'u
    paylaşmaz) aynı şirket için aynı anda iş açar. Yük: iki ayrı oturum + iki iş parçacığı.
    Beklenen savunma: veritabanı düzeyinde tek etkin iş (``create_job_if_idle``)."""
    from finsight import repository as repo

    create = getattr(repo, "create_job_if_idle", None)
    barrier = threading.Barrier(2)
    created: list[int] = []

    def start() -> None:
        with file_session_factory() as s:
            barrier.wait(5)
            if create is None:  # eski sürüm: CLI yolu kontrol + oluşturma
                created.append(repo.create_job(s, TICKER, True)["id"])
                return
            try:
                job, is_new = create(s, TICKER, True)
            except getattr(repo, "JobBusyError", RuntimeError):
                return
            if is_new:
                created.append(job["id"])

    threads = [threading.Thread(target=start) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert len(created) == 1, f"aynı şirket için {len(created)} etkin iş açıldı"


# --------------------------------------------------------------------------- savunma ekleri


def test_ingest_db_locked_returns_503(client, monkeypatch):
    """Kod incelemesi: create_job_if_idle sırasında SQLite "database is locked" (busy_timeout
    sonrası) → genel 500 değil 503 + Türkçe mesaj; ayrılan iş kapasitesi geri bırakılır."""
    from sqlalchemy.exc import OperationalError

    from finsight import repository as repo

    def locked(*_a, **_kw):
        raise OperationalError("INSERT", {}, Exception("database is locked"))

    monkeypatch.setattr(repo, "create_job_if_idle", locked)
    res = client.post(f"/api/companies/{TICKER}/ingest", json={"analyze": False})
    assert res.status_code == 503 and "meşgul" in res.json()["detail"]
    assert res.headers.get("retry-after")
    assert client.app.state.job_runner.active == 0


def test_ingest_capacity_full_returns_429(client):
    """SEC-02: kapasite doluyken yeni şirket için 429 + Retry-After + Türkçe mesaj."""
    runner = client.app.state.job_runner
    taken = 0
    while runner.try_reserve():
        taken += 1
    try:
        res = client.post("/api/companies/ZZZQ/ingest", json={"analyze": False})
        assert res.status_code == 429 and res.headers.get("retry-after")
        assert "veri çekme" in res.json()["detail"]
    finally:
        for _ in range(taken):
            runner.release()


def test_ingest_passes_sec_cik_to_job_lock(client, monkeypatch):
    """Kayıtsız şirkette de CIK tabanlı iş kilidi: SEC ticker haritası önbellekteyse CIK
    create_job_if_idle'a geçer (GOOG/GOOGL aynı kilit)."""
    from finsight import repository as repo

    seen = {}
    real = repo.create_job_if_idle

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(repo, "create_job_if_idle", spy)
    client.app.state.ticker_maps.get()  # sahte SEC'ten haritayı yükle
    res = client.post(f"/api/companies/{TICKER}/ingest", json={"analyze": False})
    assert res.status_code == 202
    assert seen.get("cik")
