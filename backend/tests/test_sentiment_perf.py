"""FR-08 duygu analizi performans iyileştirmeleri (torch'suz, ağsız, sahte modellerle).

- FinBertModel: uzunluğa göre sıralı dinamik dolgu + orijinal sıraya geri yerleştirme,
  id2label eşlemesi, ortam değişkenleri (grup boyutu, iş parçacığı, max_length, int8),
- cümle önbelleği / yinelenen cümlelerin tek tahmini (analyze.predict_with_cache),
- 10-Q hızlı modu (FINSIGHT_ANALYZE_10Q), adım süresi sayaçları,
- bench_sentiment saf yardımcıları (uyum oranı, karışıklık, uzunluk dağılımı, öneri ölçütü).
"""

import logging
import pathlib
import sys
from datetime import date

import pytest
from fake_finbert import FakeModel, FakeTokenizer, expected_label, fake_torch_module

from finsight import bench_sentiment as bench
from finsight import sentiment
from finsight.analyze import (TIMING_STEPS, analyze_sections, predict_with_cache,
                              select_filings_for_analysis, sentiment_stats)
from finsight.config import analyze_quarterly_enabled
from finsight.sentiment import (FinBertModel, LexiconModel, configured_finbert_name,
                                finbert_variant_name, length_sorted_batches,
                                normalize_sentence, restore_order)

NLP = pathlib.Path(__file__).parent / "fixtures" / "nlp"
FINBERT_ENV = ("FINSIGHT_FINBERT_BATCH", "FINSIGHT_FINBERT_MAX_LENGTH",
               "FINSIGHT_FINBERT_QUANTIZE", "FINSIGHT_TORCH_THREADS")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in FINBERT_ENV + ("FINSIGHT_ANALYZE_10Q",):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def torch_mod(monkeypatch):
    mod = fake_torch_module()
    monkeypatch.setitem(sys.modules, "torch", mod)
    return mod


class CountingModel(LexiconModel):
    """Modele giden her cümleyi kaydeden sözlük modeli."""

    def __init__(self, name="counting-model"):
        super().__init__(dictionary_path="")
        self.name = name
        self.calls: list[list[str]] = []

    def predict(self, sentences):
        self.calls.append(list(sentences))
        return super().predict(sentences)

    @property
    def seen(self) -> list[str]:
        return [s for call in self.calls for s in call]


# --------------------------------------------------------------------------- saf sıralama


def test_length_sorted_batches_longest_first_stable_and_complete():
    lengths = [5, 30, 5, 12, 30, 7, 1]
    batches = length_sorted_batches(lengths, 3)
    assert batches == [[1, 4, 3], [5, 0, 2], [6]]  # eşit uzunlukta orijinal sıra korunur
    flat = [i for b in batches for i in b]
    assert sorted(flat) == list(range(len(lengths)))
    assert length_sorted_batches([], 4) == []
    with pytest.raises(ValueError):
        length_sorted_batches([1], 0)


def test_restore_order_puts_results_back_and_validates():
    batches = [[2, 0], [1]]
    assert restore_order(batches, [["c", "a"], ["b"]], 3) == ["a", "b", "c"]
    with pytest.raises(ValueError):  # eksik indeks
        restore_order([[0]], [["a"]], 2)
    with pytest.raises(ValueError):  # grup/çıktı boyu uyuşmuyor
        restore_order([[0, 1]], [["a"]], 2)
    with pytest.raises(ValueError):  # aynı indeks iki kez
        restore_order([[0], [0]], [["a"], ["b"]], 1)


def test_sort_and_restore_roundtrip_matches_identity():
    lengths = [3, 9, 1, 9, 4, 4, 2, 8, 6]
    batches = length_sorted_batches(lengths, 4)
    outputs = [[f"s{i}" for i in b] for b in batches]
    assert restore_order(batches, outputs, len(lengths)) == [f"s{i}" for i in range(9)]


def test_normalize_sentence_only_collapses_whitespace():
    assert normalize_sentence("  Net  sales\n increased\t5%. ") == "Net sales increased 5%."
    assert normalize_sentence("Sales FELL.") != normalize_sentence("sales fell.")


# --------------------------------------------------------------------------- FinBertModel


SENTENCES = [
    "Revenue grew.",
    "Our operating results could fluctuate significantly from quarter to quarter.",
    "Cash.",
    "We face intense competition in all of our markets and may lose share.",
    "Revenue grew.",
    "Supply chain disruptions may adversely affect our ability to deliver products on time.",
    "Margins improved.",
]


