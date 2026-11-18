"""jobs.py testleri: arka plan işi uçtan uca, atlama, hata dayanıklılığı, model önbelleği.

SQLAlchemy gerekir (yoksa atlanır). Ağa çıkılmaz; modeller sözlük + hashing, LLM kapalı.
FR-15 (arka plan işi), NFR-02 (paylaşılan hız sınırı), NFR-10 (gizli bilgi sızdırmama).
"""

import pytest

pytest.importorskip("sqlalchemy")

from integration_support import (ACC_K_NEW, ACC_K_OLD, ACC_Q, ADDED_RISK_PREFIX,  # noqa: E402
                                 TICKER, FakeSec, build_routes, doc_url,
                                 memory_session_factory, use_light_models)
from sqlalchemy import func, select  # noqa: E402

from finsight import jobs  # noqa: E402
from finsight import repository as repo  # noqa: E402
from finsight.config import ConfigError  # noqa: E402
from finsight.embeddings import HashingEmbedder  # noqa: E402
from finsight.models import (ChunkEmbedding, CompanyScore, Filing, FilingDocument,  # noqa: E402
                             FilingSection, FinancialFact, RiskComparison, SentimentResult,
                             TextChunk)
from finsight.sec_client import SecError  # noqa: E402


@pytest.fixture(autouse=True)
def light_models(monkeypatch, tmp_path):
    monkeypatch.setenv("FINSIGHT_DOCUMENTS_DIR", str(tmp_path / "docs"))
    use_light_models()
    yield
    jobs.reset_models()


@pytest.fixture
def factory():
    return memory_session_factory()


def count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def run(factory, sec, ticker=TICKER, analyze=True) -> dict:
    with factory() as s:
        job = repo.create_job(s, ticker, analyze)
    jobs.run_ingest_job(job["id"], ticker, analyze, session_factory=factory,
                        client_factory=sec)
    with factory() as s:
        return repo.get_job(s, job["id"])


def test_ingest_job_end_to_end_steps_progress_and_results(factory, monkeypatch, tmp_path):
    steps: list[tuple[str | None, float | None]] = []
    original = repo.update_job

    def recording(session, job_id, **fields):
        steps.append((fields.get("step"), fields.get("progress")))
        return original(session, job_id, **fields)

    monkeypatch.setattr(repo, "update_job", recording)
    sec = FakeSec()
    job = run(factory, sec)

    assert job["status"] == "done", job
    assert job["progress"] == 1.0 and job["error"] is None and job["warnings"] == []
    assert job["started_at"] is not None and job["finished_at"] is not None
    # tickers + submissions + companyfacts + 3 belge (10-K/A ve 8-K indirilmez)
    assert job["request_count"] == len(sec.urls()) == 6
    assert sorted(sec.document_calls()) == sorted([doc_url(ACC_K_NEW), doc_url(ACC_K_OLD),
                                                   doc_url(ACC_Q)])

    names = [s for s, _ in steps if s]
    assert names[0] == "SEC verisi çekiliyor"
    assert names[1] == "Finansal veriler kaydediliyor"
    # O2: model yükleme adımı ve bitişi işte görünür
    assert names[2] == "Modeller yükleniyor"
    assert names[3] == "Modeller hazır (lexicon-fallback, hashing-fallback)"
    for prefix in ("Belge indiriliyor (10-K ", "Bölümler ve duygu analizi (10-K ",
                   "Gömme (10-K ", "Belge indiriliyor (10-Q "):
        assert any(n.startswith(prefix) for n in names), prefix
    assert names.index("Risk karşılaştırması") < names.index("Skor hesaplanıyor")
    assert names[-1] == "Tamamlandı"
    progress = [p for _, p in steps if p is not None]
    assert progress == sorted(progress) and progress[-1] == 1.0

    with factory() as s:
        company = repo.load_company(s, TICKER)
        assert company["analysis_updated_at"] is not None
        filings = {f["accession_no"]: f for f in repo.load_filings(s, company["id"])}
        assert {a for a, f in filings.items() if f["analyzed"]} == {ACC_K_NEW, ACC_K_OLD, ACC_Q}
        codes = sorted((f.accession_no, sec_.item_code) for sec_, f in s.execute(
            select(FilingSection, Filing).join(Filing)).tuples())
        assert codes == sorted([(ACC_K_NEW, "1A"), (ACC_K_NEW, "7"), (ACC_K_OLD, "1A"),
                                (ACC_K_OLD, "7"), (ACC_Q, "1A"), (ACC_Q, "2")])
        assert count(s, SentimentResult) > 0
        assert count(s, ChunkEmbedding) == count(s, TextChunk) > 0
        risk = repo.load_risk(s, company["id"])
        assert risk["old_filing"]["accession_no"] == ACC_K_OLD
        assert risk["new_filing"]["accession_no"] == ACC_K_NEW
        assert risk["counts"]["added"] >= 1
        assert any((c["new_text"] or "").startswith(ADDED_RISK_PREFIX)
                   for c in risk["changes"] if c["change_type"] == "added")
        score = repo.load_latest_score(s, company["id"])
        assert score["total"] is not None
        text_tone = next(c for c in score["components"] if c["key"] == "text_tone")
        assert text_tone["available"] is True
        docs = s.scalars(select(FilingDocument)).all()
        assert len(docs) == 3 and all(d.sha256 and d.size_bytes for d in docs)
        assert all(d.local_path and d.local_path.startswith(str(tmp_path)) for d in docs)


