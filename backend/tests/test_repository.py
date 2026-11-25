"""repository.py + db.py testleri (SQLAlchemy gerekir; yoksa atlanır).

Bellek içi SQLite (StaticPool) ve sahte SEC verisiyle: upsert, yeniden analizde eski kayıtların
silinmesi, risk/skor/iş kayıtları ve eski şemalı SQLite dosyasının yükseltilmesi.
"""

import pathlib
import sqlite3
from datetime import date, timedelta

import numpy as np
import pytest

pytest.importorskip("sqlalchemy")

from conftest import load  # noqa: E402
from integration_support import TICKER, FakeSec, build_routes  # noqa: E402
from sqlalchemy import func, inspect, select  # noqa: E402

from finsight import repository as repo  # noqa: E402
from finsight.analyze import analyze_document  # noqa: E402
from finsight.db import init_db, make_engine, make_session_factory  # noqa: E402
from finsight.edgar import build_ticker_map  # noqa: E402
from finsight.embeddings import HashingEmbedder  # noqa: E402
from finsight.models import (ChunkEmbedding, Company, CompanyScore, Filing,  # noqa: E402
                             FilingDocument, FilingSection, FinancialFact, IngestionJob,
                             QALog, RiskChange, RiskComparison, SentimentResult, TextChunk,
                             utcnow)
from finsight.pipeline import collect  # noqa: E402
from finsight.risk_diff import compare_risk_sections  # noqa: E402
from finsight.scoring import compute_score  # noqa: E402
from finsight.sentiment import LexiconModel, normalize_sentence  # noqa: E402

KESTREL_10K = pathlib.Path(__file__).parent / "fixtures" / "nlp" / "kestrel_10k.html"


@pytest.fixture
def session():
    engine = make_engine("sqlite://")
    init_db(engine)
    factory = make_session_factory(engine)
    with factory() as s:
        yield s
    engine.dispose()


@pytest.fixture
def data():
    client = FakeSec(build_routes())()
    tmap = build_ticker_map(load("company_tickers_example.json"))
    return collect(client, TICKER, tmap, years=3)


def count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def analysis(chunk_words=None, embedder=None):
    emb = embedder or HashingEmbedder()
    return analyze_document(KESTREL_10K.read_bytes(), "10-K",
                            sentiment_model=LexiconModel(dictionary_path=""), embedder=emb,
                            chunk_words=chunk_words)


# --------------------------------------------------------------------------- SEC verisi


def test_save_collected_upsert_derived_from_and_company_fields(session, data):
    company = repo.save_collected(session, data)
    counts = (count(session, Company), count(session, Filing), count(session, FinancialFact))
    repo.save_collected(session, data)  # ikinci çalıştırma mükerrer kayıt üretmez
    assert (count(session, Company), count(session, Filing),
            count(session, FinancialFact)) == counts
    assert counts == (1, 5, sum(len(s) for s in data["facts"].values()))

    loaded = repo.load_company(session, "exmp")
    assert loaded["id"] == company.id and loaded["ticker"] == "EXMP"
    assert loaded["sic_description"] == "Services-Prepackaged Software"
    assert loaded["exchanges"] == ["Nasdaq"]

    facts = repo.load_facts(session, company.id)
    tl = facts["total_liabilities"]
    assert tl[0]["tag"] == "derived"
    assert tl[0]["derived_from"] == data["facts"]["total_liabilities"][0]["derived_from"]
    assert tl[-1]["derived_from"] is None
    assert isinstance(facts["revenue"][0]["value"], float)
    assert [i["period_end"] for i in facts["revenue"]] == sorted(
        i["period_end"] for i in facts["revenue"])
    assert len(facts["revenue"]) == 4  # years+1
    assert list(repo.load_facts(session, company.id, "net_income")) == ["net_income"]


def test_load_filings_order_form_filter_and_limit(session, data):
    company = repo.save_collected(session, data)
    rows = repo.load_filings(session, company.id)
    assert [r["filing_date"] for r in rows] == sorted((r["filing_date"] for r in rows),
                                                      reverse=True)
    assert {r["form"] for r in repo.load_filings(session, company.id, form="10-K")} == {
        "10-K", "10-K/A"}
    assert len(repo.load_filings(session, company.id, limit=2)) == 2
    assert all(r["analyzed"] is False for r in rows)
    assert rows[0]["url"].startswith("https://www.sec.gov/Archives/edgar/data/1/")


