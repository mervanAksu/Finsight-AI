"""TTM (son on iki ay) testleri — docs/asama4-sozlesme.md §2–3, FR-04, FR-06, FR-11, NFR-06.

Beklenen değerler kodun çıktısından değil, fixture'daki sentetik sayılardan ELLE hesaplanmıştır
(tests/fixtures/companyfacts_ttm_example.json — "Example Quarterly Corp", mali yıl 30 Haziran):

    Gelir    A=1200 (FY2026), YTD_cur(2026-07-01..12-31)=650, YTD_prior(2025-07-01..12-31)=560
             TTM = 1200 + 650 − 560 = 1290
             önceki TTM = A_önceki 1000 + 560 − YTD_prior2(2024-07-01..12-31) 470 = 1090
             Tuzaklar: aynı 10-Q'da 3 aylık değer 320/290 (kullanılırsa 1230 çıkardı); önceki
             yılın 10-Q'sundaki 555 sonradan 560 olarak yeniden raporlandı (en son başvuru).
    Net kâr  A=120, YTD_cur 10-Q'da 70, 10-Q/A'da 66 (düzeltme, en son başvuru), YTD_prior=55
             TTM = 120 + 66 − 55 = 131; önceki TTM = 100 + 55 − 45 = 110
    Maliyet  700 + 380 − 330 = 750 → brüt kâr (türetilmiş) 1290 − 750 = 540
    Bilanço  E_Q=2026-12-31: varlık 2100, özkaynak 850, yükümlülük 1250
             E_Q − 1 yıl (2025-12-31, önceki yılın Q2 10-Q'su): varlık 1950, özkaynak 750
    OCF      önceki yılın YTD'si yok → TTM yok, yıllık 150 kullanılır (uydurma yok)
"""

from __future__ import annotations

from datetime import date

import pytest
from conftest import load

from finsight.ratios import NOTE_MIXED_BASIS, compute_ratios
from finsight.scoring import METHOD_VERSION, WEIGHTS_VERSION, compute_score
from finsight.xbrl import (REASON_TTM_NO_BALANCE, REASON_TTM_NO_YTD_CUR,
                           REASON_TTM_NO_YTD_PRIOR, build_dynamic, dynamic_from_companyfacts,
                           extract_all, extract_ttm, years_before)

E_A = date(2026, 6, 30)
E_Q = date(2026, 12, 31)
E_Q_PRIOR = date(2025, 12, 31)
ACC_Q2 = "0000000002-27-000003"
ACC_QA = "0000000002-27-000007"
ACC_Q_PREV = "0000000002-26-000001"
ACC_K26 = "0000000002-26-000010"


@pytest.fixture(scope="module")
def cf():
    return load("companyfacts_ttm_example.json")


@pytest.fixture(scope="module")
def ttm(cf):
    return extract_ttm(cf)


def latest(ttm: dict, metric: str) -> dict:
    return ttm["facts"][metric][-1]


def prior(ttm: dict, metric: str) -> dict:
    return ttm["facts"][metric][0]


# --------------------------------------------------------------------------- süreli kalemler


def test_periods_detected(ttm):
    assert ttm["annual_period"] == E_A
    assert ttm["as_of_period"] == E_Q


def test_revenue_ttm_uses_ytd_not_three_month_value(ttm):
    rev = latest(ttm, "revenue")
    assert rev["value"] == 1290  # 1230 olsaydı 3 aylık değer karışmış olurdu
    assert rev["basis"] == "ttm"
    assert rev["period_end"] == E_Q
    assert rev["period_start"] == date(2026, 1, 1)
    assert rev["form"] == "10-Q" and rev["accession_no"] == ACC_Q2
    assert rev["tag"] == "RevenueFromContractWithCustomerExcludingAssessedTax"


def test_revenue_ttm_derived_from_traceable_parts(ttm):
    """NFR-06: hesaba giren her parça (rol, accession, etiket, dönem, değer)."""
    parts = {p["role"]: p for p in latest(ttm, "revenue")["derived_from"]}
    assert set(parts) == {"annual", "ytd_current", "ytd_prior"}
    assert (parts["annual"]["value"], parts["annual"]["accession_no"]) == (1200, ACC_K26)
    assert parts["annual"]["period_end"] == E_A
    assert (parts["ytd_current"]["period_start"], parts["ytd_current"]["period_end"]) == (
        date(2026, 7, 1), E_Q)
    assert parts["ytd_current"]["value"] == 650
    # Önceki yılın YTD'si: aynı dönemi raporlayan iki başvurudan en son başvurulan (560, 555 değil)
    assert parts["ytd_prior"]["value"] == 560 and parts["ytd_prior"]["accession_no"] == ACC_Q2
    total = (parts["annual"]["value"] + parts["ytd_current"]["value"]
             - parts["ytd_prior"]["value"])
    assert total == latest(ttm, "revenue")["value"]


