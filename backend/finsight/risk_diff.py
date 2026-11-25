"""İki dönemin risk faktörleri bölümünü paragraf düzeyinde karşılaştırma (FR-09).

Yöntem:
1. Her iki metin `split_paragraphs` ile paragraflara bölünür.
2. Paragraflar seyrek (sözlük tabanlı) TF-IDF vektörlerine çevrilir: küçük harf, noktalama
   temizliği, İngilizce durak kelime listesi; özellikler kelime + ardışık kelime ikilisi
   (2-gram); alt doğrusal tf (1 + log tf), düzgünleştirilmiş idf, L2 normalizasyon.
   Yoğun (n × |V|) matris kurulmaz: 400 + 400 paragrafta yoğun float32 matris ~290 MB
   tutuyordu; seyrek temsil yalnızca sıfır olmayan ağırlıkları saklar (birkaç MB).
3. Benzerlikler ters indeksle (terim → paragraflar) seyrek nokta çarpımıyla hesaplanır;
   yalnızca `MODIFIED_THRESHOLD` üstü çiftler tutulur. Açgözlü (greedy) eşleştirme: en yüksek
   benzerlikli çiftten başlanır, her paragraf en fazla bir kez eşleşir.

Eşikler ve gerekçeleri:
- `UNCHANGED_THRESHOLD = 0.90`: Aynı paragraf 1.0 verir. Yıl, tutar veya tek kelime
  değişikliği 60+ kelimelik bir paragrafta yalnızca birkaç unigram/bigram'ı etkiler ve
  benzerliği ~0.93–0.99 aralığında tutar; bu düzey "esasen aynı" kabul edilir.
- `MODIFIED_THRESHOLD = 0.50`: Bir cümlesi eklenmiş veya yeniden yazılmış ama aynı konuyu
  anlatan paragraflar bigram'ların önemli kısmını paylaşır (sentetik testlerde ~0.69–0.76).
  Yarısından fazlası değiştirilmiş paragraflar ~0.3'e iner; 0.50'nin altında paragraflar
  çoğunlukla yalnızca genel kelime dağarcığını paylaşır. Bunlara "değişti" demek yerine
  eskisini "çıkarıldı", yenisini "eklendi" saymak daha dürüsttür.
Eşikler sentetik örneklerle ayarlandı; gerçek 10-K çiftleriyle (ör. 10 şirket) elle kontrol
edilerek yeniden değerlendirilmelidir.
"""

from __future__ import annotations

import math
import re
from collections import Counter

import numpy as np

from .text_extract import split_paragraphs

UNCHANGED_THRESHOLD = 0.90
MODIFIED_THRESHOLD = 0.50

STOPWORDS = frozenset("""
a about above after again against all also am an and any are as at be because been before
being below between both but by can could did do does doing down during each few for from
further had has have having he her here hers herself him himself his how i if in into is it
its itself just me more most my myself nor of off on once only or other our ours ourselves out
over own same she should so some such than that the their theirs them themselves then there
these they this those through to too under until up very was we were what when where which
while who whom why will with would you your yours yourself yourselves us may might must shall
""".split())