def test_second_run_skips_analyzed_filings_and_creates_no_duplicates(factory):
    sec = FakeSec()
    run(factory, sec)
    with factory() as s:
        before = {m: count(s, m) for m in (Filing, FinancialFact, FilingSection, TextChunk,
                                           SentimentResult, RiskComparison)}
    n_docs = len(sec.document_calls())
    job = run(factory, sec)
    assert job["status"] == "done"
    assert len(sec.document_calls()) == n_docs  # belgeler yeniden indirilmedi
    with factory() as s:
        after = {m: count(s, m) for m in before}
        assert after == before
        assert count(s, CompanyScore) == 2  # skor geçmişi tutulur


def test_model_change_triggers_reanalysis(factory):
    sec = FakeSec()
    run(factory, sec)

    class OtherEmbedder(HashingEmbedder):
        name = "other-embedder"

    jobs.override_models(embedder=OtherEmbedder(dim=128))
    n_docs = len(sec.document_calls())
    assert run(factory, sec)["status"] == "done"
    assert len(sec.document_calls()) == n_docs + 3
    with factory() as s:
        models = set(s.scalars(select(ChunkEmbedding.model_name).distinct()))
        assert models == {"other-embedder"}  # eski vektörler silindi
        dims = set(s.scalars(select(ChunkEmbedding.dim).distinct()))
        assert dims == {128}


def test_failing_document_does_not_stop_job(factory):
    sec = FakeSec(build_routes(missing_docs=(ACC_Q,)))
    job = run(factory, sec)
    assert job["status"] == "done"
    assert len(job["warnings"]) == 1 and job["warnings"][0].startswith("10-Q ")
    assert "404" in job["warnings"][0]
    with factory() as s:
        company = repo.load_company(s, TICKER)
        analyzed = {f["form"] for f in repo.load_filings(s, company["id"]) if f["analyzed"]}
        assert analyzed == {"10-K"}
        assert repo.load_risk(s, company["id"]) is not None