def test_prior_ttm_for_growth(ttm):
    rev_prev = prior(ttm, "revenue")
    assert rev_prev["value"] == 1090 and rev_prev["period_end"] == E_Q_PRIOR
    assert {p["role"]: p["value"] for p in rev_prev["derived_from"]} == {
        "annual": 1000, "ytd_current": 560, "ytd_prior": 470}


def test_amended_10q_wins_for_same_period(ttm):
    ni = latest(ttm, "net_income")
    assert ni["value"] == 131  # 120 + 66 − 55 (10-Q'daki 70 değil, 10-Q/A'daki 66)
    assert ni["form"] == "10-Q/A" and ni["accession_no"] == ACC_QA
    assert prior(ttm, "net_income")["value"] == 110


def test_missing_prior_ytd_means_no_ttm_no_invention(ttm):
    assert "operating_cash_flow" not in ttm["facts"]
    assert ttm["reasons"]["operating_cash_flow"].startswith(REASON_TTM_NO_YTD_PRIOR)
    assert "NetCashProvidedByUsedInOperatingActivities" in ttm["reasons"]["operating_cash_flow"]


def test_gross_profit_derived_from_ttm_revenue_minus_cost(ttm):
    gp = latest(ttm, "gross_profit")
    assert gp["value"] == 540 and gp["tag"] == "derived"
    comps = {p["metric"]: p for p in gp["derived_from"]}
    assert set(comps) == {"revenue", "cost_of_revenue"}
    assert comps["revenue"]["role"] == "component" and comps["revenue"]["value"] == 1290
    assert comps["cost_of_revenue"]["value"] == 750
    assert {p["role"] for p in comps["revenue"]["parts"]} == {"annual", "ytd_current",
                                                               "ytd_prior"}
    assert prior(ttm, "gross_profit")["value"] == 1090 - 640


# --------------------------------------------------------------------------- anlık kalemler


def test_instant_items_from_latest_10q_balance(ttm):
    assets = latest(ttm, "total_assets")
    assert (assets["value"], assets["period_end"], assets["accession_no"]) == (2100, E_Q, ACC_Q2)
    assert assets["derived_from"] is None and assets["basis"] == "ttm"
    # Ortalama için önceki: E_Q − 1 yıl bilançosu (önceki yılın aynı çeyreği)
    prev = prior(ttm, "total_assets")
    assert (prev["value"], prev["period_end"], prev["accession_no"]) == (
        1950, E_Q_PRIOR, ACC_Q_PREV)
    assert latest(ttm, "stockholders_equity")["value"] == 850
    assert latest(ttm, "total_liabilities")["value"] == 1250


# --------------------------------------------------------------------------- dinamik görünüm


def test_build_dynamic_ttm_basis_and_filing(cf, ttm):
    dyn = build_dynamic(extract_all(cf, years=0), ttm["facts"], ttm["reasons"])
    assert dyn["basis"] == "ttm"
    assert dyn["as_of_period"] == dyn["data_through"] == E_Q
    assert dyn["annual_period"] == E_A
    # Skorun dayandığı başvuru: temel kalemlerin en son başvurusu (net kâr düzeltmesi 10-Q/A)
    assert dyn["as_of_filing"] == {"form": "10-Q/A", "accession_no": ACC_QA,
                                   "filing_date": date(2027, 2, 10)}
    # TTM'si olmayan kalem yıllık değere düşer, nedeni yazılır
    ocf = dyn["latest"]["operating_cash_flow"]
    assert ocf["value"] == 150 and ocf.get("basis", "annual") == "annual"
    assert "yıllık değer kullanıldı" in dyn["reasons"]["operating_cash_flow"]
    # Hiç verisi olmayan kalem için "yıllığa düşüldü" denmez
    assert "operating_income" not in dyn["reasons"]


