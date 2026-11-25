"""finsight.bench_api (NFR-04 / NFR-05 yük testi aracı) testleri.

Saf yardımcılar doğrudan; komut satırı ise bu süreç içinde 127.0.0.1'e bağlanan küçük bir
standart kütüphane sunucusuna karşı denenir (dış ağa çıkılmaz). Beklentiler aracın belgelenmiş
kurallarından gelir: yalnızca yerel adres, ingest varsayılan olarak çağrılmaz, LLM etkinken
/ask yalnızca --allow-llm ile, yüzdelikler yalnızca başarılı isteklerden.
"""

from __future__ import annotations

import json
import random
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from finsight import bench_api as b

# --------------------------------------------------------------------------- adres ve ticker


@pytest.mark.parametrize("url,expected", [
    ("http://127.0.0.1:8000", "http://127.0.0.1:8000"),
    ("http://127.0.0.1:8000/", "http://127.0.0.1:8000"),
    ("  http://localhost:8790  ", "http://localhost:8790"),
    ("http://LOCALHOST:1", "http://LOCALHOST:1"),
    ("https://[::1]:8443", "https://[::1]:8443"),
])
def test_local_base_urls_accepted(url, expected):
    assert b.validate_base_url(url) == expected


@pytest.mark.parametrize("url", [
    "http://example.com",
    "http://127.0.0.1.example.com:8000",      # önek benzerliği
    "http://localhost.example:8000",
    "http://127.0.0.1@evil.example:8000",     # kullanıcı bilgisi hilesi: ana makine evil
    "http://user:pw@127.0.0.1:8000",          # kimlik bilgisi taşımasın
    "http://10.0.0.5:8000",
    "http://0.0.0.0:8000",
    "http://192.168.1.10",
    "ftp://127.0.0.1",
    "127.0.0.1:8000",                          # şemasız
    "",
    "http://127.0.0.1:8000/?x=1",
    "http://127.0.0.1:99999",                  # geçersiz port
])
def test_non_local_or_malformed_urls_rejected(url):
    with pytest.raises(ValueError):
        b.validate_base_url(url)


@pytest.mark.parametrize("raw,expected", [("msft", "MSFT"), (" BRK.B ", "BRK.B"),
                                          ("abc-d", "ABC-D")])
def test_ticker_normalized(raw, expected):
    assert b.validate_ticker(raw) == expected


@pytest.mark.parametrize("raw", ["", "  ", "MS FT", "../etc", "A" * 11, "MSFŢ", "MSFT/x",
                                 "MSFT?q=1", "ＭＳＦＴ"])
def test_bad_tickers_rejected(raw):
    with pytest.raises(ValueError):
        b.validate_ticker(raw)


# --------------------------------------------------------------------------- karışım


def _paths(actions):
    return [(r.method, r.path) for a in actions for r in a.requests]


def test_mix_never_contains_sec_calling_endpoints():
    """SEC hız sınırı (NFR-02): ingest ve freshness hiçbir yapılandırmada karışımda yok."""
    for kwargs in ({}, {"compare_with": "OTHR"}, {"include_ask": False}):
        actions = b.build_actions("EXMP", **kwargs)
        for method, path in _paths(actions):
            assert "/ingest" not in path and "/freshness" not in path, path
            assert path.startswith("/api/")
            assert method in {"GET", "POST"}
            if method == "POST":
                assert path.endswith("/ask")


def test_mix_optional_ask_and_compare():
    names = {a.name for a in b.build_actions("EXMP")}
    assert "ask" in names and "compare" not in names
    names = {a.name for a in b.build_actions("EXMP", compare_with="OTHR", include_ask=False)}
    assert "compare" in names and "ask" not in names
    compare = next(a for a in b.build_actions("EXMP", compare_with="OTHR") if a.name == "compare")
    assert compare.requests[0].path == "/api/compare?tickers=EXMP,OTHR"


def test_overview_screen_is_composite_of_overview_and_score():
    screen = next(a for a in b.build_actions("EXMP") if a.composite)
    assert screen.composite == b.OVERVIEW_SCREEN
    assert [r.path for r in screen.requests] == ["/api/companies/EXMP/overview",
                                                 "/api/companies/EXMP/score"]


def test_weighted_choice_is_deterministic_and_proportional():
    actions = b.build_actions("EXMP")
    seq1 = [b.pick_action(actions, random.Random(5)).name for _ in range(3)]
    seq2 = [b.pick_action(actions, random.Random(5)).name for _ in range(3)]
    assert seq1 == seq2
    rng = random.Random(1)
    draws = [b.pick_action(actions, rng).name for _ in range(20000)]
    total_w = sum(a.weight for a in actions)
    for a in actions:
        share = draws.count(a.name) / len(draws)
        assert abs(share - a.weight / total_w) < 0.02, a.name


