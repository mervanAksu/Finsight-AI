"""Uçtan uca fonksiyonel senaryo: veritabanı ve API olmadan saf modül zinciri (Aşama 4).

Sahte SEC (integration_support.FakeSec) → arama → başvuru listesi → belge indirme (önbellek) →
XBRL kalemleri → oranlar → bölüm ayıklama + duygu + gömme → risk farkı → skor → soru-cevap →
API görünümleri → CSV. Ağ yok; sözlük duygu modeli ve hashing gömme kullanılır; tarih sabit.

Beklentiler kodun bugünkü çıktısından değil, tasarım belgesindeki kabul ölçütlerinden
(docs/raporlar/00-Asama1-Analiz-ve-Tasarim-Planlama.md §4–5) ve docs/api-contract.md'den alınır.
Senaryo kimlikleri docs/test-plani-asama4.md'deki E2E-xx satırlarıyla eşleşir.
"""

from __future__ import annotations

import csv
import io
import math
from datetime import date

import pytest
from conftest import load
from integration_support import (ACC_8K, ACC_K_NEW, ACC_K_OLD, ACC_Q, ADDED_RISK_PREFIX, CIK,
                                 TICKER, FakeSec, build_routes, doc_url)

from finsight import DISCLAIMER
from finsight.analyze import (analyze_document, compare_risk, compute_text_signals,
                              latest_two_annual, score_company, select_filings_for_analysis)
from finsight.edgar import build_ticker_map, document_url, resolve_company, search_companies
from finsight.embeddings import HashingEmbedder
from finsight.pipeline import collect
from finsight.rag import answer_question
from finsight.ratios import RATIO_DEFS
from finsight.scoring import compute_score
from finsight.sec_client import FileCache
from finsight.sentiment import LexiconModel
from finsight.views import (compare_view, export_csv, export_rows, facts_view, filings_view,
                            overview_view, ratios_view, risk_view, score_view, sentiment_view)

TODAY = date(2026, 11, 23)
LABELS = {"positive", "negative", "neutral"}
ADVICE_WORDS = ("al ", "sat ", "tut ", "almalı", "satmalı", "tutmalı", "buy", "sell")


# --------------------------------------------------------------------------- ortak kurulum


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """Bir kez kurulan uçtan uca dünya (modül kapsamı: analiz birkaç yüz ms sürer)."""
    sec = FakeSec(build_routes(TODAY))
    client = sec()
    client.cache = FileCache(str(tmp_path_factory.mktemp("sec_cache")))
    tmap = build_ticker_map(load("company_tickers_example.json"))
    data = collect(client, TICKER, tmap, years=3, today=TODAY)
    company = {**data["company"], "analysis_updated_at": None}

    sentiment_model = LexiconModel(dictionary_path="")
    embedder = HashingEmbedder()
    selected = select_filings_for_analysis(data["filings"])
    analyses: dict[str, dict] = {}
    for f in selected:
        html = client.get_bytes(document_url(CIK, f["accession_no"], f["primary_document"]))
        analyses[f["accession_no"]] = analyze_document(
            html, f["form"], sentiment_model=sentiment_model, embedder=embedder)

    old_k, new_k = latest_two_annual(selected)

    def section(acc: str, code: str) -> dict | None:
        return next((s for s in analyses[acc]["sections"] if s["item_code"] == code), None)

    risk = compare_risk((section(old_k["accession_no"], "1A") or {}).get("text"),
                        (section(new_k["accession_no"], "1A") or {}).get("text"))
    risk_full = {**risk, "old_filing": old_k, "new_filing": new_k} if risk else None
    by_code = {s["item_code"]: s["summary"] for s in analyses[new_k["accession_no"]]["sections"]}
    signals = compute_text_signals(by_code, risk)
    ratios, score = score_company(data["facts"], signals)

    sentiment_rows = []
    chunks = []
    filing_by_acc = {f["accession_no"]: f for f in data["filings"]}
    for acc, analysis in analyses.items():
        f = filing_by_acc[acc]
        for s in analysis["sections"]:
            sentiment_rows.append({"filing": f, "item_code": s["item_code"], "title": s["title"],
                                   "char_start": s["char_start"],
                                   "model": analysis["sentiment_model"],
                                   "sentences": s["sentences"]})
            for c in s["chunks"]:
                chunks.append({"chunk_id": len(chunks) + 1, "text": c["text"],
                               "embedding": c["embedding"],
                               "embedding_model": analysis["embedding_model"],
                               "section": s["item_code"], "section_title": s["title"],
                               "form": f["form"], "filing_date": f["filing_date"].isoformat(),
                               "accession_no": acc,
                               "url": document_url(CIK, acc, f["primary_document"])})
    return {"sec": sec, "client": client, "tmap": tmap, "data": data, "company": company,
            "selected": selected, "analyses": analyses, "risk": risk_full, "signals": signals,
            "ratios": ratios, "score": score, "sentiment_rows": sentiment_rows,
            "chunks": chunks, "embedder": embedder}


