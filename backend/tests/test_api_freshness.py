"""Aşama 4 API testleri: /freshness, /health v1.4, TTM'li /ratios-/score, atomik iş açma.

FastAPI + SQLAlchemy gerekir (bulutta atlanır; kullanıcı bilgisayarında çalıştırılır).
docs/asama4-sozlesme.md §4–6, güvenlik SEC-16 (a)(b)(c).
"""

import json
import os
import threading
from datetime import date

import pytest
from conftest import load

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient  # noqa: E402
from integration_support import (CIK, FACTS_URL, SUBMISSIONS_URL, TICKER,  # noqa: E402
                                 FakeSec, build_routes, build_submissions,
                                 memory_session_factory, use_light_models)

from finsight import api, jobs  # noqa: E402
from finsight import repository as repo  # noqa: E402
from finsight.edgar import parse_filings  # noqa: E402
from finsight.xbrl import extract_all, extract_ttm  # noqa: E402

TODAY = date(2026, 11, 23)


@pytest.fixture(autouse=True)
def light_models(monkeypatch, tmp_path):
    monkeypatch.setenv("FINSIGHT_DOCUMENTS_DIR", str(tmp_path / "docs"))
    use_light_models()
    yield
    jobs.reset_models()


@pytest.fixture
def factory():
    return memory_session_factory()


def seed(factory, companyfacts="companyfacts_full_example.json"):
    """EXMP şirketini SEC isteği olmadan veritabanına yazar (TTM noktalarıyla)."""
    cf = load(companyfacts)
    sub = build_submissions(TODAY)
    annual = extract_all(cf, years=0)
    data = {"company": {"cik": CIK, "ticker": TICKER, "name": "Example Corp",
                        "sic": "7372", "fiscal_year_end": "0630"},
            "filings": parse_filings(sub["filings"]["recent"]),
            "facts": {m: s[-4:] for m, s in annual.items()},
            "ttm": extract_ttm(cf, annual)}
    with factory() as s:
        return repo.save_collected(s, data).id


def make_client(factory, routes=None):
    sec = FakeSec(routes if routes is not None else build_routes(TODAY))
    app = api.create_app(session_factory=factory, client_factory=sec, serve_frontend=False,
                         auto_refresh_minutes=0)
    return TestClient(app, base_url="http://127.0.0.1:8000"), sec  # SEC-03 Host denetimi


def test_freshness_unknown_company_404_without_sec_request(factory):
    client, sec = make_client(factory)
    with client:
        r = client.get("/api/companies/NOPE/freshness")
        assert r.status_code == 404
        assert client.get("/api/companies/..bad../freshness").status_code in (400, 404)
    assert sec.urls() == []  # SEC-16 (a)


def test_freshness_up_to_date_and_cached(factory):
    seed(factory)
    client, sec = make_client(factory)
    with client:
        bodies = [client.get(f"/api/companies/{TICKER}/freshness").json() for _ in range(20)]
    body = bodies[0]
    assert body["stale"] is False and body["error"] is None
    assert body["sec_latest"]["form"] in ("10-Q", "10-K", "10-K/A")
    assert "disclaimer" in body
    # SEC-16 (b): 15 dk içinde 20 çağrı → submissions'a tek istek, önbelleği atlayarak
    assert sec.urls().count(SUBMISSIONS_URL) == 1


def test_freshness_detects_new_10q(factory):
    seed(factory)
    sub = build_submissions(TODAY)
    recent = sub["filings"]["recent"]
    new = {"accessionNumber": "0000000001-26-000099", "filingDate": "2026-11-20",
           "reportDate": "2026-09-30", "form": "10-Q", "primaryDocument": "q.htm",
           "items": "", "isXBRL": 1}
    for k, v in new.items():
        recent[k].insert(0, v)
    routes = build_routes(TODAY)
    routes[SUBMISSIONS_URL] = (200, json.dumps(sub).encode())
    client, _ = make_client(factory, routes)
    with client:
        body = client.get(f"/api/companies/{TICKER}/freshness").json()
    assert body["stale"] is True
    assert body["new_filings"][0]["accession_no"] == "0000000001-26-000099"


def test_freshness_sec_error_is_200_with_reason_and_cached(factory):
    seed(factory)
    routes = build_routes(TODAY)
    routes[SUBMISSIONS_URL] = (503, b"")
    client, sec = make_client(factory, routes)
    with client:
        r = client.get(f"/api/companies/{TICKER}/freshness")
        client.get(f"/api/companies/{TICKER}/freshness")
    assert r.status_code == 200
    body = r.json()
    assert body["stale"] is None and "503" in body["error"]
    assert sec.urls().count(SUBMISSIONS_URL) == 1  # hata da önbellekte, yeniden deneme yok


