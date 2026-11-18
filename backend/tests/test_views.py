"""views.py (API sözleşmesi şekilleri) ve edgar arama/8-K yardımcıları — saf testler.

Anahtar kümeleri docs/api-contract.md'den kopyalanmıştır; sözleşme değişirse burası da değişir.
"""

import csv
import io
import json
from datetime import date, datetime
from decimal import Decimal

import numpy as np
import pytest
from conftest import load

from finsight import DISCLAIMER
from finsight.edgar import (ITEM_LABELS_8K, build_ticker_map, item_label, parse_filings,
                            search_companies, split_items)
from finsight.ratios import RATIO_DEFS, compute_ratios
from finsight.risk_diff import compare_risk_sections
from finsight.scoring import compute_score
from finsight.views import (EXPORT_COLUMNS, KEY_METRICS, company_list_item, company_view,
                            compare_view, export_csv, export_rows, facts_view, filing_ref,
                            filings_view, job_view, json_safe, overview_view, ratios_view,
                            risk_view, score_view, sentiment_view, source_view)
from finsight.xbrl import METRICS, extract_all, yoy_ratio

COMPANY_KEYS = {"ticker", "cik", "name", "sic", "sic_description", "fiscal_year_end",
                "exchanges"}
FILING_REF_KEYS = {"accession_no", "form", "filing_date", "report_date", "url"}
SOURCE_KEYS = {"xbrl_tag", "form", "accession_no", "filed", "derived_from", "url"}

COMPANY = {"id": 1, "ticker": "EXJN", "cik": "0000000002", "name": "Example June Corp",
           "sic": "7372", "sic_description": "Services-Prepackaged Software",
           "fiscal_year_end": "0630", "exchanges": ["Nasdaq"],
           "analysis_updated_at": datetime(2026, 11, 18, 13, 5, 0)}


def strict_json(obj) -> str:
    """NaN/inf veya serileştirilemeyen tip varsa hata verir."""
    return json.dumps(obj, allow_nan=False)


@pytest.fixture(scope="module")
def facts():
    return extract_all(load("companyfacts_full_example.json"), years=4)


@pytest.fixture(scope="module")
def ratios(facts):
    return compute_ratios(facts, periods=3)


@pytest.fixture(scope="module")
def score(facts, ratios):
    return compute_score(ratios, facts, {"mdna_net_tone": 0.1, "risk_net_tone": -0.5,
                                         "risk_added": 2})


def filing(acc, form, filed, report=None, items=None, analyzed=False):
    return {"id": hash(acc) % 1000, "accession_no": acc, "form": form,
            "filing_date": date.fromisoformat(filed),
            "report_date": date.fromisoformat(report) if report else None,
            "url": f"https://www.sec.gov/Archives/edgar/data/2/{acc.replace('-', '')}/d.htm",
            "items": items, "analyzed": analyzed}


K_OLD = filing("0000000002-24-000040", "10-K", "2024-08-02", "2024-06-30", analyzed=True)
K_NEW = filing("0000000002-25-000040", "10-K", "2025-08-01", "2025-06-30", analyzed=True)
Q_NEW = filing("0000000002-25-000050", "10-Q", "2025-11-01", "2025-09-30", analyzed=True)


def section(f, code, labels, model="lexicon-fallback", char_start=0):
    return {"filing": f, "item_code": code, "title": "Başlık " + code, "char_start": char_start,
            "model": model,
            "sentences": [{"index": i, "sentence": f"Sentence {code} number {i} here.",
                           "label": lab, "score": 0.5 + i / 100} for i, lab in enumerate(labels)]}


@pytest.fixture
def sentiment_sections():
    return [
        section(Q_NEW, "2", ["positive", "neutral"], char_start=10),
        section(K_NEW, "7", ["positive", "positive", "negative", "neutral"], char_start=900),
        section(K_NEW, "1A", ["negative", "negative", "neutral", "positive"], char_start=100),
        section(K_OLD, "7", ["negative"] * 3, char_start=900),
    ]


