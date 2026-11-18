"""FR-10 / NFR-10: LLM istemcileri — istek şekli, yeniden deneme, anahtar gizliliği, env seçimi.

Ağa çıkılmaz: tüm istekler httpx.MockTransport ile yakalanır.
"""

import json

import httpx
import pytest

from finsight.llm import (
    AnthropicClient,
    LLMError,
    OpenAICompatibleClient,
    get_llm_client,
)

KEY = "sk-test-SECRET-123456"
ENV_VARS = ("FINSIGHT_LLM_PROVIDER", "FINSIGHT_LLM_MODEL", "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY", "OPENAI_BASE_URL", "FINSIGHT_LLM_TIMEOUT")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)


class Recorder:
    """Sıradaki yanıtları döndürür, gelen istekleri kaydeder."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        resp = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(resp, Exception):
            raise resp
        return resp

    @property
    def transport(self):
        return httpx.MockTransport(self)


def anthropic_ok(text_parts=("Merhaba ", "dünya [1]")):
    content = [{"type": "text", "text": t} for t in text_parts]
    content.insert(1, {"type": "tool_use", "id": "x", "name": "n", "input": {}})
    return httpx.Response(200, json={"id": "msg_1", "type": "message", "content": content})


def test_anthropic_request_shape_and_text_join():
    rec = Recorder(anthropic_ok())
    client = AnthropicClient(KEY, transport=rec.transport)
    assert client.provider == "anthropic" and client.model == "claude-haiku-5-5"
    assert client.timeout == 20.0  # O7: varsayılan 20 sn (önceden 60)
    out = client.complete("SİSTEM", "Soru?", max_tokens=321)
    assert out == "Merhaba dünya [1]"

    (req,) = rec.requests
    assert req.method == "POST"
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == KEY
    assert req.headers["anthropic-version"] == "2023-06-01"
    assert req.headers["content-type"] == "application/json"
    assert json.loads(req.content) == {
        "model": "claude-haiku-5-5",
        "max_tokens": 321,
        "system": "SİSTEM",
        "messages": [{"role": "user", "content": "Soru?"}],
    }


def test_anthropic_retries_on_429_and_5xx_then_succeeds():
    sleeps = []
    rec = Recorder(httpx.Response(429, json={}), httpx.Response(503, text="busy"), anthropic_ok())
    client = AnthropicClient(KEY, transport=rec.transport, sleep=sleeps.append)
    assert client.complete("s", "u").startswith("Merhaba")
    assert len(rec.requests) == 3
    assert sleeps == [1.0, 2.0]


def test_anthropic_gives_up_after_two_retries():
    sleeps = []
    rec = Recorder(httpx.Response(500, text="err"))
    client = AnthropicClient(KEY, transport=rec.transport, sleep=sleeps.append)
    with pytest.raises(LLMError, match="HTTP 500"):
        client.complete("s", "u")
    assert len(rec.requests) == 3  # ilk deneme + 2 yeniden deneme


def test_no_retry_on_client_error():
    rec = Recorder(httpx.Response(400, json={"error": {"message": "bad request"}}))
    client = AnthropicClient(KEY, transport=rec.transport, sleep=lambda s: None)
    with pytest.raises(LLMError, match="HTTP 400"):
        client.complete("s", "u")
    assert len(rec.requests) == 1


def test_api_key_never_appears_in_errors_or_repr():
    body = {"error": {"type": "authentication_error", "message": f"invalid x-api-key {KEY}"}}
    rec = Recorder(httpx.Response(401, json=body))
    client = AnthropicClient(KEY, transport=rec.transport, sleep=lambda s: None)
    with pytest.raises(LLMError) as exc_info:
        client.complete("s", "u")
    message = str(exc_info.value)
    # D7: gövde mesaja konmaz; yalnızca durum kodu ve error.type
    assert KEY not in message and "invalid x-api-key" not in message
    assert message.endswith("HTTP 401 (authentication_error)")
    assert KEY not in repr(client)

    oa = OpenAICompatibleClient(KEY, transport=Recorder(httpx.Response(401, text=KEY)).transport,
                                sleep=lambda s: None)
    with pytest.raises(LLMError) as exc_info:
        oa.complete("s", "u")
    assert KEY not in str(exc_info.value)
    assert KEY not in repr(oa)


def test_transport_errors_are_retried_then_wrapped():
    rec = Recorder(httpx.ConnectError("boom"), anthropic_ok())
    client = AnthropicClient(KEY, transport=rec.transport, sleep=lambda s: None)
    assert client.complete("s", "u")
    rec = Recorder(httpx.ConnectTimeout("slow connect"))
    client = AnthropicClient(KEY, transport=rec.transport, sleep=lambda s: None)
    with pytest.raises(LLMError, match="bağlantı hatası"):
        client.complete("s", "u")
    assert len(rec.requests) == 3


def test_read_timeout_is_not_retried():
    # O7: okuma zaman aşımı yeniden denenmez (en kötü bekleme ~timeout, 3×60 sn değil)
    sleeps = []
    rec = Recorder(httpx.ReadTimeout("slow"))
    client = AnthropicClient(KEY, transport=rec.transport, sleep=sleeps.append)
    with pytest.raises(LLMError, match="zaman aşımı"):
        client.complete("s", "u")
    assert len(rec.requests) == 1 and sleeps == []


def test_timeout_from_env(monkeypatch):
    monkeypatch.setenv("FINSIGHT_LLM_TIMEOUT", "7.5")
    assert AnthropicClient(KEY).timeout == 7.5
    assert OpenAICompatibleClient(KEY, timeout=3).timeout == 3.0  # açık parametre önceliklidir
    monkeypatch.setenv("FINSIGHT_LLM_TIMEOUT", "abc")
    assert AnthropicClient(KEY).timeout == 20.0
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.setenv("FINSIGHT_LLM_TIMEOUT", "12")
    assert get_llm_client().timeout == 12.0


def test_retry_after_header_is_honored_and_capped():
    sleeps = []
    rec = Recorder(httpx.Response(429, headers={"retry-after": "3"}, json={}),
                   httpx.Response(529, headers={"Retry-After": "120"}, json={}), anthropic_ok())
    client = AnthropicClient(KEY, transport=rec.transport, sleep=sleeps.append)
    assert client.complete("s", "u").startswith("Merhaba")
    assert sleeps == [3.0, 10.0]
    sleeps.clear()
    rec = Recorder(httpx.Response(503, headers={"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}),
                   anthropic_ok())
    AnthropicClient(KEY, transport=rec.transport, sleep=sleeps.append).complete("s", "u")
    assert sleeps == [1.0]  # tarih biçimi → üstel bekleme


def test_error_message_has_no_response_body():
    masked = "Incorrect API key provided: sk-proj-****abcd."
    rec = Recorder(httpx.Response(401, json={"error": {"message": masked,
                                                        "type": "invalid_request_error"}}))
    client = OpenAICompatibleClient("sk-proj-XYZabcd", transport=rec.transport,
                                    sleep=lambda s: None)
    with pytest.raises(LLMError) as exc_info:
        client.complete("s", "u")
    assert str(exc_info.value) == "openai LLM isteği başarısız: HTTP 401 (invalid_request_error)"
    rec = Recorder(httpx.Response(500, text="<html>secret page</html>"))
    with pytest.raises(LLMError) as exc_info:
        AnthropicClient(KEY, transport=rec.transport, sleep=lambda s: None).complete("s", "u")
    assert str(exc_info.value) == "anthropic LLM isteği başarısız: HTTP 500"


@pytest.mark.parametrize("body", [
    [1, 2], {"content": "düz metin"}, {"content": [{"type": "text", "text": None}]},
    {"content": [{"type": "text", "text": 5}]}, "metin",
])
def test_anthropic_unexpected_shapes_raise_llm_error(body):
    rec = Recorder(httpx.Response(200, json=body))
    with pytest.raises(LLMError, match="beklenmeyen"):
        AnthropicClient(KEY, transport=rec.transport).complete("s", "u")


def test_openai_content_shapes():
    def call(content):
        rec = Recorder(httpx.Response(200, json={"choices": [{"message": {"content": content}}]}))
        return OpenAICompatibleClient(KEY, transport=rec.transport).complete("s", "u")

    assert call([{"type": "text", "text": "x "}, {"type": "text", "text": "y"}]) == "x y"
    assert call(None) == ""
    for bad in (5, {"text": "x"}, [{"type": "text", "text": None}]):
        with pytest.raises(LLMError, match="beklenmeyen"):
            call(bad)
    rec = Recorder(httpx.Response(200, json={"choices": "x"}))
    with pytest.raises(LLMError, match="choices"):
        OpenAICompatibleClient(KEY, transport=rec.transport).complete("s", "u")


def test_openai_compatible_request_shape():
    ok = httpx.Response(200, json={"choices": [{"message": {"role": "assistant",
                                                            "content": " Cevap [2] "}}]})
    rec = Recorder(ok)
    client = OpenAICompatibleClient(KEY, transport=rec.transport)
    assert client.provider == "openai" and client.model == "gpt-4o-mini"
    assert client.complete("SYS", "USER", max_tokens=50) == "Cevap [2]"
    (req,) = rec.requests
    assert str(req.url) == "https://api.openai.com/v1/chat/completions"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(req.content) == {
        "model": "gpt-4o-mini",
        "max_tokens": 50,
        "messages": [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"}],
    }


def test_openai_custom_base_url_without_key_and_bad_shape():
    rec = Recorder(httpx.Response(200, json={"choices": []}))
    client = OpenAICompatibleClient(None, model="llama3", base_url="http://localhost:11434/v1/",
                                    transport=rec.transport)
    with pytest.raises(LLMError, match="choices"):
        client.complete("s", "u")
    (req,) = rec.requests
    assert str(req.url) == "http://localhost:11434/v1/chat/completions"
    assert "authorization" not in req.headers


@pytest.mark.parametrize(
    "env, expected",
    [
        ({}, None),
        ({"FINSIGHT_LLM_PROVIDER": "none", "ANTHROPIC_API_KEY": KEY}, None),
        ({"ANTHROPIC_API_KEY": KEY}, ("anthropic", "claude-haiku-5-5")),
        ({"ANTHROPIC_API_KEY": KEY, "OPENAI_API_KEY": KEY}, ("anthropic", "claude-haiku-5-5")),
        ({"OPENAI_API_KEY": KEY}, ("openai", "gpt-4o-mini")),
        ({"FINSIGHT_LLM_PROVIDER": "openai", "ANTHROPIC_API_KEY": KEY, "OPENAI_API_KEY": KEY},
         ("openai", "gpt-4o-mini")),
        ({"ANTHROPIC_API_KEY": KEY, "FINSIGHT_LLM_MODEL": "claude-sonnet-x"},
         ("anthropic", "claude-sonnet-x")),
        ({"FINSIGHT_LLM_PROVIDER": "anthropic"}, None),  # anahtar yok
        ({"FINSIGHT_LLM_PROVIDER": "openai"}, None),  # anahtar yok, varsayılan URL
        ({"FINSIGHT_LLM_PROVIDER": "openai", "OPENAI_BASE_URL": "http://localhost:8080/v1"},
         ("openai", "gpt-4o-mini")),
        ({"FINSIGHT_LLM_PROVIDER": "bogus", "ANTHROPIC_API_KEY": KEY}, None),
    ],
)
def test_get_llm_client_env_selection(monkeypatch, env, expected):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    client = get_llm_client()
    if expected is None:
        assert client is None
    else:
        assert (client.provider, client.model) == expected


def test_get_llm_client_passes_transport_and_base_url(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.example/v1")
    rec = Recorder(httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}))
    client = get_llm_client(transport=rec.transport)
    assert client.complete("s", "u") == "ok"
    assert str(rec.requests[0].url) == "https://proxy.example/v1/chat/completions"