def test_health_v14_fields(factory):
    client, _ = make_client(factory)
    with client:
        body = client.get("/api/health").json()
    assert body["sec"]["json_ttl_seconds"] == 3600 and body["auto_refresh_minutes"] == 0


def test_ratios_and_overview_include_ttm(factory):
    seed(factory, "companyfacts_ttm_example.json")
    client, _ = make_client(factory)
    with client:
        ratios = client.get(f"/api/companies/{TICKER}/ratios").json()
        overview = client.get(f"/api/companies/{TICKER}/overview").json()
        facts = client.get(f"/api/companies/{TICKER}/facts?metric=revenue").json()
    assert ratios["periods"][-1] == "2026-12-31"
    assert ratios["period_info"][-1]["label"] == "TTM 2026-12-31"
    rev = overview["key_metrics"][0]
    assert rev["ttm"]["value"] == 1290 and rev["value"] == 1200
    # /facts yıllık tabloyu göstermeye devam eder (TTM noktası yok)
    ends = [v["period_end"] for v in facts["metrics"][0]["values"]]
    assert "2026-12-31" not in ends and ends[-1] == "2026-06-30"


def test_score_after_ttm_save_uses_ttm(factory):
    cid = seed(factory, "companyfacts_ttm_example.json")
    with factory() as s:
        score = jobs.compute_and_save_score(s, cid)
    assert score["basis"] == "ttm" and score["method_version"] == "v2-2026-11-ttm"
    assert str(score["as_of_period"]) == "2026-12-31"


def test_stale_ttm_rows_removed_when_new_10k_arrives(factory):
    cid = seed(factory, "companyfacts_ttm_example.json")
    with factory() as s:
        assert repo.load_ttm_facts(s, cid)
        # Yeni veri: TTM yok (ör. yeni 10-K geldi) → eski TTM satırları silinir
        repo.save_collected(s, {"company": {"cik": CIK, "ticker": TICKER, "name": "Example Corp"},
                                "filings": [], "facts": {}, "ttm": {"facts": {}}})
        assert repo.load_ttm_facts(s, cid) == {}
        assert repo.load_dynamic(s, cid)["basis"] == "annual"


def _file_factory(path):
    from finsight.db import init_db, make_engine, make_session_factory

    engine = make_engine(f"sqlite:///{path.as_posix()}")
    init_db(engine)
    return make_session_factory(engine), engine