def _walk_numbers(obj, path=""):
    """JSON ağacındaki tüm sayılar (bool hariç) ve yolları."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_numbers(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_numbers(v, f"{path}[{i}]")
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        yield path, obj


# --------------------------------------------------------------------------- FR-01 … FR-03


def test_e2e01_search_by_ticker_name_and_cik(world):
    """FR-01: ticker, ad ve CIK ile arama; kabul ölçütü: AAPL → 0000320193."""
    tmap = world["tmap"]
    assert resolve_company("AAPL", tmap)["cik"] == "0000320193"
    assert resolve_company("aapl ", tmap)["cik"] == "0000320193"  # küçük harf + boşluk
    assert resolve_company("320193", tmap)["cik"] == "0000320193"  # sıfırsız CIK
    by_name = search_companies(tmap, "example", limit=10)
    assert [r["ticker"] for r in by_name] == ["EXMP"]
    by_cik = search_companies(tmap, "0000000001", limit=10)
    assert by_cik and by_cik[0]["ticker"] == "EXMP"
    assert search_companies(tmap, "zzzz-yok", limit=10) == []


def test_e2e02_filing_list_forms_dates_accessions(world):
    """FR-02: yalnızca 10-K / 10-Q / 8-K (ve /A); her satırda form, tarih ve accession."""
    filings = world["data"]["filings"]
    assert {f["accession_no"] for f in filings} >= {ACC_K_NEW, ACC_K_OLD, ACC_Q, ACC_8K}
    for f in filings:
        assert f["base_form"] in {"10-K", "10-Q", "8-K"}
        assert isinstance(f["filing_date"], date)
        assert f["accession_no"].count("-") == 2
    view = filings_view(world["company"], sorted(filings, key=lambda f: f["filing_date"],
                                                 reverse=True))
    dates = [f["filing_date"] for f in view["filings"]]
    assert dates == sorted(dates, reverse=True)  # sözleşme: yeniden eskiye


def test_e2e03_document_downloaded_once_and_cached(world):
    """FR-03: belge yerel olarak saklanır; aynı belge ikinci kez indirilmez."""
    sec, client = world["sec"], world["client"]
    url = doc_url(ACC_K_NEW)
    before = sec.document_calls().count(url)
    assert before == 1  # kurulumda tam bir kez indirildi
    body = client.get_bytes(url)
    assert body and b"Risk Factors" in body
    assert sec.document_calls().count(url) == before


def test_e2e_sec_requests_all_identified_and_counted(world):
    """NFR-01: uçtan uca akıştaki her SEC isteği tanımlayıcı User-Agent taşır."""
    calls = world["sec"].fetcher.calls
    assert calls
    for _url, headers in calls:
        ua = headers.get("User-Agent", "")
        assert "@" in ua and len(ua.split()) >= 2


# --------------------------------------------------------------------------- FR-04 … FR-06


def test_e2e04_facts_traceable_to_accession_tag_and_period(world):
    """FR-04 / NFR-06: her kalem değeri accession, form, dönem ve XBRL etiketiyle gelir."""
    view = facts_view(world["company"], world["data"]["facts"])
    required = {"revenue", "net_income", "total_assets", "total_liabilities",
                "stockholders_equity", "operating_cash_flow"}
    assert required <= {m["metric"] for m in view["metrics"] if m["values"]}
    for m in view["metrics"]:
        ends = [v["period_end"] for v in m["values"]]
        assert ends == sorted(ends), f"{m['metric']}: eskiden yeniye değil"
        for v in m["values"]:
            assert v["value"] is not None  # değersiz dönem listelenmez (v1.1)
            src = v["source"]
            assert src["accession_no"] and src["form"] and src["xbrl_tag"]
            if src["xbrl_tag"] == "derived":
                assert src["derived_from"], "türetilmiş değer etiket listesi taşımalı"
            else:
                assert src["xbrl_tag"].startswith("us-gaap:")
    assert view["disclaimer"] == DISCLAIMER


def test_e2e05_yoy_matches_hand_calculation(world):
    """FR-05: YoY = (cari − önceki) / |önceki|; elle hesaplananla eşleşir."""
    view = facts_view(world["company"], world["data"]["facts"], metric="revenue")
    values = view["metrics"][0]["values"]
    assert len(values) >= 2
    assert values[0]["yoy"] is None  # ilk dönemin öncesi yok (bilinmiyor ≠ 0)
    for prev, cur in zip(values, values[1:]):
        expected = (cur["value"] - prev["value"]) / abs(prev["value"])
        assert cur["yoy"] == pytest.approx(expected, rel=1e-9)


def test_e2e06_ratios_have_formula_and_missing_is_never_zero(world):
    """FR-06: 8–10 oran, formül gösterilir; eksik veride değer None + neden (0 değil)."""
    view = ratios_view(world["company"], world["ratios"])
    assert 8 <= len(view["ratios"]) <= 10
    for r in view["ratios"]:
        assert r["formula"] and r["label"] and r["unit"] in {"percent", "times"}
        assert [v["period_end"] for v in r["values"]] == view["periods"]
        for v in r["values"]:
            if v["status"] == "ok":
                assert v["value"] is not None and math.isfinite(v["value"])
                assert v["inputs"] and all(i["accession_no"] for i in v["inputs"])
            else:
                assert v["status"] == "missing"
                assert v["value"] is None and v["reason"]


def test_e2e06_net_margin_equals_independent_calculation(world):
    """FR-06: net kâr marjı = net kâr / gelir, aynı dönemin kalemlerinden bağımsız hesap."""
    facts = world["data"]["facts"]
    by_end = {m: {f["period_end"]: f["value"] for f in facts[m]} for m in ("revenue", "net_income")}
    net_margin = next(r for r in world["ratios"]["ratios"] if r["key"] == "net_margin")
    checked = 0
    for v in net_margin["values"]:
        end = v["period_end"]
        if v["value"] is None:
            continue
        rev, ni = by_end["revenue"].get(end), by_end["net_income"].get(end)
        if rev and ni is not None:
            assert v["value"] == pytest.approx(ni / rev, rel=1e-9)
            checked += 1
    assert checked >= 1


# --------------------------------------------------------------------------- FR-07 … FR-09


def test_e2e07_sections_extracted_for_10k_and_10q(world):
    """FR-07: 10-K'da Item 1A ve Item 7; 10-Q'da Part I Item 2 (MD&A) ayıklanır."""
    a = world["analyses"]
    assert {"1A", "7"} <= {s["item_code"] for s in a[ACC_K_NEW]["sections"]}
    assert "2" in {s["item_code"] for s in a[ACC_Q]["sections"]}
    for s in a[ACC_K_NEW]["sections"]:
        assert s["word_count"] > 50, f"{s['item_code']} bölümü şüpheli kısa"
        assert s["char_end"] > s["char_start"]


def test_e2e08_every_sentence_has_label_and_probability(world):
    """FR-08: her cümle etiket + olasılıkla; bölüm özeti oran (net ton) olarak verilir."""
    view = sentiment_view(world["sentiment_rows"])
    assert view["model"] == "lexicon-fallback"  # yedek model adı yanıtta görünür
    assert view["sections"]
    for s in view["sections"]:
        assert s["positive"] + s["negative"] + s["neutral"] == s["sentence_count"]
        assert -1.0 <= s["net_tone"] <= 1.0
        assert len(s["top_negative"]) <= 5 and len(s["top_positive"]) <= 5
    for row in world["sentiment_rows"]:
        for sent in row["sentences"]:
            assert sent["label"] in LABELS
            assert 0.0 <= sent["score"] <= 1.0
    assert view["disclaimer"] == DISCLAIMER


def test_e2e09_new_risk_paragraph_listed_as_added(world):
    """FR-09: eski 10-K'da olmayan paragraf "added" grubunda, gruplar ayrı ve sıralı."""
    view = risk_view(world["risk"])
    assert view["old_filing"]["accession_no"] == ACC_K_OLD
    assert view["new_filing"]["accession_no"] == ACC_K_NEW
    added = [c for c in view["changes"] if c["change_type"] == "added"]
    assert any(c["new_text"].startswith(ADDED_RISK_PREFIX) for c in added)
    assert view["counts"]["added"] == len(added)
    order = {"added": 0, "modified": 1, "removed": 2}
    types = [order[c["change_type"]] for c in view["changes"]]
    assert types == sorted(types)
    assert view["counts"]["unchanged"] > 0  # aynı kalan paragraflar listelenmez ama sayılır