def test_dynamic_ignores_stale_ttm_points(cf, ttm):
    """Yeni 10-K geldikten sonra kalan eski TTM noktası (E_A'dan önce) yok sayılır."""
    stale = {"revenue": [{**latest(ttm, "revenue"), "period_end": date(2026, 3, 31)}]}
    dyn = build_dynamic(extract_all(cf, years=0), stale)
    assert dyn["basis"] == "annual" and dyn["as_of_period"] == E_A


def test_annual_only_company_has_annual_basis():
    """Mevcut fixture: son 10-K'dan sonra 10-Q verisi yok → TTM = yıllık (basis annual)."""
    cf = load("companyfacts_full_example.json")
    out = extract_ttm(cf)
    assert out["as_of_period"] is None and out["facts"] == {}
    assert out["reason"]
    dyn = dynamic_from_companyfacts(cf)
    annual = extract_all(cf, years=0)
    assert dyn["basis"] == "annual"
    assert dyn["as_of_period"] == annual["revenue"][-1]["period_end"]
    assert dyn["reasons"] == {}


def test_annual_behaviour_unchanged_when_dynamic_is_annual():
    """Yıllık şirkette dinamik seriyle hesaplanan oran ve skor, eski (yıllık) yolla aynıdır."""
    cf = load("companyfacts_full_example.json")
    facts = extract_all(cf, years=4)
    dyn = dynamic_from_companyfacts(cf)
    old_r = compute_ratios(facts, periods=3)
    new_r = compute_ratios(facts, periods=3, dynamic=dyn)
    assert new_r["periods"] == old_r["periods"]
    for a, b in zip(old_r["ratios"], new_r["ratios"]):
        assert [v["value"] for v in a["values"]] == [v["value"] for v in b["values"]]
        assert b["basis"] == "annual"
    old_s = compute_score(old_r, facts, None)
    new_s = compute_score(new_r, facts, None, dynamic=dyn)
    assert old_s["total"] == new_s["total"]
    assert new_s["basis"] == "annual" and new_s["basis_notes"] == []


# --------------------------------------------------------------------------- oranlar (TTM sütunu)


@pytest.fixture(scope="module")
def ttm_ratios(cf):
    facts = extract_all(cf, years=4)
    dyn = dynamic_from_companyfacts(cf)
    return facts, dyn, compute_ratios(facts, periods=3, dynamic=dyn)


def value(res: dict, key: str, end: date) -> dict:
    r = next(r for r in res["ratios"] if r["key"] == key)
    return next(v for v in r["values"] if v["period_end"] == end)


def test_ratio_periods_include_ttm_column(ttm_ratios):
    _, _, res = ttm_ratios
    assert res["periods"][-1] == E_Q
    assert res["periods"][:-1] == [date(2024, 6, 30), date(2025, 6, 30), E_A]
    assert res["period_info"][-1] == {"period_end": E_Q, "basis": "ttm",
                                      "label": "TTM 2026-12-31"}
    assert res["period_info"][0]["label"] == "2024-06-30"
    assert all(r["basis"] == "ttm" for r in res["ratios"])


def test_ttm_ratio_values_hand_calculated(ttm_ratios):
    _, _, res = ttm_ratios
    assert value(res, "net_margin", E_Q)["value"] == pytest.approx(131 / 1290)
    assert value(res, "gross_margin", E_Q)["value"] == pytest.approx(540 / 1290)
    # Ortalama: (E_Q + E_Q − 1 yıl) / 2
    roa = value(res, "roa", E_Q)
    assert roa["value"] == pytest.approx(131 / ((2100 + 1950) / 2)) and roa["note"] is None
    assert value(res, "roe", E_Q)["value"] == pytest.approx(131 / ((850 + 750) / 2))
    assert value(res, "debt_to_equity", E_Q)["value"] == pytest.approx(1250 / 850)
    assert value(res, "current_ratio", E_Q)["value"] == pytest.approx(950 / 500)
    assert value(res, "net_margin", E_Q)["basis"] == "ttm"
    # Yıllık sütun değişmedi
    assert value(res, "net_margin", E_A)["value"] == pytest.approx(120 / 1200)
    assert value(res, "net_margin", E_A)["basis"] == "annual"