@pytest.fixture
def risk():
    old = "Paragraph about suppliers and shortages that could harm us in the future markets."
    new = old + "\n\nCompletely new paragraph about cybersecurity incidents and ransomware " \
        "attacks on our factories."
    cmp = compare_risk_sections(old, new)
    return {"old_filing": K_OLD, "new_filing": K_NEW, **cmp}


# --------------------------------------------------------------------------- json_safe


def test_json_safe_converts_dates_numpy_decimal_nan():
    obj = {"d": date(2025, 6, 30), "dt": datetime(2026, 11, 18, 13, 0, 0, 123456),
           "f32": np.float32(0.5), "i64": np.int64(7), "nan": float("nan"),
           "npnan": np.float64("nan"), "inf": float("inf"), "dec": Decimal("12.50"),
           "arr": np.array([1.0, 2.0], dtype=np.float32), "tup": (1, 2), "b": np.bool_(True),
           "nested": [{"x": date(2024, 1, 1)}], 3: "int key"}
    out = json_safe(obj)
    strict_json(out)
    assert out["d"] == "2025-06-30"
    assert out["dt"] == "2026-11-18T13:00:00Z"
    assert out["f32"] == 0.5 and isinstance(out["f32"], float)
    assert out["i64"] == 7 and isinstance(out["i64"], int)
    assert out["nan"] is None and out["npnan"] is None and out["inf"] is None
    assert out["dec"] == 12.5
    assert out["arr"] == [1.0, 2.0]
    assert out["tup"] == [1, 2] and out["b"] is True
    assert out["nested"] == [{"x": "2024-01-01"}] and out["3"] == "int key"


# --------------------------------------------------------------------------- ortak nesneler


def test_company_and_filing_ref_shapes():
    c = company_view(COMPANY)
    assert set(c) == COMPANY_KEYS and c["exchanges"] == ["Nasdaq"]
    assert company_view({"ticker": "X", "cik": "1", "name": "n"})["exchanges"] == []
    ref = filing_ref(K_NEW)
    assert set(ref) == FILING_REF_KEYS
    assert ref["filing_date"] == "2025-08-01" and ref["report_date"] == "2025-06-30"
    assert filing_ref(None) is None


def test_source_view_direct_and_derived(facts):
    direct = source_view(facts["revenue"][-1], "0000000002")
    assert set(direct) == SOURCE_KEYS
    assert direct == {"xbrl_tag": "us-gaap:Revenues", "form": "10-K",
                      "accession_no": "0000000002-25-000040", "filed": "2025-08-01",
                      "derived_from": None,
                      "url": "https://www.sec.gov/Archives/edgar/data/2/000000000225000040/"}
    assert source_view(facts["revenue"][-1])["url"] is None  # CIK bilinmiyorsa
    derived_item = facts["total_liabilities"][0]
    assert derived_item["tag"] == "derived"
    derived = source_view(derived_item)
    assert derived["xbrl_tag"] == "derived"
    assert derived["derived_from"] == derived_item["derived_from"]
    assert derived["derived_from"][0] == "LiabilitiesAndStockholdersEquity"


# --------------------------------------------------------------------------- facts / ratios / score


def test_facts_view_all_metrics_shape_yoy_and_kind(facts):
    body = facts_view(COMPANY, facts)
    strict_json(body)
    assert set(body) == {"company", "metrics", "disclaimer"}
    assert body["disclaimer"] == DISCLAIMER
    assert [m["metric"] for m in body["metrics"]] == list(METRICS)
    revenue = body["metrics"][0]
    assert set(revenue) == {"metric", "label", "kind", "values"}
    assert revenue["label"] == "Gelir" and revenue["kind"] == "duration"
    assert [v["period_end"] for v in revenue["values"]] == [
        "2022-06-30", "2023-06-30", "2024-06-30", "2025-06-30"]
    assert [v["yoy"] for v in revenue["values"]] == yoy_ratio(facts["revenue"])
    assert revenue["values"][-1]["yoy"] == pytest.approx(0.1)
    for v in revenue["values"]:
        assert set(v) == {"period_start", "period_end", "value", "yoy", "source"}
        assert isinstance(v["value"], float)
    assets = next(m for m in body["metrics"] if m["metric"] == "total_assets")
    assert assets["kind"] == "instant" and assets["values"][0]["period_start"] is None
    gp = next(m for m in body["metrics"] if m["metric"] == "gross_profit")
    assert gp["values"][0]["source"]["xbrl_tag"] == "derived"
    assert gp["values"][-1]["source"]["xbrl_tag"] == "us-gaap:GrossProfit"