# --------------------------------------------------------------------------- FR-10, FR-11


def test_e2e10_answer_cites_source_with_accession_and_link(world):
    """FR-10 / NFR-07: cevap en az bir kaynak parçayla, bölüm ve belge bağlantısıyla gelir."""
    res = answer_question("What are the risks from changes in tax laws?", world["chunks"],
                          world["embedder"], llm=None)
    assert res["found"] is True and res["mode"] == "extractive"
    assert res["sources"]
    for s in res["sources"]:
        assert s["accession_no"] and s["url"].startswith("https://www.sec.gov/Archives/")
        assert s["section"] in {"1A", "7", "2"}
        assert s["text"]
    assert "[1]" in res["answer"]


def test_e2e10_unrelated_question_says_not_found(world):
    """FR-10: kaynak yoksa "raporda bulunamadı"; kaynak listesi boş (uydurma yok)."""
    res = answer_question("Kripto para madenciliği tesisleri nerede?", world["chunks"],
                          world["embedder"], llm=None)
    assert res["found"] is False and res["mode"] == "not_found"
    assert res["sources"] == []
    assert "bulunamadı" in res["answer"]


def test_e2e10_advice_question_is_refused(world):
    """R-01 / NFR-12: tavsiye sorusu reddedilir (advice_refused)."""
    res = answer_question("Bu hisseyi almalı mıyım?", world["chunks"], world["embedder"],
                          llm=None)
    assert res["advice_refused"] is True
    assert res["answer"].startswith("FinSight AI yatırım tavsiyesi vermez")


