"""FR-10: gömme katmanı — hashing yedeği, eşik makullüğü, serileştirme, top_k, model seçimi."""

import builtins
import hashlib
import os
import pathlib
import random
import subprocess
import sys

import numpy as np
import pytest

from finsight import embeddings
from finsight.embeddings import (
    HashingEmbedder,
    SentenceTransformerEmbedder,
    from_bytes,
    get_embedder,
    to_bytes,
    top_k,
)
from finsight.text_extract import chunk_text, extract_sections, html_to_text

NLP = pathlib.Path(__file__).parent / "fixtures" / "nlp"
BACKEND = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("FINSIGHT_EMBEDDER", raising=False)
    monkeypatch.delenv("FINSIGHT_EMBEDDING_MODEL", raising=False)


@pytest.fixture(scope="module")
def kestrel_chunks():
    sections = extract_sections(html_to_text((NLP / "kestrel_10k.html").read_bytes()), "10-K")
    return (chunk_text(sections["1A"]["text"], target_words=150, overlap_words=30)
            + [sections["7"]["text"]])


def test_hashing_embedder_shape_dtype_and_norm():
    emb = HashingEmbedder()
    assert emb.name == "hashing-fallback" and emb.dim == 1024
    assert emb.default_min_similarity == 0.12
    vecs = emb.embed(["Revenue grew strongly.", "Supplier shortages hurt margins.", ""])
    assert vecs.shape == (3, 1024) and vecs.dtype == np.float32
    assert np.allclose(np.linalg.norm(vecs[:2], axis=1), 1.0, atol=1e-6)
    assert not vecs[2].any()  # boş metin → sıfır vektör (NaN değil)
    assert emb.embed([]).shape == (0, 1024)


def test_hashing_is_deterministic_and_uses_hashlib(monkeypatch):
    text = "Disruption in the supply of semiconductors could delay shipments."
    first = HashingEmbedder().embed([text])
    # Python hash() kullanılmadığını doğrula: builtins.hash bozulsa bile sonuç aynı.
    monkeypatch.setattr(builtins, "hash", lambda *_: (_ for _ in ()).throw(AssertionError))
    second = HashingEmbedder().embed([text])
    assert np.array_equal(first, second)
    emb = HashingEmbedder(dim=64)
    digest = int.from_bytes(hashlib.blake2b(b"supply", digest_size=8).digest(), "little")
    assert emb._bucket("supply") == (digest % 64, 1.0 if digest >> 63 else -1.0)


def test_hashing_independent_of_pythonhashseed():
    code = ("import hashlib; from finsight.embeddings import HashingEmbedder; "
            "v = HashingEmbedder().embed(['Supplier shortages and tariff risks']); "
            "print(hashlib.sha256(v.tobytes()).hexdigest())")
    outputs = set()
    for seed in ("1", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        res = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env,
                             capture_output=True, text=True, check=True)
        outputs.add(res.stdout.strip())
    assert len(outputs) == 1


def test_similar_texts_score_higher_than_unrelated():
    emb = HashingEmbedder()
    a, b, c = emb.embed([
        "Shortages of semiconductors from our suppliers could delay product shipments.",
        "Our suppliers may face semiconductor shortages that delay shipments to customers.",
        "The board declared a quarterly cash dividend payable to shareholders in March.",
    ])
    assert float(a @ b) > 0.3
    assert float(a @ b) > float(a @ c) + 0.2


@pytest.mark.parametrize(
    "question, expected_idx",
    [
        ("What are the supplier and semiconductor shortage risks?", 0),
        ("Is there any patent infringement lawsuit?", 2),
        ("foreign currency euro exposure", 1),
        ("How much cash and cash equivalents does the company have?", 3),
    ],
)
def test_default_threshold_retrieves_relevant_chunk(kestrel_chunks, question, expected_idx):
    emb = HashingEmbedder()
    matrix = emb.embed(kestrel_chunks)
    q = emb.embed([question])[0]
    (best, sim), *_ = top_k(q, matrix, 3)
    assert sim >= emb.default_min_similarity
    assert any(word in kestrel_chunks[best].lower()
               for word in ("semiconductor", "patent", "euro", "cash equivalents"))
    assert best == expected_idx