def test_ttm_ratio_with_annual_fallback_input_is_flagged(ttm_ratios):
    _, _, res = ttm_ratios
    q = value(res, "ocf_to_net_income", E_Q)
    assert q["value"] == pytest.approx(150 / 131)  # OCF yıllık (TTM yok), net kâr TTM
    assert q["note"] == NOTE_MIXED_BASIS + "operating_cash_flow"
    # Eksik veri sıfır değildir: capex yok → oran yok
    fcf = value(res, "fcf_margin", E_Q)
    assert fcf["value"] is None and fcf["status"] == "missing"
    assert "capex" in fcf["reason"]


# --------------------------------------------------------------------------- skor


def test_score_uses_ttm_basis(ttm_ratios):
    facts, dyn, res = ttm_ratios
    signals = {"mdna_net_tone": 0.0, "risk_net_tone": None, "risk_added": 3,
               "mdna_filing": {"form": "10-Q", "accession_no": ACC_Q2,
                               "filing_date": date(2027, 1, 27)}}
    score = compute_score(res, facts, signals, dynamic=dyn)
    assert score["basis"] == "ttm"
    assert score["as_of_period"] == score["data_through"] == E_Q
    assert score["as_of_filing"]["accession_no"] == ACC_QA
    assert score["method_version"] == METHOD_VERSION == "v2-2026-11-ttm"
    assert score["weights_version"] == WEIGHTS_VERSION  # ağırlıklar değişmedi
    assert any("operating_cash_flow" in n for n in score["basis_notes"])
    assert "son on iki ay (TTM)" in score["explanation"]

    growth = next(c for c in score["components"] if c["key"] == "growth")
    rev = next(i for i in growth["indicators"] if i["key"] == "revenue_yoy")
    ni = next(i for i in growth["indicators"] if i["key"] == "net_income_yoy")
    assert rev["value"] == pytest.approx((1290 - 1090) / 1090) and rev["note"] is None
    assert ni["value"] == pytest.approx((131 - 110) / 110)

    lev = next(c for c in score["components"] if c["key"] == "leverage")
    de = next(i for i in lev["indicators"] if i["key"] == "debt_to_equity")
    assert de["value"] == pytest.approx(1250 / 850)

    tone = next(c for c in score["components"] if c["key"] == "text_tone")
    assert tone["indicators"][0]["source"]["accession_no"] == ACC_Q2


def test_growth_falls_back_to_annual_yoy_without_prior_ttm(cf):
    """Önceki TTM hesaplanamazsa büyüme yıllık YoY'a düşer ve not yazılır (sözleşme §2)."""
    annual = extract_all(cf, years=0)
    t = extract_ttm(cf, annual)
    only_latest = {m: [p for p in pts if p["period_end"] == E_Q] for m, pts in t["facts"].items()}
    dyn = build_dynamic(annual, only_latest, t["reasons"])
    facts = extract_all(cf, years=4)
    score = compute_score(compute_ratios(facts, 3, dynamic=dyn), facts, None, dynamic=dyn)
    growth = next(c for c in score["components"] if c["key"] == "growth")
    rev = next(i for i in growth["indicators"] if i["key"] == "revenue_yoy")
    assert rev["value"] == pytest.approx((1200 - 1000) / 1000)
    assert rev["note"] == "TTM büyümesi hesaplanamadı; yıllık YoY kullanıldı"
    assert "yıllık YoY" in growth["explanation"]


# --------------------------------------------------------------------------- sınır durumları


def _facts(tag: str, rows: list[tuple], unit: str = "USD") -> dict:
    out = []
    for start, end, val, accn, form, filed in rows:
        f = {"end": end, "val": val, "accn": accn, "form": form, "filed": filed,
             "fy": 2027, "fp": "Q1"}
        if start:
            f["start"] = start
        out.append(f)
    return {"facts": {"us-gaap": {tag: {"units": {unit: out}}}}}


REV = "Revenues"


def test_52_53_week_fiscal_year_dates_match_within_tolerance():
    """52–53 haftalık yıl: E_A 2026-09-26 (Cumartesi); önceki yılın Q1 sonu 2025-12-27 —
    takvim olarak E_Q − 1 yıl (2025-12-26) ile 1 gün fark (±7 gün tolerans).
    TTM = 1000 + 300 − 280 = 1020."""
    cf = _facts(REV, [
        ("2024-09-29", "2025-09-27", 950, "K25", "10-K", "2025-11-01"),
        ("2025-09-28", "2026-09-26", 1000, "K26", "10-K", "2026-11-01"),
        ("2026-09-27", "2026-12-26", 300, "Q27", "10-Q", "2027-01-30"),
        ("2025-09-28", "2025-12-27", 280, "Q27", "10-Q", "2027-01-30"),
        ("2024-09-29", "2024-12-28", 260, "Q26", "10-Q", "2026-01-30"),
    ])
    out = extract_ttm(cf)
    assert out["annual_period"] == date(2026, 9, 26)
    assert out["as_of_period"] == date(2026, 12, 26)
    rev = out["facts"]["revenue"]
    assert rev[-1]["value"] == 1020
    assert rev[0]["value"] == 950 + 280 - 260  # önceki TTM


