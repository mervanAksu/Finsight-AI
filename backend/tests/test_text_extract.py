"""FR-07: HTML temizleme, bölüm ayıklama, cümle/paragraf bölme ve parçalama testleri."""

import pathlib

import pytest

from finsight.text_extract import (
    SECTION_TITLES,
    chunk_text,
    extract_sections,
    html_to_text,
    split_paragraphs,
    split_sentences,
)

NLP = pathlib.Path(__file__).parent / "fixtures" / "nlp"


def text_of(name: str) -> str:
    return html_to_text((NLP / name).read_bytes())


# --------------------------------------------------------------------------- html_to_text


def test_html_to_text_removes_hidden_xbrl_scripts_and_display_none():
    for name in ("kestrel_10k.html", "kestrel_10q.html"):
        text = text_of(name)
        assert "HIDDENXBRLTOKEN" not in text  # ix:header (display:none içinde ve tek başına)
        assert "HIDDENCONTEXTTOKEN" not in text
        assert "SECRETHIDDENSPAN" not in text  # satır içi display:none
        assert "SCRIPTTOKEN" not in text and "font-family" not in text
        assert "kstr-2025" not in text  # <title>


def test_html_to_text_keeps_visible_inline_xbrl_and_table_cells_separated():
    text = text_of("kestrel_10k.html")
    assert "Net revenue $ 1,234.5 $ 1,102.2" in text  # hücreler boşlukla, &nbsp; normalize
    assert "Operating income 187.3 160.9" in text
    assert "\xa0" not in text and "  " not in text


def test_html_to_text_nbsp_heading_and_block_lines():
    text = text_of("kestrel_10k.html")
    assert "\nItem 1A. Risk Factors\n" in text
    assert "UNITED STATES\nSECURITIES AND EXCHANGE COMMISSION" in text  # <br/> → satır sonu
    lines = text.split("\n")
    assert "9" not in lines and "Table of Contents" not in lines  # sayfa numarası/bağlantı


def test_html_to_text_decodes_bytes_with_latin1_fallback():
    html = "<p>Caf\xe9 revenue rose.</p>".encode("latin-1")
    assert html_to_text(html) == "Café revenue rose."
    assert html_to_text("<p>a</p><div>b<br>c</div>") == "a\n\nb\nc"
    assert html_to_text(b"\xef\xbb\xbf<p>UTF-8 \xc3\xa7</p>") == "UTF-8 ç"


def test_html_to_text_handles_deeply_nested_tags():
    html = "<font>" * 3000 + "deep text" + "</font>" * 3000
    assert html_to_text(html) == "deep text"


# --------------------------------------------------------------------------- extract_sections


def test_10k_sections_skip_toc_and_pick_real_bodies():
    text = text_of("kestrel_10k.html")
    sections = extract_sections(text, "10-K")
    assert set(sections) == {"1A", "7"}

    risk = sections["1A"]
    assert risk["item_code"] == "1A" and risk["title"] == SECTION_TITLES["1A"] == "Risk Faktörleri"
    assert risk["text"] == text[risk["char_start"]:risk["char_end"]]
    assert risk["text"].startswith("Risks Related to Our Business")
    assert risk["text"].endswith("could adversely affect our net income.")
    assert "Unresolved Staff Comments" not in risk["text"]
    assert "Cybersecurity" not in risk["text"]
    assert len(risk["text"].split()) > 200

    mdna = sections["7"]  # "ITEM 7 —" varyasyonu, büyük harf
    assert mdna["title"] == "Yönetimin Değerlendirmesi (MD&A)"
    assert mdna["text"].startswith("The following discussion should be read")
    assert "Net revenue $ 1,234.5" in mdna["text"]
    assert mdna["text"].endswith("for at least the next twelve months.")
    assert "QUANTITATIVE AND QUALITATIVE" not in mdna["text"]


def test_10q_mdna_in_part_i_and_risk_factors_in_part_ii():
    text = text_of("kestrel_10q.html")
    sections = extract_sections(text, "10-Q")
    assert set(sections) == {"2", "1A"}
    mdna = sections["2"]  # "Item 2:" varyasyonu
    assert mdna["text"].startswith("Net revenue for the third quarter of 2025")
    assert "repurchased" not in mdna["text"]  # Part II Item 2 (Unregistered Sales) değil
    assert "Controls and Procedures" not in mdna["text"]
    risk = sections["1A"]  # "Item 1A" + satır sonu + "Risk Factors"
    assert risk["text"].startswith("Except as described below")
    assert risk["text"].endswith("outside the U.S.")
    assert "repurchased" not in risk["text"]
    assert risk["char_start"] > mdna["char_end"]


