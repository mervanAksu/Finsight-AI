"""Metin gömme (embedding) katmanı: RAG soru-cevap için (FR-10).

- `SentenceTransformerEmbedder`: varsayılan model
  `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`. Çok dilli seçildi çünkü
  kullanıcı soruyu Türkçe soracak, raporlar İngilizce; çok dilli model iki dili aynı
  vektör uzayına yerleştirir. `FINSIGHT_EMBEDDING_MODEL` ile değiştirilebilir.
- `HashingEmbedder`: sentence-transformers yoksa yedek. Kelime + kelime-2gram (ağırlık 0.25)
  özellik hashing'i (hashlib.blake2b; Python `hash()` KULLANILMAZ, çünkü PYTHONHASHSEED'e bağlıdır
  ve veritabanındaki vektörler süreçten sürece değişirdi), işaretli hashing, alt doğrusal tf,
  L2 normalizasyon. Diller arası eşleştirme yapamaz; bu yüzden RAG katmanı LLM varsa
  Türkçe soruyu önce İngilizce arama sorgusuna çevirir.

Tüm `embed` çıktıları float32 ve satır bazında L2 normalizedir; kosinüs = iç çarpım.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
from collections import Counter
from collections.abc import Sequence
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)

DEFAULT_ST_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
HASHING_NAME = "hashing-fallback"

# Varsayılan benzerlik eşikleri (RAG'de bunun altındaki parçalar "ilgisiz" sayılır).
# - ST (MiniLM, normalize): ilgisiz cümle çiftleri tipik olarak 0.0–0.25, ilgili soru/pasaj
#   çiftleri 0.35+; 0.30 ihtiyatlı bir alt sınırdır (gerçek veride doğrulanmalı).
# - Hashing (dim=4096, işaretli): ilgisiz metinlerde benzerlik yalnızca işaretli hash
#   çakışmalarından gelir; standart sapması ≈ 1/sqrt(dim) = 0.016. Ölçüm (6 kelimelik sorgu ×
#   150 kelimelik ilgisiz parça, 160 bin çift): ≥ 0.08 oranı %0.04, ≥ 0.10 oranı %0.005.
#   İlgili parçalar (kestrel fixture, 100–150 kelime) 0.145–0.26; ~130 kelimelik parçada tek
#   konu cümlesi + 2 ortak kelimeli sorgu %94 oranında ≥ 0.08 (0.12'de %7). Bu yüzden 0.08
#   (≈ 5σ); RAG ayrıca en az bir ortak kelime şartı arar. Sinyal parça uzunluğunun karekökü
#   ile düştüğü için ~350 kelimelik parçalarda geri çağırma azalır.
#   Neden 1024 değil: ~250 özellikli parçada bir kovanın başka bir özellikle çakışma olasılığı
#   ~%22 idi; ters işaretli çakışma ilgili kelimenin katkısını sıfırlayabiliyordu (ör.
#   "Which suppliers provide semiconductors and magnets?" ↔ tedarikçi parçası 0.017). 4096'da
#   ~%6. İşaretsiz hashing denendi: çakışma sıfırlamaz ama gürültü ortalaması pozitif kayıyor
#   (4096'da en yüksek gürültü 0.17), bu yüzden işaretli tutuldu.
ST_MIN_SIMILARITY = 0.30
HASHING_MIN_SIMILARITY = 0.08
HASHING_DIM = 4096
# Kısa anahtar kelime sorgularında bigram'lar nadiren eşleşir ama parça vektörünün normunu
# büyütür; düşük ağırlık (0.25) ifade eşleşmesine küçük bir bonus verip sinyali korur.
BIGRAM_WEIGHT = 0.25


class Embedder(Protocol):
    name: str
    dim: int
    default_min_similarity: float

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """(len(texts), dim) float32, satırlar L2 normalize."""
        ...


def _l2_normalize(mat: np.ndarray) -> np.ndarray:
    mat = np.asarray(mat, dtype=np.float32)
    norms = np.linalg.norm(mat, axis=-1, keepdims=True)
    norms[norms == 0] = 1.0
    return (mat / norms).astype(np.float32)


class SentenceTransformerEmbedder:
    default_min_similarity = ST_MIN_SIMILARITY

    def __init__(self, model_name: str | None = None, batch_size: int = 32) -> None:
        from sentence_transformers import SentenceTransformer  # ağır bağımlılık: lazy import

        self.name = model_name or os.getenv("FINSIGHT_EMBEDDING_MODEL") or DEFAULT_ST_MODEL
        self.batch_size = batch_size
        self._model = SentenceTransformer(self.name, device="cpu")
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        vecs = self._model.encode(
            list(texts), batch_size=self.batch_size, normalize_embeddings=True,
            convert_to_numpy=True, show_progress_bar=False,
        )
        return _l2_normalize(vecs)


# Hashing için küçük İngilizce + Türkçe durak kelime listesi. Soru kalıplarındaki dolgu
# kelimeleri ("how much", "tell me", "company") de çıkarılır: neredeyse her parçada geçen
# veya hiç geçmeyen bu kelimeler kısa sorgunun normunu büyütüp sinyali zayıflatır.
HASHING_STOPWORDS = frozenset("""
a an and are as at be been by can could do does did for from had has have how i if in into is
it its may might of on or our such that the their them there these they this those to was we
were what when where which who whom why will with would you your not no also than then so any
all about much many me tell please describe explain give list show company companies
ve ile bir bu şu da de mi mı mu mü ne nasıl neden hangi için gibi olan olarak daha çok en
""".split())
_WORD = re.compile(r"\w+", re.UNICODE)


def _norm_token(tok: str) -> str:
    # Basit İngilizce çoğul indirgeme: "risks"→"risk", "liabilities"→"liability".
    if len(tok) > 4 and tok.endswith("ies"):
        return tok[:-3] + "y"
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith(("ss", "us", "is")):
        return tok[:-1]
    return tok


def hashing_tokens(text: str) -> list[str]:
    toks = [t for t in _WORD.findall(text.lower()) if not t.isdigit() or len(t) == 4]
    return [_norm_token(t) for t in toks if len(t) > 1 and t not in HASHING_STOPWORDS]


class HashingEmbedder:
    """Deterministik özellik hashing'i ile gömme (yedek)."""

    name = HASHING_NAME
    default_min_similarity = HASHING_MIN_SIMILARITY

    def __init__(self, dim: int = HASHING_DIM) -> None:
        if dim <= 0:
            raise ValueError("dim pozitif olmalı")
        self.dim = dim

    def _bucket(self, feature: str) -> tuple[int, float]:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        h = int.from_bytes(digest, "little")
        sign = 1.0 if (h >> 63) & 1 else -1.0
        return h % self.dim, sign

    def _vector(self, text: str) -> np.ndarray:
        toks = hashing_tokens(text)
        vec = np.zeros(self.dim, dtype=np.float32)
        unigrams = Counter(toks)
        bigrams = Counter(f"{a} {b}" for a, b in zip(toks, toks[1:]))
        for feats, weight in ((unigrams, 1.0), (bigrams, BIGRAM_WEIGHT)):
            for feat, count in feats.items():
                idx, sign = self._bucket(feat)
                vec[idx] += sign * weight * (1.0 + math.log(count))  # alt doğrusal tf
        return vec

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return _l2_normalize(np.vstack([self._vector(t) for t in texts]))


