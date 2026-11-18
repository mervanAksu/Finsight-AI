"""Skor motoru testleri (FR-11). Beklenen değerler skill §3'teki eşiklerden elle hesaplanmıştır."""

from datetime import date

import pytest
from conftest import load

from finsight.ratios import compute_ratios
from finsight.scoring import COMPONENTS, WEIGHTS_VERSION, band, compute_score, lin
from finsight.xbrl import extract_all

FULL = load("companyfacts_full_example.json")
END = date(2025, 12, 31)
PREV = date(2024, 12, 31)
SIGNALS = {"mdna_net_tone": 0.08, "risk_net_tone": -0.5, "risk_added": 3}
NO_SIGNALS = {"mdna_net_tone": None, "risk_net_tone": None, "risk_added": None}


def fact(metric, end, value):
    return {"metric": metric, "tag": "Test", "period_start": None, "period_end": end,
            "value": value, "unit": "USD", "accession_no": "0000000009-26-000001",
            "form": "10-K", "filed": date(2026, 2, 1), "fy": 2025, "fp": "FY",
            "derived_from": None}


def one_year(**values):
    return {m: [fact(m, END, v)] for m, v in values.items() if v is not None}


# Temiz sayılı tek yıllık şirket (önceki yıl yok → büyüme yok, ROA/ROE dönem sonu)
CLEAN = dict(revenue=1000, gross_profit=400, operating_income=125, net_income=100,
             operating_cash_flow=90, capex=40, total_assets=2000, current_assets=500,
             current_liabilities=400, total_liabilities=1200, stockholders_equity=800)


def score_for(facts, signals):
    return compute_score(compute_ratios(facts), facts, signals)


def comp(result, key):
    return [c for c in result["components"] if c["key"] == key][0]


def ind(component, key):
    return [i for i in component["indicators"] if i["key"] == key][0]


# ---- lin() ve band() ----

@pytest.mark.parametrize("x, a, b, expected", [
    (-1, 0, 0.20, 0.0), (0, 0, 0.20, 0.0), (0.10, 0, 0.20, 50.0), (0.20, 0, 0.20, 100.0),
    (5, 0, 0.20, 100.0),
    # ters yön (a > b)
    (4.0, 3.0, 0.5, 0.0), (3.0, 3.0, 0.5, 0.0), (1.75, 3.0, 0.5, 50.0), (0.5, 3.0, 0.5, 100.0),
    (0.0, 3.0, 0.5, 100.0),
    (15, 15, 0, 0.0), (6, 15, 0, 60.0), (0, 15, 0, 100.0), (20, 15, 0, 0.0),
    (-0.5, -0.80, -0.20, 50.0), (0.05, -0.05, 0.15, 50.0),
])
def test_lin(x, a, b, expected):
    assert lin(x, a, b) == pytest.approx(expected)


def test_lin_none():
    assert lin(None, 0, 1) is None


@pytest.mark.parametrize("total, expected", [
    (100, "Güçlü"), (75, "Güçlü"), (74.99, "Dengeli"), (60, "Dengeli"), (59.99, "İzlenmeli"),
    (45, "İzlenmeli"), (44.99, "Zayıf"), (0, "Zayıf"), (None, None),
])
def test_band(total, expected):
    assert band(total) == expected


def test_components_definition():
    assert [(c["key"], c["weight"]) for c in COMPONENTS] == [
        ("profitability", 25), ("leverage", 20), ("liquidity", 15), ("cash_quality", 15),
        ("growth", 10), ("text_tone", 10), ("risk_change", 5)]
    assert sum(c["weight"] for c in COMPONENTS) == 100
    assert WEIGHTS_VERSION == "v1-2026-11"


# ---- tam veri (Haziran mali yılı) ----

def full_result(signals=SIGNALS):
    facts = extract_all(FULL, years=4)
    return compute_score(compute_ratios(facts, periods=3), facts, signals)