@pytest.mark.parametrize(
    "question",
    ["Who is the CEO's favorite football team?", "weather forecast tomorrow in paris",
     "dividend policy and share buyback plans", "employee headcount and labor unions"],
)
def test_default_threshold_rejects_unrelated_questions(kestrel_chunks, question):
    emb = HashingEmbedder()
    sims = emb.embed(kestrel_chunks) @ emb.embed([question])[0]
    assert float(sims.max()) < emb.default_min_similarity


def test_hash_collision_noise_rarely_exceeds_threshold():
    # Ortak kelimesi olmayan metinler: benzerlik yalnızca işaretli çakışma gürültüsü.
    # Eşik ≈ 4σ; çift başına aşma olasılığı < %0.2. Yüzlerce parçada yine de nadiren
    # aşılabildiği için rag.py hashing'de ayrıca en az bir ortak kelime şartı arar.
    rnd = random.Random(7)
    vocab_a = [f"alpha{i}" for i in range(3000)]
    vocab_b = [f"beta{i}" for i in range(3000)]
    emb = HashingEmbedder()
    queries = emb.embed([" ".join(rnd.choices(vocab_a, k=6)) for _ in range(100)])
    docs = emb.embed([" ".join(rnd.choices(vocab_b, k=150)) for _ in range(100)])
    sims = (queries @ docs.T).ravel()
    assert abs(float(sims.mean())) < 0.01
    assert 0.02 < float(sims.std()) < 0.045  # ≈ 1/sqrt(1024) = 0.031
    assert float((sims >= emb.default_min_similarity).mean()) < 0.002


def test_to_from_bytes_roundtrip():
    vec = HashingEmbedder().embed(["Liquidity remains sufficient."])[0]
    raw = to_bytes(vec)
    assert len(raw) == 1024 * 4
    back = from_bytes(raw, 1024)
    assert back.dtype == np.float32 and np.array_equal(back, vec)
    back[0] = 5.0  # yazılabilir kopya
    assert to_bytes(np.array([1.0, 2.0])) == np.array([1.0, 2.0], dtype=np.float32).tobytes()
    with pytest.raises(ValueError):
        from_bytes(raw, 512)


def test_top_k_orders_by_cosine_and_handles_edges():
    matrix = np.array([[1, 0], [0.6, 0.8], [0, 1], [0.6, 0.8]], dtype=np.float32)
    q = np.array([0.6, 0.8], dtype=np.float32)
    result = top_k(q, matrix, 3)
    assert [i for i, _ in result] == [1, 3, 2]  # eşitlikte küçük indeks önce
    assert result[0][1] == pytest.approx(1.0)
    assert len(top_k(q, matrix, 10)) == 4
    assert top_k(q, matrix, 0) == []
    assert top_k(q, np.zeros((0, 2), dtype=np.float32), 3) == []


def test_get_embedder_falls_back_without_sentence_transformers(monkeypatch):
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    emb = get_embedder()
    assert isinstance(emb, HashingEmbedder)


def test_get_embedder_env_forces_hashing(monkeypatch):
    monkeypatch.setattr(embeddings, "SentenceTransformerEmbedder",
                        lambda *a, **k: pytest.fail("ST yüklenmemeliydi"))
    monkeypatch.setenv("FINSIGHT_EMBEDDER", "hashing")
    assert isinstance(get_embedder(), HashingEmbedder)


def test_sentence_transformer_embedder_with_fake_module(monkeypatch):
    class FakeModel:
        def __init__(self, name, device):
            self.name, self.device = name, device

        def get_sentence_embedding_dimension(self):
            return 3

        def encode(self, texts, **kwargs):
            assert kwargs["normalize_embeddings"] is True
            return np.array([[3.0, 4.0, 0.0]] * len(texts))

    class FakeModule:
        SentenceTransformer = FakeModel

    monkeypatch.setitem(sys.modules, "sentence_transformers", FakeModule)
    emb = get_embedder()
    assert isinstance(emb, SentenceTransformerEmbedder)
    assert emb.name == "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    assert emb.default_min_similarity == 0.30 and emb.dim == 3
    assert emb._model.device == "cpu"
    out = emb.embed(["a", "b"])
    assert out.dtype == np.float32 and np.allclose(out[0], [0.6, 0.8, 0.0])
    monkeypatch.setenv("FINSIGHT_EMBEDDING_MODEL", "custom/model")
    assert SentenceTransformerEmbedder().name == "custom/model"
