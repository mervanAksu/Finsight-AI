from datetime import date

from conftest import load

from finsight.xbrl import extract_all, extract_annual, yoy_changes

CF = load("companyfacts_example.json")


def values(series):
    return [(s["period_end"].year, s["value"]) for s in series]


def test_last_three_years_excludes_quarters_and_10q():
    rev = extract_annual(CF, "revenue", years=3)
    assert values(rev) == [(2023, 1000), (2024, 1100), (2025, 1210)]


def test_restated_value_from_latest_filing_wins():
    rev = extract_annual(CF, "revenue", years=5)
    fy2022 = [s for s in rev if s["period_end"].year == 2022][0]
    assert fy2022["value"] == 905
    assert fy2022["accession_no"] == "0000000001-24-000010"


def test_falls_back_to_older_tag():
    rev = extract_annual(CF, "revenue", years=5)
    assert rev[0]["period_end"] == date(2021, 12, 31)
    assert rev[0]["tag"] == "SalesRevenueNet"


def test_instant_metric_ignores_10q_balances():
    assets = extract_annual(CF, "total_assets", years=3)
    assert values(assets) == [(2023, 5000), (2024, 5500), (2025, 6000)]
    assert assets[0]["period_start"] is None


def test_missing_metric_returns_empty():
    assert extract_all(CF)["total_liabilities"] == []


def test_yoy_changes():
    rev = extract_annual(CF, "revenue", years=3)
    assert yoy_changes(rev) == [None, 10.0, 10.0]
    assert yoy_changes([{"value": 0}, {"value": 5}]) == [None, None]