def test_finbert_sorted_batches_restore_original_order(torch_mod):
    tok, mdl = FakeTokenizer(), FakeModel()
    model = FinBertModel(tokenizer=tok, model=mdl, batch_size=3)
    preds = model.predict(SENTENCES)
    assert [p["label"] for p in preds] == [expected_label(s) for s in SENTENCES]
    assert all(0.33 < p["score"] <= 1.0 for p in preds)
    assert mdl.eval_called
    # 1 uzunluk ölçümü + 3 grup; gruplar dinamik dolgulu ve tensör olarak
    assert tok.calls[0]["padding"] is False and tok.calls[0]["n"] == len(SENTENCES)
    batch_calls = tok.calls[1:]
    assert [c["n"] for c in batch_calls] == [3, 3, 1]
    assert all(c["padding"] == "longest" and c["return_tensors"] == "pt" for c in batch_calls)
    # İlk grup en uzun cümleler; her grubun genişliği yalnızca kendi en uzun cümlesi kadar
    widths = [shape[1] for shape in mdl.batch_shapes]
    assert widths == sorted(widths, reverse=True)
    assert widths[-1] < widths[0]
    assert all(c["max_length"] == 512 for c in tok.calls)


def test_finbert_label_mapping_from_config_id2label(torch_mod):
    mdl = FakeModel(fixed_label=0, id2label={0: "LABEL_2", 1: "LABEL_0", 2: "LABEL_1"})
    model = FinBertModel(tokenizer=FakeTokenizer(), model=mdl)
    assert model.predict(["Anything here."])[0]["label"] == "neutral"
    mdl2 = FakeModel(fixed_label=2, id2label={0: "Positive", 1: "Negative", 2: "weird"})
    assert FinBertModel(tokenizer=FakeTokenizer(), model=mdl2).predict(["x y"])[0]["label"] \
        == "neutral"  # bilinmeyen etiket → neutral (eski eşleme mantığı)
    mdl3 = FakeModel(fixed_label=1, id2label={0: "Positive", 1: "Negative", 2: "Neutral"})
    assert FinBertModel(tokenizer=FakeTokenizer(), model=mdl3).predict(["x y"])[0]["label"] \
        == "negative"


def test_finbert_env_batch_threads_and_max_length(torch_mod, monkeypatch):
    monkeypatch.setenv("FINSIGHT_FINBERT_BATCH", "2")
    monkeypatch.setenv("FINSIGHT_TORCH_THREADS", "4")
    monkeypatch.setenv("FINSIGHT_FINBERT_MAX_LENGTH", "256")
    tok, mdl = FakeTokenizer(), FakeModel()
    model = FinBertModel(tokenizer=tok, model=mdl)
    assert model.batch_size == 2 and model.max_length == 256
    assert torch_mod.threads == [4]
    assert model.name == "ProsusAI/finbert+len256"  # doğruluğu etkileyebilir → ayrı ad
    model.predict(SENTENCES[:5])
    assert [c["n"] for c in tok.calls[1:]] == [2, 2, 1]
    assert all(c["max_length"] == 256 for c in tok.calls)


def test_finbert_defaults_do_not_touch_threads(torch_mod):
    model = FinBertModel(tokenizer=FakeTokenizer(), model=FakeModel())
    assert torch_mod.threads == []  # verilmezse torch varsayılanı
    assert model.batch_size == 32 and model.max_length == 512 and not model.quantized
    assert model.name == "ProsusAI/finbert"


def test_finbert_invalid_env_values_fall_back_to_defaults(torch_mod, monkeypatch, caplog):
    monkeypatch.setenv("FINSIGHT_FINBERT_BATCH", "abc")
    monkeypatch.setenv("FINSIGHT_FINBERT_MAX_LENGTH", "4096")
    with caplog.at_level(logging.WARNING):
        model = FinBertModel(tokenizer=FakeTokenizer(), model=FakeModel())
    assert model.batch_size == 32
    assert model.max_length == 512 and model.name == "ProsusAI/finbert"  # 512'ye kırpılır
    assert "FINSIGHT_FINBERT_BATCH" in caplog.text


