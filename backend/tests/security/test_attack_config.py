"""Saldırı testleri — yapılandırma ve dağıtım (SEC-13, SEC-14, SEC-15, SEC-16/5, SEC-17).

Saf Python + dosya analizi: bulutta koşar (``git`` yoksa SEC-13 atlanır). Kurulu Starlette
sürümü testi yalnızca Starlette kuruluysa (kullanıcının bilgisayarında) koşar.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

import pytest

from finsight import config
from finsight.config import ConfigError

from .attack_support import xfail_until

REPO = pathlib.Path(__file__).resolve().parents[3]

# --------------------------------------------------------------------------- SEC-17 CORS


@pytest.mark.parametrize("value", ["*", "null", "http://127.0.0.1:5173,*", "kotu.example"])
def test_sec17_cors_wildcard_or_malformed_origin_rejected(monkeypatch, value):
    """SEC-17 (CWE-16) — Saldırganın amacı: yanlış yapılandırma (``FINSIGHT_CORS_ORIGINS=*``)
    ile her sitenin API yanıtlarını okuyabilmesi. Yük: ortam değişkeni ``value``. Beklenen
    savunma: ``*``, ``null`` ve şemasız değer listeye girmez (veya ``ConfigError``)."""
    monkeypatch.setenv("FINSIGHT_CORS_ORIGINS", value)
    try:
        origins = config.get_cors_origins()
    except ConfigError:
        return
    bad = [o for o in origins if not re.fullmatch(r"https?://[A-Za-z0-9.\-\[\]:]+", o)]
    assert not bad, f"geçersiz köken kabul edildi: {bad}"


def test_sec17_cors_explicit_origin_still_accepted(monkeypatch):
    """SEC-17 yanlış pozitif kontrolü — açık bir köken eklenebilmeli."""
    monkeypatch.setenv("FINSIGHT_CORS_ORIGINS", "http://127.0.0.1:3000/")
    assert "http://127.0.0.1:3000" in config.get_cors_origins()


# --------------------------------------------------------------------------- SEC-17 LLM URL


def _llm_env(monkeypatch, base_url: str) -> None:
    for name in ("FINSIGHT_LLM_PROVIDER", "ANTHROPIC_API_KEY", "FINSIGHT_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-kirmizi-takim-sahte-anahtar")
    monkeypatch.setenv("OPENAI_BASE_URL", base_url)


def test_sec17_api_key_not_sent_over_plain_http(monkeypatch):
    """SEC-17 (CWE-319) — Saldırganın amacı: ``OPENAI_BASE_URL=http://ornek.com/v1`` ile API
    anahtarının ağda şifresiz gitmesi (ortak Wi-Fi'de dinlenebilir). Ağa çıkılmaz: yalnızca
    istemcinin KURULUP kurulmadığına bakılır. Beklenen savunma: ``None`` veya ``ConfigError``."""
    from finsight.llm import get_llm_client

    _llm_env(monkeypatch, "http://ornek.com/v1")
    try:
        client = get_llm_client()
    except ConfigError:
        return
    assert client is None, "uzak http:// adrese anahtarlı istemci kuruldu"


@pytest.mark.parametrize("url", ["http://127.0.0.1:11434/v1", "http://localhost:8080/v1",
                                 "https://api.openai.com/v1"])
def test_sec17_local_http_and_https_llm_allowed(monkeypatch, url):
    """SEC-17 yanlış pozitif kontrolü — yerel (Ollama vb.) http ve uzak https kalmalı."""
    from finsight.llm import get_llm_client

    _llm_env(monkeypatch, url)
    assert get_llm_client() is not None


# --------------------------------------------------------------------------- SEC-16 (5)


@pytest.mark.parametrize("value", ["0", "5"])
def test_sec16_json_ttl_too_low_rejected(monkeypatch, value):
    """SEC-16/5 — Saldırganın amacı (A4 yanlış yapılandırma): TTL=0 ile her ``/overview`` /
    tazelik çağrısının SEC'e gitmesi → hız sınırı / IP engeli. Beklenen savunma:
    ``ConfigError``. Durum: HEAD'de açıktı (0 kabul ediliyordu); Aşama 4 çalışma ağacında
    kapatıldı (≥ 300 sn)."""
    monkeypatch.setenv("FINSIGHT_SEC_JSON_TTL", value)
    with pytest.raises(ConfigError):
        config.get_sec_json_ttl()
        raise AssertionError(f"FINSIGHT_SEC_JSON_TTL={value} kabul edildi")


@pytest.mark.parametrize("value", ["1", "5"])
def test_sec16_auto_refresh_too_frequent_rejected(monkeypatch, value):
    """SEC-16/5 — Yük: ``FINSIGHT_AUTO_REFRESH_MINUTES=1`` (her dakika tüm şirketler için SEC
    isteği). Beklenen savunma: ``ConfigError`` (0 = kapalı geçerli kalır).
    Durum: HEAD'de açıktı; Aşama 4 çalışma ağacında kapatıldı (≥ 30 dk)."""
    monkeypatch.setenv("FINSIGHT_AUTO_REFRESH_MINUTES", value)
    with pytest.raises(ConfigError):
        config.get_auto_refresh_minutes()
        raise AssertionError(f"FINSIGHT_AUTO_REFRESH_MINUTES={value} kabul edildi")


@pytest.mark.parametrize("name,value", [("FINSIGHT_SEC_JSON_TTL", "-1"),
                                        ("FINSIGHT_SEC_JSON_TTL", "abc"),
                                        ("FINSIGHT_AUTO_REFRESH_MINUTES", "-5")])
def test_sec16_negative_or_garbage_env_rejected(monkeypatch, name, value):
    """SEC-16/5 kontrol — negatif ve sayı olmayan değerler bugün de ``ConfigError``."""
    monkeypatch.setenv(name, value)
    fn = (config.get_sec_json_ttl if name == "FINSIGHT_SEC_JSON_TTL"
          else config.get_auto_refresh_minutes)
    with pytest.raises(ConfigError):
        fn()


def test_sec16_auto_refresh_off_and_ttl_default_ok(monkeypatch):
    """SEC-16/5 yanlış pozitif kontrolü — 0 (kapalı) yenileme ve varsayılan TTL geçerli."""
    monkeypatch.setenv("FINSIGHT_AUTO_REFRESH_MINUTES", "0")
    monkeypatch.delenv("FINSIGHT_SEC_JSON_TTL", raising=False)
    assert config.get_auto_refresh_minutes() == 0
    assert config.get_sec_json_ttl() >= 300


# --------------------------------------------------------------------------- NFR-02 kontrol


@pytest.mark.parametrize("value", ["10", "50", "0", "-1", "nan", "inf", "1e9", "abc"])
def test_nfr02_sec_max_rps_cannot_exceed_sec_limit(monkeypatch, value):
    """NFR-02 kontrol saldırısı — ``SEC_MAX_RPS`` ile SEC'in 10 istek/sn sınırını aşmak
    (``nan``/``inf`` hileleri dahil). Bugün de ``ConfigError`` ile engelleniyor."""
    monkeypatch.setenv("SEC_MAX_RPS", value)
    with pytest.raises(ConfigError):
        config.get_max_rps()


def test_nfr01_sec_user_agent_without_email_rejected(monkeypatch):
    """NFR-01 kontrol — e-postasız/boş User-Agent ile SEC'e kimliksiz istek gönderilemez."""
    monkeypatch.setenv("SEC_USER_AGENT", "FinSight")
    with pytest.raises(ConfigError):
        config.get_user_agent()


# --------------------------------------------------------------------------- SEC-13


def _git_ignored(path: str) -> bool:
    git = shutil.which("git")
    if not git or not (REPO / ".git").exists():
        pytest.skip("git veya depo yok")
    res = subprocess.run([git, "-C", str(REPO), "check-ignore", "-q", "--no-index", path],
                         capture_output=True, timeout=30)
    if res.returncode not in (0, 1):
        pytest.skip(f"git check-ignore çalışmadı: {res.stderr!r}")
    return res.returncode == 0


@pytest.mark.parametrize("path", ["backend/finsight.db-wal", "finsight.db-shm",
                                  "finsight.db-journal", ".env.local", ".env.production",
                                  ".env.bak"])
def test_sec13_sensitive_files_are_git_ignored(path):
    """SEC-13 (CWE-538) — Saldırganın amacı (A4): ``git add .`` ile kullanıcının sorularını
    içeren WAL dosyasının veya anahtarlı ``.env`` kopyasının GitHub'a gitmesi.
    Yük: dosya yolu. Beklenen savunma: ``git check-ignore`` eşleşir."""
    assert _git_ignored(path), f"{path} git tarafından yok sayılmıyor"


@pytest.mark.parametrize("path,ignored", [(".env", True), ("backend/finsight.db", True),
                                          (".env.example", False)])
def test_sec13_existing_ignore_rules(path, ignored):
    """SEC-13 kontrol — ``.env`` ve ``*.db`` bugün de yok sayılıyor; ``.env.example`` depoda
    kalmalı (düzeltme ``!.env.example`` istisnasını unutmamalı)."""
    assert _git_ignored(path) is ignored


# --------------------------------------------------------------------------- SEC-14


def _read(rel: str) -> str:
    path = REPO / rel
    if not path.exists():
        pytest.skip(f"{rel} yok")
    return path.read_text(encoding="utf-8")


def test_sec14_documented_docker_run_binds_loopback_only():
    """SEC-14 (CWE-1327) — Saldırgan profili A3 (aynı Wi-Fi'daki biri). Belgelenen
    ``docker run -p 8000:8000`` API'yi tüm ağ arayüzlerine açar → kimlik doğrulamasız ``/ask``
    ile LLM anahtarı harcanır. Beklenen savunma: belgelerdeki her ``-p`` ``127.0.0.1:`` ile."""
    text = _read("Dockerfile")
    publishes = re.findall(r"-p\s+(\S+)", text)
    assert publishes, "Dockerfile'da docker run örneği bulunamadı"
    assert all(p.startswith("127.0.0.1:") for p in publishes), publishes


def test_sec14_container_runs_as_non_root():
    """SEC-14 (CWE-250) — Bir bağımlılık açığıyla kod çalıştıran saldırgan kapsayıcıda root
    olmamalı. Beklenen savunma: son aşamada ``USER`` (root/0 olmayan) yönergesi."""
    users = re.findall(r"^\s*USER\s+(\S+)", _read("Dockerfile"), re.M)
    assert users and users[-1] not in ("root", "0", "0:0"), users


def test_sec14_compose_has_no_default_db_password():
    """SEC-14 (CWE-1393) — Bilinen varsayılan parola (``finsight_dev``). Beklenen savunma:
    ``${POSTGRES_PASSWORD:?...}`` (tanımsızsa compose hata verir)."""
    text = _read("docker-compose.yml")
    assert ":-" not in "".join(re.findall(r"\$\{POSTGRES_PASSWORD[^}]*\}", text)), \
        "POSTGRES_PASSWORD için varsayılan değer var"


def test_sec14_compose_binds_loopback_and_hides_db():
    """SEC-14 kontrol — compose API'yi yalnızca 127.0.0.1'e yayınlıyor, Postgres portu yok."""
    text = _read("docker-compose.yml")
    published = re.findall(r'^\s*-\s*"?([0-9.:]+:\d+)"?', text, re.M)
    assert published and all(p.startswith("127.0.0.1:") for p in published), published
    assert "5432:5432" not in re.sub(r"#.*", "", text)


# --------------------------------------------------------------------------- SEC-15


def test_sec15_requirements_pin_patched_starlette():
    """SEC-15 (CWE-1104) — Eski sanal ortamda açıklı Starlette (0.39.0–0.49.0, Range başlığı
    O(n²) DoS, CVE-2025-62727) kalabilir. Beklenen savunma: ``starlette>=0.49.1`` (veya kilit
    dosyasında ≥ 0.49.1)."""
    text = _read("backend/requirements.txt")
    lock = REPO / "backend" / "requirements.lock"
    if lock.exists():
        text += "\n" + lock.read_text(encoding="utf-8")
    m = re.search(r"^starlette\s*(?:>=|==)\s*([0-9.]+)", text, re.M | re.I)
    assert m, "starlette sürüm alt sınırı yok"
    assert tuple(int(x) for x in m.group(1).split(".")[:3]) >= (0, 49, 1), m.group(0)


@xfail_until("SEC-15", "package-lock.json yok / npm ci kullanılmıyor")
def test_sec15_frontend_lockfile_and_npm_ci():
    """SEC-15 (A08:2021) — Kilit dosyası olmadan her derleme farklı (ele geçirilmiş olabilecek)
    paket sürümleri çeker. Beklenen savunma: ``frontend/package-lock.json`` var ve Dockerfile
    ``npm ci`` kullanıyor."""
    assert (REPO / "frontend" / "package-lock.json").exists(), "package-lock.json yok"
    assert re.search(r"\bnpm\s+ci\b", _read("Dockerfile")), "Dockerfile npm ci kullanmıyor"


def test_sec15_installed_starlette_not_vulnerable():
    """SEC-15 — KULLANICININ BİLGİSAYARINDA koşar (Starlette yoksa atlanır). Kurulu Starlette
    CVE-2025-62727 aralığında (0.39.0–0.49.0) olmamalı. Başarısızsa: ``pip install -U
    "starlette>=0.49.1"``."""
    starlette = pytest.importorskip("starlette")
    version = tuple(int(x) for x in re.findall(r"\d+", starlette.__version__)[:3])
    assert not ((0, 39, 0) <= version <= (0, 49, 0)), f"açıklı Starlette: {starlette.__version__}"
