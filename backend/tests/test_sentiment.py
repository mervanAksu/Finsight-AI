"""FR-08: duygu modeli seçimi, sözlük modeli davranışı ve bölüm özeti."""

import pathlib
import sys

import pytest

from finsight import sentiment
from finsight.sentiment import (
    LexiconModel,
    get_sentiment_model,
    load_lm_dictionary,
    summarize,
)

LM_CSV = pathlib.Path(__file__).parent / "fixtures" / "nlp" / "lm_dictionary_sample.csv"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("LM_DICTIONARY_PATH", raising=False)
    monkeypatch.delenv("FINSIGHT_SENTIMENT_MODEL", raising=False)


def test_embedded_lexicon_size_and_disjoint():
    assert len(sentiment.POSITIVE_WORDS) >= 60
    assert len(sentiment.NEGATIVE_WORDS) >= 60
    assert not sentiment.POSITIVE_WORDS & sentiment.NEGATIVE_WORDS
    for ambiguous in ("increase", "record", "outstanding", "risk"):
        assert ambiguous not in sentiment.POSITIVE_WORDS | sentiment.NEGATIVE_WORDS


def test_lexicon_labels_positive_negative_neutral():
    model = LexiconModel()
    assert model.name == "lexicon-fallback"
    pos, neg, neu = model.predict([
        "Revenue growth was strong and margins improved significantly.",
        "Impairment charges and litigation losses adversely affected results.",
        "The company is headquartered in Dayton, Ohio.",
    ])
    assert pos["label"] == "positive" and 0.5 < pos["score"] <= 1.0
    assert neg["label"] == "negative" and 0.5 < neg["score"] <= 1.0
    assert neu == {"label": "neutral", "score": 0.75}


def test_lexicon_confidence_grows_with_consistent_hits():
    model = LexiconModel()
    one = model.score_sentence("Demand was strong this quarter.")
    three = model.score_sentence("Demand was strong, profitable and resilient this quarter.")
    mixed = model.score_sentence("Strong demand offset weak pricing.")
    assert one["score"] == pytest.approx(0.75)
    assert three["score"] == pytest.approx(0.875)
    assert mixed == {"label": "neutral", "score": 0.5}


def test_lexicon_negation_flips_sign():
    model = LexiconModel()
    assert model.score_sentence("Results were not strong this year.")["label"] == "negative"
    # D10: olumsuzlanmış olumsuz kelime olumluya çevrilmez, nötrdür (önceden "positive").
    assert model.score_sentence("We never had a breach.") == {"label": "neutral", "score": 0.75}
    assert model.score_sentence("There was no impairment of goodwill.")["label"] == "neutral"
    # Olumsuzlama yalnızca önceki 4 kelimeye bakar.
    far = "We did not expect that the overall quarter would be so strong."
    assert model.score_sentence(far)["label"] == "positive"
    assert model.score_sentence("Margins didn't improve.")["label"] == "negative"


def test_lexicon_inflections_match_embedded_words():
    model = LexiconModel()
    assert model.score_sentence("Several disruptions occurred.")["label"] == "negative"
    assert model.score_sentence("Profitability strengthens further.")["label"] == "positive"


def test_lm_dictionary_from_env(monkeypatch):
    pos, neg = load_lm_dictionary(str(LM_CSV))
    assert pos == {"zorple"}
    assert neg == {"blargh", "flimsy"}  # -2020 (sözlükten çıkarılmış) dahil değil
    monkeypatch.setenv("LM_DICTIONARY_PATH", str(LM_CSV))
    model = LexiconModel()
    assert model.name == "loughran-mcdonald-lexicon"
    assert model.score_sentence("The zorple outlook remains.")["label"] == "positive"
    assert model.score_sentence("A flimsy blargh result here.")["label"] == "negative"
    # LM sözlüğü kullanılırken gömülü liste devre dışı.
    assert model.score_sentence("Revenue growth was strong.")["label"] == "neutral"


def test_lm_dictionary_missing_file_falls_back(monkeypatch, tmp_path):
    monkeypatch.setenv("LM_DICTIONARY_PATH", str(tmp_path / "yok.csv"))
    model = LexiconModel()
    assert model.name == "lexicon-fallback"
    assert model.score_sentence("Revenue growth was strong.")["label"] == "positive"


def test_get_sentiment_model_falls_back_when_transformers_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "transformers", None)  # import → ImportError
    model = get_sentiment_model()
    assert isinstance(model, LexiconModel)
    assert model.name == "lexicon-fallback"


