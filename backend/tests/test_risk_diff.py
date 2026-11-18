"""FR-09: risk faktörleri bölümleri arasında paragraf düzeyinde değişiklik tespiti."""

import numpy as np
import pytest

from finsight.risk_diff import (
    MODIFIED_THRESHOLD,
    UNCHANGED_THRESHOLD,
    compare_risk_sections,
    tfidf_matrix,
    tokenize,
)

CUSTOMERS = (
    "Our operating results depend on a limited number of large industrial customers. In fiscal "
    "2024, our three largest customers accounted for approximately 41% of net revenue. The loss "
    "of, or a significant reduction in orders from, any of these customers could adversely affect "
    "our revenue, results of operations and financial condition."
)
SUPPLIERS = (
    "We rely on a small number of suppliers for specialized semiconductors and rare-earth magnets. "
    "Disruption in the supply of these components, including shortages caused by geopolitical "
    "tensions or natural disasters, could delay shipments to customers and increase our costs."
)
CURRENCY = (
    "Our international operations expose us to risks associated with foreign currency fluctuations "
    "and compliance with anti-corruption laws. A strengthening of the dollar relative to the euro "
    "would reduce the reported value of our European revenue and operating margins."
)
TAX = (
    "Changes in tax laws, including the adoption of a global minimum tax, could increase our "
    "effective tax rate and reduce net income in future periods across our major jurisdictions."
)
AI_REG = (
    "New regulations governing artificial intelligence in industrial equipment could require us to "
    "redesign products, obtain certifications and incur significant compliance costs before sale."
)

CUSTOMERS_YEAR_CHANGED = CUSTOMERS.replace("2024", "2025")
SUPPLIERS_REWRITTEN = SUPPLIERS + (
    " In 2025 new export restrictions on rare-earth materials further limited availability, and we "
    "may not be able to qualify alternative suppliers in time."
)
SUPPLIERS_MOSTLY_REPLACED = (
    "We rely on a small number of suppliers for specialized semiconductors and rare-earth magnets. "
    "In 2025 new export restrictions on rare-earth materials further limited availability, and we "
    "entered into a multi-year agreement with an alternative supplier that may not fully offset "
    "these shortages."
)


def doc(*paras: str) -> str:
    return "\n\n".join(paras)


def test_thresholds_are_module_constants():
    assert UNCHANGED_THRESHOLD == 0.90
    assert MODIFIED_THRESHOLD == 0.50


def test_tokenize_and_tfidf_rows_are_normalized():
    assert tokenize("The Company's U.S. sales, and the risks!") == ["companys", "sales", "risks"]
    mat = tfidf_matrix([CUSTOMERS, SUPPLIERS, CUSTOMERS])
    assert mat.dtype == np.float32
    assert np.allclose(np.linalg.norm(mat, axis=1), 1.0, atol=1e-5)
    assert float(mat[0] @ mat[2]) == pytest.approx(1.0, abs=1e-5)
    assert float(mat[0] @ mat[1]) < 0.3


def test_added_removed_modified_unchanged():
    old = doc(CUSTOMERS, SUPPLIERS, CURRENCY, TAX)
    new = doc(AI_REG, CUSTOMERS_YEAR_CHANGED, SUPPLIERS_REWRITTEN, CURRENCY)
    result = compare_risk_sections(old, new)
    assert result["counts"] == {"added": 1, "removed": 1, "modified": 1, "unchanged": 2}
    types = [c["change_type"] for c in result["changes"]]
    assert types == ["added", "modified", "removed"]

    added, modified, removed = result["changes"]
    assert added == {"change_type": "added", "old_text": None, "new_text": AI_REG, "similarity": None}
    assert modified["old_text"] == SUPPLIERS and modified["new_text"] == SUPPLIERS_REWRITTEN
    assert MODIFIED_THRESHOLD <= modified["similarity"] < UNCHANGED_THRESHOLD
    assert modified["similarity"] == round(modified["similarity"], 3)
    assert removed == {"change_type": "removed", "old_text": TAX, "new_text": None, "similarity": None}