def test_create_job_if_idle_is_atomic_across_engines(tmp_path):
    """SEC-16 (c) / inceleme madde 10: aynı dosya SQLite'a İKİ ayrı engine (iki süreç gibi)
    aynı anda iş açmaya çalışır → yalnızca bir etkin iş."""
    path = tmp_path / "jobs.db"
    f1, e1 = _file_factory(path)
    f2, e2 = _file_factory(path)
    seed(f1)
    results, errors = [], []
    barrier = threading.Barrier(2)

    def worker(factory):
        with factory() as s:
            barrier.wait()
            try:
                results.append(repo.create_job_if_idle(s, TICKER))
            except repo.JobBusyError as exc:
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(f,)) for f in (f1, f2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    created = [job for job, new in results if new]
    assert len(created) == 1
    with f2() as s:
        running = repo.find_running_job(s, TICKER)
        assert running["id"] == created[0]["id"]
    e1.dispose()
    e2.dispose()


def test_update_job_error_releases_lock(factory):
    """Madde 13: iş error ile bitince kilit kalkar; yeni iş açılabilir."""
    seed(factory)
    with factory() as s:
        job, new = repo.create_job_if_idle(s, TICKER)
        assert new
        assert repo.create_job_if_idle(s, TICKER) == (repo.get_job(s, job["id"]), False)
        repo.update_job(s, job["id"], status="error", error="test")
        job2, new2 = repo.create_job_if_idle(s, TICKER)
        assert new2 and job2["id"] != job["id"]


def test_dead_job_lock_is_cleaned(factory):
    """Madde 13: ilerleme yazmayan (ölü) iş error'a çekilir ve kilidi temizlenir."""
    from datetime import timedelta

    from finsight.models import utcnow

    seed(factory)
    with factory() as s:
        job, _ = repo.create_job_if_idle(s, TICKER)
        later = utcnow() + repo.STALE_JOB_AFTER + timedelta(minutes=1)
        job2, new = repo.create_job_if_idle(s, TICKER, now=later)
        assert new and job2["id"] != job["id"]
        assert repo.get_job(s, job["id"])["status"] == "error"


def test_job_lock_key_uses_cik(factory):
    """Madde 11: CIK biliniyorsa (parametre / CIK girdisi / kayıtlı şirket) cik: anahtarı."""
    with factory() as s:
        assert repo.job_lock_key(s, "NEWCO", cik="2") == "cik:0000000002"
        assert repo.job_lock_key(s, "0000000002") == "cik:0000000002"
        assert repo.job_lock_key(s, "NEWCO") == "t:NEWCO"
    seed(factory)
    with factory() as s:
        assert repo.job_lock_key(s, TICKER) == f"cik:{CIK}"


def test_ttm_reasons_persisted_for_score_and_overview(factory):
    """Madde 3: TTM neden metinleri veritabanında; /score.basis_notes ve ttm_reason belirli."""
    cid = seed(factory, "companyfacts_ttm_example.json")
    with factory() as s:
        dyn = repo.load_dynamic(s, cid)
        assert "önceki yılın aynı YTD dönemi yok" in dyn["reasons"]["operating_cash_flow"]
        score = jobs.compute_and_save_score(s, cid)
    assert any("önceki yılın aynı YTD dönemi yok" in n for n in score["basis_notes"])
    client, _ = make_client(factory)
    with client:
        ov = client.get(f"/api/companies/{TICKER}/overview").json()
    ocf = next(m for m in ov["key_metrics"] if m["metric"] == "operating_cash_flow")
    assert "önceki yılın aynı YTD dönemi yok" in ocf["ttm_reason"]


def test_load_filings_marks_in_ttm(factory):
    cid = seed(factory, "companyfacts_ttm_example.json")
    with factory() as s:
        flags = {f["accession_no"]: f["in_ttm"] for f in repo.load_filings(s, cid)}
    assert flags and not any(flags.values())  # EXMP başvuruları TTM fixture'ında yok


def test_api_ingest_passes_fresh_to_collect(factory, monkeypatch):
    """Madde 13: POST /ingest → run_ingest_job(fresh=True) → collect(fresh=True)."""
    seen = []
    real_collect = jobs.collect

    def spy(*args, **kwargs):
        seen.append(kwargs.get("fresh"))
        return real_collect(*args, **kwargs)

    monkeypatch.setattr(jobs, "collect", spy)
    client, _ = make_client(factory)
    with client:
        r = client.post(f"/api/companies/{TICKER}/ingest", json={"analyze": False})
        assert r.status_code == 202
    assert seen == [True]


def test_refresh_main_end_to_end_with_file_db(tmp_path, monkeypatch, capsys):
    """Madde 13: python -m finsight.refresh --all — dosya DB + sahte SEC; yalnızca eskimiş
    şirket güncellenir, çıkış kodu 0, DB yolu yazdırılır."""
    from finsight import refresh
    from integration_support import ACC_K_AMEND, ACC_Q

    path = tmp_path / "refresh.db"
    factory, engine = _file_factory(path)
    cf = load("companyfacts_full_example.json")
    sub = build_submissions(TODAY)
    # Son 10-Q (ve ondan yeni 10-K/A) henüz kayıtlı değil → şirket eskimiş
    filings = [f for f in parse_filings(sub["filings"]["recent"])
               if f["accession_no"] not in (ACC_Q, ACC_K_AMEND)]
    with factory() as s:
        repo.save_collected(s, {"company": {"cik": CIK, "ticker": TICKER, "name": "Example Corp"},
                                "filings": filings, "facts": extract_all(cf, years=4)})
    sec = FakeSec(build_routes(TODAY))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path.as_posix()}")
    monkeypatch.setattr(jobs, "make_sec_client", sec)
    monkeypatch.setattr(jobs, "default_session_factory", lambda: factory)
    assert refresh.main(["--all"]) == 0
    out = capsys.readouterr().out
    assert "Veritabanı:" in out and "yeni rapor var" in out and "güncellendi" in out
    with factory() as s:
        accs = {f["accession_no"] for f in repo.load_filings(s, repo.load_company(s, TICKER)["id"])}
    assert ACC_Q in accs
    # İkinci çalıştırma: güncel → iş açılmaz
    assert refresh.main(["--all"]) == 0
    assert "güncel" in capsys.readouterr().out
    engine.dispose()


def test_facts_url_constant_used():
    assert FACTS_URL.endswith(f"CIK{CIK}.json")
