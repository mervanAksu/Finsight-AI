"""Ortam değişkenlerinden yapılandırma (NFR-01, NFR-10).

``.env`` dosyaları (python-dotenv kuruluysa) bu modül ilk içe aktarıldığında yüklenir:
önce çalışma klasöründeki ``.env``, sonra depo kökündeki ``.env``. Zaten tanımlı ortam
değişkenleri EZİLMEZ (kabukta verilen değer önceliklidir); iki dosyada da olan bir anahtar için
ilk yüklenen (çalışma klasörü) geçerlidir. ``FINSIGHT_DISABLE_DOTENV=1`` yüklemeyi kapatır
(testler bunu kullanır).
"""

import logging
import os
import re

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


# SEC-17: köken yalnızca "şema://ana-makine[:port]" olabilir; "*", "null", yol veya şemasız
# değer reddedilir (yanlış yapılandırma her sitenin API yanıtlarını okumasına izin vermesin).
_ORIGIN_RE = re.compile(r"https?://(?:[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*|\[[0-9A-Fa-f:.]+\])"
                        r"(?::\d{1,5})?")


def get_cors_origins() -> list[str]:
    """Geliştirme arayüzü adresleri + FINSIGHT_CORS_ORIGINS (virgülle ayrılmış) ekleri.

    Geçersiz değerler (``*``, ``null``, şemasız, yollu) uyarıyla atlanır (SEC-17)."""
    origins: list[str] = list(DEFAULT_CORS_ORIGINS)
    for item in os.getenv("FINSIGHT_CORS_ORIGINS", "").split(","):
        item = item.strip().rstrip("/")
        if not item:
            continue
        if not _ORIGIN_RE.fullmatch(item):
            log.warning("FINSIGHT_CORS_ORIGINS içindeki geçersiz köken yok sayıldı: %r "
                        "(yalnızca http(s)://ana-makine[:port]; '*' ve 'null' kabul edilmez).",
                        item[:80])
            continue
        if item not in origins:
            origins.append(item)
    return origins


# --- Güvenlik sertleştirmesi (docs/guvenlik/duzeltmeler.md) --------------------------------

# SEC-03: DNS rebinding'e karşı kabul edilen Host başlıkları (port hariç). Ek ad gerekirse
# (ör. Docker'da başka bir ad) FINSIGHT_ALLOWED_HOSTS ile virgülle eklenir.
DEFAULT_ALLOWED_HOSTS = ("127.0.0.1", "localhost", "::1")
_HOST_RE = re.compile(r"[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*|[0-9A-Fa-f:.]+")


def get_allowed_hosts() -> list[str]:
    hosts: list[str] = list(DEFAULT_ALLOWED_HOSTS)
    for item in os.getenv("FINSIGHT_ALLOWED_HOSTS", "").split(","):
        item = item.strip().strip("[]").lower().rstrip(".")
        if not item:
            continue
        if item == "*" or not _HOST_RE.fullmatch(item):
            log.warning("FINSIGHT_ALLOWED_HOSTS içindeki geçersiz değer yok sayıldı: %r",
                        item[:80])
            continue
        if item not in hosts:
            hosts.append(item)
    return hosts


def _int_env(name: str, default: int, low: int, high: int) -> int:
    value = _number_env(name)
    if value is None:
        return default
    if value != int(value) or not low <= value <= high:
        raise ConfigError(f"{name} {low} ile {high} arasında bir tam sayı olmalı.")
    return int(value)


def get_max_concurrent_jobs() -> int:
    """SEC-02: aynı anda ÇALIŞAN ingest işi sayısı (``FINSIGHT_MAX_CONCURRENT_JOBS``,
    varsayılan 2, 1–8)."""
    return _int_env("FINSIGHT_MAX_CONCURRENT_JOBS", 2, 1, 8)


def get_max_queued_jobs() -> int:
    """SEC-02: çalışanlara ek olarak kuyrukta bekleyebilecek iş sayısı
    (``FINSIGHT_MAX_QUEUED_JOBS``, varsayılan 8, 0–100). Fazlası HTTP 429."""
    return _int_env("FINSIGHT_MAX_QUEUED_JOBS", 8, 0, 100)


