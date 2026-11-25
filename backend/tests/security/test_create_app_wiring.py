"""create_app bağlantı testleri (2. inceleme turu): ara katman sırası, izinli Host / köken
listeleri, CSP özeti, 0.0.0.0 Host mesajı (O8), LLM bütçesinin yalnız gerçek çağrıda düşmesi
(O7). FastAPI/SQLAlchemy gerekir → bulutta atlanır, kullanıcının bilgisayarında koşar."""

from __future__ import annotations

import base64
import hashlib

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from integration_support import TICKER  # noqa: E402

from .attack_support import FakeLLM  # noqa: E402

SCRIPT = "\n  document.documentElement.setAttribute('data-theme', 'light');\n"


def _dist(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(f"<!doctype html><script>{SCRIPT}</script>"
                                     "<script type=module src=./a.js></script>", "utf-8")
    return str(dist)


def _options(mw) -> dict:
    return dict(getattr(mw, "kwargs", None) or getattr(mw, "options", None) or {})


def test_middleware_order_and_configuration(make_client, tmp_path, monkeypatch):
    monkeypatch.setenv("FINSIGHT_ALLOWED_HOSTS", "finsight.local")
    monkeypatch.setenv("FINSIGHT_CORS_ORIGINS", "http://127.0.0.1:3000,*")
    c = make_client(serve_frontend=True, frontend_dist=_dist(tmp_path))
    stack = c.app.user_middleware
    names = [m.cls.__name__ for m in stack]
    assert names[0] == "LocalGuardMiddleware", names   # en dışta
    assert "CORSMiddleware" in names[1:], names
    guard = _options(stack[0])
    assert {"127.0.0.1", "localhost", "::1", "finsight.local"} <= set(guard["allowed_hosts"])
    origins = set(guard["allowed_origins"])
    assert {"http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8000",
            "http://localhost:8000", "http://127.0.0.1:3000"} <= origins
    assert "*" not in origins
    digest = base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode()
    assert f"'sha256-{digest}'" in guard["app_csp"]
    assert "'unsafe-inline'" not in guard["app_csp"].split("script-src")[1].split(";")[0]
    cors = _options(stack[names.index("CORSMiddleware")])
    assert "*" not in cors["allow_origins"] and cors.get("allow_credentials") is False


def test_served_index_has_csp_and_frame_protection(make_client, tmp_path):
    c = make_client(serve_frontend=True, frontend_dist=_dist(tmp_path))
    for path in ("/", "/api/health", "/api/companies/ZZZZQ/overview"):
        h = c.get(path).headers
        assert "frame-ancestors 'none'" in h["content-security-policy"], path
        assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff"
    assert "cdn.jsdelivr.net" in c.get("/docs").headers["content-security-policy"]


def test_dev_frontend_preflight_and_post_allowed(make_client):
    c = make_client()
    pre = c.options(f"/api/companies/{TICKER}/ask",
                    headers={"Origin": "http://127.0.0.1:5173",
                             "Access-Control-Request-Method": "POST",
                             "Access-Control-Request-Headers": "content-type"})
    assert pre.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"
    res = c.post(f"/api/companies/{TICKER}/ingest", json={"analyze": False},
                 headers={"Origin": "http://127.0.0.1:5173"})
    assert res.status_code == 202


def test_o8_zero_host_rejected_with_hint(make_client):
    res = make_client(base_url="http://0.0.0.0:8000").get("/api/health")
    assert res.status_code == 400 and "127.0.0.1" in res.json()["detail"]


def test_o7_llm_budget_only_charged_when_llm_called(client, ingested, monkeypatch):
    from finsight import jobs

    llm = FakeLLM("Supplier risk discussed [1].")
    jobs.override_models(llm=llm)
    budget = client.app.state.llm_budget
    budget.limit = 1
    # İlgili parça yok ve hashing gömücüsü sorgu çevirisini çağırmasın diye LLM çağrı sayısı
    # izlenir: çağrı olmadıysa bütçe düşmemeli.
    before = len(llm.calls)
    client.post(f"/api/companies/{ingested}/ask", json={"question": "zebra giraffe xylophone"})
    if len(llm.calls) == before:
        assert budget.take() is True and budget.take() is False  # 1 birim duruyordu
    else:
        assert budget.take() is False
    budget.limit = 0