def test_list_companies_and_local_search(session, data):
    repo.save_collected(session, data)
    listed = repo.list_companies(session)
    assert len(listed) == 1 and listed[0]["analyzed"] is False and listed[0]["score"] is None
    assert listed[0]["last_filing_date"] == max(f["filing_date"] for f in data["filings"])
    assert repo.search_local(session, "exm")[0]["ticker"] == "EXMP"
    assert repo.search_local(session, "example")[0]["ticker"] == "EXMP"
    assert repo.search_local(session, "1")[0]["cik"] == "0000000001"
    assert repo.search_local(session, "nothing") == []
    assert repo.all_tickers(session) == {"EXMP"}


# --------------------------------------------------------------------------- analiz kayıtları


def test_save_analysis_document_and_reanalysis_replaces_rows(session, data):
    company = repo.save_collected(session, data)
    filing = repo.load_filings(session, company.id, form="10-K")[-1]
    first = analysis()
    saved = repo.save_analysis_document(session, filing["id"], first,
                                        document={"local_path": "/tmp/x.htm", "sha256": "ab",
                                                  "size_bytes": 10})
    assert saved["sections"] == ["1A", "7"]
    n_sent = sum(len(s["sentences"]) for s in first["sections"])
    n_chunks = sum(len(s["chunks"]) for s in first["sections"])
    assert saved["sentences"] == n_sent and saved["chunks"] == n_chunks
    assert count(session, SentimentResult) == n_sent
    assert count(session, ChunkEmbedding) == count(session, TextChunk) == n_chunks
    assert repo.get_filing(session, filing["id"])["analyzed"] is True
    assert repo.is_filing_analyzed(session, filing["id"], "lexicon-fallback",
                                   "hashing-fallback")
    assert not repo.is_filing_analyzed(session, filing["id"], "ProsusAI/finbert",
                                       "hashing-fallback")

    # Yeniden analiz: eski bölüm/parça/gömme/duygu silinir, yenisi yazılır
    second = analysis(chunk_words=60)
    saved2 = repo.save_analysis_document(session, filing["id"], second,
                                         document={"local_path": None, "sha256": "cd",
                                                   "size_bytes": 11})
    n_chunks2 = sum(len(s["chunks"]) for s in second["sections"])
    assert n_chunks2 != n_chunks
    assert count(session, FilingSection) == 2
    assert count(session, TextChunk) == count(session, ChunkEmbedding) == n_chunks2
    assert count(session, SentimentResult) == n_sent == saved2["sentences"]
    assert count(session, FilingDocument) == 1
    doc = session.scalar(select(FilingDocument))
    assert doc.sha256 == "cd" and doc.local_path is None
    assert repo.load_section_text(session, filing["id"], "1A").startswith("Risks Related")
    assert repo.load_section_text(session, filing["id"], "99") is None


def test_load_sentence_predictions_filters_company_and_model(session, data):
    """Cümle önbelleği: yalnızca bu şirketin, bu duygu modeliyle kaydedilmiş sonuçları."""
    company = repo.save_collected(session, data)
    filing = repo.load_filings(session, company.id, form="10-K")[-1]
    result = analysis()
    repo.save_analysis_document(session, filing["id"], result)
    sentences = [x for sec in result["sections"] for x in sec["sentences"]]
    unique = {normalize_sentence(x["sentence"]) for x in sentences}

    cache = repo.load_sentence_predictions(session, company.id, "lexicon-fallback")
    assert set(cache) == unique
    first = sentences[0]
    assert cache[normalize_sentence(first["sentence"])] == {
        "label": first["label"], "score": pytest.approx(first["score"])}
    assert repo.load_sentence_predictions(session, company.id, "ProsusAI/finbert") == {}
    assert repo.load_sentence_predictions(session, company.id + 999, "lexicon-fallback") == {}

    texts = repo.load_company_sentences(session, company.id)
    assert len(texts) == len(unique)
    assert {normalize_sentence(t) for t in texts} == unique