def test_q1_three_month_equals_ytd_is_accepted():
    """1. çeyrekte 3 aylık değer = YTD (başlangıç E_A + 1); doğru seçilir."""
    cf = _facts(REV, [
        ("2025-01-01", "2025-12-31", 400, "K25", "10-K", "2026-02-01"),
        ("2026-01-01", "2026-03-31", 120, "Q1", "10-Q", "2026-05-01"),
        ("2025-01-01", "2025-03-31", 100, "Q1", "10-Q", "2026-05-01"),
    ])
    out = extract_ttm(cf)
    assert out["facts"]["revenue"][-1]["value"] == 420
    assert len(out["facts"]["revenue"]) == 1  # önceki TTM için A_önceki yok
    assert out["facts"]["revenue"][-1]["period_start"] == date(2025, 4, 1)


def test_only_three_month_value_in_q2_is_not_used_as_ytd():
    """Q2 10-Q'da yalnızca 3 aylık değer varsa (YTD yok) TTM üretilmez — 3 ay ≠ YTD."""
    cf = _facts(REV, [
        ("2025-01-01", "2025-12-31", 400, "K25", "10-K", "2026-02-01"),
        ("2026-04-01", "2026-06-30", 130, "Q2", "10-Q", "2026-08-01"),
        ("2025-04-01", "2025-06-30", 110, "Q2", "10-Q", "2026-08-01"),
    ])
    out = extract_ttm(cf)
    assert "revenue" not in out["facts"]
    assert out["reasons"]["revenue"].startswith(REASON_TTM_NO_YTD_CUR)


def test_10q_older_than_latest_10k_does_not_create_ttm():
    """10-Q dönemi son 10-K'dan önceyse (karşılaştırma değeri) TTM yok → yıllık."""
    cf = _facts(REV, [
        ("2025-01-01", "2025-12-31", 400, "K25", "10-K", "2026-02-01"),
        ("2025-01-01", "2025-09-30", 290, "Q3", "10-Q", "2025-11-01"),
    ])
    out = extract_ttm(cf)
    assert out["as_of_period"] is None and out["facts"] == {}


def test_missing_balance_at_eq_falls_back_with_reason():
    cf = _facts(REV, [
        ("2025-01-01", "2025-12-31", 400, "K25", "10-K", "2026-02-01"),
        ("2026-01-01", "2026-03-31", 120, "Q1", "10-Q", "2026-05-01"),
        ("2025-01-01", "2025-03-31", 100, "Q1", "10-Q", "2026-05-01"),
    ])
    cf["facts"]["us-gaap"]["Assets"] = {"units": {"USD": [
        {"end": "2025-12-31", "val": 900, "accn": "K25", "form": "10-K", "filed": "2026-02-01"}]}}
    out = extract_ttm(cf)
    assert out["reasons"]["total_assets"].startswith(REASON_TTM_NO_BALANCE)
    dyn = build_dynamic(extract_all(cf, years=0), out["facts"], out["reasons"])
    assert dyn["basis"] == "ttm"
    assert dyn["latest"]["total_assets"]["value"] == 900  # yıllık değer, uydurma yok
    assert "10-Q bilanço değeri yok" in dyn["reasons"]["total_assets"]


