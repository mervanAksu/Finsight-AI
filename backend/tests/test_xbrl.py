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


# ---- Aşama 3: 12 kalem, türetmeler, yoy_ratio (FR-04, FR-05, NFR-06) ----

from finsight.xbrl import METRIC_LABELS, METRICS, yoy_ratio  # noqa: E402

FULL = load("companyfacts_full_example.json")  # mali yılı 30 Haziran'da biten sentetik şirket
A22, A23, A24, A25 = ("0000000002-22-000040", "0000000002-23-000040",
                      "0000000002-24-000040", "0000000002-25-000040")


def test_metrics_match_skill_table_order():
    assert list(METRICS) == [
        "revenue", "cost_of_revenue", "gross_profit", "operating_income", "net_income",
        "operating_cash_flow", "capex", "total_assets", "current_assets",
        "current_liabilities", "total_liabilities", "stockholders_equity",
    ]
    assert set(METRIC_LABELS) == set(METRICS)
    assert METRIC_LABELS["revenue"] == "Gelir"
    assert "LiabilitiesAndStockholdersEquity" not in [t for tags, _ in METRICS.values() for t in tags]
    assert METRICS["revenue"][0][-1] == "RevenueFromContractWithCustomerIncludingAssessedTax"
    assert METRICS["net_income"][0] == ["NetIncomeLoss", "ProfitLoss"]


def test_fact_items_have_derived_from_none():
    for item in extract_annual(CF, "revenue", years=5):
        assert item["derived_from"] is None


def test_june_fiscal_year_excludes_quarters():
    rev = extract_annual(FULL, "revenue", years=0)
    assert [(s["period_start"], s["period_end"], s["value"]) for s in rev] == [
        (date(2021, 7, 1), date(2022, 6, 30), 4_000_000_000),
        (date(2022, 7, 1), date(2023, 6, 30), 4_400_000_000),
        (date(2023, 7, 1), date(2024, 6, 30), 5_000_000_000),
        (date(2024, 7, 1), date(2025, 6, 30), 5_500_000_000),
    ]
    # Karşılaştırma sütunu olarak tekrar raporlanan FY2024: en son başvuru seçilir
    assert rev[2]["accession_no"] == A25
    assets = extract_annual(FULL, "total_assets", years=0)
    assert [s["period_end"] for s in assets] == [date(y, 6, 30) for y in (2022, 2023, 2024, 2025)]


def test_extract_all_derives_gross_profit_only_where_missing():
    gp = extract_all(FULL, years=4)["gross_profit"]
    assert [(s["period_end"].year, s["value"], s["tag"]) for s in gp] == [
        (2022, 4_000_000_000 - 1_700_000_000, "derived"),
        (2023, 4_400_000_000 - 1_800_000_000, "derived"),
        (2024, 5_000_000_000 - 2_000_000_000, "derived"),
        (2025, 3_400_000_000, "GrossProfit"),  # doğrudan değer varsa türetilmez
    ]
    fy24 = gp[2]
    assert fy24["derived_from"] == ["Revenues", "CostOfRevenue"]
    # Gelir 2025 10-K'sından (en son), maliyet 2024 10-K'sından → en son başvuru
    assert fy24["accession_no"] == A25
    assert fy24["filed"] == date(2025, 8, 1)
    assert fy24["period_start"] == date(2023, 7, 1)
    assert gp[0]["accession_no"] == A22
    assert gp[3]["derived_from"] is None


def test_extract_all_derives_total_liabilities():
    tl = extract_all(FULL, years=4)["total_liabilities"]
    assert [(s["period_end"].year, s["value"], s["tag"]) for s in tl] == [
        (2022, 9_000_000_000 - 4_000_000_000, "derived"),  # NCI dahil özkaynak yok → SE
        (2023, 10_000_000_000 - 4_600_000_000, "derived"),  # NCI dahil özkaynak öncelikli
        (2024, 11_000_000_000 - 5_100_000_000, "derived"),
        (2025, 6_400_000_000, "Liabilities"),
    ]
    assert tl[0]["derived_from"] == ["LiabilitiesAndStockholdersEquity", "StockholdersEquity"]
    assert tl[1]["derived_from"] == [
        "LiabilitiesAndStockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ]
    # LSE 2023 10-K'sından, NCI dahil özkaynak 2024 10-K'sından → en son başvuru
    assert tl[1]["accession_no"] == A24
    assert tl[1]["period_start"] is None
    assert tl[0]["accession_no"] == A22


def test_extract_all_years_slices_after_derivation():
    facts = extract_all(FULL, years=3)
    assert [s["period_end"].year for s in facts["gross_profit"]] == [2023, 2024, 2025]
    assert [s["period_end"].year for s in facts["total_liabilities"]] == [2023, 2024, 2025]
    assert len(extract_all(FULL, years=0)["total_assets"]) == 4


def test_extract_all_no_derivation_without_inputs():
    facts = extract_all(CF)
    assert facts["gross_profit"] == []  # maliyet yok
    assert facts["capex"] == []


def test_yoy_ratio():
    def s(*vals):
        return [{"value": v} for v in vals]

    assert yoy_ratio(s(100, 110, 99)) == [None, (110 - 100) / 100, (99 - 110) / 110]
    # negatif taban: mutlak değerle bölünür
    assert yoy_ratio(s(-50, 25)) == [None, (25 - (-50)) / 50]
    assert yoy_ratio(s(0, 5)) == [None, None]
    assert yoy_ratio(s(None, 5)) == [None, None]
    assert yoy_ratio(s(5, None)) == [None, None]
    assert yoy_ratio([]) == [None]
