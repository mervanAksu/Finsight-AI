"""analyze.py (saf analiz hattı) testleri — FR-07, FR-08, FR-09, FR-10, FR-11."""

import pathlib
from datetime import date

import numpy as np
import pytest
from conftest import load

from finsight.analyze import (CHUNK_OVERLAP_WORDS, DEFAULT_CHUNK_WORDS, HASHING_CHUNK_WORDS,
                              analyze_document, analyze_sections, compare_risk,
                              compute_text_signals, default_chunk_words, embed_sections,
                              latest_annual_summaries, latest_two_annual, score_company,
                              select_filings_for_analysis)
from finsight.edgar import parse_filings
from finsight.embeddings import HashingEmbedder
from finsight.sentiment import LexiconModel, normalize_sentence
from finsight.xbrl import extract_all

NLP = pathlib.Path(__file__).parent / "fixtures" / "nlp"


def F(acc, form, filed, report=None, doc="doc.htm"):
    return {"accession_no": acc, "form": form, "filing_date": date.fromisoformat(filed),
            "report_date": date.fromisoformat(report) if report else None,
            "primary_document": doc, "is_amendment": form.endswith("/A")}


class CountingLexicon(LexiconModel):
    """predict çağrı sayısını sayan sözlük modeli (toplu tahmin kontrolü)."""

    def __init__(self):
        super().__init__(dictionary_path="")
        self.calls: list[int] = []

    def predict(self, sentences):
        self.calls.append(len(sentences))
        return super().predict(sentences)


class NamedEmbedder(HashingEmbedder):
    """Hashing vektörleri üreten ama adı farklı (anlamsal model gibi davranan) gömücü."""

    name = "fake-semantic-model"


# --------------------------------------------------------------------------- başvuru seçimi


def test_select_two_latest_annual_and_latest_quarterly_oldest_first():
    filings = [
        F("a1", "10-K", "2023-02-10", "2022-12-31"),
        F("a2", "10-K", "2024-02-10", "2023-12-31"),
        F("a3", "10-K", "2025-02-10", "2024-12-31"),
        F("q1", "10-Q", "2024-11-01", "2024-09-30"),
        F("q2", "10-Q", "2025-05-01", "2025-03-31"),
        F("e1", "8-K", "2025-06-01"),
    ]
    picked = select_filings_for_analysis(filings)
    assert [f["accession_no"] for f in picked] == ["a2", "a3", "q2"]


def test_amendment_only_when_original_missing_for_same_period():
    filings = [
        F("orig", "10-K", "2025-02-10", "2024-12-31"),
        F("amend", "10-K/A", "2025-04-01", "2024-12-31"),   # orijinali var → atlanır
        F("amend_only", "10-K/A", "2024-05-01", "2023-12-31"),  # orijinali yok → aday
        F("old", "10-K", "2023-02-10", "2022-12-31"),
    ]
    picked = select_filings_for_analysis(filings)
    assert [f["accession_no"] for f in picked] == ["amend_only", "orig"]


def test_quarterly_amendment_rules_and_missing_document_skipped():
    filings = [
        F("q_orig", "10-Q", "2025-05-01", "2025-03-31"),
        F("q_amend", "10-Q/A", "2025-06-01", "2025-03-31"),
        F("k_nodoc", "10-K", "2025-02-10", "2024-12-31", doc=None),
        F("k_old", "10-K", "2024-02-10", "2023-12-31"),
    ]
    picked = select_filings_for_analysis(filings)
    assert [f["accession_no"] for f in picked] == ["k_old", "q_orig"]


def test_select_accepts_iso_strings_and_submissions_fixture():
    sub = load("submissions_example.json")
    filings = parse_filings(sub["filings"]["recent"], since=date(2020, 1, 1))
    picked = select_filings_for_analysis(filings)
    # 10-K/A aynı dönem (2025-12-31) için orijinal olduğundan seçilmez; 8-K'lar seçilmez.
    assert [(f["form"], f["accession_no"]) for f in picked] == [
        ("10-K", "0000000001-26-000010"), ("10-Q", "0000000001-26-000012")]
    as_strings = [{**f, "filing_date": f["filing_date"].isoformat(),
                   "report_date": f["report_date"].isoformat() if f["report_date"] else None}
                  for f in filings]
    assert [f["accession_no"] for f in select_filings_for_analysis(as_strings)] == \
        [f["accession_no"] for f in picked]