def test_sentiment_sections_and_rag_chunks(session, data):
    company = repo.save_collected(session, data)
    filing = repo.load_filings(session, company.id, form="10-K")[-1]
    result = analysis()
    repo.save_analysis_document(session, filing["id"], result)
    sections = repo.load_sentiment_sections(session, company.id)
    assert [s["item_code"] for s in sections] == ["1A", "7"]
    assert sections[0]["model"] == "lexicon-fallback"
    assert sections[0]["filing"]["accession_no"] == filing["accession_no"]
    assert [x["sentence"] for x in sections[0]["sentences"]] == [
        x["sentence"] for x in result["sections"][0]["sentences"]]

    chunks = repo.load_chunks_for_rag(session, company.id, "hashing-fallback")
    expected = [c for s in result["sections"] for c in s["chunks"]]
    assert len(chunks) == len(expected)
    assert set(chunks[0]) == {"chunk_id", "text", "embedding", "section", "section_title",
                              "form", "filing_date", "accession_no", "url"}
    assert np.allclose(chunks[0]["embedding"], expected[0]["embedding"])
    assert chunks[0]["embedding"].dtype == np.float32
    assert chunks[0]["filing_date"] == filing["filing_date"].isoformat()
    assert chunks[0]["section"] == "1A" and chunks[0]["section_title"] == "Risk Faktörleri"
    # Başka gömme modelinin vektörleri karıştırılmaz
    assert repo.load_chunks_for_rag(session, company.id, "other-model") == []


def test_unanalyzed_filing_sections_are_not_listed(session, data):
    company = repo.save_collected(session, data)
    assert repo.load_sentiment_sections(session, company.id) == []
    assert repo.load_chunks_for_rag(session, company.id, "hashing-fallback") == []
    assert repo.is_company_analyzed(session, company.id) is False


# --------------------------------------------------------------------------- risk, skor, qa


def test_risk_comparison_replace_same_pair_and_load_latest(session, data):
    company = repo.save_collected(session, data)
    old, new = sorted(repo.load_filings(session, company.id, form="10-K"),
                      key=lambda f: f["filing_date"])[:2]
    cmp = compare_risk_sections("Old paragraph about supply chain disruptions here.",
                                "Old paragraph about supply chain disruptions here.\n\n"
                                "New paragraph describing ransomware attacks on plants.")
    repo.save_risk_comparison(session, company.id, old["id"], new["id"], cmp)
    repo.save_risk_comparison(session, company.id, old["id"], new["id"], cmp)
    assert count(session, RiskComparison) == 1
    assert count(session, RiskChange) == len(cmp["changes"])
    risk = repo.load_risk(session, company.id)
    assert risk["old_filing"]["id"] == old["id"] and risk["new_filing"]["id"] == new["id"]
    assert risk["counts"] == cmp["counts"]
    assert [c["change_type"] for c in risk["changes"]] == [c["change_type"]
                                                           for c in cmp["changes"]]
    assert repo.load_risk(session, company.id + 99) is None


def test_score_save_and_load_latest(session, data):
    company = repo.save_collected(session, data)
    from finsight.ratios import compute_ratios

    facts = repo.load_facts(session, company.id)
    score = compute_score(compute_ratios(facts, 3), facts, {})
    repo.save_score(session, company.id, score)
    repo.save_score(session, company.id, {**score, "total": 12.3, "band": "Zayıf"})
    latest = repo.load_latest_score(session, company.id)
    assert latest["total"] == 12.3 and latest["band"] == "Zayıf"
    assert latest["as_of_period"] == score["as_of_period"].isoformat()
    assert count(session, CompanyScore) == 2
    row = session.scalar(select(CompanyScore).order_by(CompanyScore.id.desc()))
    assert row.as_of_period == score["as_of_period"]
    assert repo.load_latest_score(session, company.id + 99) is None


def test_log_qa(session, data):
    company = repo.save_collected(session, data)
    qa_id = repo.log_qa(session, company.id, "Soru?", {
        "answer": "Cevap [1]", "mode": "extractive", "model": "hashing-fallback",
        "latency_ms": 12, "sources": [{"chunk_id": 5}, {"chunk_id": 9}]})
    row = session.get(QALog, qa_id)
    assert row.source_chunk_ids == "[5, 9]" and row.mode == "extractive"


# --------------------------------------------------------------------------- işler


