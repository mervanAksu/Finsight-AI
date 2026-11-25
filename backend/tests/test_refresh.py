"""Tazelik (dinamik yapı) testleri — docs/asama4-sozlesme.md §4; güvenlik SEC-16.

Veritabanı ve ağ yok: SEC sahte ``fetch`` fonksiyonları ve sahte saatlerle test edilir.
Şirketler sentetiktir ("Example Corp").
"""

from __future__ import annotations

from datetime import date

import pytest

from finsight.refresh import (ERROR_BUDGET, FRESHNESS_TTL_SECONDS, AutoRefresher,
                              FreshnessService, ProcessLock, RequestBudget, compute_freshness,
                              refresh_companies, sec_fetcher)
from finsight.sec_client import SecError
from finsight.views import freshness_view


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def subs(*rows: tuple) -> dict:
    """rows: (accession, form, filing_date, report_date)."""
    return {"filings": {"recent": {
        "accessionNumber": [r[0] for r in rows], "form": [r[1] for r in rows],
        "filingDate": [r[2] for r in rows], "reportDate": [r[3] for r in rows],
        "primaryDocument": ["d.htm"] * len(rows), "items": [""] * len(rows),
        "isXBRL": [1] * len(rows)}}}


def db(acc, form, filed, report=None, analyzed=True) -> dict:
    return {"accession_no": acc, "form": form, "filing_date": date.fromisoformat(filed),
            "report_date": date.fromisoformat(report) if report else None,
            "analyzed": analyzed}


SEC_ROWS = (("0000000001-26-000123", "10-Q", "2026-10-28", "2026-09-30"),
            ("0000000001-26-000200", "8-K", "2026-10-28", "2026-10-28"),
            ("0000000001-26-000100", "10-K", "2026-07-30", "2026-06-30"),
            ("0000000001-26-000050", "8-K", "2026-04-02", "2026-04-01"),
            ("0000000001-25-000100", "10-K", "2025-07-31", "2025-06-30"))
DB_ROWS = [db("0000000001-26-000100", "10-K", "2026-07-30", "2026-06-30"),
           db("0000000001-25-000100", "10-K", "2025-07-31", "2025-06-30")]


def state(service_or_subs) -> dict:
    svc = FreshnessService(lambda _cik: service_or_subs, clock=Clock(),
                           wall_clock=lambda: 1795000000.0)
    return svc.sec_state("0000000001")


# --------------------------------------------------------------------------- compute_freshness


def test_new_10q_makes_company_stale():
    out = compute_freshness(state(subs(*SEC_ROWS)), DB_ROWS)
    assert out["stale"] is True and out["error"] is None
    assert out["sec_latest"] == {"form": "10-Q", "filing_date": "2026-10-28",
                                 "report_date": "2026-09-30",
                                 "accession_no": "0000000001-26-000123"}
    assert out["analyzed_latest"]["form"] == "10-K"
    assert out["analyzed_latest"]["filing_date"] == "2026-07-30"
    forms = [f["form"] for f in out["new_filings"]]
    assert "10-Q" in forms and "8-K" in forms
    # Kayıt penceresinden eski 8-K "yeni" sayılmaz
    assert all(f["filing_date"] >= "2026-07-30" for f in out["new_filings"])
    assert out["checked_at"].endswith("Z")


def test_only_8k_is_listed_but_not_stale():
    rows = (("0000000001-26-000200", "8-K", "2026-10-28", "2026-10-28"),) + SEC_ROWS[2:]
    out = compute_freshness(state(subs(*rows)), DB_ROWS)
    assert out["stale"] is False
    assert [f["form"] for f in out["new_filings"]] == ["8-K"]


def test_up_to_date_company_and_amendment_counts():
    out = compute_freshness(state(subs(*SEC_ROWS[2:])), DB_ROWS)
    assert out["stale"] is False and out["new_filings"] == []
    amended = (("0000000001-26-000300", "10-K/A", "2026-08-15", "2026-06-30"),) + SEC_ROWS[2:]
    assert compute_freshness(state(subs(*amended)), DB_ROWS)["stale"] is True


