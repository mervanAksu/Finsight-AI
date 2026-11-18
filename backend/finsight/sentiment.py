"""Cümle düzeyinde finansal duygu (ton) analizi (FR-08).

- `FinBertModel`: ProsusAI/finbert (transformers, CPU). İsteğe bağlı ağır bağımlılık;
  yalnızca kurucu içinde içe aktarılır.
- `LexiconModel`: transformers yoksa yedek. Finans odaklı küçük gömülü sözlük
  (Loughran-McDonald yaklaşımından esinlenen ama listesi bu projede yazılmış kısa bir
  sözlük). `LM_DICTIONARY_PATH` ortam değişkeni Loughran-McDonald Master Dictionary
  CSV'sini gösterirse o sözlük kullanılır.

Hangi modelin kullanıldığı `model.name` ile yanıtlara yazılır (CLAUDE.md: ağır ML
bağımlılıkları isteğe bağlıdır).
"""

from __future__ import annotations

import csv
import logging
import os
import re
from collections.abc import Sequence
from typing import Protocol

log = logging.getLogger(__name__)

FINBERT_NAME = "ProsusAI/finbert"
LEXICON_NAME = "lexicon-fallback"
LM_LEXICON_NAME = "loughran-mcdonald-lexicon"
LABELS = ("positive", "negative", "neutral")


class SentimentModel(Protocol):
    name: str

    def predict(self, sentences: Sequence[str]) -> list[dict]:
        """Her cümle için `{"label": "positive"|"negative"|"neutral", "score": float}`."""
        ...


# --------------------------------------------------------------------------- FinBERT

# ProsusAI/finbert config.json id2label: 0=positive, 1=negative, 2=neutral
_FINBERT_LABEL_MAP = {
    "positive": "positive", "negative": "negative", "neutral": "neutral",
    "label_0": "positive", "label_1": "negative", "label_2": "neutral",
}


class FinBertModel:
    """transformers `text-classification` hattı ile FinBERT (CPU)."""

    name = FINBERT_NAME

    def __init__(self, model_name: str = FINBERT_NAME, batch_size: int = 16) -> None:
        from transformers import pipeline  # ağır bağımlılık: lazy import

        self.name = model_name
        self.batch_size = batch_size
        self._pipe = pipeline(
            "text-classification", model=model_name, tokenizer=model_name, device=-1
        )

    def predict(self, sentences: Sequence[str]) -> list[dict]:
        if not sentences:
            return []
        raw = self._pipe(
            list(sentences), batch_size=self.batch_size, truncation=True, max_length=512
        )
        out = []
        for r in raw:
            if isinstance(r, list):  # bazı sürümler top_k ile liste döndürür
                r = max(r, key=lambda x: x["score"])
            label = _FINBERT_LABEL_MAP.get(str(r["label"]).lower(), "neutral")
            out.append({"label": label, "score": float(r["score"])})
        return out


# --------------------------------------------------------------------------- sözlük modeli

# Projeye özgü kısa finans sözlüğü (Loughran-McDonald fikrinden esinlenilmiştir, listesi
# kopyalanmamıştır). Belirsiz kelimeler ("increase", "record", "outstanding", "risk")
# bilinçli olarak dışarıda bırakıldı: finans metninde iki yöne de kullanılırlar.
POSITIVE_WORDS = frozenset("""
accelerate accelerated achieve achieved achievement advance advantage advantageous attractive
beneficial benefit benefited best boost breakthrough confident durable efficiency efficient
enhance enhanced excellent exceed exceeded expand expanded expansion favorable favorably gain
gained growth grew healthy improve improved improvement innovative innovation momentum
optimistic outperform outperformed profitable profitability progress rebound recovered recovery
resilient resilience robust solid stability stable strength strengthen strengthened strong
stronger strongest succeed success successful successfully superior surpass surpassed tailwind
upturn upgrade upgraded
""".split())

NEGATIVE_WORDS = frozenset("""
adverse adversely bankruptcy breach challenging closure decline declined declining decrease
decreased default deficit deficiency delay delayed deteriorate deteriorated deterioration
difficult difficulty disruption disrupt disrupted downgrade downgraded downturn fail failed
failure fine fraud harm harmed headwind impair impaired impairment inability investigation
lawsuit layoff litigation loss lost misconduct misstatement negative negatively penalty
problem recession restatement restructuring shortage shortfall slowdown terminate terminated
termination unable unfavorable unfavorably violation weak weaken weakened weaker weakness worse
worsen worsened writedown
""".split())

NEGATIONS = frozenset({"not", "no", "never", "cannot", "neither", "nor", "without"})
NEGATION_WINDOW = 3
_TOKEN = re.compile(r"[a-z]+(?:'[a-z]+)?")
_SUFFIXES = ("ies", "es", "s", "ed", "ing", "ly")


def _is_negation(token: str) -> bool:
    return token in NEGATIONS or token.endswith("n't")