def test_select_empty_and_latest_two_annual():
    assert select_filings_for_analysis([]) == []
    filings = [F("a1", "10-K", "2024-02-10", "2023-12-31"), F("q", "10-Q", "2025-05-01"),
               F("a2", "10-K", "2025-02-10", "2024-12-31")]
    old, new = latest_two_annual(filings)
    assert (old["accession_no"], new["accession_no"]) == ("a1", "a2")
    assert latest_two_annual([F("a1", "10-K", "2024-02-10")])[0] is None


# --------------------------------------------------------------------------- belge analizi


def test_analyze_document_10k_sections_sentiment_and_chunks():
    model = CountingLexicon()
    embedder = HashingEmbedder()
    result = analyze_document((NLP / "kestrel_10k.html").read_bytes(), "10-K",
                              sentiment_model=model, embedder=embedder)
    sections = result["sections"]
    assert [s["item_code"] for s in sections] == ["1A", "7"]  # belge sırası
    assert result["sentiment_model"] == "lexicon-fallback"
    assert result["embedding_model"] == "hashing-fallback"
    assert result["embedding_dim"] == embedder.dim
    assert result["chunk_words"] == HASHING_CHUNK_WORDS
    # Duygu tahmini belge başına tek toplu çağrı; yinelenen cümleler bir kez tahmin edilir
    unique = {normalize_sentence(x["sentence"]) for s in sections for x in s["sentences"]}
    assert model.calls == [len(unique)]

    risk = sections[0]
    assert risk["title"] == "Risk Faktörleri"
    assert risk["word_count"] == len(risk["text"].split()) > 300
    assert risk["char_end"] > risk["char_start"] >= 0
    assert "limited number of large industrial customers" in risk["text"]
    for i, s in enumerate(risk["sentences"]):
        assert s["index"] == i
        assert s["label"] in {"positive", "negative", "neutral"}
        assert 0.0 <= s["score"] <= 1.0
    assert risk["summary"]["sentence_count"] == len(risk["sentences"])
    assert risk["summary"]["negative"] > 0
    assert set(risk["summary"]) >= {"sentence_count", "positive", "negative", "neutral",
                                   "net_tone", "top_negative", "top_positive"}

    for sec in sections:
        assert sec["chunks"], sec["item_code"]
        for j, ch in enumerate(sec["chunks"]):
            assert ch["index"] == j
            assert ch["word_count"] == len(ch["text"].split())
            assert ch["word_count"] <= HASHING_CHUNK_WORDS + CHUNK_OVERLAP_WORDS
            assert isinstance(ch["embedding"], np.ndarray)
            assert ch["embedding"].dtype == np.float32
            assert ch["embedding"].shape == (embedder.dim,)
            assert np.linalg.norm(ch["embedding"]) == pytest.approx(1.0, abs=1e-5)
    # 339 kelimelik risk bölümü 150 kelimelik parçalara bölünür
    assert len(risk["chunks"]) >= 3


def test_analyze_document_10q_sections():
    result = analyze_document((NLP / "kestrel_10q.html").read_text(), "10-Q",
                              sentiment_model=LexiconModel(dictionary_path=""),
                              embedder=HashingEmbedder())
    assert [s["item_code"] for s in result["sections"]] == ["2", "1A"]
    assert all(s["sentences"] and s["chunks"] for s in result["sections"])


def test_chunk_words_selection():
    assert default_chunk_words(HashingEmbedder()) == HASHING_CHUNK_WORDS == 150
    assert default_chunk_words(NamedEmbedder()) == DEFAULT_CHUNK_WORDS == 350
    html = (NLP / "kestrel_10k.html").read_bytes()
    semantic = analyze_document(html, "10-K", sentiment_model=LexiconModel(dictionary_path=""),
                                embedder=NamedEmbedder())
    assert semantic["chunk_words"] == 350
    assert len(semantic["sections"][0]["chunks"]) == 1  # 339 kelime tek parça
    small = analyze_document(html, "10-K", sentiment_model=LexiconModel(dictionary_path=""),
                             embedder=HashingEmbedder(), chunk_words=60)
    assert small["chunk_words"] == 60
    assert len(small["sections"][0]["chunks"]) > 4