def test_amendment_forms_use_same_rules():
    text = text_of("kestrel_10k.html")
    assert extract_sections(text, "10-K/A").keys() == {"1A", "7"}
    q = text_of("kestrel_10q.html")
    assert extract_sections(q, "10-q/a").keys() == {"2", "1A"}
    assert extract_sections(text, "8-K") == {}
    assert extract_sections("", "10-K") == {}


def test_smaller_reporting_company_not_applicable_risk_section_is_omitted():
    sections = extract_sections(text_of("wren_small_10k.html"), "10-K")
    assert "1A" not in sections  # "ITEM 1A: RISK FACTORS" → "Not applicable."
    assert sections["7"]["text"].startswith("Revenue decreased 18%")  # "Item 7 -" varyasyonu
    assert "going concern" in sections["7"]["text"]


@pytest.mark.parametrize(
    "heading",
    ["ITEM 1A. RISK FACTORS", "Item 1A: Risk Factors", "Item 1A — Risk Factors",
     "Item 1A - Risk Factors", "Item 1A\nRisk Factors", "item  1a.   risk factors",
     "Part I, Item 1A. Risk Factors"],
)
def test_heading_variations(heading):
    body = " ".join(["Demand for our products may decline sharply."] * 10)
    text = f"Item 1A. Risk Factors 12\nItem 1B. Unresolved Staff Comments 20\n\n{heading}\n\n{body}\n\nItem 1B. Unresolved Staff Comments\n\nNone."
    section = extract_sections(text, "10-K")["1A"]
    assert section["text"] == body


def test_risk_section_ends_at_item_2_when_1b_and_1c_missing():
    body = " ".join(["Competition could reduce our market share and margins."] * 5)
    text = f"Item 1A. Risk Factors\n\n{body}\n\nItem 2. Properties\n\nWe lease our office."
    assert extract_sections(text, "10-K")["1A"]["text"] == body


def test_mdna_ends_at_item_8_without_7a():
    body = " ".join(["Revenue grew because of higher unit volumes in Asia."] * 5)
    text = f"Item 7. Management's Discussion and Analysis\n\n{body}\n\nItem 8. Financial Statements"
    assert extract_sections(text, "10-K")["7"]["text"] == body


def test_long_not_applicable_mention_does_not_drop_real_section():
    body = " ".join(["Our results may fluctuate and certain disclosures are not applicable."] * 30)
    text = f"Item 1A. Risk Factors\n\n{body}\n\nItem 1B. Unresolved Staff Comments"
    assert "1A" in extract_sections(text, "10-K")


# --------------------------------------------------------------------------- cümle / paragraf


def test_split_sentences_keeps_abbreviations_together():
    text = (
        "Sales in the U.S. rose 5%. Acme Inc. reported results, e.g. higher margins, i.e. better "
        "mix. See Note No. 4 for details. Mr. Smith and Dr. Jones vs. the board approx. agreed. "
        "Ok. Is demand strong? Yes it is strong!"
    )
    assert split_sentences(text) == [
        "Sales in the U.S. rose 5%.",
        "Acme Inc. reported results, e.g. higher margins, i.e. better mix.",
        "See Note No. 4 for details.",
        "Mr. Smith and Dr. Jones vs. the board approx. agreed.",
        "Is demand strong?",
        "Yes it is strong!",
    ]


def test_split_sentences_drops_short_and_cuts_long():
    assert split_sentences("Overview\nNet revenue increased 12% in 2025.") == [
        "Net revenue increased 12% in 2025."
    ]
    long_sentence = "word " * 600 + "end."
    parts = split_sentences(long_sentence)
    assert len(parts) > 1 and all(len(p) <= 1000 for p in parts)
    assert sum(len(p.split()) for p in parts) == 601


def test_split_paragraphs_merges_short_heading_into_next():
    text = (
        "Risks Related to Our Business\n\nWe depend on a small number of customers for most of "
        "our revenue.\n\nWe rely on key suppliers for semiconductors and other critical parts."
    )
    assert split_paragraphs(text) == [
        "Risks Related to Our Business We depend on a small number of customers for most of our revenue.",
        "We rely on key suppliers for semiconductors and other critical parts.",
    ]


