"""API ve veritabanı testleri (FastAPI + SQLAlchemy gerekir)."""

import os
from datetime import date

import pytest
from conftest import load

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
fastapi = pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from finsight import api  # noqa: E402
from finsight.edgar import parse_filings  # noqa: E402
from finsight.ingest import save  # noqa: E402
from finsight.models import Base  # noqa: E402
from finsight.xbrl import extract_all  # noqa: E402


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    sub = load("submissions_example.json")
    data = {
        "company": {"cik": "0000000001", "ticker": "EXMP", "name": "Example Corp",
                    "sic": "7372", "fiscal_year_end": "1231"},
        "filings": parse_filings(sub["filings"]["recent"], since=date(2023, 1, 1)),
        "facts": extract_all(load("companyfacts_example.json")),
    }
    with Session() as s:
        save(s, data)
        save(s, data)  # ikinci çalıştırma mükerrer kayıt üretmemeli

    def override():
        with Session() as s:
            yield s

    api.app.dependency_overrides[api.get_session] = override
    yield TestClient(api.app)
    api.app.dependency_overrides.clear()


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_filings_filter_by_form(client):
    body = client.get("/companies/exmp/filings", params={"form": "10-K"}).json()
    assert [f["form"] for f in body["filings"]] == ["10-K/A", "10-K"]
    assert body["filings"][1]["url"].startswith("https://www.sec.gov/Archives/edgar/data/1/")
    assert "yatırım tavsiyesi değildir" in body["disclaimer"]


def test_facts_have_source_and_no_duplicates(client):
    body = client.get("/companies/EXMP/facts", params={"metric": "revenue"}).json()
    assert [f["value"] for f in body["facts"]] == [1000, 1100, 1210]
    assert body["facts"][0]["source"]["xbrl_tag"].startswith("us-gaap:")


def test_unknown_company_and_metric(client):
    assert client.get("/companies/ZZZZ/facts").status_code == 404
    assert client.get("/companies/EXMP/facts", params={"metric": "ebitda"}).status_code == 400