def test_finbert_quantize_on_success(monkeypatch):
    torch_mod = fake_torch_module()
    monkeypatch.setitem(sys.modules, "torch", torch_mod)
    monkeypatch.setenv("FINSIGHT_FINBERT_QUANTIZE", "1")
    model = FinBertModel(tokenizer=FakeTokenizer(), model=FakeModel())
    assert model.quantized and model.name == "ProsusAI/finbert+int8"
    assert torch_mod.quantize_calls == [({"Linear"}, "qint8")]
    assert [p["label"] for p in model.predict(SENTENCES)] == [expected_label(s)
                                                              for s in SENTENCES]


def test_finbert_quantize_failure_falls_back_with_warning(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "torch",
                        fake_torch_module(quantize=RuntimeError("no engine")))
    with caplog.at_level(logging.WARNING):
        model = FinBertModel(tokenizer=FakeTokenizer(), model=FakeModel(), quantize=True)
    assert not model.quantized and model.name == "ProsusAI/finbert"
    assert "kuantizasyon" in caplog.text
    assert len(model.predict(SENTENCES)) == len(SENTENCES)


def test_configured_finbert_name_reflects_env(monkeypatch):
    assert configured_finbert_name() == "ProsusAI/finbert"
    monkeypatch.setenv("FINSIGHT_FINBERT_QUANTIZE", "1")
    monkeypatch.setenv("FINSIGHT_FINBERT_MAX_LENGTH", "256")
    assert configured_finbert_name() == "ProsusAI/finbert+int8+len256"
    assert finbert_variant_name("m", 512, False) == "m"


def test_get_sentiment_model_falls_back_when_torch_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)  # import torch → ImportError
    monkeypatch.delenv("FINSIGHT_SENTIMENT_MODEL", raising=False)
    assert isinstance(sentiment.get_sentiment_model(), LexiconModel)


# --------------------------------------------------------------------------- önbellek


def test_cache_hits_never_reach_the_model_and_keep_order():
    model = CountingModel()
    cache = {normalize_sentence("Revenue  grew strongly."): {"label": "negative", "score": 0.66}}
    stats: dict = {}
    sentences = ["Margins improved.", "Revenue grew strongly.", "Litigation losses rose."]
    preds = predict_with_cache(model, sentences, cache=cache, stats=stats)
    assert model.seen == ["Margins improved.", "Litigation losses rose."]
    # isabet önbellekteki değeri döndürür (model "positive" derdi)
    assert preds[1] == {"label": "negative", "score": 0.66}
    assert preds[0]["label"] == "positive" and preds[2]["label"] == "negative"
    assert stats["sentences"] == 3 and stats["predicted"] == 2 and stats["cache_hits"] == 1
    assert stats["duplicates"] == 0 and stats["sentiment_s"] >= 0
    # yeni tahminler önbelleğe yazıldı → ikinci çağrıda model hiç çağrılmaz
    again = predict_with_cache(model, sentences, cache=cache)
    assert again == preds and len(model.calls) == 1


def test_duplicate_sentences_are_predicted_once():
    model = CountingModel()
    stats: dict = {}
    sentences = ["Demand was strong.", "Costs rose.", "Demand  was strong.", "Costs rose."]
    preds = predict_with_cache(model, sentences, stats=stats)
    assert model.seen == ["Demand was strong.", "Costs rose."]
    assert preds[0] == preds[2] and preds[1] == preds[3]
    assert preds[0] is not preds[2]  # çağıran sonuçları değiştirirse diğerleri etkilenmez
    assert stats["duplicates"] == 2 and stats["predicted"] == 2


def test_predict_with_cache_without_cache_does_not_leak_state():
    model = CountingModel()
    predict_with_cache(model, ["Costs rose."])
    predict_with_cache(model, ["Costs rose."])
    assert len(model.calls) == 2  # önbellek verilmezse çağrılar arası paylaşım yok


def test_predict_with_cache_wrong_length_raises():
    class Broken(CountingModel):
        def predict(self, sentences):
            return []

    with pytest.raises(ValueError):
        predict_with_cache(Broken(), ["a b c"])