def load_lm_dictionary(path: str) -> tuple[frozenset[str], frozenset[str]]:
    """Loughran-McDonald Master Dictionary CSV'sinden (Word, Negative, Positive) listeleri.

    LM sözlüğünde sütun değeri kelimenin eklendiği yıldır; >0 ise kelime o kategoridedir
    (negatif değerler sözlükten çıkarılmış kelimeleri gösterir).
    """
    pos: set[str] = set()
    neg: set[str] = set()

    def as_number(value: str | None) -> float:
        try:
            return float(value or 0)
        except ValueError:
            return 0.0

    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            word = (row.get("Word") or "").strip().lower()
            if not word:
                continue
            if as_number(row.get("Positive")) > 0:
                pos.add(word)
            if as_number(row.get("Negative")) > 0:
                neg.add(word)
    if not pos and not neg:
        raise ValueError("LM sözlüğünde olumlu/olumsuz kelime bulunamadı")
    return frozenset(pos), frozenset(neg)


class LexiconModel:
    """Sözlük tabanlı yedek duygu modeli.

    Kurallar: olumlu kelime +1, olumsuz −1; önceki 3 kelimede "not/no/never" (ve
    "cannot", "...n't") varsa işaret çevrilir. net > 0 → positive, net < 0 → negative,
    aksi neutral. Güven: polar etiketlerde `0.5 + 0.5·|net|/(eşleşme+1)` (tek eşleşme 0.75,
    eşleşme arttıkça ve tutarlılık arttıkça 1'e yaklaşır); neutral'da eşleşme yoksa 0.75,
    eşleşmeler birbirini götürüyorsa 0.5.
    """

    def __init__(self, dictionary_path: str | None = None) -> None:
        path = dictionary_path if dictionary_path is not None else os.getenv("LM_DICTIONARY_PATH")
        self.positive: frozenset[str] = POSITIVE_WORDS
        self.negative: frozenset[str] = NEGATIVE_WORDS
        self.name = LEXICON_NAME
        self._stem_fallback = True
        if path:
            try:
                self.positive, self.negative = load_lm_dictionary(path)
                self.name = LM_LEXICON_NAME
                self._stem_fallback = False  # LM sözlüğü tüm çekimli biçimleri içerir
            except (OSError, ValueError, csv.Error) as exc:
                log.warning("LM sözlüğü okunamadı (%s); gömülü sözlük kullanılıyor.", exc)

    def _polarity(self, token: str) -> int:
        if token in self.positive:
            return 1
        if token in self.negative:
            return -1
        if self._stem_fallback:
            for suf in _SUFFIXES:
                if token.endswith(suf) and len(token) - len(suf) >= 3:
                    base = token[: -len(suf)]
                    for cand in (base, base + "e", base + "y"):
                        if cand in self.positive:
                            return 1
                        if cand in self.negative:
                            return -1
        return 0

    def score_sentence(self, sentence: str) -> dict:
        tokens = _TOKEN.findall(sentence.lower().replace("’", "'"))
        net = 0
        hits = 0
        for i, tok in enumerate(tokens):
            pol = self._polarity(tok)
            if not pol:
                continue
            hits += 1
            window = tokens[max(0, i - NEGATION_WINDOW):i]
            if any(_is_negation(w) for w in window):
                pol = -pol
            net += pol
        if net > 0:
            return {"label": "positive", "score": 0.5 + 0.5 * net / (hits + 1)}
        if net < 0:
            return {"label": "negative", "score": 0.5 + 0.5 * (-net) / (hits + 1)}
        return {"label": "neutral", "score": 0.75 if hits == 0 else 0.5}

    def predict(self, sentences: Sequence[str]) -> list[dict]:
        return [self.score_sentence(s) for s in sentences]


# --------------------------------------------------------------------------- seçim ve özet


def get_sentiment_model(prefer: str = "finbert") -> SentimentModel:
    """FinBERT yüklenebiliyorsa onu, değilse sözlük modelini döndürür.

    `FINSIGHT_SENTIMENT_MODEL=lexicon` sözlük modelini zorlar.
    """
    forced = os.getenv("FINSIGHT_SENTIMENT_MODEL", "").strip().lower()
    if forced == "lexicon" or prefer == "lexicon":
        return LexiconModel()
    try:
        return FinBertModel()
    except Exception as exc:  # ImportError, model indirme/yükleme hataları
        log.warning("FinBERT yüklenemedi (%s); sözlük modeline düşülüyor.", type(exc).__name__)
        return LexiconModel()


def summarize(sentences: Sequence[str], predictions: Sequence[dict], top_n: int = 5) -> dict:
    """Bölüm özeti: API `/sentiment` bölüm alanları.

    `net_tone = (positive − negative) / sentence_count` (cümle yoksa None);
    `top_negative` / `top_positive`: en yüksek güvenli `top_n` cümle.
    """
    if len(sentences) != len(predictions):
        raise ValueError("Cümle ve tahmin sayıları eşit olmalı")
    counts = {label: 0 for label in LABELS}
    for p in predictions:
        counts[p["label"] if p["label"] in counts else "neutral"] += 1
    n = len(sentences)

    def top(label: str) -> list[dict]:
        items = [(float(p["score"]), i) for i, p in enumerate(predictions) if p["label"] == label]
        items.sort(key=lambda x: (-x[0], x[1]))
        return [{"sentence": sentences[i], "score": round(s, 3)} for s, i in items[:top_n]]

    return {
        "sentence_count": n,
        "positive": counts["positive"],
        "negative": counts["negative"],
        "neutral": counts["neutral"],
        "net_tone": round((counts["positive"] - counts["negative"]) / n, 3) if n else None,
        "top_negative": top("negative"),
        "top_positive": top("positive"),
    }