# --------------------------------------------------------------------------- istatistik


def test_percentile_linear_interpolation_matches_numpy_default():
    np = pytest.importorskip("numpy")
    rng = random.Random(3)
    data = [rng.uniform(1, 500) for _ in range(257)]
    for q in (0, 1, 50, 90, 95, 99, 100):
        assert b.percentile(data, q) == pytest.approx(float(np.percentile(data, q)), rel=1e-12)


def test_percentile_edges():
    assert b.percentile([], 95) is None
    assert b.percentile([7.0], 99) == 7.0
    assert b.percentile([4, 1, 3, 2], 50) == 2.5  # sırasız girdi
    assert b.percentile([1, 2, 3, 4], 95) == pytest.approx(3.85)
    with pytest.raises(ValueError):
        b.percentile([1, 2], 101)
    with pytest.raises(ValueError):
        b.percentile([1, 2], -1)


def test_summarize_excludes_failed_requests_from_latency():
    R = b.Record
    recs = [R("GET /a", 10, True, 200), R("GET /a", 20, True, 200), R("GET /a", 30, True, 200),
            R("GET /a", 5000, False, 500), R("GET /a", 1, False, None, "ConnectError"),
            R("GET /b", 7, True, 200)]
    s = b.summarize(recs, wall_seconds=2.0)
    a = s["GET /a"]
    assert a["requests"] == 5 and a["errors"] == 2
    assert a["error_rate"] == pytest.approx(0.4)
    assert a["p50_ms"] == 20.0 and a["max_ms"] == 30.0  # 5000 ms'lik hata sayılmaz
    assert a["error_kinds"] == {"500": 1, "ConnectError": 1}
    assert a["rps"] == 2.5
    assert list(s) == ["GET /a", "GET /b"]  # ilk görülme sırası


def test_summarize_all_failed_and_zero_wall_time():
    s = b.summarize([b.Record("x", 3, False, 404)], wall_seconds=0)
    assert s["x"]["p95_ms"] is None and s["x"]["error_rate"] == 1.0 and s["x"]["rps"] is None
    assert b.summarize([], 1.0) == {}


def test_total_row_excludes_composite_screen_records():
    R = b.Record
    recs = [R("GET /a", 10, True, 200), R("GET /b", 20, True, 200),
            R(b.OVERVIEW_SCREEN, 30, True, None)]
    assert b.total_row(recs, 1.0)["requests"] == 2
    assert b.total_row([], 1.0)["requests"] == 0


def _summary(series, p95, error_rate=0.0):
    return {series: {"p95_ms": p95, "error_rate": error_rate}}


def test_nfr_comparison_verdicts():
    ov = "GET /api/companies/{ticker}/overview"
    rows = {(r["nfr"], r["series"]): r for r in b.nfr_comparison(_summary(ov, 1999.9))}
    assert rows[("NFR-04", ov)]["met"] is True
    assert rows[("NFR-05", "POST /api/companies/{ticker}/ask")]["met"] is None  # ölçülmedi
    rows = {r["series"]: r for r in b.nfr_comparison(_summary(ov, 2000.0))}
    assert rows[ov]["met"] is False  # sınır: "< 2 sn" katı
    rows = {r["series"]: r for r in b.nfr_comparison(_summary(ov, 50.0, error_rate=0.05))}
    assert rows[ov]["met"] is False and "hata oranı" in rows[ov]["note"]
    rows = {r["series"]: r for r in b.nfr_comparison(_summary(ov, None, error_rate=1.0))}
    assert rows[ov]["met"] is None


def test_format_table_contains_every_series_and_verdict():
    R = b.Record
    recs = [R("GET /api/companies/{ticker}/overview", 12.0, True, 200),
            R(b.OVERVIEW_SCREEN, 20.0, True, None)]
    s = b.summarize(recs, 1.0)
    text = b.format_table(s, b.total_row(recs, 1.0), b.nfr_comparison(s))
    assert "TOPLAM (HTTP)" in text and b.OVERVIEW_SCREEN in text
    assert "SAĞLANDI" in text and "DEĞERLENDİRİLEMEDİ" in text


# --------------------------------------------------------------------------- komut satırı