def test_get_sentiment_model_env_forces_lexicon(monkeypatch):
    calls = []
    monkeypatch.setattr(sentiment, "FinBertModel", lambda *a, **k: calls.append(1))
    monkeypatch.setenv("FINSIGHT_SENTIMENT_MODEL", "lexicon")
    assert isinstance(get_sentiment_model(), LexiconModel)
    assert calls == []


def test_get_sentiment_model_uses_finbert_when_available(monkeypatch):
    """FinBERT AutoTokenizer + AutoModel ile yüklenir (sahte torch/transformers, ağsız)."""
    from fake_finbert import FakeModel, FakeTokenizer, fake_torch_module

    seen = {}

    class AutoTokenizer:
        @staticmethod
        def from_pretrained(name):
            seen["tokenizer"] = name
            return FakeTokenizer()

    class AutoModelForSequenceClassification:
        @staticmethod
        def from_pretrained(name):
            seen["model"] = name
            return FakeModel(fixed_label=1)  # id2label[1] = "negative"

    class FakeTransformers:
        pass

    FakeTransformers.AutoTokenizer = AutoTokenizer
    FakeTransformers.AutoModelForSequenceClassification = AutoModelForSequenceClassification
    monkeypatch.setitem(sys.modules, "transformers", FakeTransformers)
    monkeypatch.setitem(sys.modules, "torch", fake_torch_module())
    for name in ("FINSIGHT_FINBERT_BATCH", "FINSIGHT_FINBERT_MAX_LENGTH",
                 "FINSIGHT_FINBERT_QUANTIZE", "FINSIGHT_TORCH_THREADS"):
        monkeypatch.delenv(name, raising=False)
    model = get_sentiment_model()
    assert model.name == "ProsusAI/finbert"
    assert seen == {"tokenizer": "ProsusAI/finbert", "model": "ProsusAI/finbert"}
    assert model.batch_size == 32 and model.max_length == 512
    [pred] = model.predict(["Sales fell."])
    assert pred["label"] == "negative" and 0.5 < pred["score"] <= 1.0
    assert model.predict([]) == []


def test_summarize_counts_tone_and_top_sentences():
    sentences = [f"s{i}" for i in range(8)]
    preds = [
        {"label": "positive", "score": 0.9},
        {"label": "negative", "score": 0.95},
        {"label": "negative", "score": 0.7},
        {"label": "neutral", "score": 0.8},
        {"label": "negative", "score": 0.99},
        {"label": "positive", "score": 0.6},
        {"label": "neutral", "score": 0.5},
        {"label": "negative", "score": 0.8},
    ]
    out = summarize(sentences, preds, top_n=3)
    assert out["sentence_count"] == 8
    assert (out["positive"], out["negative"], out["neutral"]) == (2, 4, 2)
    assert out["net_tone"] == pytest.approx(-0.25)
    assert [x["sentence"] for x in out["top_negative"]] == ["s4", "s1", "s7"]
    assert out["top_positive"] == [{"sentence": "s0", "score": 0.9}, {"sentence": "s5", "score": 0.6}]


def test_summarize_empty_and_mismatch():
    out = summarize([], [])
    assert out["net_tone"] is None and out["sentence_count"] == 0
    assert out["top_negative"] == [] and out["top_positive"] == []
    with pytest.raises(ValueError):
        summarize(["a"], [])


def test_summarize_default_top_n_is_five():
    sentences = [f"n{i}" for i in range(9)]
    preds = [{"label": "negative", "score": 0.5 + i / 100} for i in range(9)]
    assert len(summarize(sentences, preds)["top_negative"]) == 5


# ---- D10: güvence vermeme kalıpları, 4 kelimelik pencere, olumsuzlanmış olumsuz ----


@pytest.mark.parametrize("sentence", [
    "There can be no assurance that we will achieve profitability.",
    "We cannot assure you that our growth strategy will be successful.",
    "We can not assure investors that margins will improve.",
    "There is no assurance that the expansion will succeed.",
])
def test_no_assurance_phrases_are_negative(sentence):
    assert LexiconModel().score_sentence(sentence)["label"] == "negative"


def test_negated_negative_within_four_words_is_neutral():
    model = LexiconModel()
    s = "We did not experience any material disruption during the year."
    assert model.score_sentence(s) == {"label": "neutral", "score": 0.75}
    # Pencere dışındaki olumsuz kelime olumsuz kalır.
    far = "We did not expect that the overall supply would see disruption."
    assert model.score_sentence(far)["label"] == "negative"
    # Olumsuzlanmış olumlu kelime hâlâ olumsuzdur (4 kelime içinde).
    assert model.score_sentence("Demand was not particularly very strong.")["label"] == "negative"
    # Olumsuzlanmış olumsuz + olumlu kelime → olumlu
    assert model.score_sentence("Margins were strong with no impairment.")["label"] == "positive"