def test_all_documents_missing_writes_no_score(factory):
    """K2: hiçbir başvuru analiz edilemezse (tüm belgeler 404) skor ve analysis_updated_at
    yazılmaz, risk karşılaştırması yapılmaz; iş uyarıyla tamamlanır."""
    sec = FakeSec(build_routes(missing_docs=(ACC_K_NEW, ACC_K_OLD, ACC_Q)))
    job = run(factory, sec)
    assert job["status"] == "done"
    assert len(job["warnings"]) == 4
    assert all("404" in w for w in job["warnings"][:3])
    assert job["warnings"][-1].startswith("Hiçbir başvuru analiz edilemedi")
    with factory() as s:
        company = repo.load_company(s, TICKER)
        assert company["analysis_updated_at"] is None
        assert repo.load_latest_score(s, company["id"]) is None
        assert repo.load_risk(s, company["id"]) is None
        assert count(s, FinancialFact) > 0  # finansal veriler yine kaydedildi
        summary = jobs.run_analysis_for_company(s, sec(), company["id"])
    assert summary["score"] is None and summary["risk"] is None
    assert [f["status"] for f in summary["filings"]] == ["error"] * 3


def test_previously_analyzed_company_keeps_score_when_all_documents_fail(factory):
    """K2: önceden analiz edilmiş başvurular 'atlandı' sayılır; skor yine güncellenir. Model
    değişip tüm belgeler indirilemezse yeni skor yazılmaz (eski skor kalır)."""
    run(factory, FakeSec())
    with factory() as s:
        company = repo.load_company(s, TICKER)
        n_scores = count(s, CompanyScore)
        updated = company["analysis_updated_at"]

    class OtherEmbedder(HashingEmbedder):
        name = "other-embedder"

    jobs.override_models(embedder=OtherEmbedder())
    job = run(factory, FakeSec(build_routes(missing_docs=(ACC_K_NEW, ACC_K_OLD, ACC_Q))))
    assert job["status"] == "done"
    assert job["warnings"][-1].startswith("Hiçbir başvuru analiz edilemedi")
    with factory() as s:
        assert count(s, CompanyScore) == n_scores
        assert repo.load_company(s, TICKER)["analysis_updated_at"] == updated


def test_unknown_ticker_marks_job_error(factory):
    job = run(factory, FakeSec(), ticker="ZZZZ")
    assert job["status"] == "error"
    assert "ZZZZ" in job["error"] and job["finished_at"] is not None