def test_full_data_score():
    r = full_result()
    profitability = (100 + 100 + (1.1 / 11.5) / 0.15 * 100 + (1.1 / 5.25) / 0.25 * 100) / 4
    leverage = ((3.0 - 6.4 / 5.5) / 2.5 * 100 + (0.9 - 6.4 / 12) / 0.6 * 100) / 2
    liquidity = (1.5 - 0.5) / 1.5 * 100
    cash = (100 + 100) / 2           # 1,32/1,1 = 1,2 → 100; FCF marjı %20 ≥ %15 → 100
    growth = (80 + 75) / 2           # gelir +%10 → 80; net kâr +%10 → 75
    tone = (0.08 + 0.40) / 0.60 * 100
    risk = (15 - 3) / 15 * 100
    expected = (25 * profitability + 20 * leverage + 15 * liquidity + 15 * cash
                + 10 * growth + 10 * tone + 5 * risk) / 100
    assert r["total"] == round(expected, 1) == 79.9
    assert r["band"] == "Güçlü"
    assert r["as_of_period"] == date(2025, 6, 30)
    assert r["weights_version"] == "v1-2026-11"
    assert r["missing_components"] == []
    assert comp(r, "profitability")["score"] == round(profitability, 1)
    assert comp(r, "leverage")["score"] == round(leverage, 1)
    assert comp(r, "liquidity")["score"] == 66.7
    assert comp(r, "cash_quality")["score"] == 100.0
    assert comp(r, "growth")["score"] == 77.5
    assert comp(r, "text_tone")["score"] == 80.0
    assert comp(r, "risk_change")["score"] == 80.0
    g = comp(r, "growth")
    assert ind(g, "revenue_yoy")["value"] == pytest.approx(0.1)
    assert ind(g, "net_income_yoy")["value"] == pytest.approx(0.1)


def test_output_shape_and_explanations():
    r = full_result()
    assert set(r) == {"total", "band", "as_of_period", "weights_version", "components",
                      "missing_components", "explanation"}
    assert [c["key"] for c in r["components"]] == [c["key"] for c in COMPONENTS]
    for c in r["components"]:
        assert {"key", "label", "weight", "score", "available", "indicators",
                "explanation"} <= set(c)
        for i in c["indicators"]:
            assert {"key", "label", "value", "display", "points"} <= set(i)
    p = comp(r, "profitability")
    nm = ind(p, "net_margin")
    assert (nm["display"], nm["points"]) == ("%20,0", 100.0)
    assert "Net kâr marjı %20,0 (eşik %0–%20 → 100 puan)" in p["explanation"]
    assert "bileşen puanı 86,9" in p["explanation"]
    assert ind(comp(r, "leverage"), "debt_to_equity")["display"] == "1,16x"
    assert ind(comp(r, "text_tone"), "mdna_net_tone")["display"] == "0,08"
    assert "Toplam skor" in r["explanation"] and "7 bileşen" in r["explanation"]


# ---- eksik bileşen, yeniden oranlama ve < 50 ağırlık ----

def test_missing_components_reweighted():
    r = score_for(one_year(**CLEAN), NO_SIGNALS)
    profitability = (50 + 50 + (0.05 / 0.15 * 100) + 50) / 4  # net %10, faal. %12,5, ROA %5, ROE %12,5
    leverage = (60 + 50) / 2       # B/Ö 1,5 → 60; Y/V 0,6 → 50
    liquidity = 50                 # cari oran 1,25
    cash = (75 + 50) / 2           # 90/100 = 0,9 → 75; FCF marjı %5 → 50
    expected = (25 * profitability + 20 * leverage + 15 * liquidity + 15 * cash) / 75
    assert r["total"] == round(expected, 1) == 52.4
    assert r["band"] == "İzlenmeli"
    assert r["missing_components"] == ["growth", "text_tone", "risk_change"]
    for key in r["missing_components"]:
        c = comp(r, key)
        assert c["available"] is False and c["score"] is None
        assert c["explanation"].startswith("Hesaplanamadı")
    assert "75/100" in r["explanation"]
    roa = ind(comp(r, "profitability"), "roa")
    assert roa["value"] == pytest.approx(0.05)


def test_total_none_when_available_weight_below_50():
    r = score_for({}, SIGNALS)  # yalnızca metin tonu (10) + risk (5) = 15
    assert r["total"] is None and r["band"] is None
    assert r["as_of_period"] is None
    assert comp(r, "text_tone")["available"] is True
    assert set(r["missing_components"]) == {"profitability", "leverage", "liquidity",
                                            "cash_quality", "growth"}
    assert "50" in r["explanation"]


def test_weight_exactly_50_produces_total_and_45_does_not():
    facts = one_year(revenue=1000, operating_income=125, net_income=100, total_assets=2000,
                     total_liabilities=1200, stockholders_equity=800)
    r = score_for(facts, {"risk_added": 0})  # kârlılık 25 + kaldıraç 20 + risk 5 = 50
    profitability = (50 + 50 + (0.05 / 0.15 * 100) + 50) / 4
    expected = (25 * profitability + 20 * 55 + 5 * 100) / 50
    assert r["total"] == round(expected, 1)
    r = score_for(facts, {"risk_added": None})  # 45
    assert r["total"] is None


# ---- özel kurallar ----