def test_same_cik_tickers_both_resolve_company(session, data):
    """K3: GOOGL/GOOG benzeri — aynı CIK'li iki ticker; ikisi de aynı şirketi bulur, birincil
    ticker değişmez, tickers listesi güncellenir."""
    first = {**data, "company": {**data["company"], "ticker": "EXMP", "tickers": ["EXMP", "EXMQ"]}}
    company = repo.save_collected(session, first)
    second = {**data, "company": {**data["company"], "ticker": "EXMQ", "tickers": ["EXMQ", "EXMP"]}}
    assert repo.save_collected(session, second).id == company.id
    for t in ("EXMP", "exmq"):
        loaded = repo.load_company(session, t)
        assert loaded is not None and loaded["id"] == company.id, t
        assert loaded["ticker"] == "EXMP" and loaded["tickers"] == ["EXMP", "EXMQ"]
    assert repo.load_company(session, "EXM") is None  # JSON içinde tam eşleşme
    assert repo.load_company(session, "EXMQX") is None
    assert repo.all_tickers(session) == {"EXMP", "EXMQ"}
    assert repo.search_local(session, "exmq")[0]["cik"] == company.cik
    assert count(session, Company) == 1


def test_ticker_change_and_requested_ticker_added(session, data):
    """K3: istenen ticker SEC listesinde olmasa da tickers'a eklenir; birincil ticker SEC'in
    güncel listesinde artık yoksa (ticker değişikliği) yenisiyle değiştirilir."""
    legacy_company = {k: v for k, v in data["company"].items() if k != "tickers"}
    legacy = {**data, "company": {**legacy_company, "ticker": "OLDT"}}  # tickers alanı yok
    company = repo.save_collected(session, legacy)
    assert repo.load_company(session, "OLDT")["tickers"] == ["OLDT"]
    renamed = {**data, "company": {**data["company"], "ticker": "NEWT", "tickers": ["NEWT"]}}
    repo.save_collected(session, renamed)
    loaded = repo.load_company(session, "NEWT")
    assert loaded["id"] == company.id and loaded["ticker"] == "NEWT"
    assert loaded["tickers"] == ["NEWT", "OLDT"]
    assert repo.load_company(session, "OLDT")["id"] == company.id  # eski ticker hâlâ bulunur


def test_running_job_matches_other_ticker_of_same_company(session, data):
    """K3: GOOGL için çalışan iş varken GOOG isteği aynı işi bulur (şirket / CIK eşleşmesi)."""
    repo.save_collected(session, {**data, "company": {**data["company"], "ticker": "EXMP",
                                                      "tickers": ["EXMP", "EXMQ"]}})
    job = repo.create_job(session, "EXMP")
    assert repo.find_running_job(session, "EXMQ")["id"] == job["id"]
    assert repo.find_running_job(session, "OTHR") is None
    # Şirket henüz veritabanında yokken SEC listesinden gelen takma adlarla eşleşme
    other = repo.create_job(session, "NEWA")
    assert repo.find_running_job(session, "NEWB", aliases=["NEWB", "NEWA"])["id"] == other["id"]
    assert repo.job_tickers(session, "exmq") == ["EXMQ", "EXMP"]


def test_count_chunks_other_models(session, data):
    """K4: başka gömme modeliyle oluşturulmuş parça sayısı."""
    company = repo.save_collected(session, data)
    filing = repo.load_filings(session, company.id, form="10-K")[0]
    repo.save_analysis_document(session, filing["id"], analysis())
    n = count(session, ChunkEmbedding)
    assert n > 0
    assert repo.count_chunks_other_models(session, company.id, "hashing-fallback") == 0
    assert repo.count_chunks_other_models(session, company.id, "other-model") == n
    assert repo.load_chunks_for_rag(session, company.id, "other-model") == []
    assert repo.count_chunks_other_models(session, company.id + 99, "other-model") == 0


def test_job_lifecycle_and_running_lookup(session):
    job = repo.create_job(session, "msft", analyze=True)
    assert job["ticker"] == "MSFT" and job["status"] == "queued" and job["progress"] == 0.0
    assert repo.find_running_job(session, "MSFT")["id"] == job["id"]
    updated = repo.update_job(session, job["id"], status="running", step="Gömme",
                              progress=1.7, request_count=5)
    assert updated["progress"] == 1.0 and updated["started_at"] is not None
    assert repo.update_job(session, job["id"], progress=None)["progress"] == 1.0
    repo.add_job_warning(session, job["id"], "10-Q: belge yok")
    assert repo.get_job(session, job["id"])["warnings"] == ["10-Q: belge yok"]
    with pytest.raises(ValueError):
        repo.update_job(session, job["id"], ticker="X")
    repo.update_job(session, job["id"], status="done", finished_at=utcnow())
    assert repo.find_running_job(session, "MSFT") is None
    assert repo.get_job(session, 999) is None and repo.update_job(session, 999) is None