def test_e2e11_score_components_weights_and_explanations(world):
    """FR-11: 100 puanlık skor; her bileşen ayrı puan, ağırlık ve gerekçeyle gösterilir."""
    view = score_view(world["score"])
    assert view["total"] is not None and 0 <= view["total"] <= 100
    assert view["band"]
    comps = view["components"]
    assert sum(c["weight"] for c in comps) == 100
    for c in comps:
        assert c["explanation"]
        if c["available"]:
            assert 0 <= c["score"] <= 100
            assert c["indicators"]
    available = [c for c in comps if c["available"]]
    used = sum(c["weight"] for c in available)
    expected = sum(c["weight"] * c["score"] for c in available) / used
    # Bileşen puanları 1 ondalığa yuvarlanmış gösterilir; toplam en fazla ±0,1 sapar.
    assert view["total"] == pytest.approx(expected, abs=0.1)
    assert view["disclaimer"] == DISCLAIMER


def test_e2e11_text_signals_flow_into_score(world):
    """FR-11: metin tonu bileşeni son 10-K'nın duygu özetinden ve risk farkından beslenir."""
    sig = world["signals"]
    assert sig["risk_added"] == world["risk"]["counts"]["added"] >= 1
    assert sig["mdna_net_tone"] is not None and sig["risk_net_tone"] is not None
    without_text = compute_score(world["ratios"], world["data"]["facts"], None)
    keys = {c["key"] for c in without_text["components"] if not c["available"]}
    assert keys, "metin sinyali yokken en az bir bileşen 'kullanılamaz' olmalı"


# --------------------------------------------------------------------------- FR-13, FR-14, FR-16


def test_e2e13_compare_two_companies_side_by_side(world):
    """FR-13: iki şirketin oran tablosu tek yanıtta; eksik oran null (0 değil)."""
    other_facts = collect_other()
    other_ratios, other_score = score_company(other_facts, None)
    view = compare_view([
        {"company": world["company"], "ratios": world["ratios"], "score": world["score"]},
        {"company": {"ticker": "OTHR", "name": "Other Example"}, "ratios": other_ratios,
         "score": other_score},
    ])
    assert [c["ticker"] for c in view["companies"]] == [TICKER, "OTHR"]
    keys = {m["key"] for m in view["ratio_meta"]}
    for c in view["companies"]:
        assert set(c["ratios"]) == keys
        for v in c["ratios"].values():
            assert v is None or math.isfinite(v)
    assert view["disclaimer"] == DISCLAIMER


def collect_other() -> dict:
    from finsight.xbrl import extract_all

    return extract_all(load("companyfacts_example.json"), years=4)