def test_negative_equity_sets_leverage_to_zero():
    facts = one_year(**{**CLEAN, "stockholders_equity": -200, "total_liabilities": 1500})
    r = score_for(facts, NO_SIGNALS)
    lev = comp(r, "leverage")
    assert lev["available"] is True and lev["score"] == 0.0
    assert ind(lev, "debt_to_equity")["points"] is None
    assert ind(lev, "liabilities_to_assets")["points"] == pytest.approx(25.0)  # 0,75 → 25
    assert "özkaynak negatif veya sıfır" in lev["explanation"]
    prof = comp(r, "profitability")
    assert ind(prof, "roe")["points"] is None
    assert prof["score"] == pytest.approx((50 + 50 + 0.05 / 0.15 * 100) / 3, abs=0.05)


def test_negative_net_income_positive_ocf_scores_50():
    facts = one_year(**{**CLEAN, "net_income": -50, "operating_cash_flow": 80, "capex": 60})
    c = comp(score_for(facts, NO_SIGNALS), "cash_quality")
    assert ind(c, "ocf_to_net_income")["points"] == 50.0
    assert ind(c, "ocf_to_net_income")["value"] is None
    assert ind(c, "fcf_margin")["points"] == pytest.approx(35.0)  # FCF marjı %2 → 35
    assert c["score"] == pytest.approx((50 + 35) / 2)


def test_negative_net_income_negative_ocf_scores_0():
    facts = one_year(**{**CLEAN, "net_income": -50, "operating_cash_flow": -10, "capex": 60})
    c = comp(score_for(facts, NO_SIGNALS), "cash_quality")
    assert ind(c, "ocf_to_net_income")["points"] == 0.0
    assert ind(c, "fcf_margin")["points"] == 0.0  # FCF marjı −%7 ≤ −%5
    assert c["score"] == 0.0 and c["available"] is True


def test_growth_skips_net_income_when_prior_non_positive():
    facts = one_year(**CLEAN)
    facts["revenue"].append(fact("revenue", PREV, 800))       # +%25 → 100
    facts["net_income"].append(fact("net_income", PREV, -10))
    g = comp(score_for(facts, NO_SIGNALS), "growth")
    assert ind(g, "revenue_yoy")["value"] == pytest.approx(0.25)
    assert ind(g, "net_income_yoy")["points"] is None
    assert g["score"] == 100.0


def test_growth_net_income_decline():
    facts = one_year(**CLEAN)
    facts["revenue"].append(fact("revenue", PREV, 1000))      # %0 → 40
    facts["net_income"].append(fact("net_income", PREV, 125))  # −%20 → 0
    g = comp(score_for(facts, NO_SIGNALS), "growth")
    assert ind(g, "revenue_yoy")["points"] == pytest.approx(40.0)
    assert ind(g, "net_income_yoy")["points"] == 0.0
    assert g["score"] == pytest.approx(20.0)


def test_text_tone_falls_back_to_risk_section():
    r = score_for({}, {"mdna_net_tone": None, "risk_net_tone": -0.5, "risk_added": 20})
    t = comp(r, "text_tone")
    assert [i["key"] for i in t["indicators"]] == ["risk_net_tone"]
    assert t["score"] == pytest.approx(50.0)
    assert comp(r, "risk_change")["score"] == 0.0  # 20 ≥ 15


def test_text_signals_none_is_accepted():
    r = score_for(one_year(**CLEAN), None)
    assert "text_tone" in r["missing_components"]


def test_growth_uses_tolerant_period_matching():
    # D1: gelir/net kâr 28 Aralık (52–53 hafta), bilanço 31 Aralık → aynı dönem; büyüme hesaplanır.
    flow, prev_flow, bs = date(2024, 12, 28), date(2023, 12, 30), date(2024, 12, 31)
    facts = {"revenue": [fact("revenue", prev_flow, 900), fact("revenue", flow, 1000)],
             "net_income": [fact("net_income", prev_flow, 80), fact("net_income", flow, 100)],
             "total_assets": [fact("total_assets", bs, 2000)],
             "stockholders_equity": [fact("stockholders_equity", bs, 800)]}
    result = compute_score(compute_ratios(facts), facts, NO_SIGNALS)
    assert result["as_of_period"] in (bs, bs.isoformat())
    growth = comp(result, "growth")
    assert ind(growth, "revenue_yoy")["value"] == pytest.approx(100 / 900)
    assert ind(growth, "net_income_yoy")["value"] == pytest.approx(0.25)
    assert ind(comp(result, "profitability"), "roa")["value"] == pytest.approx(100 / 2000)