def test_shared_cache_second_10k_only_new_sentences_reach_model():
    """Eski 10-K'dan sonra yeni 10-K: yalnızca eklenen (değişen) cümleler modele gider."""
    new_html = (NLP / "kestrel_10k.html").read_text(encoding="utf-8")
    old_html = "\n".join(line for line in new_html.splitlines()
                         if "Changes in tax laws" not in line)
    model = CountingModel()
    cache: dict = {}
    old_stats: dict = {}
    old = analyze_sections(old_html, "10-K", sentiment_model=model, prediction_cache=cache,
                           stats=old_stats)
    first_calls = len(model.seen)
    new_stats: dict = {}
    new = analyze_sections(new_html, "10-K", sentiment_model=model, prediction_cache=cache,
                           stats=new_stats)
    new_only = model.seen[first_calls:]
    old_keys = {normalize_sentence(s["sentence"]) for sec in old for s in sec["sentences"]}
    new_keys = [normalize_sentence(s["sentence"]) for sec in new for s in sec["sentences"]]
    assert new_only and all(normalize_sentence(s) not in old_keys for s in new_only)
    assert any(s.startswith("Changes in tax laws") for s in new_only)
    assert new_stats["cache_hits"] == len({k for k in new_keys if k in old_keys})
    assert new_stats["predicted"] == len(new_only) < new_stats["sentences"]
    # Sonuçlar önbelleksiz analizle birebir aynı
    fresh = analyze_sections(new_html, "10-K", sentiment_model=LexiconModel(dictionary_path=""))
    assert [sec["sentences"] for sec in new] == [sec["sentences"] for sec in fresh]
    assert [sec["summary"] for sec in new] == [sec["summary"] for sec in fresh]
    for key in ("html_to_text_s", "sections_s", "sentiment_s"):
        assert new_stats[key] >= 0


def test_sentiment_stats_rates():
    st = sentiment_stats({"sentences": 100, "predicted": 40, "cache_hits": 55, "duplicates": 5,
                          "sentiment_s": 4.0}, cache_enabled=True, cache_preloaded=12)
    assert st["sentences_per_second"] == 25.0 and st["model_sentences_per_second"] == 10.0
    assert st["cache_hits"] == 55 and st["cache_preloaded"] == 12 and st["cache_enabled"]
    empty = sentiment_stats({}, cache_enabled=False)
    assert empty["sentences_per_second"] is None and empty["seconds"] == 0.0
    keys = [k for k, _ in TIMING_STEPS]
    assert {"download", "html_to_text", "sections", "sentiment", "embedding", "risk",
            "score"} <= set(keys)


# --------------------------------------------------------------------------- 10-Q hızlı modu


def F(acc, form, filed):
    return {"accession_no": acc, "form": form, "filing_date": date.fromisoformat(filed),
            "report_date": None, "primary_document": "d.htm", "is_amendment": False}


def test_select_filings_without_quarterly():
    filings = [F("k1", "10-K", "2024-02-01"), F("k2", "10-K", "2025-02-01"),
               F("q1", "10-Q", "2025-05-01")]
    assert [f["accession_no"] for f in select_filings_for_analysis(filings)] == ["k1", "k2", "q1"]
    assert [f["accession_no"] for f in
            select_filings_for_analysis(filings, include_quarterly=False)] == ["k1", "k2"]


@pytest.mark.parametrize("raw,expected", [(None, True), ("", True), ("1", True),
                                          ("0", False), ("false", False), ("no", False)])
def test_analyze_quarterly_env(monkeypatch, raw, expected):
    if raw is not None:
        monkeypatch.setenv("FINSIGHT_ANALYZE_10Q", raw)
    assert analyze_quarterly_enabled() is expected


# --------------------------------------------------------------------------- ölçüm aracı


def test_bench_agreement_and_confusion():
    ref = [{"label": x} for x in ("positive", "negative", "neutral", "neutral")]
    oth = [{"label": x} for x in ("positive", "neutral", "neutral", "neutral")]
    result = bench.agreement(ref, oth)
    assert result["rate"] == pytest.approx(0.75) and result["matches"] == 3
    assert result["confusion"]["negative"]["neutral"] == 1
    assert result["confusion"]["neutral"]["neutral"] == 2
    text = bench.format_confusion("c", result)
    assert "olumsuz" in text and "%0.0" in text and "%100.0" in text
    with pytest.raises(ValueError):
        bench.agreement(ref, oth[:2])


def test_bench_length_distribution():
    dist = bench.length_distribution([10, 70, 130, 300, 600])
    assert dist["n"] == 5 and dist["max"] == 600 and dist["p50"] == 130
    assert dist["over"] == {64: 4, 128: 3, 256: 2, 512: 1}
    text = bench.format_length_distribution(dist)
    assert "128 token'ı aşan" in text and "(60.0%)" in text
    assert bench.length_distribution([])["n"] == 0


