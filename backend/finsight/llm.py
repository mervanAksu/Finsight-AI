"""LLM istemcileri: Anthropic Messages API ve OpenAI uyumlu Chat Completions (FR-10).

Anahtarlar yalnızca ortam değişkenlerinden okunur (NFR-10) ve hiçbir hata mesajında,
günlükte veya `repr` çıktısında gösterilmez. Testlerde `transport=httpx.MockTransport(...)`
verilerek ağa çıkılmaz.
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
DEFAULT_TIMEOUT = 60.0
MAX_RETRIES = 2
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


class _HttpLLMClient:
    provider = "base"

    def __init__(self, api_key: str | None, model: str, timeout: float = DEFAULT_TIMEOUT,
                 max_retries: int = MAX_RETRIES, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._api_key = api_key or ""
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self._transport = transport
        self._sleep = sleep

    def __repr__(self) -> str:  # anahtar repr'de görünmesin
        return f"{type(self).__name__}(provider={self.provider!r}, model={self.model!r})"

    def _post(self, url: str, headers: dict[str, str], body: dict) -> dict:
        last_error = "bilinmeyen hata"
        with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
            for attempt in range(self.max_retries + 1):
                try:
                    resp = client.post(url, headers=headers, json=body)
                except httpx.TransportError as exc:  # zaman aşımı, bağlantı hatası
                    last_error = f"bağlantı hatası ({type(exc).__name__})"
                else:
                    if resp.status_code == 200:
                        try:
                            return resp.json()
                        except ValueError:
                            raise LLMError(
                                f"{self.provider} yanıtı JSON olarak çözülemedi"
                            ) from None
                    detail = resp.text[:200]
                    last_error = f"HTTP {resp.status_code}: {detail}"
                    if resp.status_code not in RETRY_STATUSES:
                        break
                if attempt < self.max_retries:
                    self._sleep(2.0 ** attempt)
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
        parts = [
            c.get("text", "") for c in data.get("content", []) or []
            if isinstance(c, dict) and c.get("type") == "text"
        ]
        return "".join(parts).strip()


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
        except (KeyError, IndexError, TypeError):
            raise LLMError("openai yanıtında choices[0].message.content yok") from None
        return (content or "").strip()


def get_llm_client(transport: httpx.BaseTransport | None = None) -> LLMClient | None:
    """Ortam değişkenlerinden LLM istemcisi kurar; yapılandırma yoksa None.

    - `FINSIGHT_LLM_PROVIDER`: `anthropic | openai | none`. Boşsa `ANTHROPIC_API_KEY` varsa
      anthropic, yoksa `OPENAI_API_KEY` varsa openai, yoksa None.
    - `FINSIGHT_LLM_MODEL`: varsayılan anthropic için `claude-haiku-5-5`, openai için
      `gpt-4o-mini`.
    - `OPENAI_BASE_URL`: varsayılan `https://api.openai.com/v1` (anahtarsız yerel sunucu
      için özel bir adres verilebilir).
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
        if not openai_key and base_url == DEFAULT_OPENAI_BASE_URL:
            log.warning("FINSIGHT_LLM_PROVIDER=openai ama OPENAI_API_KEY yok; LLM kapalı.")
            return None
        return OpenAICompatibleClient(openai_key or None, model or DEFAULT_OPENAI_MODEL,
                                      base_url=base_url, transport=transport)
    log.warning("Bilinmeyen FINSIGHT_LLM_PROVIDER=%r; LLM kapalı.", provider)
    return None
