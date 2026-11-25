"""Saldırı testleri — ayrıştırıcılar ve girdi doğrulama (SEC-08, SEC-11, SEC-18).

- SEC-08 ReDoS: ``split_sentences`` ayrı bir süreçte zaman sınırıyla çalıştırılır (açık varken
  test dakikalarca takılmasın diye süreç sınır aşılınca öldürülür). Bulutta koşar.
- SEC-18 ``check_ticker``: ``api.py`` FastAPI'yi içe aktardığı için fonksiyon ve ``TICKER_RE``
  kaynak koddan (AST) okunup sahte ``HTTPException`` ile çalıştırılır → bulutta koşar.
  Asıl HTTP doğrulaması ``test_attack_csrf_host.py``'dedir.
- SEC-11 ``parseHash``: ``frontend/src/router.ts`` esbuild ile paketlenip Node'da çalıştırılır;
  Node veya esbuild yoksa atlanır.
"""

from __future__ import annotations

import ast
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
REDOS_LIMIT_SECONDS = 1.0


def _run_with_limit(code: str, limit: float) -> float | None:
    """Kodu ayrı Python sürecinde çalıştırır; süreyi (sn) veya sınır aşıldıysa None döndürür."""
    script = ("import sys, time; sys.path.insert(0, %r)\n" % str(BACKEND)
              + "t0 = time.perf_counter()\n" + code + "\nprint(time.perf_counter() - t0)")
    try:
        out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                             timeout=limit + 5.0, check=True)  # +5 sn: yorumlayıcı açılışı
    except subprocess.TimeoutExpired:
        return None
    return float(out.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------------------- SEC-08

def test_sec08_redos_dot_run_in_filing_text():
    """SEC-08 (CWE-1333) — Saldırganın amacı: 10-K'ya 100 000 karakterlik tek satır nokta
    dizisi koyarak duygu analizi adımını dakikalarca kilitlemek (iş parçacığını tutar, SEC-02).
    Yük: ``"Risk " + "." * 100_000 + "x"``. Hedef: ``text_extract.split_sentences``.
    Beklenen savunma: < 1 sn."""
    elapsed = _run_with_limit(
        "from finsight.text_extract import split_sentences\n"
        "split_sentences('Risk ' + '.' * 100_000 + 'x')", REDOS_LIMIT_SECONDS)
    assert elapsed is not None and elapsed < REDOS_LIMIT_SECONDS, \
        f"split_sentences sınırı aştı: {elapsed!r} sn (None = süreç öldürüldü)"


def test_sec08_redos_mixed_punctuation_run():
    """SEC-08 varyantı — "?!." karışık noktalama dizisi (``[.!?]+`` aynı geri izlemeyi yapar).
    Yük: ``"Risk " + "?!." * 30_000 + "x"``. Beklenen savunma: < 1 sn."""
    elapsed = _run_with_limit(
        "from finsight.text_extract import split_sentences\n"
        "split_sentences('Risk ' + '?!.' * 30_000 + 'x')", REDOS_LIMIT_SECONDS)
    assert elapsed is not None and elapsed < REDOS_LIMIT_SECONDS, \
        f"split_sentences sınırı aştı: {elapsed!r} sn"


def test_sec08_normal_text_still_split():
    """SEC-08 yanlış pozitif kontrolü — düzeltme normal cümle bölmeyi bozmamalı."""
    from finsight.text_extract import split_sentences

    out = split_sentences("Revenue increased by ten percent. Costs declined in the period. "
                          "We expect continued supplier risk...")
    assert len(out) == 3


# --------------------------------------------------------------------------- SEC-18


class _FakeHTTPException(Exception):
    def __init__(self, status_code: int, detail: str = "") -> None:
        super().__init__(status_code, detail)
        self.status_code = status_code


def _load_check_ticker():
    """``api.py``'den ``check_ticker`` ve modül düzeyindeki BÜYÜK_HARF sabitlerini (AST) alır."""
    tree = ast.parse((BACKEND / "finsight" / "api.py").read_text(encoding="utf-8"))
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and all(
                isinstance(t, ast.Name) and t.id.isupper() for t in node.targets):
            nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in ("check_ticker",):
            nodes.append(node)
    module = ast.Module(body=nodes, type_ignores=[])
    namespace = {"re": re, "HTTPException": _FakeHTTPException}
    try:
        exec(compile(module, "api.py(check_ticker)", "exec"), namespace)  # noqa: S102
    except NameError as exc:  # düzeltme yeni yardımcılar kullanıyorsa HTTP testi esastır
        pytest.skip(f"check_ticker AST ile yüklenemedi ({exc}); test_attack_csrf_host.py esas")
    if "check_ticker" not in namespace:
        pytest.skip("check_ticker api.py'de bulunamadı")
    return namespace["check_ticker"]


def _rejected(check, value: str) -> bool:
    try:
        check(value)
    except _FakeHTTPException as exc:
        return exc.status_code in (400, 404, 422)
    return False


@pytest.mark.parametrize("ticker", ["..", ".", "-", "-.-", "..."])
def test_sec18_dot_dash_only_ticker_rejected(ticker):
    """SEC-18 (CWE-20) — Saldırganın amacı: ``curl --path-as-is .../companies/../ingest`` ile
    geçersiz iş kayıtları biriktirmek. Yük: ``ticker``. Beklenen savunma: 400."""
    assert _rejected(_load_check_ticker(), ticker), f"{ticker!r} kabul edildi"


@pytest.mark.parametrize("ticker", ["../../etc/passwd", "AAPL;rm", "A" * 11, "", "<svg>",
                                    "ÇAĞ", "AA PL"])
def test_sec18_malformed_ticker_rejected(ticker):
    """SEC-18 kontrol — yol geçişi, kabuk karakteri, uzun, boş, HTML, ASCII dışı ve boşluklu
    ticker'lar bugün de 400 ile reddediliyor."""
    assert _rejected(_load_check_ticker(), ticker), f"{ticker!r} kabul edildi"


@pytest.mark.parametrize("ticker", ["AAPL\n", "..\n"])
def test_sec18_trailing_newline_ticker_rejected(ticker):
    """SEC-18 (kırmızı takımın yeni bulduğu varyant) — Python'da ``$`` sondaki ``\\n``'den önce
    de eşleşir; ``/api/companies/AAPL%0A/ingest`` → ticker ``"AAPL\\n"`` doğrulamayı geçer ve
    veritabanına/iş kaydına satır sonuyla yazılır (günlük satırı sahteciliği, CWE-117).
    Yük: ``ticker``. Beklenen savunma: 400 (``TICKER_RE.fullmatch`` veya ``\\Z``)."""
    assert _rejected(_load_check_ticker(), ticker), f"{ticker!r} kabul edildi"


@pytest.mark.parametrize("ticker", ["AAPL", "BRK.B", "BF-B", "brk.a", "0000320193"])
def test_sec18_legit_ticker_accepted(ticker):
    """SEC-18 yanlış pozitif kontrolü — BRK.B, BF-B ve sayısal CIK geçerli kalmalı."""
    assert not _rejected(_load_check_ticker(), ticker)


# --------------------------------------------------------------------------- SEC-11


def _find_esbuild() -> str | None:
    candidates = [REPO / "frontend" / "node_modules" / ".bin",
                  pathlib.Path("/opt/npm-tools/node_modules/.bin")]
    for folder in candidates:
        for name in ("esbuild.cmd", "esbuild"):
            exe = folder / name
            if exe.exists():
                return str(exe)
    return shutil.which("esbuild")


def _parse_hash(tmp_path: pathlib.Path, hashes: list[str]) -> list[dict]:
    node = shutil.which("node")
    esbuild = _find_esbuild()
    if not node or not esbuild:
        pytest.skip("Node.js veya esbuild yok (SEC-11 testi frontend araçlarıyla koşar)")
    bundle = tmp_path / "router.cjs"
    env = dict(os.environ)
    node_paths = [str(REPO / "frontend" / "node_modules"), "/opt/npm-tools/node_modules"]
    env["NODE_PATH"] = os.pathsep.join(p for p in node_paths if os.path.isdir(p))
    subprocess.run([esbuild, str(REPO / "frontend" / "src" / "router.ts"), "--bundle",
                    "--platform=node", "--format=cjs", "--log-level=error",
                    f"--outfile={bundle}"], check=True, env=env, timeout=60,
                   shell=esbuild.endswith(".cmd"))
    script = (
        f"const r = require({json.dumps(str(bundle))});"
        f"const out = {json.dumps(hashes)}.map(h => {{ try {{ return r.parseHash(h); }}"
        " catch (e) { return { thrown: e.name }; } });"
        "console.log(JSON.stringify(out));"
    )
    res = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True,
                         timeout=30)
    return json.loads(res.stdout.strip().splitlines()[-1])


def test_sec11_malformed_hash_does_not_crash_router(tmp_path):
    """SEC-11 (CWE-248) — Saldırganın amacı: kullanıcıya
    ``http://127.0.0.1:8000/#/company/%E0/overview`` bağlantısı gönderip arayüzü beyaz
    sayfaya düşürmek. Yük: ``#/company/%E0/overview``, ``#/%``. Hedef: ``parseHash``.
    Beklenen savunma: istisna yok, ``{name: "notFound"}``."""
    results = _parse_hash(tmp_path, ["#/company/%E0/overview", "#/%"])
    for r in results:
        assert "thrown" not in r, f"parseHash istisna fırlattı: {r}"
        assert r.get("name") == "notFound", r


def test_sec11_valid_hash_still_parsed(tmp_path):
    """SEC-11 yanlış pozitif kontrolü — geçerli karma doğru çözülmeli."""
    (route,) = _parse_hash(tmp_path, ["#/company/brk.b/overview"])
    assert route == {"name": "company", "ticker": "BRK.B", "tab": "overview"}
