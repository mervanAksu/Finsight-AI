"""Aşama 4 performans iyileştirmeleri (docs/performans-asama4.md) — torch'suz, ağsız.

- FR-07/FR-08/FR-10 metin hattı: html_to_text lxml hızlı yolu = BeautifulSoup yolu (birebir),
  derin iç içe belgede güvenli geri dönüş, bs4 yolunda alt ağaç taraması (find) yapılmaması.
- FR-08 FinBERT: FINSIGHT_DEVICE / FINSIGHT_FP16 çözümü, CUDA'ya taşıma, fp16 adı, GPU belleği
  yetmezse grubun bölünmesi, ONNX Runtime arka ucu (sahte optimum).
- FR-10 gömme: yinelenen metinlerin bir kez gömülmesi, hashing kova önbelleği, ST cihazı.
- FR-09 risk farkı: numpy benzerlik = saf Python başvuru uygulaması.
- bench_pipeline (sentetik belge, ağsız ölçüm) ve bench_sentiment GPU yapılandırmaları.
"""

from __future__ import annotations

import json
import logging
import pathlib
import random
import sys
import types
from types import SimpleNamespace

import numpy as np
import pytest
from fake_finbert import FakeModel, FakeTokenizer, expected_label, fake_torch_module

from finsight import bench_pipeline as bp
from finsight import bench_sentiment as bench
from finsight import embeddings, sentiment, text_extract
from finsight.risk_diff import (compare_risk_sections, sparse_similarities,
                                sparse_similarities_reference, tfidf_vectors)
from finsight.sentiment import FinBertModel, configured_finbert_name, resolve_device

NLP = pathlib.Path(__file__).parent / "fixtures" / "nlp"
ENV = ("FINSIGHT_DEVICE", "FINSIGHT_FP16", "FINSIGHT_FINBERT_BACKEND", "FINSIGHT_FINBERT_BATCH",
       "FINSIGHT_FINBERT_MAX_LENGTH", "FINSIGHT_FINBERT_QUANTIZE", "FINSIGHT_TORCH_THREADS")
