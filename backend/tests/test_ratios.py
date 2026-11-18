"""Finansal oran testleri (FR-06, FR-07, NFR-06). Beklenen değerler elle hesaplanmıştır."""

from datetime import date

import pytest
from conftest import load

from finsight.ratios import RATIO_DEFS, compute_ratios
from finsight.xbrl import extract_all

FULL = load("companyfacts_full_example.json")  # mali yıl sonu 30 Haziran
A22, A23, A24, A25 = ("0000000002-22-000040", "0000000002-23-000040",
                      "0000000002-24-000040", "0000000002-25-000040")


def by_key(result):
    return {r["key"]: r for r in result["ratios"]}


def value_at(result, key, end):
    return [v for v in by_key(result)[key]["values"] if v["period_end"] == end][0]


def fact(metric, end, value, accn="0000000009-26-000001", start=None):
    return {"metric": metric, "tag": "Test", "period_start": start, "period_end": end,
            "value": value, "unit": "USD", "accession_no": accn, "form": "10-K",
            "filed": date(2026, 2, 1), "fy": 2025, "fp": "FY", "derived_from": None}


END = date(2025, 12, 31)
PREV = date(2024, 12, 31)


def facts_one_year(**values):
    """Tek dönemlik (END) sentetik kalemler; None verilen kalem eksik sayılır."""
    return {m: ([] if v is None else [fact(m, END, v)]) for m, v in values.items()}


BASE = dict(revenue=1000, gross_profit=400, operating_income=150, net_income=100,
            operating_cash_flow=130, capex=30, total_assets=2000, current_assets=600,
            current_liabilities=400, total_liabilities=1200, stockholders_equity=800)


def test_ratio_defs_match_skill():
    assert [d["key"] for d in RATIO_DEFS] == [
        "gross_margin", "operating_margin", "net_margin", "roa", "roe", "current_ratio",
        "debt_to_equity", "liabilities_to_assets", "ocf_to_net_income", "fcf_margin",
    ]
    units = {d["key"]: d["unit"] for d in RATIO_DEFS}
    assert units["current_ratio"] == "times" and units["liabilities_to_assets"] == "percent"
    lower_better = {d["key"] for d in RATIO_DEFS if not d["higher_is_better"]}
    assert lower_better == {"debt_to_equity", "liabilities_to_assets"}
    for d in RATIO_DEFS:
        assert set(d) == {"key", "label", "formula", "unit", "higher_is_better"}


def test_periods_june_fiscal_year():
    res = compute_ratios(extract_all(FULL, years=4), periods=3)
    assert res["periods"] == [date(2023, 6, 30), date(2024, 6, 30), date(2025, 6, 30)]
    for r in res["ratios"]:
        assert [v["period_end"] for v in r["values"]] == res["periods"]


def test_all_ratios_latest_year_full_fixture():
    res = compute_ratios(extract_all(FULL, years=4), periods=3)
    end = date(2025, 6, 30)
    expected = {
        "gross_margin": 3.4 / 5.5,                 # doğrudan GrossProfit
        "operating_margin": 1.5 / 5.5,
        "net_margin": 1.1 / 5.5,
        "roa": 1.1 / ((12 + 11) / 2),              # ortalama varlık 11,5 mr
        "roe": 1.1 / ((5.5 + 5.0) / 2),            # ortalama özkaynak 5,25 mr
        "current_ratio": 3.6 / 2.4,
        "debt_to_equity": 6.4 / 5.5,
        "liabilities_to_assets": 6.4 / 12,
        "ocf_to_net_income": 1.32 / 1.1,
        "fcf_margin": (1.32 - 0.22) / 5.5,
    }
    for key, exp in expected.items():
        v = value_at(res, key, end)
        assert v["status"] == "ok", key
        assert v["reason"] is None and v["note"] is None
        assert v["value"] == pytest.approx(exp, rel=1e-12), key
        assert isinstance(v["value"], float)


def test_ratios_earlier_years_use_derived_values_and_fy2022_for_average():
    res = compute_ratios(extract_all(FULL, years=4), periods=3)
    y23, y24 = date(2023, 6, 30), date(2024, 6, 30)
    assert value_at(res, "gross_margin", y23)["value"] == pytest.approx((4.4 - 1.8) / 4.4)
    assert value_at(res, "gross_margin", y24)["value"] == pytest.approx((5.0 - 2.0) / 5.0)
    assert value_at(res, "roa", y23)["value"] == pytest.approx(0.8 / ((10 + 9) / 2))
    assert value_at(res, "roe", y23)["value"] == pytest.approx(0.8 / ((4.5 + 4.0) / 2))
    assert value_at(res, "roa", y23)["note"] is None
    assert value_at(res, "debt_to_equity", y23)["value"] == pytest.approx((10 - 4.6) / 4.5)
    assert value_at(res, "debt_to_equity", y24)["value"] == pytest.approx((11 - 5.1) / 5.0)
    assert value_at(res, "ocf_to_net_income", y23)["value"] == pytest.approx(1.0 / 0.8)
    assert value_at(res, "fcf_margin", y24)["value"] == pytest.approx((1.2 - 0.35) / 5.0)


def test_inputs_trace_accessions_including_average():
    res = compute_ratios(extract_all(FULL, years=4), periods=3)
    roa = value_at(res, "roa", date(2025, 6, 30))
    assert roa["inputs"] == [
        {"metric": "net_income", "value": 1_100_000_000.0, "accession_no": A25},
        {"metric": "total_assets", "value": 12_000_000_000.0, "accession_no": A25},
        {"metric": "total_assets", "value": 11_000_000_000.0, "accession_no": A24},
    ]
    fcf = value_at(res, "fcf_margin", date(2025, 6, 30))
    assert [i["metric"] for i in fcf["inputs"]] == ["operating_cash_flow", "capex", "revenue"]
    gm = value_at(res, "gross_margin", date(2024, 6, 30))
    assert gm["inputs"][0] == {"metric": "gross_profit", "value": 3_000_000_000.0, "accession_no": A25}