def test_split_paragraphs_hard_wrapped_text_and_single_newlines():
    wrapped = "Our revenue depends on a few large customers\nand any loss could hurt results.\n\nNone"
    assert split_paragraphs(wrapped) == [
        "Our revenue depends on a few large customers and any loss could hurt results. None"
    ]
    lines = "First paragraph has enough words to stand alone here.\nSecond paragraph also has enough words in it."
    assert len(split_paragraphs(lines)) == 2
    assert split_paragraphs("   ") == []


def test_chunk_text_respects_target_overlap_and_paragraphs():
    paras = [" ".join(f"p{i}w{j}" for j in range(100)) for i in range(7)]
    text = "\n\n".join(paras)
    chunks = chunk_text(text, target_words=350, overlap_words=50)
    assert len(chunks) == 3
    first = chunks[0].split()
    assert len(first) == 300 and first[-1] == "p2w99"  # paragraf bölünmedi
    second = chunks[1].split()
    assert second[:50] == first[-50:]  # örtüşme
    assert "p3w0" in second and len(second) <= 350
    all_words = set(" ".join(chunks).split())
    assert all_words == set(text.split())


def test_chunk_text_splits_oversized_paragraph():
    sentence = "The company faces significant competition in every market it serves."
    big = " ".join([sentence] * 60)  # ~600 kelime tek paragraf
    chunks = chunk_text(big, target_words=200, overlap_words=20)
    assert len(chunks) >= 3
    assert all(len(c.split()) <= 220 for c in chunks)
    assert chunk_text("", 100, 10) == []
    with pytest.raises(ValueError):
        chunk_text("x", target_words=10, overlap_words=10)


# --------------------------------------------------------------------------- başlık benzeri satır (Y1/D4)

_FILL = " ".join(["Demand for our products may decline and our results could suffer materially."] * 30)
_BUS = " ".join(["We design and sell industrial products to customers worldwide every year."] * 60)
_MD = " ".join(["Revenue increased primarily due to higher volumes and favorable pricing."] * 30)
_TOC = (
    "<table><tr><td>Item 1.</td><td>Business</td><td>3</td></tr>"
    "<tr><td>Item 1A.</td><td>Risk Factors</td><td>8</td></tr>"
    "<tr><td>Item 1B.</td><td>Unresolved Staff Comments</td><td>15</td></tr>"
    "<tr><td>Item 7.</td><td>Management’s Discussion and Analysis of Financial Condition and "
    "Results of Operations</td><td>20</td></tr>"
    "<tr><td>Item 7A.</td><td>Quantitative and Qualitative Disclosures About Market Risk</td>"
    "<td>30</td></tr><tr><td>Item 8.</td><td>Financial Statements</td><td>31</td></tr></table>"
)
_MDNA_HEAD = (
    "<p>Item 7. Management’s Discussion and Analysis of Financial Condition and Results of "
    "Operations</p>"
)


def _doc(risk_extra="", mdna_extra="", bus_extra="", risk_head="<p>Item 1A. Risk Factors</p>",
         mdna_head=_MDNA_HEAD, bus_head="<p>Item 1. Business</p>"):
    return (
        f"<html><body>{_TOC}<p>PART I</p>{bus_head}<p>{_BUS}</p>{bus_extra}"
        f"{risk_head}<p>{_FILL}</p>{risk_extra}<p>Final risk sentence here for the end.</p>"
        "<p>Item 1B. Unresolved Staff Comments</p><p>None.</p><p>Item 2. Properties</p>"
        "<p>We lease offices in many places around the world today.</p>"
        f"<p>PART II</p>{mdna_head}<p>{_MD}</p>{mdna_extra}<p>Final MD&amp;A sentence for the end.</p>"
        "<p>ITEM 7A. QUANTITATIVE AND QUALITATIVE DISCLOSURES ABOUT MARKET RISK</p>"
        "<p>Interest rate risk is limited.</p><p>Item 8. Financial Statements</p>"
        "<p>See the statements.</p></body></html>"
    )


def _sections(html: str) -> dict:
    return extract_sections(html_to_text(html), "10-K")