class _Handler(BaseHTTPRequestHandler):
    seen: list[tuple[str, str]] = []
    llm_enabled = True
    lock = threading.Lock()

    def log_message(self, *args):  # sessiz
        return

    def _send(self, status: int, payload) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        with self.lock:
            self.seen.append(("GET", self.path))
        if self.path == "/api/health":
            self._send(200, {"status": "ok", "models": {"llm": {"enabled": self.llm_enabled}}})
        elif "/companies/NOPE/" in self.path:
            self._send(404, {"detail": "yok"})
        else:
            self._send(200, {"analyzed": True, "disclaimer": "x"})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        with self.lock:
            self.seen.append(("POST", self.path))
        self._send(202 if self.path.endswith("/ingest") else 200, {"ok": True})


@pytest.fixture
def server():
    pytest.importorskip("httpx")
    _Handler.seen = []
    _Handler.llm_enabled = True
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield srv, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _run(url, tmp_path, *extra):
    out = tmp_path / "r.json"
    code = b.main(["--base-url", url, "--ticker", "EXMP", "--users", "2", "--duration", "0.3",
                   "--json", str(out), *extra])
    return code, (json.loads(out.read_text(encoding="utf-8")) if out.exists() else None)


def test_cli_end_to_end_skips_ingest_and_paid_llm_by_default(server, tmp_path, capsys):
    _srv, url = server
    code, result = _run(url, tmp_path)
    assert code == 0
    paths = [p for _m, p in _Handler.seen]
    assert not any("/ingest" in p for p in paths)
    assert not any(p.endswith("/ask") for p in paths)  # LLM etkin → /ask çıkarıldı
    assert result["users"] == 2 and result["total"]["requests"] > 0
    assert result["total"]["errors"] == 0
    assert b.OVERVIEW_SCREEN in result["endpoints"]
    assert any("LLM" in n for n in result["notes"])
    assert {r["nfr"] for r in result["nfr"]} == {"NFR-04", "NFR-05"}
    out = capsys.readouterr().out
    assert "NFR hedefleriyle karşılaştırma" in out


def test_cli_ask_included_when_llm_disabled_or_allowed(server, tmp_path):
    _srv, url = server
    _Handler.llm_enabled = False
    code, _ = _run(url, tmp_path)
    assert code == 0
    assert any(p.endswith("/ask") for _m, p in _Handler.seen)
    _Handler.seen = []
    _Handler.llm_enabled = True
    code, _ = _run(url, tmp_path, "--allow-llm")
    assert code == 0 and any(p.endswith("/ask") for _m, p in _Handler.seen)
    _Handler.seen = []
    code, _ = _run(url, tmp_path, "--no-ask", "--allow-llm")
    assert code == 0 and not any(p.endswith("/ask") for _m, p in _Handler.seen)


def test_cli_include_ingest_calls_it_exactly_once(server, tmp_path):
    _srv, url = server
    code, result = _run(url, tmp_path, "--include-ingest")
    assert code == 0
    ingests = [p for m, p in _Handler.seen if m == "POST" and p.endswith("/ingest")]
    assert ingests == ["/api/companies/EXMP/ingest"]
    assert result["endpoints"]["POST /api/companies/{ticker}/ingest"]["requests"] == 1


def test_cli_unprocessed_company_counts_errors_and_warns(server, tmp_path):
    _srv, url = server
    out = tmp_path / "r.json"
    code = b.main(["--base-url", url, "--ticker", "NOPE", "--users", "1", "--duration", "0.2",
                   "--json", str(out)])
    assert code == 0
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["endpoints"]["GET /api/companies/{ticker}/overview"]["error_rate"] == 1.0
    assert any("404" in n for n in result["notes"])
    ov = next(r for r in result["nfr"] if r["series"].endswith("/overview"))
    assert ov["met"] is None  # başarılı istek yok → "geçti" denmez


@pytest.mark.parametrize("args", [
    ["--base-url", "http://example.com"],
    ["--users", "0"], ["--users", str(b.MAX_USERS + 1)],
    ["--duration", "0"], ["--duration", "-1"], ["--duration", "nan"],
    ["--timeout", "0"], ["--think-ms", "-5"],
    ["--ticker", "BAD TICKER"], ["--compare-with", "../x"],
])
def test_cli_rejects_invalid_arguments_without_network(args, capsys):
    assert b.main(args) == 2
    assert "Hata" in capsys.readouterr().err


def test_cli_unreachable_server_exits_2(capsys):
    pytest.importorskip("httpx")
    with socket.socket() as s:  # boş bir port bul, sonra kapat
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert b.main(["--base-url", f"http://127.0.0.1:{port}", "--duration", "0.1",
                   "--timeout", "2"]) == 2
    assert "ulaşılamadı" in capsys.readouterr().err