def test_stale_running_job_is_closed(session):
    job = repo.create_job(session, "EXMP")
    repo.update_job(session, job["id"], status="running")
    later = utcnow() + repo.STALE_JOB_AFTER + timedelta(minutes=1)
    assert repo.find_running_job(session, "EXMP", now=later) is None
    stale = repo.get_job(session, job["id"])
    assert stale["status"] == "error" and stale["error"] == repo.STALE_JOB_ERROR
    assert count(session, IngestionJob) == 1


# --------------------------------------------------------------------------- şema yükseltme

OLD_SCHEMA = """
CREATE TABLE company (id INTEGER NOT NULL, cik VARCHAR(10) NOT NULL, ticker VARCHAR(16),
  name VARCHAR(255), sic VARCHAR(8), fiscal_year_end VARCHAR(4),
  updated_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, PRIMARY KEY (id));
CREATE UNIQUE INDEX ix_company_cik ON company (cik);
CREATE TABLE filing (id INTEGER NOT NULL, company_id INTEGER NOT NULL,
  accession_no VARCHAR(25) NOT NULL, form VARCHAR(12) NOT NULL, is_amendment BOOLEAN NOT NULL,
  filing_date DATE NOT NULL, report_date DATE, primary_document VARCHAR(255),
  items VARCHAR(255), is_xbrl BOOLEAN NOT NULL, url VARCHAR(512), PRIMARY KEY (id),
  UNIQUE (accession_no), FOREIGN KEY(company_id) REFERENCES company (id));
CREATE TABLE financial_fact (id INTEGER NOT NULL, company_id INTEGER NOT NULL,
  metric VARCHAR(64) NOT NULL, tag VARCHAR(255) NOT NULL, unit VARCHAR(16) NOT NULL,
  value NUMERIC(24, 2) NOT NULL, period_start DATE, period_end DATE NOT NULL, fy BIGINT,
  fp VARCHAR(4), form VARCHAR(12) NOT NULL, accession_no VARCHAR(25) NOT NULL,
  filed DATE NOT NULL, PRIMARY KEY (id),
  CONSTRAINT uq_fact_period UNIQUE (company_id, metric, period_end),
  FOREIGN KEY(company_id) REFERENCES company (id));
INSERT INTO company (id, cik, ticker, name, sic, fiscal_year_end)
  VALUES (1, '0000000001', 'EXMP', 'Example Corp', '7372', '1231');
INSERT INTO filing (id, company_id, accession_no, form, is_amendment, filing_date, is_xbrl)
  VALUES (1, 1, '0000000001-24-000001', '10-K', 0, '2024-02-01', 1);
INSERT INTO financial_fact (company_id, metric, tag, unit, value, period_end, form,
  accession_no, filed)
  VALUES (1, 'revenue', 'Revenues', 'USD', 1000, '2023-12-31', '10-K',
  '0000000001-24-000001', '2024-02-01');
"""


def test_init_db_adds_missing_columns_to_old_sqlite_file(tmp_path, data):
    path = tmp_path / "finsight.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(OLD_SCHEMA)

    engine = make_engine(f"sqlite:///{path}")
    added = init_db(engine)
    assert set(added) == {"company.sic_description", "company.exchanges",
                          "company.analysis_updated_at", "company.tickers", "filing.analyzed",
                          "financial_fact.derived_from", "financial_fact.basis"}
    insp = inspect(engine)
    assert {"filing_section", "text_chunk", "chunk_embedding", "sentiment_result",
            "risk_comparison", "risk_change", "company_score", "qa_log", "ingestion_job",
            "filing_document"} <= set(insp.get_table_names())
    assert "analyzed" in {c["name"] for c in insp.get_columns("filing")}
    assert init_db(engine) == []  # ikinci çalıştırma bir şey eklemez

    factory = make_session_factory(engine)
    with factory() as s:
        company = repo.load_company(s, "EXMP")
        assert company["exchanges"] == [] and company["sic_description"] is None
        old_filing = repo.load_filings(s, company["id"])[0]
        assert old_filing["analyzed"] is False  # NULL → analiz edilmedi
        assert repo.load_facts(s, company["id"])["revenue"][0]["derived_from"] is None
        # Eski veritabanına yeni sürüm normal yazabilir
        repo.save_collected(s, data)
        assert repo.load_company(s, "EXMP")["exchanges"] == ["Nasdaq"]
        assert s.scalar(select(func.count()).select_from(Company)) == 1
    engine.dispose()