def test_risk_section_not_cut_by_part_ii_cross_reference():
    # Gözden geçirici senaryo 1: "Part II, Item 7 of this report ..." PART II başlığı değildir.
    extra = ("<p>Part II, Item 7 of this report discusses how these risks have affected our "
             f"results in recent periods.</p><p>{_FILL}</p>")
    risk = _sections(_doc(risk_extra=extra))["1A"]["text"]
    assert "Part II, Item 7 of this report" in risk
    assert risk.endswith("Final risk sentence here for the end.")


def test_mdna_not_cut_by_item_8_cross_reference():
    # Senaryo 2: "Item 8 of this Form 10-K contains ..." bitiş başlığı değildir.
    extra = ("<p>Item 8 of this Form 10-K contains the notes referenced in this discussion of "
             f"liquidity.</p><p>{_MD}</p>")
    mdna = _sections(_doc(mdna_extra=extra))["7"]["text"]
    assert "Item 8 of this Form 10-K contains" in mdna
    assert mdna.endswith("Final MD&A sentence for the end.")
    assert "QUANTITATIVE" not in mdna


def test_wrapped_cross_reference_in_item_1_is_not_risk_heading():
    # Senaryo 3b: Item 1 başında <br> ile satır başına düşen "Item 1A. Risk Factors, of this ...".
    xref = ("<p>For more information about the risks we face, see Part I,<br>Item 1A. Risk "
            "Factors, of this Annual Report on Form 10-K.</p>")
    sections = _sections(_doc(bus_head="<p>Item 1. Business</p>" + xref))
    risk = sections["1A"]["text"]
    assert risk.startswith("Demand for our products may decline")
    assert "We design and sell industrial products" not in risk
    assert "Annual Report on Form 10-K" not in risk


def test_wrapped_item_2_cross_reference_does_not_end_risk_section():
    # Senaryo 10: "Item 2. Properties, and damage to them ..." bitiş başlığı değildir.
    extra = ("<p>Our facilities are described under<br>Item 2. Properties, and damage to them "
             f"could disrupt operations.</p><p>{_FILL}</p>")
    risk = _sections(_doc(risk_extra=extra))["1A"]["text"]
    assert "and damage to them could disrupt operations." in risk
    assert risk.endswith("Final risk sentence here for the end.")
    assert len(risk.split()) > 700


@pytest.mark.parametrize(
    "risk_head",
    ["<p>Item 1 A. Risk Factors</p>", "<p>ITEM 1 A — RISK FACTORS</p>",
     "<table><tr><td>Item 1A</td><td>—</td><td>Risk Factors</td></tr></table>",
     "<p>PART I — ITEM 1A. RISK FACTORS</p>"],
)
def test_risk_heading_spacing_variants(risk_head):
    risk = _sections(_doc(risk_head=risk_head))["1A"]["text"]
    assert risk.startswith("Demand for our products may decline")
    assert risk.endswith("Final risk sentence here for the end.")


@pytest.mark.parametrize("apos", ["’", "'", "´", "ʼ", "`"])
def test_mdna_heading_apostrophe_variants(apos):
    head = f"<p>Item 7. Management{apos}s Discussion and Analysis</p>"
    mdna = _sections(_doc(mdna_head=head))["7"]["text"]
    assert mdna.startswith("Revenue increased primarily")
    assert mdna.endswith("Final MD&A sentence for the end.")


def test_body_sentence_on_heading_line_is_kept_in_section():
    head = "<p><b>Item 1A. Risk Factors</b> Investing in our stock involves a high degree of risk.</p>"
    risk = _sections(_doc(risk_head=head))["1A"]["text"]
    assert risk.startswith("Investing in our stock involves a high degree of risk.")
    head7 = ("<p>Item 7. Management’s Discussion and Analysis of Financial Condition and Results "
             "of Operations The following discussion should be read with our statements.</p>")
    mdna = _sections(_doc(mdna_head=head7))["7"]["text"]
    assert mdna.startswith("The following discussion should be read with our statements.")


def test_split_title_heading_and_toc_rows_are_skipped():
    head = ("<div>Item 7.</div><div>Management’s Discussion and Analysis of Financial Condition "
            "and Results of Operations</div>")
    sections = _sections(_doc(mdna_head=head))
    assert sections["7"]["text"].startswith("Revenue increased primarily")
    # içindekiler satırı (Item 1A → Item 1B arası 0 kelime) seçilmez
    assert sections["1A"]["char_start"] > html_to_text(_doc()).index("PART I")