def test_average_falls_back_to_period_end_with_note():
    # years=3: FY2022 bilançosu yok → FY2023 ortalaması yapılamaz
    res = compute_ratios(extract_all(FULL, years=3), periods=3)
    roa = value_at(res, "roa", date(2023, 6, 30))
    assert roa["value"] == pytest.approx(0.8 / 10)
    assert roa["note"] == "ortalama yerine dönem sonu değeri"
    roe = value_at(res, "roe", date(2023, 6, 30))
    assert roe["value"] == pytest.approx(0.8 / 4.5)
    assert roe["note"] == "ortalama yerine dönem sonu değeri"
    assert value_at(res, "roa", date(2024, 6, 30))["note"] is None


def test_prior_balance_more_than_400_days_old_is_not_used():
    facts = facts_one_year(**BASE)
    facts["total_assets"].append(fact("total_assets", date(2023, 12, 31), 1000))
    roa = value_at(compute_ratios(facts), "roa", END)
    assert roa["value"] == pytest.approx(100 / 2000)
    assert roa["note"] == "ortalama yerine dönem sonu değeri"


def test_average_with_synthetic_prior_year():
    facts = facts_one_year(**BASE)
    facts["total_assets"].append(fact("total_assets", PREV, 1800))
    facts["stockholders_equity"].append(fact("stockholders_equity", PREV, 600))
    res = compute_ratios(facts)
    assert value_at(res, "roa", END)["value"] == pytest.approx(100 / 1900)
    assert value_at(res, "roe", END)["value"] == pytest.approx(100 / 700)
    # Ortalama gerektirmeyen oranlar dönem sonu değerini kullanır
    assert value_at(res, "debt_to_equity", END)["value"] == pytest.approx(1200 / 800)


def test_missing_input_reason():
    res = compute_ratios(facts_one_year(**{**BASE, "capex": None, "current_assets": None}))
    fcf = value_at(res, "fcf_margin", END)
    assert fcf == {
        "period_end": END, "value": None, "status": "missing", "reason": "eksik veri: capex",
        "note": None,
        "inputs": [{"metric": "operating_cash_flow", "value": 130.0,
                    "accession_no": "0000000009-26-000001"},
                   {"metric": "revenue", "value": 1000.0,
                    "accession_no": "0000000009-26-000001"}],
    }
    assert value_at(res, "current_ratio", END)["reason"] == "eksik veri: current_assets"


def test_zero_denominator():
    res = compute_ratios(facts_one_year(**{**BASE, "current_liabilities": 0, "total_assets": 0}))
    for key in ("current_ratio", "liabilities_to_assets", "roa"):
        v = value_at(res, key, END)
        assert (v["value"], v["status"], v["reason"]) == (None, "missing", "payda sıfır")


def test_revenue_zero_or_negative_blocks_margins():
    res = compute_ratios(facts_one_year(**{**BASE, "revenue": 0}))
    for key in ("gross_margin", "operating_margin", "net_margin", "fcf_margin"):
        assert value_at(res, key, END)["reason"] == "payda sıfır"
    res = compute_ratios(facts_one_year(**{**BASE, "revenue": -50}))
    for key in ("gross_margin", "operating_margin", "net_margin", "fcf_margin"):
        v = value_at(res, key, END)
        assert v["value"] is None and v["status"] == "missing"
        assert v["reason"] == "gelir negatif; marj oranı anlamlı değil"
    assert value_at(res, "current_ratio", END)["status"] == "ok"


@pytest.mark.parametrize("equity", [0, -300])
def test_non_positive_equity(equity):
    res = compute_ratios(facts_one_year(**{**BASE, "stockholders_equity": equity}))
    for key in ("roe", "debt_to_equity"):
        v = value_at(res, key, END)
        assert v["value"] is None and v["status"] == "missing"
        assert v["reason"] == "özkaynak negatif veya sıfır; oran anlamlı değil"
    assert value_at(res, "liabilities_to_assets", END)["value"] == pytest.approx(1200 / 2000)


@pytest.mark.parametrize("net_income", [0, -40])
def test_non_positive_net_income_blocks_cash_quality(net_income):
    res = compute_ratios(facts_one_year(**{**BASE, "net_income": net_income}))
    v = value_at(res, "ocf_to_net_income", END)
    assert v["value"] is None
    assert v["reason"] == "net kâr sıfır veya negatif; oran anlamlı değil"
    # Negatif net kârla diğer oranlar hesaplanır (negatif marj)
    assert value_at(res, "net_margin", END)["value"] == pytest.approx(net_income / 1000)


def test_periods_fall_back_to_net_income_and_limit():
    facts = facts_one_year(**{**BASE, "revenue": None})
    facts["net_income"].append(fact("net_income", PREV, 90))
    res = compute_ratios(facts, periods=3)
    assert res["periods"] == [PREV, END]
    assert value_at(res, "net_margin", END)["reason"] == "eksik veri: revenue"
    assert compute_ratios(extract_all(FULL, years=4), periods=2)["periods"] == [
        date(2024, 6, 30), date(2025, 6, 30)]


def test_no_data():
    res = compute_ratios({})
    assert res["periods"] == []
    assert len(res["ratios"]) == 10 and all(r["values"] == [] for r in res["ratios"])
