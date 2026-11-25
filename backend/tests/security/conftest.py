"""HTTP saldırı testlerinin fikstürleri (FastAPI + SQLAlchemy gerekir; yoksa atlanır).

``tests/test_api.py``'deki kalıp: bellek içi SQLite + sahte SEC (``integration_support``),
hafif modeller (sözlük + hashing), LLM kapalı. Bu dosya modül düzeyinde FastAPI içe AKTARMAZ;
böylece saf Python saldırı testleri bulutta koşmaya devam eder.

TestClient varsayılan olarak ``http://127.0.0.1:8000`` ana makinesini kullanır (``testserver``
değil): SEC-03 düzeltmesi (``TrustedHostMiddleware``) gelince meşru istekler etkilenmesin.
"""

from __future__ import annotations

import pytest

from .attack_support import LOCAL_BASE


def _require_http_stack() -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("sqlalchemy")
    pytest.importorskip("httpx")


@pytest.fixture
def light_models(monkeypatch, tmp_path):
    _require_http_stack()
    monkeypatch.setenv("FINSIGHT_DOCUMENTS_DIR", str(tmp_path / "docs"))
    monkeypatch.setenv("SEC_CACHE_DIR", str(tmp_path / "sec_cache"))
    for name in ("FINSIGHT_SEC_JSON_TTL", "FINSIGHT_AUTO_REFRESH_MINUTES", "FINSIGHT_QA_LOG",
                 "FINSIGHT_CORS_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    from integration_support import use_light_models

    from finsight import jobs

    use_light_models()
    yield
    jobs.reset_models()


@pytest.fixture
def sec():
    _require_http_stack()
    from integration_support import FakeSec

    return FakeSec()


@pytest.fixture
def session_factory():
    _require_http_stack()
    from integration_support import memory_session_factory

    return memory_session_factory()


@pytest.fixture
def file_session_factory(tmp_path):
    """Dosya tabanlı SQLite (çok iş parçacıklı DoS testleri için; WAL + busy_timeout)."""
    _require_http_stack()
    from finsight.db import init_db, make_engine, make_session_factory

    engine = make_engine(f"sqlite:///{(tmp_path / 'attack.db').as_posix()}")
    init_db(engine)
    return make_session_factory(engine)


@pytest.fixture
def make_client(light_models, session_factory, sec):
    """``make_client(base_url=..., **create_app_kwargs)`` → girilmiş TestClient."""
    from fastapi.testclient import TestClient

    from finsight import api

    opened = []

    def _make(base_url: str = LOCAL_BASE, **kwargs):
        kwargs.setdefault("session_factory", session_factory)
        kwargs.setdefault("client_factory", sec)
        kwargs.setdefault("serve_frontend", False)
        app = api.create_app(**kwargs)
        client = TestClient(app, base_url=base_url)
        client.__enter__()
        opened.append(client)
        return client

    yield _make
    for client in opened:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client):
    return make_client()


@pytest.fixture
def ingested(client):
    """EXMP için meşru (JSON gövdeli, aynı kökenli) ingest + analiz; ticker döndürür."""
    from integration_support import TICKER

    res = client.post(f"/api/companies/{TICKER}/ingest", json={"analyze": True})
    assert res.status_code == 202, res.text
    job = client.get(f"/api/jobs/{res.json()['job_id']}").json()
    assert job["status"] == "done", job
    return TICKER
