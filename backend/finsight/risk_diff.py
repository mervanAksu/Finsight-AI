"""İki dönemin risk faktörleri bölümünü paragraf düzeyinde karşılaştırma (FR-09).

Yöntem:
1. Her iki metin `split_paragraphs` ile paragraflara bölünür.
2. Paragraflar numpy ile hesaplanan TF-IDF vektörlerine çevrilir: küçük harf, noktalama
   temizliği, İngilizce durak kelime listesi; özellikler kelime + ardışık kelime ikilisi
   (2-gram); alt doğrusal tf (1 + log tf), düzgünleştirilmiş idf, L2 normalizasyon.
3. Kosinüs benzerlik matrisi üzerinde açgözlü (greedy) eşleştirme: en yüksek benzerlikli
   çiftten başlanır, her paragraf en fazla bir kez eşleşir.

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


def tfidf_matrix(texts: list[str]) -> np.ndarray:
    """Satırları L2 normalize TF-IDF vektörleri olan (n, |V|) float32 matris."""
    feats = [_features(t) for t in texts]
    vocab: dict[str, int] = {}
    df: Counter = Counter()
    for f in feats:
        for term in f:
            if term not in vocab:
                vocab[term] = len(vocab)
        df.update(f.keys())
    n = len(texts)
    mat = np.zeros((n, max(len(vocab), 1)), dtype=np.float32)
    if not vocab:
        return mat
    idf = np.zeros(len(vocab), dtype=np.float32)
    for term, j in vocab.items():
        idf[j] = math.log((1 + n) / (1 + df[term])) + 1.0
    for i, f in enumerate(feats):
        for term, c in f.items():
            j = vocab[term]
            mat[i, j] = (1.0 + math.log(c)) * idf[j]
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


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
        mat = tfidf_matrix(old_paras + new_paras)
        sims = np.clip(mat[:n_old] @ mat[n_old:].T, 0.0, 1.0)
        # Metni özdeş paragraflar (yalnızca durak kelimelerden oluşanlar dahil) 1.0 sayılır.
        new_index: dict[str, list[int]] = {}
        for j, p in enumerate(new_paras):
            new_index.setdefault(_normalized(p), []).append(j)
        for i, p in enumerate(old_paras):
            for j in new_index.get(_normalized(p), []):
                sims[i, j] = 1.0
        for i, j in np.argwhere(sims >= MODIFIED_THRESHOLD):
            pairs.append((float(sims[i, j]), int(i), int(j)))
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