def test_e2e14_8k_items_listed_with_labels(world):
    """FR-14: 8-K listesinde madde numaraları ve etiketleri görünür."""
    view = filings_view(world["company"], world["data"]["filings"])
    eight_k = next(f for f in view["filings"] if f["accession_no"] == ACC_8K)
    assert eight_k["items"] == ["2.02", "9.01"]
    assert len(eight_k["item_labels"]) == 2 and all(eight_k["item_labels"])
    ten_k = next(f for f in view["filings"] if f["accession_no"] == ACC_K_NEW)
    assert ten_k["items"] == []


def test_e2e16_csv_contains_all_ratios_and_source_accessions(world):
    """FR-16: CSV tüm oranları ve kaynak accession numaralarını içerir; eksik hücre boş."""
    rows = export_rows(world["company"], world["data"]["facts"], world["ratios"], world["score"])
    text = export_csv(rows)
    assert text.startswith("﻿")
    parsed = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    assert parsed and list(parsed[0].keys())[0] == "ticker"
    ratio_keys = {r["key"] for r in parsed if r["section"] == "ratio"}
    assert ratio_keys == {d["key"] for d in RATIO_DEFS}
    fact_accessions = {f["accession_no"] for series in world["data"]["facts"].values()
                       for f in series if f.get("value") is not None}
    csv_accessions = {a for r in parsed for a in r["accession_no"].split(";") if a}
    assert fact_accessions <= csv_accessions
    for r in parsed:
        if r["status"] == "missing":
            assert r["value"] == ""  # eksik değer 0 yazılmaz
    assert any(r["section"] == "score" and r["key"] == "total" for r in parsed)


# --------------------------------------------------------------------------- NFR-06, NFR-12


def test_e2e_nfr06_every_overview_number_has_source(world):
    """NFR-06: özet ekranındaki her temel finansal değer kaynağa (accession, etiket) bağlı."""
    filings = sorted(world["data"]["filings"], key=lambda f: f["filing_date"], reverse=True)
    view = overview_view(world["company"], world["data"]["facts"], world["score"], filings,
                         world["sentiment_rows"], world["risk"])
    assert [m["metric"] for m in view["key_metrics"]] == [
        "revenue", "net_income", "operating_cash_flow", "total_assets", "total_liabilities",
        "stockholders_equity"]
    for m in view["key_metrics"]:
        if m["value"] is not None:
            assert m["source"]["accession_no"] and m["source"]["xbrl_tag"], m["metric"]
            assert m["period_end"]
    assert view["analyzed"] is True
    assert view["risk_summary"]["added"] == world["risk"]["counts"]["added"]


def test_e2e_nfr12_every_analysis_view_carries_disclaimer(world):
    """NFR-12: analiz içeren her yanıtta aynı uyarı metni; metin "tavsiye değildir" der."""
    c, facts = world["company"], world["data"]["facts"]
    filings = world["data"]["filings"]
    views = {
        "overview": overview_view(c, facts, world["score"], filings, world["sentiment_rows"],
                                  world["risk"]),
        "facts": facts_view(c, facts),
        "ratios": ratios_view(c, world["ratios"]),
        "score": score_view(world["score"]),
        "filings": filings_view(c, filings),
        "sentiment": sentiment_view(world["sentiment_rows"]),
        "risk": risk_view(world["risk"]),
        "risk_empty": risk_view(None),
        "score_empty": score_view(None),
        "compare": compare_view([{"company": c, "ratios": world["ratios"],
                                  "score": world["score"]}]),
    }
    for name, v in views.items():
        assert v.get("disclaimer") == DISCLAIMER, name
    assert "yatırım tavsiyesi değildir" in DISCLAIMER


def test_e2e_score_explanations_use_no_buy_sell_language(world):
    """R-01: skor açıklamaları "al / sat / tut" önerisi içermez."""
    texts = [world["score"].get("explanation") or ""]
    texts += [c["explanation"] or "" for c in world["score"]["components"]]
    for t in texts:
        low = f" {t.lower()} "
        for w in ADVICE_WORDS:
            assert f" {w}" not in low, (w, t)


def test_e2e_views_are_json_serializable_without_nan(world):
    """Sözleşme: yanıtlar JSON'a NaN/inf içermeden çevrilir (eksik → null)."""
    import json

    c, facts = world["company"], world["data"]["facts"]
    for v in (facts_view(c, facts), ratios_view(c, world["ratios"]), score_view(world["score"]),
              sentiment_view(world["sentiment_rows"]), risk_view(world["risk"])):
        json.dumps(v, allow_nan=False)
        for path, num in _walk_numbers(v):
            assert math.isfinite(num), path