def get_embedder() -> Embedder:
    """sentence-transformers kullanılabiliyorsa ST, değilse hashing gömücüsü.

    `FINSIGHT_EMBEDDER=hashing` hashing gömücüsünü zorlar.
    """
    if os.getenv("FINSIGHT_EMBEDDER", "").strip().lower() == "hashing":
        return HashingEmbedder()
    try:
        return SentenceTransformerEmbedder()
    except Exception as exc:  # ImportError, model indirme/yükleme hataları
        log.warning("sentence-transformers yüklenemedi (%s); hashing gömücüsü kullanılıyor.",
                    type(exc).__name__)
        return HashingEmbedder()


def to_bytes(vec: np.ndarray) -> bytes:
    """Vektörü float32 ham baytlara çevirir (veritabanında saklamak için)."""
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_bytes(b: bytes, dim: int) -> np.ndarray:
    vec = np.frombuffer(b, dtype=np.float32)
    if vec.size != dim:
        raise ValueError(f"Beklenen boyut {dim}, okunan {vec.size}")
    return vec.copy()


def top_k(query: np.ndarray, matrix: np.ndarray, k: int) -> list[tuple[int, float]]:
    """Kosinüs benzerliğine göre en iyi `k` satır: `[(indeks, benzerlik)]`, azalan sırada.

    Girdilerin L2 normalize olduğu varsayılır (kosinüs = iç çarpım).
    """
    matrix = np.asarray(matrix, dtype=np.float32)
    if k <= 0 or matrix.size == 0:
        return []
    scores = matrix @ np.asarray(query, dtype=np.float32).reshape(-1)
    k = min(k, scores.shape[0])
    # Kararlı sıralama: eşit benzerlikte küçük indeks önce.
    order = np.lexsort((np.arange(scores.shape[0]), -scores))[:k]
    return [(int(i), float(scores[i])) for i in order]