def test_analyzed_latest_prefers_analyzed_filing():
    rows = [db("0000000001-26-000123", "10-Q", "2026-10-28", analyzed=False), *DB_ROWS]
    out = compute_freshness(state(subs(*SEC_ROWS)), rows)
    assert out["analyzed_latest"]["form"] == "10-K"
    assert out["stale"] is False  # 10-Q artık veritabanında


def test_sec_error_gives_null_stale_and_reason():
    svc = FreshnessService(lambda _c: (_ for _ in ()).throw(SecError("SEC isteği başarısız "
                                                                     "(503)", 503)),
                           clock=Clock(), error_fn=lambda e: f"SEC'e ulaşılamadı: {e}")
    st = svc.sec_state("0000000001")
    assert st["status"] == 503
    out = compute_freshness(st, DB_ROWS)
    assert out["stale"] is None and out["error"].startswith("SEC'e ulaşılamadı")
    assert out["analyzed_latest"]["form"] == "10-K"
    view = freshness_view(out)
    assert set(view) == {"checked_at", "sec_latest", "analyzed_latest", "new_filings", "stale",
                         "error", "xbrl_pending", "xbrl_note", "disclaimer"}


# --------------------------------------------------------------------------- önbellek + bütçe


def test_cache_15_minutes_success_and_error():
    """SEC-16 (b): 15 dk içinde 20 çağrı → SEC'e 1 istek; hata sonucu da önbellekte."""
    clock = Clock()
    calls = []

    def fetch(cik):
        calls.append(cik)
        raise SecError("SEC isteği başarısız (500)", 500)

    svc = FreshnessService(fetch, clock=clock, error_fn=str)
    for _ in range(20):
        assert svc.sec_state("0000000001")["error"]
    assert len(calls) == 1
    clock.now += FRESHNESS_TTL_SECONDS - 1
    svc.sec_state("0000000001")
    assert len(calls) == 1
    clock.now += 2
    svc.sec_state("0000000001")
    assert len(calls) == 2


def test_global_budget_limits_fresh_requests_across_companies():
    clock = Clock()
    calls = []
    svc = FreshnessService(lambda cik: calls.append(cik) or subs(*SEC_ROWS), clock=clock,
                           budget=RequestBudget(limit=3, window=60, clock=clock))
    results = [svc.sec_state(f"{i:010d}") for i in range(5)]
    assert len(calls) == 3
    assert [r["error"] for r in results[3:]] == [ERROR_BUDGET, ERROR_BUDGET]
    # Bütçe aşımı önbelleğe alınmaz; pencere geçince SEC'e gidilir
    clock.now += 61
    assert svc.sec_state("0000000004")["error"] is None
    assert len(calls) == 4


def test_cache_is_bounded_lru():
    clock = Clock()
    svc = FreshnessService(lambda cik: subs(*SEC_ROWS), clock=clock, max_entries=2,
                           budget=RequestBudget(limit=None))
    for cik in ("A", "B", "C"):
        svc.sec_state(cik)
    assert svc.cached("A") is None and svc.cached("C") is not None
    assert len(svc._cache) == 2


def test_sec_fetcher_bypasses_cache_and_disables_retries():
    seen = {}

    class FakeClient:
        max_retries = 3

        def submissions(self, cik, *, fresh=False):
            seen.update(cik=cik, fresh=fresh, retries=self.max_retries)
            return {}

    sec_fetcher(FakeClient)("0000000001")
    assert seen == {"cik": "0000000001", "fresh": True, "retries": 0}


# --------------------------------------------------------------------------- otomatik yenileme


COMPANIES = [{"ticker": "EXA", "cik": "1", "analyzed": True},
             {"ticker": "EXB", "cik": "2", "analyzed": True}]


def make_refresher(check, start=None, companies=COMPANIES):
    started = []

    def start_fn(c):
        started.append(c["ticker"])
        return (start or (lambda _c: {"id": 1}))(c)

    r = AutoRefresher(30, list_fn=lambda: companies, check_fn=check, start_fn=start_fn,
                      pace_seconds=0)
    return r, started