def test_total_liabilities_derived_at_eq_when_tag_missing():
    cf = _facts(REV, [
        ("2025-01-01", "2025-12-31", 400, "K25", "10-K", "2026-02-01"),
        ("2026-01-01", "2026-03-31", 120, "Q1", "10-Q", "2026-05-01"),
        ("2025-01-01", "2025-03-31", 100, "Q1", "10-Q", "2026-05-01"),
    ])
    g = cf["facts"]["us-gaap"]
    k25 = {"end": "2025-12-31", "accn": "K25", "form": "10-K", "filed": "2026-02-01"}
    g["LiabilitiesAndStockholdersEquity"] = {"units": {"USD": [
        {**k25, "val": 950},
        {"end": "2026-03-31", "val": 1000, "accn": "Q1", "form": "10-Q", "filed": "2026-05-01"}]}}
    g["StockholdersEquity"] = {"units": {"USD": [
        {**k25, "val": 280},
        {"end": "2026-03-31", "val": 300, "accn": "Q1", "form": "10-Q", "filed": "2026-05-01"}]}}
    out = extract_ttm(cf)
    tl = out["facts"]["total_liabilities"][-1]
    assert tl["value"] == 700 and tl["tag"] == "derived"
    # NFR-06: bileşenler tam sözlük (rol, kalem, etiket, accession, dönem, değer)
    comps = {c["tag"]: c for c in tl["derived_from"]}
    assert set(comps) == {"LiabilitiesAndStockholdersEquity", "StockholdersEquity"}
    assert comps["LiabilitiesAndStockholdersEquity"]["value"] == 1000
    assert all(c["role"] == "component" and c["accession_no"] == "Q1"
               and c["period_end"] == date(2026, 3, 31) for c in comps.values())


def test_non_december_fiscal_year_end_and_leap_day():
    assert years_before(date(2028, 2, 29)) == date(2027, 2, 28)
    assert years_before(date(2026, 9, 30), 2) == date(2024, 9, 30)


def test_no_annual_data():
    out = extract_ttm({"facts": {"us-gaap": {}}})
    assert out["annual_period"] is None and out["reason"] == "yıllık veri yok"
    dyn = build_dynamic({}, {})
    assert dyn["basis"] == "annual" and dyn["as_of_period"] is None
    assert dyn["as_of_filing"] is None


# --------------------------------------------------------------------------- inceleme düzeltmeleri


def _q(start, end, val, accn, form="10-Q", filed="2026-11-01"):
    return (start, end, val, accn, form, filed)


def _add(cf: dict, tag: str, rows: list[tuple]) -> None:
    cf["facts"]["us-gaap"][tag] = _facts(tag, rows)["facts"]["us-gaap"][tag]


def test_balance_only_ttm_keeps_annual_basis():
    """Madde 1: gelir/net kâr TTM'si yok (YTD_prior eksik), yalnızca bilanço 10-Q'da → dayanak
    yıllık; skor ve oran sütunu E_A'da kalır."""
    cf = _facts(REV, [
        ("2025-01-01", "2025-12-31", 400, "K25", "10-K", "2026-02-01"),
        ("2026-01-01", "2026-03-31", 120, "Q1", "10-Q", "2026-05-01"),  # YTD_prior yok
    ])
    _add(cf, "Assets", [(None, "2025-12-31", 900, "K25", "10-K", "2026-02-01"),
                        (None, "2026-03-31", 950, "Q1", "10-Q", "2026-05-01")])
    out = extract_ttm(cf)
    assert "total_assets" in out["facts"] and "revenue" not in out["facts"]
    dyn = build_dynamic(extract_all(cf, years=0), out["facts"], out["reasons"])
    assert dyn["basis"] == "annual" and dyn["as_of_period"] == date(2025, 12, 31)
    assert "dayanak yıllık" in dyn["note"]
    assert dyn["latest"]["total_assets"]["value"] == 900
    facts = extract_all(cf, years=4)
    res = compute_ratios(facts, 3, dynamic=dyn)
    assert res["periods"][-1] == date(2025, 12, 31)
    assert compute_score(res, facts, None, dynamic=dyn)["basis"] == "annual"


def test_no_ttm_from_non_primary_revenue_tag():
    """Madde 2: yıllık birincil etiket RevenueFromContract...; YTD parçaları yalnızca
    Revenues altında → TTM üretilmez, diğer etikete geçilmez."""
    cf = _facts("RevenueFromContractWithCustomerExcludingAssessedTax", [
        ("2025-01-01", "2025-12-31", 400, "K25", "10-K", "2026-02-01")])
    _add(cf, "Revenues", [
        ("2024-01-01", "2024-12-31", 380, "K24", "10-K", "2025-02-01"),
        ("2025-01-01", "2025-12-31", 401, "K25", "10-K", "2025-02-01"),
        _q("2026-01-01", "2026-03-31", 120, "Q1"), _q("2025-01-01", "2025-03-31", 100, "Q1")])
    out = extract_ttm(cf)
    assert "revenue" not in out["facts"]
    assert out["reasons"]["revenue"] == (
        "TTM parçaları birincil etiket (RevenueFromContractWithCustomerExcludingAssessedTax) "
        "altında eksik; Revenues etiketine geçilmedi")