def get_ask_per_minute() -> int:
    """SEC-03 ek önlem: dakikada en fazla ``/ask`` sayısı (``FINSIGHT_ASK_PER_MINUTE``,
    varsayılan 20, 1–600)."""
    return _int_env("FINSIGHT_ASK_PER_MINUTE", 20, 1, 600)


def get_llm_daily_limit() -> int:
    """SEC-03 ek önlem: LLM ile cevaplanan günlük en fazla soru (``FINSIGHT_LLM_DAILY_LIMIT``,
    varsayılan 300; 0 = sınırsız). Sınır dolunca cevap LLM'siz (çıkarımsal) üretilir."""
    return _int_env("FINSIGHT_LLM_DAILY_LIMIT", 300, 0, 1_000_000)


def qa_log_enabled() -> bool:
    """SEC-19: ``FINSIGHT_QA_LOG=0`` (veya false/no/off) soruların veritabanına yazılmasını
    kapatır. Varsayılan açık (geriye uyumlu)."""
    raw = os.getenv("FINSIGHT_QA_LOG", "").strip().lower()
    return raw not in ("0", "false", "no", "off", "hayir", "hayır")


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


def analyze_quarterly_enabled() -> bool:
    """``FINSIGHT_ANALYZE_10Q`` (varsayılan 1). 0/false/no/off → "hızlı mod": en son 10-Q
    başvuru listesinde görünür ama metni indirilip duygu/gömme analizi yapılmaz."""
    raw = os.getenv("FINSIGHT_ANALYZE_10Q", "").strip().lower()
    return raw not in ("0", "false", "no", "off", "hayir", "hayır")


# --- Aşama 4: SEC önbellek süresi ve otomatik yenileme (docs/asama4-sozlesme.md §4–5) -------
# Alt sınırlar güvenlik denetimi SEC-16 madde 5'ten: çok kısa TTL / aralık her çağrıyı SEC'e
# gönderir (adil erişim politikası).

DEFAULT_SEC_JSON_TTL = 3600
MIN_SEC_JSON_TTL = 300
MIN_AUTO_REFRESH_MINUTES = 30


def _number_env(name: str) -> float | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None  # .env'de boş bırakılmış olabilir → varsayılan
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} bir sayı olmalı.") from None
    if value != value or value in (float("inf"), float("-inf")):  # NaN / sonsuz
        raise ConfigError(f"{name} sonlu bir sayı olmalı.")
    return value


def get_sec_json_ttl() -> int:
    """submissions / companyfacts önbellek süresi (sn): ``FINSIGHT_SEC_JSON_TTL``, varsayılan
    3600, en az 300 (aksi ConfigError)."""
    value = _number_env("FINSIGHT_SEC_JSON_TTL")
    if value is None:
        return DEFAULT_SEC_JSON_TTL
    if value < MIN_SEC_JSON_TTL:
        raise ConfigError(f"FINSIGHT_SEC_JSON_TTL en az {MIN_SEC_JSON_TTL} saniye olmalı "
                          f"(varsayılan {DEFAULT_SEC_JSON_TTL}).")
    return int(value)


def get_auto_refresh_minutes() -> int:
    """``FINSIGHT_AUTO_REFRESH_MINUTES``: API süreci içinde eskimiş şirketleri arka planda
    güncelleme aralığı (dk). 0 / boş = kapalı (varsayılan); açıksa en az 30 (aksi ConfigError)."""
    value = _number_env("FINSIGHT_AUTO_REFRESH_MINUTES")
    if value is None or value == 0:
        return 0
    if value < MIN_AUTO_REFRESH_MINUTES:
        raise ConfigError("FINSIGHT_AUTO_REFRESH_MINUTES 0 (kapalı) veya en az "
                          f"{MIN_AUTO_REFRESH_MINUTES} dakika olmalı.")
    return int(value)
