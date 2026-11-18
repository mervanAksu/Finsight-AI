"""Ortam değişkenlerinden yapılandırma (NFR-01, NFR-10).

``.env`` dosyaları (python-dotenv kuruluysa) bu modül ilk içe aktarıldığında yüklenir:
önce çalışma klasöründeki ``.env``, sonra depo kökündeki ``.env``. Zaten tanımlı ortam
değişkenleri EZİLMEZ (kabukta verilen değer önceliklidir); iki dosyada da olan bir anahtar için
ilk yüklenen (çalışma klasörü) geçerlidir. ``FINSIGHT_DISABLE_DOTENV=1`` yüklemeyi kapatır
(testler bunu kullanır).
"""

import logging
import os

log = logging.getLogger(__name__)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def default_env_files() -> list[str]:
    """Yüklenecek .env adayları: çalışma klasörü, ardından depo kökü (tekrarsız)."""
    out: list[str] = []
    for path in (os.path.join(os.getcwd(), ".env"), os.path.join(_REPO_ROOT, ".env")):
        real = os.path.realpath(path)
        if real not in out:
            out.append(real)
    return out


def load_env_files(paths: list[str] | None = None) -> list[str]:
    """Var olan .env dosyalarını yükler (mevcut ortam değişkenlerini ezmeden).

    python-dotenv kurulu değilse veya ``FINSIGHT_DISABLE_DOTENV`` doluysa hiçbir şey yapmaz.
    Yüklenen dosya yollarını döndürür.
    """
    if os.getenv("FINSIGHT_DISABLE_DOTENV", "").strip().lower() in ("1", "true", "yes"):
        return []
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv isteğe bağlı: yoksa yalnızca ortam değişkenleri
        return []
    loaded: list[str] = []
    for path in paths if paths is not None else default_env_files():
        if os.path.isfile(path):
            load_dotenv(path, override=False)
            loaded.append(path)
            log.debug(".env yüklendi: %s", path)
    return loaded


load_env_files()


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
    raw = os.getenv("SEC_MAX_RPS", "").strip() or "8"  # .env'de boş bırakılmış olabilir
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError("SEC_MAX_RPS bir sayı olmalı (ör. 8).") from None
    if not 0 < value < 10:
        raise ConfigError("SEC_MAX_RPS 0 ile 10 arasında (10 hariç) olmalı.")
    return value


def get_database_url() -> str:
    return os.getenv("DATABASE_URL", "").strip() or "sqlite:///finsight.db"


def get_cache_dir() -> str:
    return os.getenv("SEC_CACHE_DIR", "").strip() or ".sec_cache"


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
    candidates.append(os.path.join(_REPO_ROOT, "frontend", "dist"))
    for path in candidates:
        if os.path.isfile(os.path.join(path, "index.html")):
            return path
    return None


def get_documents_dir() -> str:
    """İndirilen rapor belgelerinin (10-K/10-Q HTML) saklandığı klasör."""
    return os.getenv("FINSIGHT_DOCUMENTS_DIR") or os.path.join(get_cache_dir(), "documents")
