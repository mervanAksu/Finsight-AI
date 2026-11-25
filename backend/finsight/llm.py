"""LLM istemcileri: Anthropic Messages API ve OpenAI uyumlu Chat Completions (FR-10).

Anahtarlar yalnızca ortam değişkenlerinden okunur (NFR-10) ve hiçbir hata mesajında,
günlükte veya `repr` çıktısında gösterilmez. Hata mesajları HTTP gövdesini içermez (sağlayıcı
gövdesi maskeli anahtar parçası yansıtabilir); yalnızca durum kodu ve varsa `error.type`.
Testlerde `transport=httpx.MockTransport(...)` verilerek ağa çıkılmaz.

Zaman aşımı varsayılan 20 sn (`FINSIGHT_LLM_TIMEOUT`); okuma zaman aşımı yeniden denenmez
(kullanıcı en kötü durumda ~20 sn bekler). 429/5xx'te `Retry-After` (saniye, en fazla 10)
varsa ona uyulur, yoksa 1, 2 sn üstel bekleme.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from typing import Protocol

import httpx

log = logging.getLogger(__name__)

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-5-5"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_TIMEOUT = 20.0
TIMEOUT_ENV = "FINSIGHT_LLM_TIMEOUT"
MAX_RETRIES = 2
MAX_RETRY_AFTER = 10.0
RETRY_STATUSES = {429, 500, 502, 503, 504, 529}


class LLMError(RuntimeError):
    """LLM'den kullanılabilir yanıt alınamadı. Mesaj asla API anahtarı içermez."""


class LLMClient(Protocol):
    provider: str
    model: str

    def complete(self, system: str, user: str, max_tokens: int = 800) -> str: ...


def _redact(text: str, secret: str | None) -> str:
    if secret:
        text = text.replace(secret, "***")
    return text


def _default_timeout() -> float:
    raw = os.getenv(TIMEOUT_ENV, "").strip()
    if raw:
        try:
            value = float(raw)
            if value > 0:
                return value
        except ValueError:
            pass
        log.warning("Geçersiz %s=%r; varsayılan %.0f sn kullanılıyor.", TIMEOUT_ENV, raw,
                    DEFAULT_TIMEOUT)
    return DEFAULT_TIMEOUT


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("retry-after")
    if raw is None:
        return None
    try:
        value = float(raw.strip())
    except ValueError:  # HTTP tarih biçimi desteklenmez → üstel bekleme
        return None
    return min(max(value, 0.0), MAX_RETRY_AFTER)


def _error_type(resp: httpx.Response) -> str | None:
    try:
        data = resp.json()
    except ValueError:
        return None
    err = data.get("error") if isinstance(data, dict) else None
    etype = err.get("type") if isinstance(err, dict) else None
    if isinstance(etype, str) and etype.replace("_", "").isalnum() and len(etype) <= 64:
        return etype
    return None


class _HttpLLMClient:
    provider = "base"

    def __init__(self, api_key: str | None, model: str, timeout: float | None = None,
                 max_retries: int = MAX_RETRIES, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._api_key = api_key or ""
        self.model = model
        self.timeout = float(timeout) if timeout is not None else _default_timeout()
        self.max_retries = max_retries
        self._transport = transport
        self._sleep = sleep

    def __repr__(self) -> str:  # anahtar repr'de görünmesin
        return f"{type(self).__name__}(provider={self.provider!r}, model={self.model!r})"

    def _post(self, url: str, headers: dict[str, str], body: dict) -> dict:
        last_error = "bilinmeyen hata"
        with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
            for attempt in range(self.max_retries + 1):
                wait: float | None = None
                try:
                    resp = client.post(url, headers=headers, json=body)
                except httpx.ReadTimeout:
                    # Yanıt beklenirken zaman aşımı: yeniden denemek bekleme süresini katlar.
                    last_error = f"zaman aşımı ({self.timeout:g} sn)"
                    break
                except httpx.TransportError as exc:  # bağlantı hatası, bağlantı zaman aşımı
                    last_error = f"bağlantı hatası ({type(exc).__name__})"
                else:
                    if resp.status_code == 200:
                        try:
                            data = resp.json()
                        except ValueError:
                            raise LLMError(
                                f"{self.provider} yanıtı JSON olarak çözülemedi"
                            ) from None
                        if not isinstance(data, dict):
                            raise LLMError(f"{self.provider} yanıtı beklenmeyen biçimde")
                        return data
                    # Gövde mesaja konmaz (D7): yalnızca durum kodu ve varsa error.type.
                    etype = _error_type(resp)
                    last_error = f"HTTP {resp.status_code}" + (f" ({etype})" if etype else "")
                    if resp.status_code not in RETRY_STATUSES:
                        break
                    wait = _retry_after(resp)
                if attempt < self.max_retries:
                    self._sleep(wait if wait is not None else 2.0 ** attempt)
        raise LLMError(_redact(f"{self.provider} LLM isteği başarısız: {last_error}",
                               self._api_key))


class AnthropicClient(_HttpLLMClient):
    """Anthropic Messages API (`POST /v1/messages`)."""

    provider = "anthropic"

    def __init__(self, api_key: str, model: str = DEFAULT_ANTHROPIC_MODEL,
                 url: str = ANTHROPIC_URL, **kwargs) -> None:
        super().__init__(api_key, model, **kwargs)
        self.url = url

    def complete(self, system: str, user: str, max_tokens: int = 800) -> str:
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        data = self._post(self.url, headers, body)
        try:
            content = data.get("content") or []
            if not isinstance(content, list):
                raise TypeError("content liste değil")
            parts = []
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    text = c.get("text")
                    if not isinstance(text, str):
                        raise TypeError("text metin değil")
                    parts.append(text)
            return "".join(parts).strip()
        except (AttributeError, TypeError, ValueError):
            raise LLMError("anthropic yanıtı beklenmeyen biçimde (content)") from None


class OpenAICompatibleClient(_HttpLLMClient):
    """OpenAI uyumlu `POST {base_url}/chat/completions` (OpenAI, yerel sunucular vb.)."""

    provider = "openai"

    def __init__(self, api_key: str | None, model: str = DEFAULT_OPENAI_MODEL,
                 base_url: str = DEFAULT_OPENAI_BASE_URL, **kwargs) -> None:
        super().__init__(api_key, model, **kwargs)
        self.base_url = base_url.rstrip("/")

    def complete(self, system: str, user: str, max_tokens: int = 800) -> str:
        headers = {"content-type": "application/json"}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        data = self._post(f"{self.base_url}/chat/completions", headers, body)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, AttributeError):
            raise LLMError("openai yanıtında choices[0].message.content yok") from None
        if content is None:
            return ""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):  # bazı uyumlu sunucular parça listesi döndürür
            texts = [p.get("text") for p in content if isinstance(p, dict)
                     and p.get("type") in ("text", "output_text")]
            if all(isinstance(t, str) for t in texts):
                return "".join(t for t in texts if isinstance(t, str)).strip()
        raise LLMError("openai yanıtı beklenmeyen biçimde (content)")


