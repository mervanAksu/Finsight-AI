"""API testleri (FastAPI + SQLAlchemy gerekir; yoksa atlanır) — docs/api-contract.md.

Bellek içi SQLite + sahte SEC (integration_support) ile uçtan uca: ingest işi → done, tüm
okuma uç noktaları, soru-cevap (LLM yok → extractive), karşılaştırma, CSV, arama, hata kodları.
"""

import os
from datetime import date
from urllib.parse import unquote

import pytest
from conftest import load

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
fastapi = pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient  # noqa: E402
from integration_support import (ACC_8K, ACC_K_NEW, ACC_K_OLD, ACC_Q,  # noqa: E402
                                 ADDED_RISK_PREFIX, TICKER, FakeSec, memory_session_factory,
                                 use_light_models)
from sqlalchemy import func, select  # noqa: E402

from finsight import __version__, api, jobs  # noqa: E402
from finsight import repository as repo  # noqa: E402
from finsight.edgar import parse_filings  # noqa: E402
from finsight.ingest import save  # noqa: E402
from finsight.models import Filing, FilingSection, FinancialFact, QALog  # noqa: E402
from finsight.rag import EMBEDDING_MISMATCH_WARNING  # noqa: E402
from finsight.xbrl import METRICS, extract_all  # noqa: E402

# SEC-03: TrustedHost/Host denetimi yalnızca yerel adları kabul eder ("testserver" değil).
LOCAL_BASE = "http://127.0.0.1:8000"

DISCLAIMER_PART = "yatırım tavsiyesi değildir"
COMPANY_KEYS = {"ticker", "cik", "name", "sic", "sic_description", "fiscal_year_end",
                "exchanges"}
FILING_REF_KEYS = {"accession_no", "form", "filing_date", "report_date", "url"}


@pytest.fixture(autouse=True)
def light_models(monkeypatch, tmp_path):
    monkeypatch.setenv("FINSIGHT_DOCUMENTS_DIR", str(tmp_path / "docs"))
    use_light_models()
    yield
    jobs.reset_models()


@pytest.fixture
def factory():
    return memory_session_factory()


@pytest.fixture
def sec():
    return FakeSec()


@pytest.fixture
def client(factory, sec):
    app = api.create_app(session_factory=factory, client_factory=sec, serve_frontend=False)
    with TestClient(app, base_url=LOCAL_BASE) as c:
        yield c


def seed_example(factory, ticker="EXMP", cik="0000000001", tickers=None):
    """Aşama 1 verisiyle (analizsiz) bir şirket yazar; iki kez — mükerrer kayıt olmamalı."""
    sub = load("submissions_example.json")
    data = {
        "company": {"cik": cik, "ticker": ticker, "name": f"{ticker} Example Corp",
                    "sic": "7372", "fiscal_year_end": "1231",
                    **({"tickers": tickers} if tickers is not None else {})},
        "filings": parse_filings(sub["filings"]["recent"], since=date(2020, 1, 1)),
        "facts": extract_all(load("companyfacts_example.json")),
    }
    if cik != "0000000001":  # accession numaraları benzersiz olmalı
        for f in data["filings"]:
            f["accession_no"] = f["accession_no"].replace("0000000001-", cik + "-")
    with factory() as s:
        save(s, data)
        save(s, data)


@pytest.fixture
def ingested(client, factory):
    """EXMP için ingest + analiz işi (TestClient arka plan görevini yanıt dönmeden bitirir)."""
    res = client.post(f"/api/companies/{TICKER}/ingest", json={"analyze": True})
    assert res.status_code == 202, res.text
    body = res.json()
    assert set(body) == {"job_id", "status"} and body["status"] == "queued"
    job = client.get(f"/api/jobs/{body['job_id']}").json()
    assert job["status"] == "done", job
    return job


# --------------------------------------------------------------------------- Aşama 1 testleri
# (yeni sözleşmeye uyarlandı: /api öneki, metrics[].values, items listesi)


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["version"] == __version__ == "0.3.0"
    assert body["models"]["sentiment"] == "lexicon-fallback"
    assert body["models"]["embedding"] == "hashing-fallback"
    assert body["models"]["llm"] == {"provider": "none", "model": None, "enabled": False}