def test_bench_sample_is_deduplicated_and_deterministic():
    pool = ["a b", "a  b", "c d", "e f", "g h"]
    assert bench.sample_sentences(pool, None) == ["a b", "c d", "e f", "g h"]
    s1 = bench.sample_sentences(pool, 2, seed=1)
    assert s1 == bench.sample_sentences(pool, 2, seed=1) and len(s1) == 2


def test_bench_table_and_recommendations():
    rows = [
        {"key": "a", "description": bench.CONFIG_DESCRIPTIONS["a"], "n": 500, "seconds": 50.0,
         "agreement": 1.0},
        {"key": "b", "description": bench.CONFIG_DESCRIPTIONS["b"], "n": 500, "seconds": 25.0,
         "agreement": 0.999},
        {"key": "c", "description": bench.CONFIG_DESCRIPTIONS["c"], "n": 500, "seconds": 12.5,
         "agreement": 0.975},
        {"key": "d", "description": bench.CONFIG_DESCRIPTIONS["d"], "n": 500, "seconds": 24.0,
         "agreement": 0.99},
    ]
    table = bench.format_results_table(rows)
    assert "Cümle/sn" in table and "2.00x" in table and "4.00x" in table and "%97.5" in table
    recs = bench.recommendations(rows)
    assert any("FINSIGHT_FINBERT_QUANTIZE=1" in r and "açılması önerilir" in r for r in recs)
    # max_length 256: uyum yüksek ama hızlanma 1,04x < 1,2x → önerilmez
    assert any("MAX_LENGTH=256" in r and "önerilmez" in r for r in recs)
    rows[2]["agreement"] = 0.95
    assert any("QUANTIZE" in r and "önerilmez" in r for r in bench.recommendations(rows))
    failed = bench.format_results_table([{"key": "c", "description": "x", "n": 1,
                                          "seconds": None, "note": "hata"}])
    assert "çalışmadı" in failed


def test_bench_exits_cleanly_without_torch(monkeypatch, capsys):
    monkeypatch.setattr(bench, "_deps_missing", lambda: ["torch", "transformers"])
    assert bench.main(["MSFT"]) == 2
    err = capsys.readouterr().err
    assert "torch" in err and "requirements-ml.txt" in err
    assert bench.main([]) == 2  # ticker / --file yok


def test_bench_main_end_to_end_with_fake_models(monkeypatch, tmp_path, capsys):
    """Ölçüm aracının çalışma yolu (sahte torch/transformers, --file ile; ağ ve DB yok)."""
    import types

    torch_mod = fake_torch_module()
    torch_mod.__version__ = "0.0-fake"  # type: ignore[attr-defined]
    torch_mod.get_num_threads = lambda: 4  # type: ignore[attr-defined]

    def fake_pipeline(task, model=None, tokenizer=None, device=None):
        assert task == "text-classification" and device == -1

        def run(texts, batch_size=None, truncation=None, max_length=None):
            assert batch_size == 16 and truncation is True and max_length == 512
            return [{"label": expected_label(t).upper(), "score": 0.9} for t in texts]
        return run

    transformers = types.ModuleType("transformers")
    transformers.pipeline = fake_pipeline  # type: ignore[attr-defined]
    transformers.AutoTokenizer = types.SimpleNamespace(  # type: ignore[attr-defined]
        from_pretrained=lambda _name: FakeTokenizer())
    transformers.AutoModelForSequenceClassification = types.SimpleNamespace(  # type: ignore[attr-defined]
        from_pretrained=lambda _name: FakeModel())
    utils = types.ModuleType("transformers.utils")
    utils.logging = types.SimpleNamespace(set_verbosity_error=lambda: None)  # type: ignore[attr-defined]
    transformers.utils = utils  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "torch", torch_mod)
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    monkeypatch.setitem(sys.modules, "transformers.utils", utils)
    monkeypatch.setattr(bench, "_deps_missing", lambda: [])

    path = tmp_path / "cumleler.txt"
    path.write_text("\n".join(SENTENCES * 3), encoding="utf-8")
    assert bench.main(["--file", str(path), "--limit", "0", "--threads", "2"]) == 0
    out = capsys.readouterr().out
    assert torch_mod.threads == [2]
    assert "Token uzunluğu" in out and "Cümle/sn" in out
    for key in "abcd":
        assert f"\n{key}  " in out
    assert out.count("%100.0") >= 3  # b, c, d sahte modelde (a) ile birebir uyumlu
    assert "(c) karışıklık" in out and "Öneri" in out
