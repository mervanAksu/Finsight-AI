"""Depo düzeyinde statik NFR denetimleri (Aşama 4): NFR-08, NFR-10, NFR-11, NFR-12.

Bu testler kod çalıştırmaz; depodaki dosyaları okur. Ağ yok. Kaynak: tasarım belgesi Bölüm 5
(docs/raporlar/00-Asama1-Analiz-ve-Tasarim-Planlama.md) ve docs/test-plani-asama4.md.
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
FRONTEND_SRC = REPO / "frontend" / "src"
SKIP_DIRS = {"node_modules", "dist", ".git", ".venv", "__pycache__", ".pytest_cache",
             ".sec_cache", ".verify", "ekran-goruntuleri"}
TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".md", ".yml", ".yaml", ".toml",
                 ".txt", ".cfg", ".ini", ".html", ".css", ".example", ".sh", ".ps1"}

# Gerçek anahtar biçimleri (sağlayıcıların yayımladığı önekler). Test dosyalarındaki
# "sk-ant-secret-123" gibi açıkça sahte değerler bu kalıplara uymaz.
SECRET_PATTERNS = {
    "anthropic": re.compile(r"sk-ant-(?:api|admin)\d{2}-[A-Za-z0-9_\-]{20,}"),
    "openai": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "slack_token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}"),
}


def _repo_text_files():
    for path in REPO.rglob("*"):
        if any(part in SKIP_DIRS for part in path.relative_to(REPO).parts):
            continue
        if path.is_file() and (path.suffix in TEXT_SUFFIXES or path.name in
                               {"Dockerfile", ".gitignore", ".dockerignore", ".env.example"}):
            yield path


# --------------------------------------------------------------------------- NFR-10


def test_nfr10_no_real_secret_patterns_in_repository():
    """NFR-10: depoda gerçek API anahtarı / özel anahtar biçiminde metin yok."""
    hits = []
    for path in _repo_text_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for name, pattern in SECRET_PATTERNS.items():
            for m in pattern.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                hits.append(f"{path.relative_to(REPO)}:{line} ({name})")
    assert hits == [], "Gizli bilgi şüphesi: " + ", ".join(hits)


def test_nfr10_secret_scanner_detects_real_looking_keys():
    """Tarayıcının kendisi çalışıyor mu (yanlış negatif denetimi; anahtarlar uydurmadır)."""
    fake_anthropic = "sk-ant-api03-" + "A" * 40
    fake_aws = "AKIA" + "ABCDEFGHIJKLMNOP"
    assert SECRET_PATTERNS["anthropic"].search(f'key = "{fake_anthropic}"')
    assert SECRET_PATTERNS["aws_access_key"].search(fake_aws)
    assert not SECRET_PATTERNS["anthropic"].search("sk-ant-secret-123")


def test_nfr10_env_file_ignored_and_example_has_no_keys():
    """NFR-10: .env git'e ve Docker bağlamına girmez; örnek dosyada anahtar değeri boş."""
    gitignore = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in [line.strip() for line in gitignore]
    dockerignore = (REPO / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in [line.strip() for line in dockerignore]
    example = (REPO / ".env.example").read_text(encoding="utf-8")
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        m = re.search(rf"^{key}=(.*)$", example, re.MULTILINE)
        assert m is not None, f"{key} örnek dosyada yok"
        assert m.group(1).strip() == "", f"{key} örnek dosyada dolu"


def test_nfr10_llm_keys_read_from_environment_only():
    """NFR-10: LLM anahtarları kodda sabit değil, ortam değişkeninden okunur."""
    src = (REPO / "backend" / "finsight" / "llm.py").read_text(encoding="utf-8")
    assert re.search(r"os\.(?:getenv|environ)[^\n]*ANTHROPIC_API_KEY", src)
    assert not re.search(r"ANTHROPIC_API_KEY\s*=\s*['\"][^'\"]+['\"]", src)


# --------------------------------------------------------------------------- NFR-08


def test_nfr08_single_command_compose_has_api_and_db():
    """NFR-08: `docker compose up` için api + db servisleri, gizli bilgi .env'den."""
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    assert re.search(r"^services:\s*$", compose, re.MULTILINE)
    for service in ("db", "api"):
        assert re.search(rf"^  {service}:\s*$", compose, re.MULTILINE), service
    assert "env_file: .env" in compose
    assert (REPO / "Dockerfile").is_file()
    # Portlar yalnızca yerel makineye açılır (127.0.0.1:...).
    for m in re.finditer(r'^\s*-\s*"([^"]+)"', compose, re.MULTILINE):
        if re.match(r"^\d+:\d+$", m.group(1)) or m.group(1).startswith("0.0.0.0"):
            pytest.fail(f"Port tüm arayüzlere açık: {m.group(1)}")


# --------------------------------------------------------------------------- NFR-11


def _dict_keys(source: str, name: str) -> set[str]:
    """i18n.ts içindeki `const <name> ... = { ... };` sözlüğünün anahtarları."""
    m = re.search(rf"const {name}\b[^=]*=\s*\{{(.*?)\n\}}(?: as const)?;", source, re.DOTALL)
    assert m, f"{name} sözlüğü bulunamadı"
    return set(re.findall(r'^\s*"([A-Za-z0-9_.\-]+)"\s*:', m.group(1), re.MULTILINE))


def test_nfr11_turkish_and_english_ui_dictionaries_have_same_keys():
    """NFR-11: arayüz Türkçe ve İngilizce etiketleri birlikte destekler (anahtar eşitliği)."""
    source = (FRONTEND_SRC / "i18n.ts").read_text(encoding="utf-8")
    tr, en = _dict_keys(source, "tr"), _dict_keys(source, "en")
    assert len(tr) > 50
    assert tr - en == set(), f"İngilizcesi eksik: {sorted(tr - en)[:10]}"
    assert en - tr == set(), f"Türkçesi eksik: {sorted(en - tr)[:10]}"


def test_nfr11_english_dictionary_is_actually_translated():
    """NFR-11: İngilizce değerlerin çoğu Türkçe değerden farklıdır (kopyala-yapıştır değil)."""
    source = (FRONTEND_SRC / "i18n.ts").read_text(encoding="utf-8")

    def values(name: str) -> dict[str, str]:
        m = re.search(rf"const {name}\b[^=]*=\s*\{{(.*?)\n\}}(?: as const)?;", source, re.DOTALL)
        return dict(re.findall(r'^\s*"([A-Za-z0-9_.\-]+)"\s*:\s*\n?\s*"((?:[^"\\]|\\.)*)"',
                               m.group(1), re.MULTILINE))

    tr, en = values("tr"), values("en")
    common = [k for k in tr if k in en]
    same = [k for k in common if tr[k] == en[k]]
    assert common and len(same) / len(common) < 0.2, same[:10]
    assert not re.search(r"[çğıöşüÇĞİÖŞÜ]", en.get("disclaimer.full", "x"))


# --------------------------------------------------------------------------- NFR-12


@pytest.mark.parametrize("screen", [
    "pages/company/OverviewTab.tsx", "pages/company/FinancialsTab.tsx",
    "pages/company/FilingsTab.tsx", "pages/company/RiskTab.tsx", "pages/company/AskTab.tsx",
    "pages/ComparePage.tsx",
])
def test_nfr12_every_analysis_screen_renders_disclaimer(screen):
    """NFR-12: her analiz ekranı uyarı bileşenini (<Disclaimer) gösterir."""
    src = (FRONTEND_SRC / screen).read_text(encoding="utf-8")
    assert "<Disclaimer" in src, screen


def test_nfr12_layout_footer_always_shows_disclaimer():
    """NFR-12: tüm sayfaların ortak alt bilgisinde kısa + tam uyarı metni bulunur."""
    src = (FRONTEND_SRC / "components" / "Layout.tsx").read_text(encoding="utf-8")
    assert 't("disclaimer.short")' in src and 't("disclaimer.full")' in src
    i18n = (FRONTEND_SRC / "i18n.ts").read_text(encoding="utf-8")
    assert "yatırım tavsiyesi değildir" in i18n