def test_filings_filter_by_form(client, factory):
    seed_example(factory)
    body = client.get("/api/companies/exmp/filings", params={"form": "10-K"}).json()
    assert [f["form"] for f in body["filings"]] == ["10-K/A", "10-K"]
    assert body["filings"][1]["url"].startswith("https://www.sec.gov/Archives/edgar/data/1/")
    assert DISCLAIMER_PART in body["disclaimer"]
    assert body["filings"][0]["items"] == [] and body["filings"][0]["analyzed"] is False
    eight_k = client.get("/api/companies/EXMP/filings", params={"form": "8-K"}).json()
    assert eight_k["filings"][0]["items"] == ["2.02", "9.01"]
    assert eight_k["filings"][0]["item_labels"][0] == "Faaliyet sonuçları ve finansal durum"


def test_facts_have_source_and_no_duplicates(client, factory):
    seed_example(factory)
    body = client.get("/api/companies/EXMP/facts", params={"metric": "revenue"}).json()
    values = body["metrics"][0]["values"]
    assert [v["value"] for v in values] == [1000, 1100, 1210]
    assert values[0]["source"]["xbrl_tag"].startswith("us-gaap:")
    assert values[1]["yoy"] == pytest.approx(0.1)
    assert values[0]["source"]["url"].startswith("https://www.sec.gov/Archives/edgar/data/1/")


def test_unknown_company_and_metric(client, factory):
    seed_example(factory)
    res = client.get("/api/companies/ZZZZ/facts")
    assert res.status_code == 404 and "ZZZZ" in res.json()["detail"]
    assert client.get("/api/companies/EXMP/facts", params={"metric": "ebitda"}).status_code \
        == 400
    assert client.get("/api/companies/bad$tick/overview").status_code == 400
    assert client.get("/api/companies/ABCDEFGHIJK/overview").status_code == 400


# --------------------------------------------------------------------------- uçtan uca