def test_no_ttm_from_profitloss_when_primary_is_netincomeloss():
    cf = _facts("NetIncomeLoss", [("2025-01-01", "2025-12-31", 40, "K25", "10-K", "2026-02-01")])
    _add(cf, "ProfitLoss", [_q("2026-01-01", "2026-03-31", 12, "Q1"),
                            _q("2025-01-01", "2025-03-31", 10, "Q1"),
                            ("2025-01-01", "2025-12-31", 42, "K24", "10-K", "2025-02-01")])
    out = extract_ttm(cf)
    assert "net_income" not in out["facts"]
    assert "birincil etiket (NetIncomeLoss)" in out["reasons"]["net_income"]
    assert "ProfitLoss etiketine geçilmedi" in out["reasons"]["net_income"]


def test_instant_prior_only_from_same_tag_else_period_end_note():
    """Madde 2 + 7: önceki bilanço başka etikette olsa da kullanılmaz → ROA'da dönem sonu notu."""
    cf = _facts(REV, [
        ("2025-01-01", "2025-12-31", 400, "K25", "10-K", "2026-02-01"),
        _q("2026-01-01", "2026-03-31", 120, "Q1"), _q("2025-01-01", "2025-03-31", 100, "Q1")])
    _add(cf, "NetIncomeLoss", [("2025-01-01", "2025-12-31", 40, "K25", "10-K", "2026-02-01"),
                               _q("2026-01-01", "2026-03-31", 12, "Q1"),
                               _q("2025-01-01", "2025-03-31", 10, "Q1")])
    _add(cf, "Assets", [(None, "2025-12-31", 900, "K25", "10-K", "2026-02-01"),
                        (None, "2026-03-31", 1000, "Q1", "10-Q", "2026-05-01")])
    out = extract_ttm(cf)
    assert [p["period_end"] for p in out["facts"]["total_assets"]] == [date(2026, 3, 31)]
    dyn = build_dynamic(extract_all(cf, years=0), out["facts"], out["reasons"])
    facts = extract_all(cf, years=4)
    res = compute_ratios(facts, 3, dynamic=dyn)
    roa = value(res, "roa", date(2026, 3, 31))
    assert roa["value"] == pytest.approx((40 + 12 - 10) / 1000)
    assert roa["note"] == "ortalama yerine dönem sonu değeri"


def test_restated_comparative_flagged_with_original_value(ttm):
    """Madde 4: YTD_prior en son başvurudan (560); önceki 10-Q'daki 555 original_value."""
    parts = {p["role"]: p for p in latest(ttm, "revenue")["derived_from"]}
    assert parts["ytd_prior"]["restated"] is True
    assert parts["ytd_prior"]["original_value"] == 555
    assert parts["ytd_current"]["restated"] is False
    assert parts["ytd_current"]["original_value"] is None
    ni_cur = {p["role"]: p for p in latest(ttm, "net_income")["derived_from"]}["ytd_current"]
    assert ni_cur["restated"] is True and ni_cur["original_value"] == 70  # 10-Q/A düzeltmesi


def test_q3_nine_month_ytd_not_three_month():
    """Madde 7: Q3 10-Q'da 9 aylık (YTD) ve 3 aylık değer → 9 ay seçilir.
    TTM = 1000 + 780 − 720 = 1060 (3 ay kullanılsaydı 1000 + 270 − 250 = 1020)."""
    cf = _facts(REV, [
        ("2025-01-01", "2025-12-31", 1000, "K25", "10-K", "2026-02-01"),
        _q("2026-01-01", "2026-09-30", 780, "Q3"), _q("2026-07-01", "2026-09-30", 270, "Q3"),
        _q("2025-01-01", "2025-09-30", 720, "Q3"), _q("2025-07-01", "2025-09-30", 250, "Q3")])
    out = extract_ttm(cf)
    assert out["as_of_period"] == date(2026, 9, 30)
    assert out["facts"]["revenue"][-1]["value"] == 1060