def test_facts_view_omits_periods_without_value(facts):
    series = [dict(i) for i in facts["revenue"]]
    series[1]["value"] = None  # 2023 değeri yok
    body = facts_view(COMPANY, {"revenue": series}, "revenue")
    values = body["metrics"][0]["values"]
    assert [v["period_end"] for v in values] == ["2022-06-30", "2024-06-30", "2025-06-30"]
    assert all(v["value"] is not None for v in values)
    assert values[0]["source"]["url"].startswith("https://www.sec.gov/Archives/edgar/data/2/")


def test_facts_view_single_metric_and_empty_series():
    body = facts_view(COMPANY, {}, "net_income")
    assert [m["metric"] for m in body["metrics"]] == ["net_income"]
    assert body["metrics"][0]["values"] == []


def test_ratios_view_shape(ratios):
    body = ratios_view(COMPANY, ratios)
    strict_json(body)
    assert set(body) == {"company", "periods", "ratios", "disclaimer"}
    assert body["periods"] == ["2023-06-30", "2024-06-30", "2025-06-30"]
    assert [r["key"] for r in body["ratios"]] == [d["key"] for d in RATIO_DEFS]
    for r in body["ratios"]:
        assert set(r) >= {"key", "label", "formula", "unit", "higher_is_better", "values"}
        assert [v["period_end"] for v in r["values"]] == body["periods"]
        for v in r["values"]:
            assert set(v) >= {"period_end", "value", "status", "reason", "note", "inputs"}
            assert v["status"] in {"ok", "missing"}
            for i in v["inputs"]:
                assert set(i) == {"metric", "value", "accession_no"}
    net_margin = next(r for r in body["ratios"] if r["key"] == "net_margin")
    assert net_margin["values"][-1]["value"] == pytest.approx(1.1 / 5.5)


def test_score_view_with_and_without_score(score):
    body = score_view(score)
    strict_json(body)
    assert set(body) >= {"total", "band", "as_of_period", "weights_version", "components",
                         "missing_components", "explanation", "disclaimer"}
    assert body["as_of_period"] == "2025-06-30"
    assert body["total"] == score["total"] and len(body["components"]) == 7
    comp = body["components"][0]
    assert set(comp) >= {"key", "label", "weight", "score", "available", "indicators",
                         "explanation"}
    assert set(comp["indicators"][0]) >= {"key", "label", "value", "display", "points"}
    empty = score_view(None)
    assert empty["total"] is None and empty["components"] == []
    assert empty["band"] is None and empty["as_of_period"] is None
    assert empty["explanation"] is None
    assert empty["weights_version"] == "v1-2026-11" and empty["disclaimer"] == DISCLAIMER
    # JSON'dan geri okunmuş skor (tarihler metin) de aynı şekli üretir
    roundtrip = json.loads(json.dumps(json_safe(score)))
    assert score_view(roundtrip) == body


# --------------------------------------------------------------------------- overview