def test_ingest_job_and_analysis_endpoints(client, factory, ingested, sec):
    assert ingested["progress"] == 1.0 and ingested["error"] is None
    assert ingested["request_count"] == 6 and ingested["started_at"].endswith("Z")

    ov = client.get("/api/companies/exmp/overview").json()
    assert set(ov) >= {"company", "analyzed", "score", "key_metrics", "latest_filings",
                       "sentiment_summary", "risk_summary", "analysis_updated_at",
                       "disclaimer"}
    assert set(ov["company"]) == COMPANY_KEYS and ov["company"]["exchanges"] == ["Nasdaq"]
    assert ov["company"]["sic_description"] == "Services-Prepackaged Software"
    assert ov["analyzed"] is True and ov["analysis_updated_at"].endswith("Z")
    assert ov["score"]["total"] is not None and ov["score"]["as_of_period"] == "2025-06-30"
    assert [m["metric"] for m in ov["key_metrics"]] == [
        "revenue", "net_income", "operating_cash_flow", "total_assets", "total_liabilities",
        "stockholders_equity"]
    assert ov["key_metrics"][0]["value"] == 5_500_000_000
    assert ov["sentiment_summary"]["filing"]["accession_no"] == ACC_K_NEW
    assert ov["sentiment_summary"]["model"] == "lexicon-fallback"
    assert ov["risk_summary"]["added"] >= 1
    assert 0 < len(ov["latest_filings"]) <= 5
    assert set(ov["latest_filings"][0]) == FILING_REF_KEYS

    ratios = client.get("/api/companies/EXMP/ratios").json()
    assert ratios["periods"] == ["2023-06-30", "2024-06-30", "2025-06-30"]
    roa = next(r for r in ratios["ratios"] if r["key"] == "roa")
    assert roa["values"][0]["note"] is None  # 2022 bilançosu çekildi (years+1)

    score = client.get("/api/companies/EXMP/score").json()
    assert len(score["components"]) == 7 and score["weights_version"] == "v1-2026-11"
    assert DISCLAIMER_PART in score["disclaimer"]

    facts = client.get("/api/companies/EXMP/facts").json()
    assert [m["metric"] for m in facts["metrics"]] == list(METRICS)
    tl = next(m for m in facts["metrics"] if m["metric"] == "total_liabilities")
    assert tl["kind"] == "instant"
    assert tl["values"][0]["source"]["xbrl_tag"] == "derived"
    assert tl["values"][0]["source"]["derived_from"][0] == "LiabilitiesAndStockholdersEquity"

    sentiment = client.get("/api/companies/EXMP/sentiment").json()
    assert sentiment["model"] == "lexicon-fallback"
    order = [(s["filing"]["accession_no"], s["item_code"]) for s in sentiment["sections"]]
    assert order == [(ACC_K_OLD, "1A"), (ACC_K_OLD, "7"), (ACC_K_NEW, "1A"), (ACC_K_NEW, "7"),
                     (ACC_Q, "2"), (ACC_Q, "1A")]
    assert all(len(s["top_negative"]) <= 5 for s in sentiment["sections"])

    risk = client.get("/api/companies/EXMP/risk-changes").json()
    assert risk["old_filing"]["accession_no"] == ACC_K_OLD
    assert risk["new_filing"]["accession_no"] == ACC_K_NEW
    assert risk["changes"][0]["change_type"] == "added"
    assert risk["changes"][0]["new_text"].startswith(ADDED_RISK_PREFIX)
    assert risk["counts"]["unchanged"] >= 1

    filings = client.get("/api/companies/EXMP/filings").json()["filings"]
    by_acc = {f["accession_no"]: f for f in filings}
    assert {a for a, f in by_acc.items() if f["analyzed"]} == {ACC_K_NEW, ACC_K_OLD, ACC_Q}
    assert by_acc[ACC_8K]["items"] == ["2.02", "9.01"]
    assert [f["filing_date"] for f in filings] == sorted((f["filing_date"] for f in filings),
                                                         reverse=True)

    listed = client.get("/api/companies").json()
    assert listed[0]["ticker"] == "EXMP" and listed[0]["analyzed"] is True
    assert listed[0]["score"]["total"] == ov["score"]["total"]