def test_missing_user_agent_marks_job_error(factory, monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with factory() as s:
        job = repo.create_job(s, TICKER)
    jobs.run_ingest_job(job["id"], TICKER, True, session_factory=factory,
                        client_factory=jobs.make_sec_client)
    with factory() as s:
        failed = repo.get_job(s, job["id"])
    assert failed["status"] == "error" and failed["error"].startswith("Yapılandırma hatası")


def test_analyze_false_saves_financials_without_text_analysis(factory):
    job = run(factory, FakeSec(), analyze=False)
    assert job["status"] == "done" and job["request_count"] == 3
    with factory() as s:
        company = repo.load_company(s, TICKER)
        assert count(s, FilingSection) == 0
        assert count(s, FinancialFact) > 0
        # sözleşme: analiz yoksa skor null
        assert repo.load_latest_score(s, company["id"]) is None


def test_run_analysis_for_company_summary(factory, tmp_path):
    sec = FakeSec()
    run(factory, sec, analyze=False)
    with factory() as s:
        company = repo.load_company(s, TICKER)
        summary = jobs.run_analysis_for_company(s, sec(), company["id"],
                                                documents_dir=str(tmp_path / "d"))
    assert summary["sentiment_model"] == "lexicon-fallback"
    assert summary["embedding_model"] == "hashing-fallback"
    assert summary["llm"] is None
    assert [f["status"] for f in summary["filings"]] == ["analyzed"] * 3
    assert summary["risk"]["added"] >= 1 and summary["score"]["total"] is not None
    assert (tmp_path / "d").exists()


# --------------------------------------------------------------------------- yardımcılar


def test_safe_error_hides_secrets(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-123")
    assert jobs.safe_error(ValueError("bad key sk-ant-secret-123")) == "bad key ***"
    assert jobs.safe_error(RuntimeError("sk-ant-secret-123")) == \
        "Beklenmeyen hata (RuntimeError)"
    assert jobs.safe_error(KeyError("Şirket bulunamadı: ZZZZ")) == "Şirket bulunamadı: ZZZZ"
    assert jobs.safe_error(ConfigError("UA yok")).startswith("Yapılandırma hatası")
    assert jobs.safe_error(SecError("SEC isteği başarısız (404): u")).endswith("u")


def test_store_document_write_failure_keeps_hash(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    meta = jobs.store_document(b"<html/>", "0000000001", "0000000001-26-000010", "a/b.htm",
                               documents_dir=str(blocker))
    assert meta["local_path"] is None and meta["size_bytes"] == 7 and len(meta["sha256"]) == 64
    ok = jobs.store_document(b"<html/>", "1", "0000000001-26-000010", "../x.htm",
                             documents_dir=str(tmp_path / "docs"))
    assert ok["local_path"].endswith("000000000126000010/x.htm")  # yol dışına çıkılmaz


def test_models_are_loaded_once_per_process(monkeypatch):
    jobs.reset_models()
    calls = {"sentiment": 0, "embedder": 0, "llm": 0}

    def fake_sentiment():
        calls["sentiment"] += 1
        from finsight.sentiment import LexiconModel
        return LexiconModel(dictionary_path="")

    def fake_embedder():
        calls["embedder"] += 1
        return HashingEmbedder()

    def fake_llm():
        calls["llm"] += 1
        return None

    monkeypatch.setattr(jobs, "get_sentiment_model", fake_sentiment)
    monkeypatch.setattr(jobs, "get_embedder", fake_embedder)
    monkeypatch.setattr(jobs, "get_llm_client", fake_llm)
    for _ in range(3):
        jobs.get_sentiment()
        jobs.get_embedder_cached()
        assert jobs.get_llm() is None
    assert calls == {"sentiment": 1, "embedder": 1, "llm": 1}


def test_model_status_does_not_load_models(monkeypatch):
    jobs.reset_models()
    monkeypatch.setenv("FINSIGHT_SENTIMENT_MODEL", "lexicon")
    monkeypatch.setenv("FINSIGHT_EMBEDDER", "hashing")
    monkeypatch.setenv("FINSIGHT_LLM_PROVIDER", "none")
    monkeypatch.delenv("LM_DICTIONARY_PATH", raising=False)
    status = jobs.model_status()
    assert status["sentiment"] == "lexicon-fallback"
    assert status["embedding"] == "hashing-fallback"
    assert status["llm"] == {"provider": "none", "model": None, "enabled": False}
    assert status["loaded"] == {"sentiment": False, "embedding": False}
    assert jobs._cache.sentiment is None and jobs._cache.embedder is None


def test_shared_limiter_is_process_wide(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "FinSight-AI Test test@example.com")
    a, b = jobs.make_sec_client(), jobs.make_sec_client()
    assert a.limiter is b.limiter is jobs.shared_limiter()


def test_ingest_cli_save_error_does_not_stop_other_companies(monkeypatch, tmp_path, capsys):
    """O13: bir şirketin veritabanı yazım hatası diğer şirketlerin işlenmesini durdurmaz."""
    from finsight import ingest
    from finsight.rate_limiter import RateLimiter
    from finsight.sec_client import SecClient

    sec = FakeSec()
    monkeypatch.setenv("SEC_USER_AGENT", "FinSight-AI Test test@example.com")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    monkeypatch.setenv("SEC_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(ingest, "SecClient", lambda ua, **_kw: SecClient(
        ua, fetcher=sec.fetcher, limiter=RateLimiter(8, clock=sec.clock, sleep=sec.clock.sleep),
        sleep=lambda _s: None))
    real_save = ingest.save
    calls = {"n": 0}

    def flaky_save(session, data):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("veritabanı kilitli")
        return real_save(session, data)

    monkeypatch.setattr(ingest, "save", flaky_save)
    assert ingest.main([TICKER, TICKER]) == 1  # bir başarısız → çıkış kodu 1
    assert calls["n"] == 2
    out = capsys.readouterr().out
    assert "Example Corp" in out  # ikinci deneme işlendi ve özet yazıldı
