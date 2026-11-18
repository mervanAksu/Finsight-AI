"""Ortam değişkenlerinden yapılandırma (NFR-01, NFR-10)."""

import os


class ConfigError(RuntimeError):
    """Eksik veya hatalı yapılandırma."""


def get_user_agent() -> str:
    """SEC'in istediği tanımlayıcı User-Agent'ı döndürür.

    Biçim: "<Uygulama adı> <Ad Soyad> <e-posta>". Boş ya da e-postasız
    bir değerle uygulama hiç başlamaz; böylece SEC'e kimliksiz istek gitmez.
    """
    ua = os.getenv("SEC_USER_AGENT", "").strip()
    if not ua or "@" not in ua:
        raise ConfigError(
            "SEC_USER_AGENT tanımlı değil veya e-posta içermiyor. "
            "Örnek: SEC_USER_AGENT='FinSight-AI Ad Soyad eposta@ornek.com'"
        )
    return ua


def get_max_rps() -> float:
    """Saniyedeki en fazla istek. SEC sınırı 10; varsayılan 8 (NFR-02)."""
    value = float(os.getenv("SEC_MAX_RPS", "8"))
    if not 0 < value < 10:
        raise ConfigError("SEC_MAX_RPS 0 ile 10 arasında (10 hariç) olmalı.")
    return value


def get_database_url() -> str:
    return os.getenv("DATABASE_URL", "sqlite:///finsight.db")


def get_cache_dir() -> str:
    return os.getenv("SEC_CACHE_DIR", ".sec_cache")


# --- Aşama 3 eklemeleri: API, arayüz ve belge depolama -----------------------------------

DEFAULT_CORS_ORIGINS = ("http://127.0.0.1:5173", "http://localhost:5173")


def get_cors_origins() -> list[str]:
    """Geliştirme arayüzü adresleri + FINSIGHT_CORS_ORIGINS (virgülle ayrılmış) ekleri."""
    origins: list[str] = list(DEFAULT_CORS_ORIGINS)
    for item in os.getenv("FINSIGHT_CORS_ORIGINS", "").split(","):
        item = item.strip().rstrip("/")
        if item and item not in origins:
            origins.append(item)
    return origins


def get_frontend_dist() -> str | None:
    """Derlenmiş arayüz klasörü: FINSIGHT_FRONTEND_DIST veya depo kökündeki frontend/dist.

    Klasör yoksa (veya index.html içermiyorsa) None; API arayüzsüz çalışır.
    """
    candidates = []
    env = os.getenv("FINSIGHT_FRONTEND_DIST", "").strip()
    if env:
        candidates.append(env)
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    candidates.append(os.path.join(repo_root, "frontend", "dist"))
    for path in candidates:
        if os.path.isfile(os.path.join(path, "index.html")):
            return path
    return None


def get_documents_dir() -> str:
    """İndirilen rapor belgelerinin (10-K/10-Q HTML) saklandığı klasör."""
    return os.getenv("FINSIGHT_DOCUMENTS_DIR") or os.path.join(get_cache_dir(), "documents")