def test_overview_view_key_metrics_order_latest_values_and_summaries(
        facts, score, sentiment_sections, risk):
    filings = [Q_NEW, K_NEW, K_OLD]
    body = overview_view(COMPANY, facts, score, filings, sentiment_sections, risk)
    strict_json(body)
    assert set(body) == {"company", "analyzed", "score", "key_metrics", "latest_filings",
                         "sentiment_summary", "risk_summary", "analysis_updated_at",
                         "disclaimer"}
    assert body["analyzed"] is True
    assert body["score"] == {"total": score["total"], "band": score["band"],
                             "as_of_period": "2025-06-30"}
    assert [m["metric"] for m in body["key_metrics"]] == list(KEY_METRICS) == [
        "revenue", "net_income", "operating_cash_flow", "total_assets", "total_liabilities",
        "stockholders_equity"]
    rev = body["key_metrics"][0]
    assert set(rev) >= {"metric", "label", "value", "period_end", "yoy", "source"}
    assert rev["value"] == 5_500_000_000 and rev["period_end"] == "2025-06-30"
    assert rev["yoy"] == pytest.approx(0.1)
    assert set(rev["source"]) == SOURCE_KEYS
    assert rev["source"]["url"].endswith("/edgar/data/2/000000000225000040/")
    assert [f["accession_no"] for f in body["latest_filings"]] == [
        Q_NEW["accession_no"], K_NEW["accession_no"], K_OLD["accession_no"]]
    ss = body["sentiment_summary"]
    assert set(ss) == {"filing", "mdna_net_tone", "risk_net_tone", "model"}
    assert ss["filing"]["accession_no"] == K_NEW["accession_no"]  # 10-Q değil, son 10-K
    assert ss["mdna_net_tone"] == pytest.approx(0.25)   # (2 − 1) / 4
    assert ss["risk_net_tone"] == pytest.approx(-0.25)  # (1 − 2) / 4
    assert ss["model"] == "lexicon-fallback"
    assert body["risk_summary"] == {"added": 1, "removed": 0, "modified": 0, "unchanged": 1}
    assert body["analysis_updated_at"] == "2026-11-18T13:05:00Z"


def test_overview_without_analysis_has_nulls_and_missing_metric_reason():
    facts = {"revenue": extract_all(load("companyfacts_full_example.json"))["revenue"]}
    body = overview_view({**COMPANY, "analysis_updated_at": None}, facts, None, [], [], None)
    strict_json(body)
    assert body["analyzed"] is False
    assert body["score"] is None and body["sentiment_summary"] is None
    assert body["risk_summary"] is None and body["analysis_updated_at"] is None
    ni = body["key_metrics"][1]
    assert ni["metric"] == "net_income" and ni["value"] is None  # 0 değil
    assert ni["source"] is None and ni["reason"] == "veri yok"


# --------------------------------------------------------------------------- filings


def test_filings_view_items_list_and_labels():
    sub = load("submissions_example.json")
    rows = parse_filings(sub["filings"]["recent"], since=date(2020, 1, 1))
    rows = [{**r, "url": "u", "analyzed": r["form"] == "10-K"} for r in rows]
    body = filings_view(COMPANY, rows)
    strict_json(body)
    assert set(body) == {"company", "filings", "disclaimer"}
    by_acc = {f["accession_no"]: f for f in body["filings"]}
    eight_k = by_acc["0000000001-26-000011"]
    assert set(eight_k) == FILING_REF_KEYS | {"items", "item_labels", "analyzed"}
    assert eight_k["items"] == ["2.02", "9.01"]
    assert eight_k["item_labels"] == ["Faaliyet sonuçları ve finansal durum",
                                      "Finansal tablolar ve ekler"]
    ten_k = by_acc["0000000001-26-000010"]
    assert ten_k["items"] == [] and ten_k["item_labels"] == [] and ten_k["analyzed"] is True
    assert by_acc["0000000001-22-000099"]["item_labels"] == [
        "Yönetici veya yönetim kurulu üyesi değişikliği"]


def test_edgar_item_helpers():
    assert split_items("2.02, 9.01,") == ["2.02", "9.01"]
    assert split_items(None) == [] and split_items("") == []
    assert split_items(["5.02"]) == ["5.02"]
    assert item_label("7.01") == ITEM_LABELS_8K["7.01"]
    assert item_label("9.99") == "Madde 9.99"