SENTENCES = [
    "Revenue increased due to strong demand for cloud services.",
    "We may not be able to protect our intellectual property.",
    "Net income was flat.",
    "Supply chain disruptions could adversely affect our results of operations and margins.",
    "The Company recorded an impairment charge.",
    "Demand improved in all regions during the fourth quarter of the fiscal year.",
    "Costs rose.",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


# --------------------------------------------------------------------------- html_to_text

TRICKY_HTML = [
    "a<b>c</b>d",
    "<p>x<!--yorum-->y</p>kuyruk",
    "<html><body><table><tr><td>1<br>2</td><td><p>3</p></td></tr></table></body></html>",
    b"\xff\xfe bozuk <p>latin\xe9</p>",
    "<div hidden>GIZLI</div>sonra<span style='DISPLAY : none'>s</span>t",
    "",
    "yalnızca düz metin",
    "<?xml version='1.0'?><html><head><title>T</title></head><body><script>x</script>ok"
    "<style>s</style><noscript>n</noscript></body></html>son",
    "<div>Item&nbsp;1A.&#160;Risk Factors</div><div>12</div><div>Table of Contents</div>",
    "<ix:header><ix:hidden>XBRL</ix:hidden></ix:header><ix:nonFraction>1,234</ix:nonFraction>",
    "<table><tr><td><table><tr><td>iç</td></tr></table></td><td>dış</td></tr></table>",
    "<p>a<br/>b</p><ul><li>bir</li><li>iki</li></ul><pre>  boşluk  </pre>",
]


def _fixture_inputs() -> list:
    docs = [p.read_bytes() for p in sorted(NLP.glob("*.html"))]
    docs += TRICKY_HTML
    docs += [h for _n, _f, h in bp.synthetic_company(scale=0.1)]
    return docs


@pytest.mark.parametrize("index", range(len(_fixture_inputs())))
def test_html_to_text_lxml_fast_path_matches_bs4(index):
    html = _fixture_inputs()[index]
    assert text_extract.html_to_text(html, backend="lxml") == \
        text_extract.html_to_text(html, backend="bs4")


def test_html_to_text_default_backend_is_lxml_and_drops_hidden_blocks():
    html = (NLP / "kestrel_10k.html").read_bytes()
    text = text_extract.html_to_text(html)
    assert text == text_extract.html_to_text(html, backend="lxml")
    assert "HIDDENXBRLTOKEN" not in text and "SCRIPTTOKEN" not in text
    with pytest.raises(ValueError):
        text_extract.html_to_text(html, backend="html5lib")


@pytest.mark.parametrize("depth", [50, 239, 240, 300, 3000])
def test_deeply_nested_documents_fall_back_without_losing_text(depth):
    # libxml2 ağaç ayrıştırıcısı ~256 düzeyden sonrasını sessizce atar; güvenli geri dönüş.
    html = "<p>önce</p>" + "<font>" * depth + "derin <b>metin</b>" + "</font>" * (depth // 2) \
        + " kuyruk"
    text = text_extract.html_to_text(html)
    assert "derin metin" in text and text.endswith("kuyruk")
    assert text == text_extract.html_to_text(html, backend="bs4")


def test_bs4_path_does_not_scan_subtrees(monkeypatch):
    """bs4 4.13+ ``Tag.decomposed`` → ``__getattr__`` → ``find`` alt ağaç taraması yapıyordu."""
    from bs4.element import Tag

    calls = {"find": 0}
    original = Tag.find

    def counting_find(self, *args, **kwargs):
        calls["find"] += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Tag, "find", counting_find)
    text_extract.html_to_text((NLP / "kestrel_10k.html").read_bytes(), backend="bs4")
    assert calls["find"] == 0


# --------------------------------------------------------------------------- risk farkı


def _random_vectors(rng: random.Random, n: int, vocab: int) -> list[dict[int, float]]:
    out = []
    for _ in range(n):
        terms = rng.sample(range(vocab), rng.randint(0, 25))
        vec = {t: rng.random() for t in terms}
        norm = sum(w * w for w in vec.values()) ** 0.5
        out.append({t: w / norm for t, w in vec.items()} if norm else {})
    return out


@pytest.mark.parametrize("seed", range(5))
@pytest.mark.parametrize("threshold", [0.0, 0.2, 0.5, 0.9])
def test_numpy_similarities_match_reference(seed, threshold):
    rng = random.Random(seed)
    left = _random_vectors(rng, rng.randint(0, 40), 60)
    right = _random_vectors(rng, rng.randint(0, 40), 80)  # sağda solda olmayan terimler de var
    fast = sparse_similarities(left, right, threshold)
    ref = sparse_similarities_reference(left, right, threshold)
    assert set(fast) == set(ref)
    for key, value in ref.items():
        assert fast[key] == pytest.approx(value, abs=1e-12)


def test_numpy_similarities_on_real_paragraph_vectors_and_edge_cases():
    d = bp.synthetic_company(scale=0.3)
    texts = [text_extract.extract_sections(text_extract.html_to_text(h), "10-K")["1A"]["text"]
             for _n, _f, h in d[:2]]
    paras = [text_extract.split_paragraphs(t) for t in texts]
    vecs = tfidf_vectors(paras[0] + paras[1])
    n = len(paras[0])
    assert sparse_similarities(vecs[:n], vecs[n:], 0.5) == \
        sparse_similarities_reference(vecs[:n], vecs[n:], 0.5)
    assert sparse_similarities([], vecs, 0.5) == {}
    assert sparse_similarities([{}], [{}], 0.0) == {}
    # Ortak terimi olmayan çift eşik 0 olsa da listelenmez (başvuru uygulamasıyla aynı).
    assert sparse_similarities([{1: 1.0}], [{2: 1.0}], 0.0) == {}


def test_compare_risk_sections_counts_on_synthetic_filings():
    d = bp.synthetic_company(scale=0.3)
    old, new = (text_extract.extract_sections(text_extract.html_to_text(h), "10-K")["1A"]["text"]
                for _n, _f, h in d[:2])
    result = compare_risk_sections(old, new)
    counts = result["counts"]
    assert counts["unchanged"] > 0 and counts["added"] + counts["modified"] > 0
    assert compare_risk_sections(old, old)["counts"]["unchanged"] == \
        len(text_extract.split_paragraphs(old))


# --------------------------------------------------------------------------- gömme


def test_unique_texts_preserves_first_occurrence_order():
    assert embeddings.unique_texts(["b", "a", "b", "c", "a"]) == (["b", "a", "c"], [0, 1, 0, 2, 1])
    assert embeddings.unique_texts([]) == ([], [])


def test_hashing_embedder_dedup_and_bucket_cache_do_not_change_vectors(monkeypatch):
    texts = ["supply chain disruption risk", "cloud revenue growth", "supply chain disruption risk"]
    emb = embeddings.HashingEmbedder()
    mat = emb.embed(texts)
    fresh = embeddings.HashingEmbedder()
    one_by_one = np.vstack([fresh.embed([t]) for t in texts])
    np.testing.assert_array_equal(mat, one_by_one)
    np.testing.assert_array_equal(mat[0], mat[2])
    assert emb._buckets  # önbellek dolu
    monkeypatch.setattr(embeddings, "BUCKET_CACHE_MAX", 1)  # sınırda sıfırlanır, sonuç aynı
    small = embeddings.HashingEmbedder()
    np.testing.assert_array_equal(small.embed(texts), mat)
    assert len(small._buckets) <= 1


class _FakeST:
    instances: list = []

    def __init__(self, name, device=None):
        self.name, self.device = name, device
        self.encoded: list[list[str]] = []
        _FakeST.instances.append(self)

    def get_sentence_embedding_dimension(self):
        return 4

    def encode(self, texts, **kwargs):
        self.encoded.append(list(texts))
        assert kwargs["normalize_embeddings"] is True
        return np.array([[len(t), 1.0, 0.0, float(i)] for i, t in enumerate(texts)])


def _install_fake_st(monkeypatch):
    mod = types.ModuleType("sentence_transformers")
    mod.SentenceTransformer = _FakeST  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", mod)
    _FakeST.instances.clear()


def test_st_embedder_encodes_each_unique_text_once_in_order(monkeypatch):
    _install_fake_st(monkeypatch)
    emb = embeddings.SentenceTransformerEmbedder(device="cpu")
    out = emb.embed(["aa", "bbb", "aa", "c"])
    assert _FakeST.instances[0].encoded == [["aa", "bbb", "c"]]
    assert out.shape == (4, 4) and out.dtype == np.float32
    np.testing.assert_array_equal(out[0], out[2])
    np.testing.assert_allclose(np.linalg.norm(out, axis=1), 1.0, rtol=1e-6)


def test_st_embedder_device_follows_finsight_device(monkeypatch):
    _install_fake_st(monkeypatch)
    monkeypatch.setitem(sys.modules, "torch", _cuda_torch())
    assert embeddings.SentenceTransformerEmbedder().device == "cuda"
    monkeypatch.setenv("FINSIGHT_DEVICE", "cpu")
    assert embeddings.SentenceTransformerEmbedder().device == "cpu"
    monkeypatch.setenv("FINSIGHT_DEVICE", "auto")
    monkeypatch.setitem(sys.modules, "torch", fake_torch_module())  # CUDA'sız torch
    assert embeddings.SentenceTransformerEmbedder().device == "cpu"


# --------------------------------------------------------------------------- FinBERT cihazı


def _cuda_torch(capability=(5, 0), arch_list=("sm_50", "sm_60", "sm_61", "sm_70", "sm_75"),
                available=True, smoke_error: Exception | None = None):
    """fake_torch_module + sahte torch.cuda (varsayılan: MX130 benzeri Maxwell sm_50)."""
    torch = fake_torch_module()
    torch.empty_cache_calls = 0  # type: ignore[attr-defined]

    def empty_cache():
        torch.empty_cache_calls += 1  # type: ignore[attr-defined]

    torch.cuda = SimpleNamespace(  # type: ignore[attr-defined]
        is_available=lambda: available,
        get_device_capability=lambda _i=0: capability,
        get_device_name=lambda _i=0: "Sahte GeForce",
        get_arch_list=lambda: list(arch_list),
        empty_cache=empty_cache,
    )

    def ones(n, device=None):
        if smoke_error is not None:
            raise smoke_error
        return np.ones(n)

    torch.ones = ones  # type: ignore[attr-defined]
    return torch


class _MovableArray(np.ndarray):
    moved_to: list = []

    def to(self, device):
        _MovableArray.moved_to.append(device)
        return np.asarray(self)


class _TensorTokenizer(FakeTokenizer):
    def __call__(self, texts, **kwargs):
        enc = super().__call__(texts, **kwargs)
        if kwargs.get("return_tensors") == "pt":
            return {k: np.asarray(v).view(_MovableArray) for k, v in enc.items()}
        return enc


class _MovableModel(FakeModel):
    def __init__(self, oom_above: int | None = None, error: str = "CUDA out of memory"):
        super().__init__()
        self.to_calls: list[str] = []
        self.half_called = False
        self.oom_above = oom_above
        self.error = error

    def to(self, device):
        self.to_calls.append(device)
        return self

    def half(self):
        self.half_called = True
        return self

    def __call__(self, input_ids, attention_mask=None, **kwargs):
        if self.oom_above is not None and np.asarray(input_ids).shape[0] > self.oom_above:
            raise RuntimeError(self.error)
        return super().__call__(input_ids, attention_mask, **kwargs)


def test_resolve_device_rules():
    cpu_only = fake_torch_module()
    assert resolve_device(cpu_only, "auto", "0")[:2] == ("cpu", False)
    assert resolve_device(_cuda_torch(), "cpu", "1")[:2] == ("cpu", False)
    dev, fp16, info = resolve_device(_cuda_torch(), "auto", "0")
    assert (dev, fp16) == ("cuda", False) and "sm_50" in info
    # fp16 auto: Maxwell/Pascal kapalı, Volta+ açık; "1" zorlar.
    assert resolve_device(_cuda_torch((5, 0)), "auto", "auto")[1] is False
    assert resolve_device(_cuda_torch((6, 1)), "auto", "auto")[1] is False
    assert resolve_device(_cuda_torch((7, 5)), "auto", "auto")[1] is True
    assert resolve_device(_cuda_torch((5, 0)), "cuda", "1")[1] is True


def test_resolve_device_rejects_gpu_missing_from_torch_build(caplog):
    # Maxwell kartı + yalnızca Turing+ çekirdekli derleme (ör. CUDA 13 wheel'i).
    torch = _cuda_torch((5, 0), arch_list=("sm_75", "sm_80", "sm_86", "sm_90"))
    dev, _fp16, info = resolve_device(torch, "auto", "0")
    assert dev == "cpu" and "desteklenmiyor" in info
    # PTX (compute_XX ≤ cc) varsa JIT ile çalışabilir → kabul.
    ptx = _cuda_torch((8, 9), arch_list=("sm_80", "compute_80"))
    assert resolve_device(ptx, "auto", "0")[0] == "cuda"
    with caplog.at_level(logging.WARNING):
        assert resolve_device(torch, "cuda", "0")[0] == "cpu"
    assert "FINSIGHT_DEVICE=cuda" in caplog.text


def test_resolve_device_smoke_test_failure_falls_back_to_cpu():
    torch = _cuda_torch(smoke_error=RuntimeError("no kernel image is available"))
    dev, _fp16, info = resolve_device(torch, "auto", "0")
    assert dev == "cpu" and "no kernel image" in info
    assert resolve_device(_cuda_torch(available=False), "auto", "0")[0] == "cpu"


def test_device_and_fp16_env_parsing(monkeypatch, caplog):
    assert sentiment.device_from_env() == "auto" and sentiment.fp16_from_env() == "0"
    monkeypatch.setenv("FINSIGHT_DEVICE", "GPU")
    monkeypatch.setenv("FINSIGHT_FP16", "belki")
    with caplog.at_level(logging.WARNING):
        assert sentiment.device_from_env() == "auto"
        assert sentiment.fp16_from_env() == "0"
    assert "FINSIGHT_DEVICE" in caplog.text and "FINSIGHT_FP16" in caplog.text
    monkeypatch.setenv("FINSIGHT_DEVICE", "CUDA")
    monkeypatch.setenv("FINSIGHT_FP16", "auto")
    assert sentiment.device_from_env() == "cuda" and sentiment.fp16_from_env() == "auto"


def test_finbert_on_cuda_fp32_moves_model_and_inputs_and_keeps_name(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _cuda_torch())
    _MovableArray.moved_to = []
    mdl = _MovableModel()
    model = FinBertModel(tokenizer=_TensorTokenizer(), model=mdl, batch_size=3)
    assert model.device == "cuda" and not model.fp16 and not mdl.half_called
    assert mdl.to_calls == ["cuda"]
    assert model.name == "ProsusAI/finbert"  # fp32 GPU: aynı ad, aynı önbellek
    preds = model.predict(SENTENCES)
    assert [p["label"] for p in preds] == [expected_label(s) for s in SENTENCES]
    assert set(_MovableArray.moved_to) == {"cuda"}


def test_finbert_fp16_gets_separate_name(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _cuda_torch((7, 5)))
    monkeypatch.setenv("FINSIGHT_FP16", "auto")
    mdl = _MovableModel()
    model = FinBertModel(tokenizer=_TensorTokenizer(), model=mdl)
    assert model.fp16 and mdl.half_called and model.name == "ProsusAI/finbert+fp16"


def test_finbert_fp16_ignored_on_cpu(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", fake_torch_module())
    monkeypatch.setenv("FINSIGHT_FP16", "1")
    model = FinBertModel(tokenizer=FakeTokenizer(), model=FakeModel())
    assert model.device == "cpu" and not model.fp16 and model.name == "ProsusAI/finbert"


def test_finbert_splits_batch_on_gpu_oom_without_changing_labels(monkeypatch, caplog):
    torch = _cuda_torch()
    monkeypatch.setitem(sys.modules, "torch", torch)
    model = FinBertModel(tokenizer=_TensorTokenizer(), model=_MovableModel(oom_above=2),
                         batch_size=7)
    with caplog.at_level(logging.WARNING):
        preds = model.predict(SENTENCES)
    assert [p["label"] for p in preds] == [expected_label(s) for s in SENTENCES]
    assert model.oom_splits >= 2 and torch.empty_cache_calls == model.oom_splits
    assert caplog.text.count("GPU belleği yetmedi") == 1  # tek uyarı


def test_finbert_oom_on_single_sentence_or_other_errors_propagate(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _cuda_torch())
    model = FinBertModel(tokenizer=_TensorTokenizer(), model=_MovableModel(oom_above=0))
    with pytest.raises(RuntimeError, match="out of memory"):
        model.predict(SENTENCES[:3])
    other = FinBertModel(tokenizer=_TensorTokenizer(),
                         model=_MovableModel(oom_above=1, error="device-side assert"))
    with pytest.raises(RuntimeError, match="device-side assert"):
        other.predict(SENTENCES[:3])


def test_finbert_cpu_runtime_error_is_not_split(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", fake_torch_module())
    model = FinBertModel(tokenizer=FakeTokenizer(), model=_MovableModel(oom_above=1))
    with pytest.raises(RuntimeError):
        model.predict(SENTENCES[:3])


def test_quantize_is_skipped_on_cuda(monkeypatch, caplog):
    torch = _cuda_torch()
    monkeypatch.setitem(sys.modules, "torch", torch)
    with caplog.at_level(logging.WARNING):
        model = FinBertModel(tokenizer=_TensorTokenizer(), model=_MovableModel(), quantize=True)
    assert not model.quantized and torch.quantize_calls == []
    assert "yalnızca CPU" in caplog.text


def test_onnx_backend_uses_optimum_and_separate_name(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _cuda_torch())
    calls = []

    def from_pretrained(name, export=False):
        calls.append((name, export))
        return FakeModel()  # eval() var; ORTModel'de olmayabilir (hasattr ile korunur)

    optimum = types.ModuleType("optimum")
    ort = types.ModuleType("optimum.onnxruntime")
    ort.ORTModelForSequenceClassification = SimpleNamespace(  # type: ignore[attr-defined]
        from_pretrained=from_pretrained)
    monkeypatch.setitem(sys.modules, "optimum", optimum)
    monkeypatch.setitem(sys.modules, "optimum.onnxruntime", ort)
    monkeypatch.setenv("FINSIGHT_FINBERT_BACKEND", "onnx")
    model = FinBertModel(tokenizer=FakeTokenizer())
    assert calls == [("ProsusAI/finbert", True)]
    assert model.device == "cpu" and model.name == "ProsusAI/finbert+onnx"
    assert [p["label"] for p in model.predict(SENTENCES)] == [expected_label(s)
                                                              for s in SENTENCES]


def test_configured_name_reflects_fp16_and_backend(monkeypatch, caplog):
    assert configured_finbert_name() == "ProsusAI/finbert"
    monkeypatch.setenv("FINSIGHT_FP16", "auto")  # donanıma bağlı: yüklemeden tahmin edilmez
    assert configured_finbert_name() == "ProsusAI/finbert"
    monkeypatch.setenv("FINSIGHT_FP16", "1")
    monkeypatch.setenv("FINSIGHT_FINBERT_BACKEND", "onnx")
    assert configured_finbert_name() == "ProsusAI/finbert+onnx+fp16"
    monkeypatch.setenv("FINSIGHT_FINBERT_BACKEND", "tensorrt")
    with caplog.at_level(logging.WARNING):
        assert sentiment.finbert_backend_from_env() == "torch"


# --------------------------------------------------------------------------- bench araçları


def test_bench_sentiment_gpu_config_without_cuda_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", fake_torch_module())
    with pytest.raises(bench.CudaUnavailable):
        bench.build_config_model("e", "m", 8, FakeTokenizer(), FakeModel())


def test_bench_sentiment_gpu_config_uses_copy_of_weights(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _cuda_torch())
    base = _MovableModel()
    model = bench.build_config_model("f", "m", 8, _TensorTokenizer(), base)
    assert base.to_calls == [] and not base.half_called  # CPU ağırlıkları dokunulmadı
    assert model.device == "cuda" and model.fp16 and model.name == "m+fp16"
    cpu = bench.build_config_model("b", "m", 8, FakeTokenizer(), base)
    assert cpu.device == "cpu" and cpu.name == "m"


def test_bench_recommendations_cover_gpu_and_onnx_rows():
    rows = [{"key": "b", "seconds": 10.0, "agreement": 1.0},
            {"key": "e", "seconds": 4.0, "agreement": 1.0},
            {"key": "f", "seconds": 3.0, "agreement": 0.95},
            {"key": "g", "seconds": 9.5, "agreement": 1.0}]
    recs = "\n".join(bench.recommendations(rows, "b"))
    assert "FINSIGHT_DEVICE=cuda" in recs and "2.50x" in recs
    assert "FINSIGHT_FP16=1: (b)'ye göre 3.33x, (b) ile uyum %95.0 → açılması önerilmez" in recs
    assert "FINSIGHT_FINBERT_BACKEND=onnx: (b)'ye göre 1.05x" in recs and "önerilmez" in recs


def test_synthetic_filings_have_expected_sections():
    docs = bp.synthetic_company(scale=0.2)
    assert [f for _n, f, _h in docs] == ["10-K", "10-K", "10-Q"]
    for _name, form, html in docs:
        sections = text_extract.extract_sections(text_extract.html_to_text(html), form)
        expected = {"1A", "7"} if form == "10-K" else {"2", "1A"}
        assert set(sections) == expected
        assert bp.detect_form(html.encode()) == form
    # Aynı tohum → aynı belge (tekrarlanabilir ölçüm).
    assert bp.synthetic_filing_html(seed=3, risk_paragraphs=5) == \
        bp.synthetic_filing_html(seed=3, risk_paragraphs=5)


def test_run_pipeline_with_fallback_models_reports_all_steps():
    from finsight.embeddings import HashingEmbedder
    from finsight.sentiment import LexiconModel

    docs = [(n, f, h.encode()) for n, f, h in bp.synthetic_company(scale=0.2)]
    result = bp.run_pipeline(docs, LexiconModel(dictionary_path=""), HashingEmbedder())
    assert set(result["timings"]) == {key for key, _ in bp.STEPS}
    assert result["counts"]["documents"] == 3 and result["counts"]["sentences"] > 50
    assert 0 < result["counts"]["predicted"] <= result["counts"]["sentences"]
    assert result["risk"] is not None and result["risk"]["unchanged"] > 0
    assert result["sentiment_model"] == "lexicon-fallback"
    report = bp.format_report(bp.best_of([result, result]))
    assert "HTML → metin" in report and "Risk farkı" in report and "en iyi 2" in report


def test_load_documents_and_main_cli(tmp_path, capsys):
    a = tmp_path / "docs" / "0001" / "a.htm"
    a.parent.mkdir(parents=True)
    a.write_text(bp.synthetic_filing_html("10-Q", risk_paragraphs=10, mdna_paragraphs=10,
                                          tables=1, hidden_facts=5), encoding="utf-8")
    b = tmp_path / "b.html"
    b.write_text((NLP / "kestrel_10k.html").read_text(encoding="utf-8"), encoding="utf-8")
    docs = bp.load_documents([f"{b}:10-K"], str(tmp_path / "docs"))
    assert [(n, f) for n, f, _ in docs] == [("b.html", "10-K"), ("a.htm", "10-Q")]
    out_json = tmp_path / "sonuc.json"
    assert bp.main(["--scale", "0.05", "--json", str(out_json)]) == 0
    out = capsys.readouterr().out
    assert "Duygu analizi" in out and "yatırım tavsiyesi değildir" in out
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["counts"]["documents"] == 3
    assert bp.main(["--dir", str(tmp_path / "bos")]) == 2