def test_auto_refresher_starts_only_stale_companies():
    def check(c):
        return {"status": None}, {"stale": c["ticker"] == "EXB", "error": None}

    r, started = make_refresher(check)
    summary = r.run_once()
    assert started == ["EXB"] and summary["started"] == ["2"]
    assert r.next_delay() == 30 * 60


def test_auto_refresher_backs_off_on_throttle():
    def check(c):
        return {"status": 429}, {"stale": None, "error": "SEC 429"}

    r, started = make_refresher(check)
    s1 = r.run_once()
    assert s1["throttled"] and s1["checked"] == ["1"] and started == []  # tur kesildi
    assert r.next_delay() == 2 * 30 * 60
    r.run_once()
    assert r.next_delay() == 4 * 30 * 60
    for _ in range(20):
        r.run_once()
    assert r.next_delay() == 24 * 3600  # üst sınır


def test_one_failing_company_does_not_block_loop():
    def check(c):
        if c["ticker"] == "EXA":
            raise RuntimeError("bozuk")
        return {"status": None}, {"stale": True, "error": None}

    r, started = make_refresher(check)
    r.run_once()
    assert started == ["EXB"]
    s2 = r.run_once()  # EXA bir süre atlanır, EXB yine işlenir
    assert "1" in s2["skipped"] and started == ["EXB", "EXB"]
    assert r.next_delay() == 30 * 60  # tek şirket hatası küresel beklemeyi artırmaz


def test_auto_refresher_stop_and_busy_job():
    def check(c):
        return {"status": None}, {"stale": True, "error": None}

    r, started = make_refresher(check, start=lambda _c: None)  # etkin iş var → None
    s = r.run_once()
    assert s["started"] == [] and started == ["EXA", "EXB"]
    r.stop()
    assert r.stopped
    assert r.run_once()["checked"] == []  # durdurulmuş döngü şirket işlemez


def test_process_lock_single_owner(tmp_path):
    path = str(tmp_path / "auto-refresh.lock")
    a, b = ProcessLock(path), ProcessLock(path)
    assert a.acquire() is True
    assert b.acquire() is False  # ikinci worker sahip olamaz
    a.release()
    assert b.acquire() is True
    b.release()


def test_refresher_start_without_lock_does_not_run(tmp_path):
    path = str(tmp_path / "lock")
    owner = ProcessLock(path)
    assert owner.acquire()
    r = AutoRefresher(30, list_fn=lambda: [], check_fn=lambda c: ({}, {}),
                      start_fn=lambda c: None, lock=ProcessLock(path))
    assert r.start() is False
    owner.release()


# --------------------------------------------------------------------------- CLI çekirdeği


def test_refresh_companies_updates_only_stale():
    fresh = {"EXA": {"stale": False, "error": None, "new_filings": []},
             "EXB": {"stale": True, "error": None,
                     "new_filings": [{"form": "10-Q", "filing_date": "2026-10-28"}]},
             "EXC": {"stale": None, "error": "SEC'e ulaşılamadı", "new_filings": []}}
    started, lines = [], []
    companies = [{"ticker": t, "cik": t} for t in fresh]
    result = refresh_companies(companies, check=lambda c: ({}, fresh[c["ticker"]]),
                               start=lambda c: started.append(c["ticker"]) or {"id": 7},
                               out=lines.append)
    assert started == ["EXB"]
    assert result == {"stale": ["EXB"], "updated": ["EXB"], "busy": [], "errors": ["EXC"],
                      "fresh": ["EXA"]}
    assert any("10-Q (2026-10-28)" in line for line in lines)


def test_refresh_companies_dry_run_and_busy():
    fresh = {"stale": True, "error": None, "new_filings": []}
    started = []
    r = refresh_companies([{"ticker": "EXB"}], check=lambda c: ({}, fresh),
                          start=lambda c: started.append(1), dry_run=True, out=lambda _l: None)
    assert started == [] and r["stale"] == ["EXB"] and r["updated"] == []
    r2 = refresh_companies([{"ticker": "EXB"}], check=lambda c: ({}, fresh),
                           start=lambda c: None, out=lambda _l: None)
    assert r2["busy"] == ["EXB"]


@pytest.mark.parametrize("argv", [[], ["--dry-run"]])
def test_cli_requires_ticker_or_all(argv):
    from finsight.refresh import main

    with pytest.raises(SystemExit):
        main(argv)


