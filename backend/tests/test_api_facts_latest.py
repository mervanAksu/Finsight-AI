"""GET /facts v1.4 `latest` alanı API üzerinden (FastAPI/SQLAlchemy yoksa atlanır)."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient  # noqa: E402
from integration_support import (TICKER, FakeSec, memory_session_factory,  # noqa: E402
                                 use_light_models)

from finsight import api, jobs  # noqa: E402


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FINSIGHT_DOCUMENTS_DIR", str(tmp_path / "docs"))
    use_light_models()
    app = api.create_app(session_factory=memory_session_factory(), client_factory=FakeSec(),
                         serve_frontend=False)
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        yield c
    jobs.reset_models()


def test_facts_endpoint_includes_latest(client):
    res = client.post(f"/api/companies/{TICKER}/ingest", json={"analyze": False})
    assert res.status_code == 202
    body = client.get(f"/api/companies/{TICKER}/facts").json()
    assert body["metrics"] and all("latest" in m for m in body["metrics"])
    rev = next(m for m in body["metrics"] if m["metric"] == "revenue")
    assert rev["latest"] is None or {"value", "period_end", "basis"} <= set(rev["latest"])
    one = client.get(f"/api/companies/{TICKER}/facts?metric=revenue").json()
    assert [m["metric"] for m in one["metrics"]] == ["revenue"] and "latest" in one["metrics"][0]
