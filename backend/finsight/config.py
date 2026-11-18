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