def test_search_companies_ranking():
    tmap = build_ticker_map({
        "0": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
        "1": {"cik_str": 1, "ticker": "EXMP", "title": "Example Corp"},
        "2": {"cik_str": 2, "ticker": "MS", "title": "Morgan Stanley"},
        "3": {"cik_str": 3, "ticker": "AMIC", "title": "American Micro Devices"},
    })
    assert [r["ticker"] for r in search_companies(tmap, "ms")] == ["MS", "MSFT"]
    assert [r["ticker"] for r in search_companies(tmap, "micro")] == ["MSFT", "AMIC"]
    assert search_companies(tmap, "789019")[0] == {"ticker": "MSFT", "cik": "0000789019",
                                                   "name": "MICROSOFT CORP"}
    assert search_companies(tmap, "CIK0000000001")[0]["ticker"] == "EXMP"
    assert search_companies(tmap, "corp", limit=1) == [
        {"ticker": "EXMP", "cik": "0000000001", "name": "Example Corp"}]
    assert search_companies(tmap, "  ") == [] and search_companies(tmap, "zzzz") == []


# --------------------------------------------------------------------------- sentiment / risk


def test_sentiment_view_order_model_and_summary(sentiment_sections):
    body = sentiment_view(sentiment_sections)
    strict_json(body)
    assert set(body) == {"model", "sections", "disclaimer"}
    assert body["model"] == "lexicon-fallback"
    order = [(s["filing"]["accession_no"], s["item_code"]) for s in body["sections"]]
    assert order == [(K_OLD["accession_no"], "7"), (K_NEW["accession_no"], "1A"),
                     (K_NEW["accession_no"], "7"), (Q_NEW["accession_no"], "2")]
    sec = body["sections"][1]
    assert set(sec) == {"filing", "item_code", "title", "sentence_count", "positive",
                        "negative", "neutral", "net_tone", "top_negative", "top_positive"}
    assert (sec["sentence_count"], sec["positive"], sec["negative"], sec["neutral"]) == \
        (4, 1, 2, 1)
    assert sec["net_tone"] == pytest.approx(-0.25)
    assert len(sec["top_negative"]) == 2 and set(sec["top_negative"][0]) == {"sentence",
                                                                             "score"}


def test_sentiment_view_keeps_only_current_model_and_defaults(sentiment_sections):
    # En eski başvuru başka modelle işlenmiş: en yeni başvurunun modeli geçerlidir.
    mixed = sentiment_sections[:3] + [{**sentiment_sections[3], "model": "old-model"}]
    body = sentiment_view(mixed)
    assert body["model"] == "lexicon-fallback"
    assert K_OLD["accession_no"] not in {s["filing"]["accession_no"] for s in body["sections"]}
    assert len(body["sections"]) == 3
    empty = sentiment_view([], default_model="ProsusAI/finbert")
    assert empty["model"] == "ProsusAI/finbert" and empty["sections"] == []


def test_risk_view_with_and_without_comparison(risk):
    body = risk_view(risk)
    strict_json(body)
    assert set(body) == {"old_filing", "new_filing", "counts", "changes", "disclaimer"}
    assert body["old_filing"]["accession_no"] == K_OLD["accession_no"]
    assert body["counts"] == {"added": 1, "removed": 0, "modified": 0, "unchanged": 1}
    assert [c["change_type"] for c in body["changes"]] == ["added"]
    assert set(body["changes"][0]) == {"change_type", "old_text", "new_text", "similarity"}
    reordered = {**risk, "changes": [
        {"change_type": "removed", "old_text": "r", "new_text": None, "similarity": None},
        {"change_type": "unchanged", "old_text": "u", "new_text": "u", "similarity": 1.0},
        {"change_type": "modified", "old_text": "a", "new_text": "b", "similarity": 0.7},
        {"change_type": "added", "old_text": None, "new_text": "n", "similarity": None}]}
    assert [c["change_type"] for c in risk_view(reordered)["changes"]] == [
        "added", "modified", "removed"]
    none = risk_view(None)  # sözleşme v1.1: karşılaştırma yoksa sayılar 0
    assert none["old_filing"] is None and none["new_filing"] is None
    assert none["changes"] == []
    assert none["counts"] == {"added": 0, "removed": 0, "modified": 0, "unchanged": 0}


# --------------------------------------------------------------------------- compare / liste / iş