def test_small_edit_counts_as_unchanged():
    mat = tfidf_matrix([CUSTOMERS, CUSTOMERS_YEAR_CHANGED])
    assert float(mat[0] @ mat[1]) >= UNCHANGED_THRESHOLD
    result = compare_risk_sections(CUSTOMERS, CUSTOMERS_YEAR_CHANGED)
    assert result == {"counts": {"added": 0, "removed": 0, "modified": 0, "unchanged": 1},
                      "changes": []}


def test_modified_sorted_by_similarity_ascending():
    light_edit = SUPPLIERS.replace(
        "could delay shipments to customers and increase our costs.",
        "have already delayed some shipments and increased costs in 2025."
    )
    currency_edit = CURRENCY.replace(
        "and compliance with anti-corruption laws.",
        "including new tariffs and sanctions imposed on several of our export markets."
    )
    result = compare_risk_sections(doc(SUPPLIERS, CURRENCY), doc(currency_edit, light_edit))
    sims = [c["similarity"] for c in result["changes"]]
    assert [c["change_type"] for c in result["changes"]] == ["modified", "modified"]
    assert sims == sorted(sims)
    assert all(MODIFIED_THRESHOLD <= s < UNCHANGED_THRESHOLD for s in sims)


def test_greedy_matching_uses_each_paragraph_once():
    # Aynı eski paragrafa benzeyen iki yeni paragraf: biri eşleşir, diğeri eklenmiş sayılır.
    result = compare_risk_sections(CUSTOMERS, doc(CUSTOMERS, CUSTOMERS_YEAR_CHANGED))
    assert result["counts"] == {"added": 1, "removed": 0, "modified": 0, "unchanged": 1}
    assert result["changes"][0]["new_text"] == CUSTOMERS_YEAR_CHANGED


def test_dissimilar_paragraphs_are_added_and_removed_not_modified():
    result = compare_risk_sections(TAX, AI_REG)
    assert result["counts"] == {"added": 1, "removed": 1, "modified": 0, "unchanged": 0}
    assert [c["change_type"] for c in result["changes"]] == ["added", "removed"]


def test_mostly_replaced_paragraph_falls_below_modified_threshold():
    # Yalnızca ilk cümlesi korunan paragraf "değişti" değil, çıkarıldı + eklendi sayılır.
    mat = tfidf_matrix([SUPPLIERS, SUPPLIERS_MOSTLY_REPLACED])
    assert float(mat[0] @ mat[1]) < MODIFIED_THRESHOLD
    counts = compare_risk_sections(SUPPLIERS, SUPPLIERS_MOSTLY_REPLACED)["counts"]
    assert counts == {"added": 1, "removed": 1, "modified": 0, "unchanged": 0}


def test_empty_inputs():
    empty = {"added": 0, "removed": 0, "modified": 0, "unchanged": 0}
    assert compare_risk_sections("", "") == {"counts": empty, "changes": []}
    only_new = compare_risk_sections("", doc(TAX, AI_REG))
    assert only_new["counts"]["added"] == 2
    assert [c["new_text"] for c in only_new["changes"]] == [TAX, AI_REG]  # belge sırası
    assert compare_risk_sections(doc(TAX), None)["counts"]["removed"] == 1


# ---- D8: seyrek TF-IDF (bellek) ----


def test_sparse_similarities_match_dense_matrix():
    from finsight.risk_diff import sparse_similarities, tfidf_vectors

    texts = [CUSTOMERS, SUPPLIERS, CUSTOMERS_YEAR_CHANGED, SUPPLIERS_MOSTLY_REPLACED]
    mat = tfidf_matrix(texts)
    dense = mat[:2] @ mat[2:].T
    vecs = tfidf_vectors(texts)
    sparse = sparse_similarities(vecs[:2], vecs[2:], 0.0)
    for i in range(2):
        for j in range(2):
            assert sparse.get((i, j), 0.0) == pytest.approx(float(dense[i, j]), abs=1e-6)


def test_large_sections_use_little_memory():
    import random
    import tracemalloc

    rng = random.Random(0)
    vocab = [f"w{i}" for i in range(20000)]

    def section():
        return "\n\n".join(" ".join(rng.choice(vocab) for _ in range(90)) for _ in range(400))

    old, new = section(), section()
    tracemalloc.start()
    try:
        result = compare_risk_sections(old, new)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 50e6  # yoğun matrisle ~290 MB idi
    assert result["counts"] == {"added": 400, "removed": 400, "modified": 0, "unchanged": 0}