_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def base_url_is_safe(base_url: str, *, has_key: bool) -> bool:
    """SEC-17 (CWE-319): ``OPENAI_BASE_URL`` https olmalı; şifresiz ``http://`` yalnızca bu
    bilgisayardaki (127.0.0.1 / localhost) sunucular için kabul edilir. Anahtarsız uzak http
    sunucusu uyarıyla kabul edilir (gönderilecek gizli bilgi yok). Başka şema reddedilir."""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(base_url)
        host = (parts.hostname or "").lower()
    except ValueError:
        parts, host = None, ""
    if parts is None or parts.scheme not in ("http", "https") or not host:
        log.warning("OPENAI_BASE_URL geçersiz (http(s)://ana-makine/... olmalı); LLM kapalı.")
        return False
    if parts.scheme == "https" or host in _LOOPBACK_HOSTS:
        return True
    if has_key:
        log.warning("OPENAI_BASE_URL şifresiz http:// ile uzak bir adres; API anahtarı ağda açık "
                    "metin gider. LLM kapalı. https:// kullanın (http yalnızca 127.0.0.1 / "
                    "localhost için).")
        return False
    log.warning("OPENAI_BASE_URL şifresiz http:// ile uzak bir adres (anahtarsız); sorular ağda "
                "açık metin gider.")
    return True


def get_llm_client(transport: httpx.BaseTransport | None = None) -> LLMClient | None:
    """Ortam değişkenlerinden LLM istemcisi kurar; yapılandırma yoksa None.

    - `FINSIGHT_LLM_PROVIDER`: `anthropic | openai | none`. Boşsa `ANTHROPIC_API_KEY` varsa
      anthropic, yoksa `OPENAI_API_KEY` varsa openai, yoksa None.
    - `FINSIGHT_LLM_MODEL`: varsayılan anthropic için `claude-haiku-5-5`, openai için
      `gpt-4o-mini`.
    - `OPENAI_BASE_URL`: varsayılan `https://api.openai.com/v1` (anahtarsız yerel sunucu
      için özel bir adres verilebilir).
    - `FINSIGHT_LLM_TIMEOUT`: istek zaman aşımı (sn), varsayılan 20.
    """
    provider = os.getenv("FINSIGHT_LLM_PROVIDER", "").strip().lower()
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "").strip() or DEFAULT_OPENAI_BASE_URL
    model = os.getenv("FINSIGHT_LLM_MODEL", "").strip()

    if provider == "none":
        return None
    if not provider:
        provider = "anthropic" if anthropic_key else "openai" if openai_key else ""
        if not provider:
            return None
    if provider == "anthropic":
        if not anthropic_key:
            log.warning("FINSIGHT_LLM_PROVIDER=anthropic ama ANTHROPIC_API_KEY yok; LLM kapalı.")
            return None
        return AnthropicClient(anthropic_key, model or DEFAULT_ANTHROPIC_MODEL,
                               transport=transport)
    if provider == "openai":
        if not base_url_is_safe(base_url, has_key=bool(openai_key)):
            return None
        if not openai_key and base_url == DEFAULT_OPENAI_BASE_URL:
            log.warning("FINSIGHT_LLM_PROVIDER=openai ama OPENAI_API_KEY yok; LLM kapalı.")
            return None
        return OpenAICompatibleClient(openai_key or None, model or DEFAULT_OPENAI_MODEL,
                                      base_url=base_url, transport=transport)
    log.warning("Bilinmeyen FINSIGHT_LLM_PROVIDER=%r; LLM kapalı.", provider)
    return None