# --------------------------------------------------------------------------- inceleme düzeltmeleri


def test_xbrl_pending_when_latest_10q_not_in_ttm():
    """Madde 6: 10-Q kayıtlı ama XBRL'i TTM'ye girmemiş → xbrl_pending; stale etkilenmez."""
    q = {**db("0000000001-26-000123", "10-Q", "2026-10-28", "2026-09-30"), "in_ttm": False}
    rows = [q] + [{**r, "in_ttm": False} for r in DB_ROWS]
    out = compute_freshness(state(subs(*SEC_ROWS)), rows)
    assert out["xbrl_pending"] is True and "companyfacts" in out["xbrl_note"]
    assert out["stale"] is False
    ok = compute_freshness(state(subs(*SEC_ROWS)), [{**q, "in_ttm": True}] + rows[1:])
    assert ok["xbrl_pending"] is False and ok["xbrl_note"] is None
    # 10-Q son 10-K'dan eskiyse TTM beklenmez
    old_q = {**db("0000000001-26-000010", "10-Q", "2026-05-01"), "in_ttm": False}
    assert compute_freshness(state(subs(*SEC_ROWS)), [old_q, *DB_ROWS])["xbrl_pending"] is False
    assert "xbrl_pending" in freshness_view(out)


def test_auto_refresher_budget_simulation_12_companies():
    """Madde 8: kendi bütçesi dakikada 10; 12 şirket → ilk tur 10'da kesilir (hata sayılmaz),
    sonraki tur 11. şirketten devam eder."""
    clock = Clock()
    fetched = []
    svc = FreshnessService(lambda cik: fetched.append(cik) or subs(*SEC_ROWS[2:]), clock=clock,
                           budget=RequestBudget(limit=10, window=60, clock=clock))
    companies = [{"ticker": f"EX{i:02d}", "cik": f"{i:010d}"} for i in range(12)]

    def check(c):
        st = svc.sec_state(c["cik"])
        return st, compute_freshness(st, DB_ROWS)

    r = AutoRefresher(30, list_fn=lambda: companies, check_fn=check,
                      start_fn=lambda c: None, pace_seconds=0)
    s1 = r.run_once()
    assert s1["budget"] is True and len(s1["checked"]) == 10 and s1["errors"] == []
    assert r.failures == {} and r.cursor == f"{10:010d}"
    clock.now += 61
    s2 = r.run_once()
    assert s2["checked"][:2] == [f"{10:010d}", f"{11:010d}"]
    assert len(fetched) == 12  # her şirket bir kez SEC'e (önbellekli olanlar tekrar gitmez)


def test_auto_refresher_job_error_with_sec_status_backs_off():
    """Madde 9: işin SEC 429 hatası küresel geri çekilmeye yol açar."""
    from finsight.refresh import RefreshJobError, job_error_status

    assert job_error_status("SEC isteği başarısız (429): https://data.sec.gov/x") == 429
    assert job_error_status("Beklenmeyen hata (KeyError)") is None

    def start(c):
        raise RefreshJobError("SEC isteği başarısız (429)", 429)

    r, _ = make_refresher(lambda c: ({"status": None}, {"stale": True, "error": None}),
                          start=start)
    s = r.run_once()
    assert s["throttled"] and s["errors"] == ["1"]
    assert r.next_delay() == 2 * 30 * 60


def test_refresh_companies_job_error_is_cli_error():
    from finsight.refresh import RefreshJobError

    def start(c):
        raise RefreshJobError("Şirket bulunamadı")

    r = refresh_companies([{"ticker": None, "cik": "0000000002"}],
                          check=lambda c: ({}, {"stale": True, "error": None,
                                                "new_filings": []}),
                          start=start, out=lambda _l: None)
    assert r["errors"] == ["0000000002"]  # ticker yoksa CIK ile


def test_masked_db_url():
    from finsight.refresh import _masked_db_url

    assert _masked_db_url("postgresql://u:gizli@h/db") == "postgresql://u:***@h/db"
    assert _masked_db_url("sqlite:///finsight.db") == "sqlite:///finsight.db"