def test_ask_extractive_without_llm_and_logs(client, factory, ingested):
    res = client.post("/api/companies/EXMP/ask",
                      json={"question": "What are the supply chain risks for semiconductors?"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["mode"] == "extractive" and body["found"] is True
    assert body["model"] == "hashing-fallback" and DISCLAIMER_PART in body["disclaimer"]
    src = body["sources"][0]
    assert set(src) >= {"n", "chunk_id", "section", "section_title", "form", "filing_date",
                        "accession_no", "url", "text", "similarity"}
    assert src["n"] == 1 and isinstance(src["chunk_id"], int)
    assert src["url"].startswith("https://www.sec.gov/Archives/edgar/data/1/")
    assert "[1]" in body["answer"]

    nf = client.post("/api/companies/EXMP/ask", json={"question": "zebra giraffe xylophone"})
    assert nf.json()["mode"] == "not_found" and nf.json()["sources"] == []
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(QALog)) == 2


def test_ask_validation(client, factory):
    seed_example(factory)
    assert client.post("/api/companies/EXMP/ask", json={"question": "x" * 501}).status_code \
        == 422
    assert client.post("/api/companies/EXMP/ask", json={"question": ""}).status_code == 422
    assert client.post("/api/companies/EXMP/ask", json={}).status_code == 422
    blank = client.post("/api/companies/EXMP/ask", json={"question": "   "})
    assert blank.status_code == 400 and blank.json()["detail"] == "Soru boş olamaz."
    assert client.post("/api/companies/ZZZZ/ask", json={"question": "a"}).status_code == 404
    # analiz yok → kaynak yok → not_found (LLM çağrılmaz)
    assert client.post("/api/companies/EXMP/ask",
                       json={"question": "suppliers"}).json()["mode"] == "not_found"


def test_second_ingest_creates_no_duplicates(client, factory, ingested, sec):
    with factory() as s:
        before = [s.scalar(select(func.count()).select_from(m))
                  for m in (Filing, FinancialFact, FilingSection)]
    docs_before = len(sec.document_calls())
    job_id = client.post(f"/api/companies/{TICKER}/ingest").json()["job_id"]  # gövdesiz
    assert job_id != ingested["id"]
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "done"
    assert len(sec.document_calls()) == docs_before  # analiz edilmiş başvurular atlandı
    with factory() as s:
        after = [s.scalar(select(func.count()).select_from(m))
                 for m in (Filing, FinancialFact, FilingSection)]
    assert after == before


def test_ingest_returns_running_job_and_validates(client, factory):
    with factory() as s:
        job = repo.create_job(s, "EXMP")
        repo.update_job(s, job["id"], status="running", step="Gömme")
    res = client.post("/api/companies/exmp/ingest", json={"analyze": False})
    assert res.status_code == 202
    assert res.json() == {"job_id": job["id"], "status": "running"}
    assert client.post("/api/companies/bad$/ingest").status_code == 400
    assert client.get("/api/jobs/9999").status_code == 404


def test_ingest_without_user_agent_is_503(factory, monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    app = api.create_app(session_factory=factory, serve_frontend=False)
    with TestClient(app, base_url=LOCAL_BASE) as c:
        res = c.post("/api/companies/EXMP/ingest")
    assert res.status_code == 503 and "SEC_USER_AGENT" in res.json()["detail"]


def test_ingest_analyze_false_job(client, factory):
    job_id = client.post(f"/api/companies/{TICKER}/ingest",
                         json={"analyze": False}).json()["job_id"]
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "done" and job["request_count"] == 3
    ov = client.get("/api/companies/EXMP/overview").json()
    assert ov["analyzed"] is False and ov["score"] is None
    assert ov["sentiment_summary"] is None and ov["risk_summary"] is None
    assert client.get("/api/companies/EXMP/score").json()["total"] is None


def test_unanalyzed_company_empty_analysis_shapes(client, factory):
    seed_example(factory)
    risk = client.get("/api/companies/EXMP/risk-changes").json()
    assert risk["old_filing"] is None and risk["new_filing"] is None and risk["changes"] == []
    assert risk["counts"] == {"added": 0, "removed": 0, "modified": 0, "unchanged": 0}
    sentiment = client.get("/api/companies/EXMP/sentiment").json()
    assert sentiment["sections"] == [] and sentiment["model"] == "lexicon-fallback"
    score = client.get("/api/companies/EXMP/score").json()
    assert score["total"] is None and score["components"] == []
    assert score["explanation"] is None
    for path in ("overview", "facts", "ratios", "score", "filings", "sentiment",
                 "risk-changes", "export.csv"):
        assert client.get(f"/api/companies/NOPE/{path}").status_code == 404, path


def test_compare(client, factory):
    seed_example(factory, "EXMP", "0000000001")
    seed_example(factory, "EXTW", "0000000009")
    body = client.get("/api/compare", params={"tickers": "exmp,EXTW"}).json()
    assert [c["ticker"] for c in body["companies"]] == ["EXMP", "EXTW"]
    assert body["companies"][0]["period_end"] == "2025-12-31"
    assert body["companies"][0]["score"] is None  # analiz yok, karşılaştırma yine çalışır
    assert "net_margin" in body["companies"][0]["ratios"]
    assert body["ratio_meta"][0]["key"] == "gross_margin"
    assert client.get("/api/compare", params={"tickers": "EXMP"}).status_code == 400
    assert client.get("/api/compare", params={"tickers": "EXMP,exmp"}).status_code == 400
    assert client.get("/api/compare", params={"tickers": "A,B,C,D,E"}).status_code == 400
    assert client.get("/api/compare", params={"tickers": "EXMP,ZZZZ"}).status_code == 404
    assert client.get("/api/compare", params={"tickers": "EXMP,b@d"}).status_code == 400


def test_export_csv(client, factory, ingested):
    res = client.get("/api/companies/EXMP/export.csv")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "attachment" in res.headers["content-disposition"]
    raw = res.content
    assert raw.startswith(b"\xef\xbb\xbf")
    lines = raw.decode("utf-8-sig").splitlines()
    assert lines[0] == ("ticker,cik,section,key,label,period_start,period_end,value,unit,"
                        "status,xbrl_tag,form,accession_no,filed")
    sections = {line.split(",")[2] for line in lines[1:]}
    assert sections == {"fact", "ratio", "score"}
    # O12: uyarı CSV içinde değil, ASCII güvenli yanıt başlığında
    header = res.headers["x-finsight-disclaimer"]
    assert header.isascii() and DISCLAIMER_PART in unquote(header)
    assert not lines[0].startswith("#")


def test_export_csv_without_analysis_has_only_facts_and_ratios(client, factory):
    seed_example(factory)
    lines = client.get("/api/companies/EXMP/export.csv").content.decode("utf-8-sig") \
        .splitlines()
    assert {line.split(",")[2] for line in lines[1:]} == {"fact", "ratio"}


def test_search_uses_sec_ticker_list_and_marks_ingested(client, factory):
    seed_example(factory)
    res = client.get("/api/companies/search", params={"q": "exm"}).json()
    assert res == [{"ticker": "EXMP", "cik": "0000000001", "name": "Example Corp",
                    "ingested": True}]
    apple = client.get("/api/companies/search", params={"q": "apple"}).json()
    assert apple[0]["ticker"] == "AAPL" and apple[0]["ingested"] is False
    assert client.get("/api/companies/search", params={"q": ""}).status_code == 422


def test_search_falls_back_to_local_companies(factory):
    seed_example(factory)

    def broken():
        raise RuntimeError("SEC'e erişilemiyor")

    app = api.create_app(session_factory=factory, client_factory=broken, serve_frontend=False)
    with TestClient(app, base_url=LOCAL_BASE) as c:
        res = c.get("/api/companies/search", params={"q": "exmp"})
    assert res.status_code == 200
    assert res.json() == [{"ticker": "EXMP", "cik": "0000000001", "name": "EXMP Example Corp",
                           "ingested": True}]
    assert res.headers[api.SEARCH_SCOPE_HEADER] == "local"
    assert res.headers[api.SEARCH_REASON_HEADER] == "sec_unavailable"


def test_search_scope_header_sec_when_list_available(client, factory):
    seed_example(factory)
    res = client.get("/api/companies/search", params={"q": "apple"})
    assert res.headers[api.SEARCH_SCOPE_HEADER] == "sec"
    assert api.SEARCH_REASON_HEADER not in res.headers


def test_search_reports_missing_user_agent(factory, monkeypatch):
    """FR-01, NFR-01: SEC_USER_AGENT yoksa arama sessizce daralmaz; neden başlıkta ve
    /health'te görünür (arayüz kullanıcıya .env'i doldurmasını söyler)."""
    from finsight.config import ConfigError

    seed_example(factory)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)

    def no_user_agent():
        raise ConfigError("SEC_USER_AGENT tanımlı değil veya e-posta içermiyor.")

    app = api.create_app(session_factory=factory, client_factory=no_user_agent,
                         serve_frontend=False)
    with TestClient(app, base_url=LOCAL_BASE) as c:
        res = c.get("/api/companies/search", params={"q": "exmp"})
        health = c.get("/api/health").json()
        pre = c.get("/api/companies/search", params={"q": "exmp"},
                    headers={"Origin": "http://localhost:5173"})
    assert res.status_code == 200 and res.json()[0]["ticker"] == "EXMP"
    assert res.headers[api.SEARCH_SCOPE_HEADER] == "local"
    assert res.headers[api.SEARCH_REASON_HEADER] == "sec_user_agent_missing"
    assert health["sec"]["user_agent_configured"] is False
    # v1.4: önbellek süresi ve otomatik yenileme aralığı
    assert health["sec"]["json_ttl_seconds"] == 3600
    assert health["auto_refresh_minutes"] == 0
    exposed = pre.headers.get("access-control-expose-headers", "")
    assert api.SEARCH_SCOPE_HEADER in exposed and api.SEARCH_REASON_HEADER in exposed


def test_root_info_frontend_and_cors(factory, tmp_path):
    app = api.create_app(session_factory=factory, client_factory=FakeSec(),
                         serve_frontend=False)
    with TestClient(app, base_url=LOCAL_BASE) as c:
        assert c.get("/").json()["api"] == "/api/health"
        pre = c.options("/api/health", headers={"Origin": "http://localhost:5173",
                                                "Access-Control-Request-Method": "GET"})
        assert pre.headers.get("access-control-allow-origin") == "http://localhost:5173"
        assert c.get("/api/nope").status_code == 404

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>FinSight</title>", "utf-8")
    app = api.create_app(session_factory=factory, client_factory=FakeSec(),
                         frontend_dist=str(dist))
    with TestClient(app, base_url=LOCAL_BASE) as c:
        assert "FinSight" in c.get("/").text
        assert c.get("/api/health").json()["status"] == "ok"  # API önce eşleşir
        assert c.get("/docs").status_code == 200


def test_two_tickers_same_cik_both_return_overview(client, factory):
    """K3: GOOGL/GOOG benzeri iki ticker (kurgusal EXMA/EXMB, aynı CIK) aynı şirketi gösterir."""
    seed_example(factory, "EXMA", "0000000001", tickers=["EXMA", "EXMB"])
    seed_example(factory, "EXMB", "0000000001", tickers=["EXMB", "EXMA"])
    a = client.get("/api/companies/EXMA/overview")
    b = client.get("/api/companies/exmb/overview")
    assert a.status_code == b.status_code == 200
    assert a.json()["company"] == b.json()["company"]
    assert a.json()["company"]["ticker"] == "EXMA"  # birincil ticker korunur
    assert set(a.json()["company"]) == COMPANY_KEYS
    assert client.get("/api/companies/EXMB/facts").json()["metrics"]
    assert client.get("/api/companies/EXM/overview").status_code == 404
    listed = client.get("/api/companies").json()
    assert len(listed) == 1


def test_ingest_finds_running_job_of_other_ticker_same_company(client, factory):
    """K3 + O1: aynı CIK'in diğer ticker'ıyla çalışan iş varsa yeni iş açılmaz."""
    seed_example(factory, "EXMA", "0000000001", tickers=["EXMA", "EXMB"])
    with factory() as s:
        job = repo.create_job(s, "EXMA")
    res = client.post("/api/companies/EXMB/ingest", json={"analyze": False})
    assert res.status_code == 202 and res.json()["job_id"] == job["id"]


def test_ask_warns_when_chunks_use_other_embedding_model(client, factory, ingested):
    """K4: parçalar başka gömme modeliyle oluşturulmuşsa /ask uyarı döndürür."""
    from finsight.embeddings import HashingEmbedder

    class OtherEmbedder(HashingEmbedder):
        name = "other-embedder"

    jobs.override_models(embedder=OtherEmbedder())
    body = client.post("/api/companies/EXMP/ask", json={"question": "supplier risks"}).json()
    assert body["mode"] == "not_found" and body["found"] is False
    assert body["warning"] == EMBEDDING_MISMATCH_WARNING


def test_lazy_app_wraps_attribute_error(monkeypatch):
    """K7: create_app içindeki AttributeError 'app özniteliği yok' olarak gizlenmez."""
    def broken(**_kw):
        raise AttributeError("'NoneType' object has no attribute 'kw'")

    monkeypatch.setattr(api, "_app", None)
    monkeypatch.setattr(api, "create_app", broken)
    with pytest.raises(RuntimeError, match="uygulaması kurulamadı"):
        _ = api.app


def test_lazy_module_app(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setattr(api, "_app", None)
    app = api.app
    assert isinstance(app, fastapi.FastAPI) and api.app is app
    with TestClient(app, base_url=LOCAL_BASE) as c:
        assert c.get("/api/health").status_code == 200