def test_upgrade_skips_not_null_columns(tmp_path, caplog):
    """NOT NULL bir sütun eksikse eklenmez (uyarı); diğerleri eklenir."""
    path = tmp_path / "partial.db"
    with sqlite3.connect(path) as conn:
        # ingestion_job'un yalnızca kimlik sütunu olan eski bir sürümü
        conn.executescript("CREATE TABLE ingestion_job (id INTEGER PRIMARY KEY);")
    engine = make_engine(f"sqlite:///{path}")
    added = init_db(engine)
    assert "ingestion_job.ticker" not in added
    assert "ingestion_job.step" in added and "ingestion_job.updated_at" in added
    assert "Eksik NOT NULL" in caplog.text
    # O3: skaler varsayılanlı NOT NULL sütunlar DEFAULT ile eklenir
    assert {"ingestion_job.status", "ingestion_job.progress", "ingestion_job.request_count",
            "ingestion_job.analyze"} <= set(added)
    # çağrılabilir varsayılan (utcnow) NOT NULL değil → normal NULL olabilir sütun
    engine.dispose()


def test_upgrade_adds_not_null_columns_with_scalar_default(tmp_path):
    """O3: eski şemalı SQLite'ta NOT NULL + skaler varsayılanlı sütunlar DEFAULT ile eklenir;
    mevcut satırlar varsayılanı alır, yeni ORM yazımları çalışır."""
    path = tmp_path / "old_jobs.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE ingestion_job (id INTEGER PRIMARY KEY, ticker VARCHAR(16) NOT NULL,
              status VARCHAR(16) NOT NULL, step VARCHAR(255), progress FLOAT NOT NULL,
              error TEXT, request_count INTEGER NOT NULL, created_at DATETIME NOT NULL,
              started_at DATETIME, finished_at DATETIME);
            INSERT INTO ingestion_job (id, ticker, status, progress, request_count, created_at)
              VALUES (1, 'EXMP', 'done', 1.0, 3, '2026-01-01 00:00:00');
            CREATE TABLE risk_change (id INTEGER PRIMARY KEY, comparison_id INTEGER NOT NULL,
              change_type VARCHAR(16) NOT NULL, old_text TEXT, new_text TEXT, similarity FLOAT);
            INSERT INTO risk_change (id, comparison_id, change_type) VALUES (1, 1, 'added');
        """)
    engine = make_engine(f"sqlite:///{path}")
    added = init_db(engine)
    assert {"ingestion_job.analyze", "ingestion_job.warnings", "ingestion_job.updated_at",
            "risk_change.position"} <= set(added)
    cols = {c["name"]: c for c in inspect(engine).get_columns("ingestion_job")}
    assert cols["analyze"]["nullable"] is False
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT "analyze" FROM ingestion_job WHERE id = 1').fetchone() == (1,)
        assert conn.execute("SELECT position FROM risk_change WHERE id = 1").fetchone() == (0,)
        ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'risk_change'").fetchone()
        assert "NOT NULL DEFAULT 0" in ddl[0]
    factory = make_session_factory(engine)
    with factory() as s:
        assert repo.get_job(s, 1)["analyze"] is True
        job = repo.create_job(s, "EXMP", analyze=False)
        assert repo.get_job(s, job["id"])["analyze"] is False
    assert init_db(engine) == []
    engine.dispose()


def test_today_independent_fixture_dates():
    """integration_support tarihleri bugüne göre üretir (collect süzgeci için)."""
    from integration_support import build_submissions

    sub = build_submissions(date(2030, 1, 1))
    dates = sub["filings"]["recent"]["filingDate"]
    assert all(d.startswith(("2028", "2029")) for d in dates)