def test_compare_view_shape(ratios, score):
    entries = [{"company": COMPANY, "ratios": ratios, "score": score},
               {"company": {"ticker": "EMPT", "name": "Empty Corp"},
                "ratios": compute_ratios({}, 3), "score": None}]
    body = compare_view(entries)
    strict_json(body)
    assert set(body) == {"ratio_meta", "companies", "disclaimer"}
    assert body["ratio_meta"][0] == {"key": "gross_margin", "label": "Brüt kâr marjı",
                                     "unit": "percent", "higher_is_better": True}
    first, second = body["companies"]
    assert set(first) == {"ticker", "name", "period_end", "score", "ratios"}
    assert first["period_end"] == "2025-06-30"
    assert first["score"] == {"total": score["total"], "band": score["band"]}
    assert set(first["ratios"]) == {d["key"] for d in RATIO_DEFS}
    assert first["ratios"]["net_margin"] == pytest.approx(0.2)
    assert second["period_end"] is None and second["score"] is None
    assert all(v is None for v in second["ratios"].values())


def test_company_list_item_and_job_view(score):
    item = company_list_item(COMPANY, True, date(2025, 8, 1), score)
    assert set(item) == {"ticker", "cik", "name", "sic_description", "analyzed",
                         "last_filing_date", "score"}
    assert item["last_filing_date"] == "2025-08-01"
    assert company_list_item(COMPANY, False, None, None)["score"] is None
    job = job_view({"id": 7, "ticker": "MSFT", "status": "running", "step": "Gömme",
                    "progress": 0.6, "error": None,
                    "started_at": datetime(2026, 11, 18, 13, 0, 0), "finished_at": None,
                    "request_count": 14, "warnings": ["10-Q: hata"]})
    assert set(job) >= {"id", "ticker", "status", "step", "progress", "error", "started_at",
                        "finished_at", "request_count"}
    assert job["started_at"] == "2026-11-18T13:00:00Z" and job["warnings"] == ["10-Q: hata"]


# --------------------------------------------------------------------------- CSV dışa aktarım


def test_export_rows_and_csv(facts, ratios, score):
    rows = export_rows(COMPANY, facts, ratios, score)
    sections = {r["section"] for r in rows}
    assert sections == {"fact", "ratio", "score"}
    assert all(set(r) == set(EXPORT_COLUMNS) for r in rows)
    n_facts = sum(len(s) for s in facts.values())
    assert sum(r["section"] == "fact" for r in rows) == n_facts
    assert sum(r["section"] == "ratio" for r in rows) == len(RATIO_DEFS) * 3
    assert sum(r["section"] == "score" for r in rows) == 1 + 7
    derived = next(r for r in rows if r["section"] == "fact" and r["key"] == "gross_profit")
    assert derived["xbrl_tag"] == "derived"

    text = export_csv(rows)
    assert text.startswith("﻿")
    parsed = list(csv.reader(io.StringIO(text[1:])))
    assert parsed[0] == list(EXPORT_COLUMNS) == (
        "ticker,cik,section,key,label,period_start,period_end,value,unit,status,xbrl_tag,"
        "form,accession_no,filed").split(",")
    rev = next(p for p in parsed[1:] if p[2] == "fact" and p[3] == "revenue")
    assert rev[:2] == ["EXJN", "0000000002"] and rev[7] == "4000000000"
    assert rev[10] == "us-gaap:Revenues" and rev[8] == "USD"
    total = next(p for p in parsed[1:] if p[2] == "score" and p[3] == "total")
    assert float(total[7]) == score["total"] and total[8] == "points"


def test_export_missing_values_are_blank_not_zero():
    rows = export_rows(COMPANY, {}, compute_ratios({}, 3), None)
    assert rows == []  # dönem yok → oran satırı yok, skor yok
    facts = {"revenue": extract_all(load("companyfacts_full_example.json"))["revenue"]}
    rows = export_rows(COMPANY, facts, compute_ratios(facts, 3), None)
    missing = [r for r in rows if r["section"] == "ratio" and r["status"] == "missing"]
    assert missing and all(r["value"] is None for r in missing)
    parsed = list(csv.reader(io.StringIO(export_csv(rows)[1:])))
    ratio_line = next(p for p in parsed[1:] if p[2] == "ratio" and p[9] == "missing")
    assert ratio_line[7] == ""