_NON_WORD = re.compile(r"[^a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Küçük harf, noktalama temizliği, durak kelime ve tek karakterlik belirteç ayıklama."""
    tokens = _NON_WORD.sub(" ", text.lower().replace("’", "'").replace("'", "")).split()
    return [t for t in tokens if len(t) > 1 and t not in STOPWORDS]


def _features(text: str) -> Counter:
    toks = tokenize(text)
    feats = Counter(toks)
    feats.update(f"{a} {b}" for a, b in zip(toks, toks[1:]))
    return feats


def tfidf_vectors(texts: list[str]) -> list[dict[int, float]]:
    """Her metin için L2 normalize seyrek TF-IDF vektörü: {terim_indeksi: ağırlık}."""
    feats = [_features(t) for t in texts]
    vocab: dict[str, int] = {}
    df: Counter = Counter()
    for f in feats:
        for term in f:
            if term not in vocab:
                vocab[term] = len(vocab)
        df.update(f.keys())
    n = len(texts)
    idf = {term: math.log((1 + n) / (1 + df[term])) + 1.0 for term in vocab}
    vectors: list[dict[int, float]] = []
    for k in range(n):
        f, feats[k] = feats[k], Counter()  # tepe belleği düşürmek için işlenen sayaç bırakılır
        vec = {vocab[term]: (1.0 + math.log(c)) * idf[term] for term, c in f.items()}
        norm = math.sqrt(sum(w * w for w in vec.values()))
        if norm > 0:
            vec = {j: w / norm for j, w in vec.items()}
        vectors.append(vec)
    return vectors


def tfidf_matrix(texts: list[str]) -> np.ndarray:
    """Satırları L2 normalize TF-IDF vektörleri olan (n, |V|) float32 matris.

    Yalnızca küçük girdiler (testler, inceleme) içindir; karşılaştırma seyrek vektörleri
    (`tfidf_vectors`) kullanır.
    """
    vectors = tfidf_vectors(texts)
    width = max((max(v) + 1 for v in vectors if v), default=1)
    mat = np.zeros((len(texts), width), dtype=np.float32)
    for i, vec in enumerate(vectors):
        for j, w in vec.items():
            mat[i, j] = w
    return mat


def sparse_similarities(left: list[dict[int, float]], right: list[dict[int, float]],
                        min_similarity: float) -> dict[tuple[int, int], float]:
    """Seyrek kosinüs benzerlikleri (vektörler L2 normalize): yalnızca ≥ `min_similarity`
    çiftler, [0, 1] aralığına kırpılmış olarak döner.

    numpy ile vektörleştirilmiş: sol vektörler terime göre sıralı (CSC benzeri) dizilere
    konur; her sağ paragraf için ortak terimlerin sol girdileri tek seferde toplanır ve
    ``np.bincount`` ile paragraf başına iç çarpım hesaplanır. Aynı çarpımlar, yalnızca
    toplama sırası farklı (float64; fark ~1e-16). Saf Python sürümüne göre büyük risk
    bölümlerinde ~10x+ hızlı (docs/performans-asama4.md); yoğun n × |V| matris kurulmaz.
    """
    n_left = len(left)
    out: dict[tuple[int, int], float] = {}
    if not n_left or not right:
        return out
    terms_l = np.fromiter((t for vec in left for t in vec), dtype=np.int64)
    rows_l = np.repeat(np.arange(n_left, dtype=np.int64),
                       np.fromiter((len(vec) for vec in left), dtype=np.int64, count=n_left))
    weights_l = np.fromiter((w for vec in left for w in vec.values()), dtype=np.float64)
    if terms_l.size == 0:
        return out
    order = np.argsort(terms_l, kind="stable")
    terms_l, rows_l, weights_l = terms_l[order], rows_l[order], weights_l[order]
    width = int(terms_l[-1]) + 1
    starts = np.searchsorted(terms_l, np.arange(width), side="left")
    counts = np.searchsorted(terms_l, np.arange(width), side="right") - starts
    for j, vec in enumerate(right):
        if not vec:
            continue
        t = np.fromiter(vec.keys(), dtype=np.int64, count=len(vec))
        w = np.fromiter(vec.values(), dtype=np.float64, count=len(vec))
        keep = t < width
        t, w = t[keep], w[keep]
        lens = counts[t]
        nz = lens > 0
        t, w, lens = t[nz], w[nz], lens[nz]
        total = int(lens.sum())
        if total == 0:
            continue
        # Ortak terimlerin soldaki girdi konumları (ardışık aralıkların birleşimi).
        offsets = np.repeat(starts[t] - (np.cumsum(lens) - lens), lens) + np.arange(total)
        rows = rows_l[offsets]
        dots = np.bincount(rows, weights=weights_l[offsets] * np.repeat(w, lens),
                           minlength=n_left)
        touched = np.bincount(rows, minlength=n_left) > 0  # ortak terimi olan paragraflar
        for i in np.flatnonzero(touched & (dots >= min_similarity)):
            out[(int(i), j)] = min(1.0, max(0.0, float(dots[i])))
    return out


def sparse_similarities_reference(left: list[dict[int, float]], right: list[dict[int, float]],
                                  min_similarity: float) -> dict[tuple[int, int], float]:
    """Saf Python başvuru uygulaması (ters indeks + sözlük toplama). Testlerde numpy
    sürümünün doğruluğunu karşılaştırmak için tutulur."""
    index: dict[int, list[tuple[int, float]]] = {}
    for i, vec in enumerate(left):
        for term, w in vec.items():
            index.setdefault(term, []).append((i, w))
    out: dict[tuple[int, int], float] = {}
    for j, vec in enumerate(right):
        acc: dict[int, float] = {}
        for term, w in vec.items():
            for i, wi in index.get(term, ()):
                acc[i] = acc.get(i, 0.0) + w * wi
        for i, dot in acc.items():
            sim = min(1.0, max(0.0, dot))
            if sim >= min_similarity:
                out[(i, j)] = sim
    return out


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def compare_risk_sections(old_text: str, new_text: str) -> dict:
    """Eski ve yeni risk bölümlerini karşılaştırır.

    Dönüş: `{"counts": {"added", "removed", "modified", "unchanged"}, "changes": [...]}`;
    `changes` öğeleri `{"change_type", "old_text", "new_text", "similarity"}`. Sıra: önce
    added (yeni metindeki sırayla), sonra modified (benzerlik artan), sonra removed (eski
    metindeki sırayla). `unchanged` yalnızca sayılır, listelenmez.
    """
    old_paras = split_paragraphs(old_text or "")
    new_paras = split_paragraphs(new_text or "")
    n_old, n_new = len(old_paras), len(new_paras)

    pairs: list[tuple[float, int, int]] = []
    if n_old and n_new:
        vectors = tfidf_vectors(old_paras + new_paras)
        sims = sparse_similarities(vectors[:n_old], vectors[n_old:], MODIFIED_THRESHOLD)
        # Metni özdeş paragraflar (yalnızca durak kelimelerden oluşanlar dahil) 1.0 sayılır.
        new_index: dict[str, list[int]] = {}
        for j, p in enumerate(new_paras):
            new_index.setdefault(_normalized(p), []).append(j)
        for i, p in enumerate(old_paras):
            for j in new_index.get(_normalized(p), []):
                sims[(i, j)] = 1.0
        pairs = [(sim, i, j) for (i, j), sim in sims.items()]
    # Açgözlü eşleştirme: yüksek benzerlik önce; eşitlikte belge sırası korunur.
    pairs.sort(key=lambda p: (-p[0], p[1], p[2]))
    used_old: set[int] = set()
    used_new: set[int] = set()
    matches: list[tuple[float, int, int]] = []
    for s, i, j in pairs:
        if i in used_old or j in used_new:
            continue
        used_old.add(i)
        used_new.add(j)
        matches.append((s, i, j))

    unchanged = [m for m in matches if m[0] >= UNCHANGED_THRESHOLD]
    modified = sorted(
        (m for m in matches if m[0] < UNCHANGED_THRESHOLD), key=lambda m: (m[0], m[2])
    )
    added = [j for j in range(n_new) if j not in used_new]
    removed = [i for i in range(n_old) if i not in used_old]

    changes: list[dict] = []
    changes += [
        {"change_type": "added", "old_text": None, "new_text": new_paras[j], "similarity": None}
        for j in added
    ]
    changes += [
        {"change_type": "modified", "old_text": old_paras[i], "new_text": new_paras[j],
         "similarity": round(s, 3)}
        for s, i, j in modified
    ]
    changes += [
        {"change_type": "removed", "old_text": old_paras[i], "new_text": None, "similarity": None}
        for i in removed
    ]
    return {
        "counts": {
            "added": len(added),
            "removed": len(removed),
            "modified": len(modified),
            "unchanged": len(unchanged),
        },
        "changes": changes,
    }