def test_embed_sections_short_chunk_words_keeps_overlap_valid():
    sections = analyze_sections((NLP / "kestrel_10k.html").read_bytes(), "10-K",
                                sentiment_model=LexiconModel(dictionary_path=""))
    embed_sections(sections, embedder=HashingEmbedder(dim=64), chunk_words=20)  # 20 ≤ 40
    assert all(ch["embedding"].shape == (64,) for s in sections for ch in s["chunks"])


def test_analyze_document_without_sections_and_non_10k_form():
    html = "<html><body><p>Nothing to see here at all in this filing text.</p></body></html>"
    model = CountingLexicon()
    result = analyze_document(html, "10-K", sentiment_model=model, embedder=HashingEmbedder())
    assert result["sections"] == [] and model.calls == []
    assert analyze_document(html, "8-K", sentiment_model=model,
                            embedder=HashingEmbedder())["sections"] == []


def test_sentiment_model_returning_wrong_length_raises():
    class Broken(LexiconModel):
        def predict(self, sentences):
            return []

    with pytest.raises(ValueError):
        analyze_sections((NLP / "kestrel_10k.html").read_bytes(), "10-K",
                         sentiment_model=Broken(dictionary_path=""))


# --------------------------------------------------------------------------- risk ve sinyaller


def test_compare_risk_requires_both_texts():
    assert compare_risk(None, "x") is None
    assert compare_risk("", "x") is None
    text = ("Our suppliers may fail to deliver components on time and this could harm "
            "our operations and revenue in many markets around the world.")
    added = text + "\n\nA brand new cybersecurity threat paragraph describing ransomware " \
        "attacks against our manufacturing plants and data centers worldwide."
    result = compare_risk(text, added)
    assert result["counts"]["added"] == 1 and result["counts"]["unchanged"] == 1


def test_compute_text_signals_dict_list_and_missing():
    summaries = {"7": {"net_tone": 0.25}, "1A": {"net_tone": -0.5}}
    risk = {"counts": {"added": 3, "removed": 0, "modified": 1, "unchanged": 9}}
    assert compute_text_signals(summaries, risk) == {
        "mdna_net_tone": 0.25, "risk_net_tone": -0.5, "risk_added": 3}
    as_list = [{"item_code": "7", "summary": {"net_tone": 0.1}}]
    assert compute_text_signals(as_list, None) == {
        "mdna_net_tone": 0.1, "risk_net_tone": None, "risk_added": None}
    assert compute_text_signals(None, None) == {
        "mdna_net_tone": None, "risk_net_tone": None, "risk_added": None}
    assert compute_text_signals({"7": {"net_tone": None}}, {"counts": {}})["mdna_net_tone"] \
        is None


def test_latest_annual_summaries_picks_newest_10k_only():
    def sec(acc, form, filed, code, tone):
        return {"filing": {"accession_no": acc, "form": form, "filing_date": filed},
                "item_code": code, "net_tone": tone}

    sections = [sec("old", "10-K", "2024-02-01", "7", 0.9),
                sec("new", "10-K", "2025-02-01", "7", 0.1),
                sec("new", "10-K", "2025-02-01", "1A", -0.4),
                sec("q", "10-Q", "2025-05-01", "2", -0.9)]
    filing, by_code = latest_annual_summaries(sections)
    assert filing["accession_no"] == "new"
    assert compute_text_signals(by_code, None)["mdna_net_tone"] == 0.1
    assert compute_text_signals(by_code, None)["risk_net_tone"] == -0.4
    assert latest_annual_summaries([sections[-1]]) == (None, {})


def test_score_company_uses_last_three_periods():
    facts = extract_all(load("companyfacts_full_example.json"), years=4)
    ratios, score = score_company(facts, {"mdna_net_tone": 0.1, "risk_net_tone": None,
                                          "risk_added": 2})
    assert [p.isoformat() for p in ratios["periods"]] == ["2023-06-30", "2024-06-30",
                                                          "2025-06-30"]
    assert score["total"] is not None and score["as_of_period"] == date(2025, 6, 30)
    # 2023 ROA ortalaması için 2022 bilanço değeri kullanılır (bir yıl fazla çekildi)
    roa = next(r for r in ratios["ratios"] if r["key"] == "roa")
    assert roa["values"][0]["note"] is None