def test_52_53_week_income_and_balance_dates_differ():
    """Madde 7: gelir 2026-12-27'de, bilanço 2026-12-31'de bitiyor → aynı TTM dönemi."""
    cf = _facts(REV, [
        ("2024-12-29", "2025-12-28", 1000, "K25", "10-K", "2026-02-01"),
        _q("2025-12-29", "2026-03-29", 260, "Q1"), _q("2024-12-29", "2025-03-30", 240, "Q1")])
    _add(cf, "NetIncomeLoss", [("2024-12-29", "2025-12-28", 100, "K25", "10-K", "2026-02-01"),
                               _q("2025-12-29", "2026-03-29", 30, "Q1"),
                               _q("2024-12-29", "2025-03-30", 20, "Q1")])
    _add(cf, "Assets", [(None, "2025-12-31", 2000, "K25", "10-K", "2026-02-01"),
                        (None, "2026-03-31", 2100, "Q1", "10-Q", "2026-05-01"),
                        (None, "2025-03-31", 1900, "Q0", "10-Q", "2025-05-01")])
    out = extract_ttm(cf)
    assert out["as_of_period"] == date(2026, 3, 31)
    assert out["facts"]["revenue"][-1]["value"] == 1020
    dyn = build_dynamic(extract_all(cf, years=0), out["facts"], out["reasons"])
    assert dyn["basis"] == "ttm"
    facts = extract_all(cf, years=4)
    res = compute_ratios(facts, 3, dynamic=dyn)
    assert res["periods"][-1] == date(2026, 3, 31)
    assert value(res, "roa", date(2026, 3, 31))["value"] == pytest.approx(110 / 2000)
    assert value(res, "net_margin", date(2026, 3, 31))["value"] == pytest.approx(110 / 1020)


def test_negative_ttm_net_income_and_equity():
    """Madde 7: TTM net kâr negatif (40 + 5 − 60 = −15), E_Q özkaynağı negatif."""
    cf = _facts(REV, [
        ("2025-01-01", "2025-12-31", 400, "K25", "10-K", "2026-02-01"),
        _q("2026-01-01", "2026-03-31", 120, "Q1"), _q("2025-01-01", "2025-03-31", 100, "Q1")])
    _add(cf, "NetIncomeLoss", [("2025-01-01", "2025-12-31", 40, "K25", "10-K", "2026-02-01"),
                               _q("2026-01-01", "2026-03-31", 5, "Q1"),
                               _q("2025-01-01", "2025-03-31", 60, "Q1")])
    _add(cf, "StockholdersEquity", [(None, "2025-12-31", 50, "K25", "10-K", "2026-02-01"),
                                    (None, "2026-03-31", -20, "Q1", "10-Q", "2026-05-01")])
    _add(cf, "Liabilities", [(None, "2025-12-31", 500, "K25", "10-K", "2026-02-01"),
                             (None, "2026-03-31", 520, "Q1", "10-Q", "2026-05-01")])
    _add(cf, "NetCashProvidedByUsedInOperatingActivities", [
        ("2025-01-01", "2025-12-31", 30, "K25", "10-K", "2026-02-01"),
        _q("2026-01-01", "2026-03-31", 10, "Q1"), _q("2025-01-01", "2025-03-31", 8, "Q1")])
    out = extract_ttm(cf)
    assert out["facts"]["net_income"][-1]["value"] == -15
    dyn = build_dynamic(extract_all(cf, years=0), out["facts"], out["reasons"])
    facts = extract_all(cf, years=4)
    res = compute_ratios(facts, 3, dynamic=dyn)
    end = date(2026, 3, 31)
    assert value(res, "net_margin", end)["value"] == pytest.approx(-15 / 420)
    assert value(res, "roe", end)["reason"] == "özkaynak negatif veya sıfır; oran anlamlı değil"
    assert value(res, "debt_to_equity", end)["value"] is None
    assert value(res, "ocf_to_net_income", end)["reason"] == (
        "net kâr sıfır veya negatif; oran anlamlı değil")
    score = compute_score(res, facts, None, dynamic=dyn)
    lev = next(c for c in score["components"] if c["key"] == "leverage")
    assert lev["score"] == 0.0  # özkaynak ≤ 0 kuralı TTM bilançosunda da geçerli
    cq = next(c for c in score["components"] if c["key"] == "cash_quality")
    assert cq["indicators"][0]["points"] == 50.0  # net kâr ≤ 0, OCF > 0
