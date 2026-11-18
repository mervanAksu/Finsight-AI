"""Cümle düzeyinde finansal duygu (ton) analizi (FR-08).

- `FinBertModel`: ProsusAI/finbert (torch + transformers, CPU). İsteğe bağlı ağır bağımlılık;
  yalnızca kurucu içinde içe aktarılır. Uzunluğa göre sıralı dinamik dolgulu gruplar,
  inference_mode; isteğe bağlı int8 / kısa max_length (README "Performans").
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

import numpy as np

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

DEFAULT_FINBERT_BATCH = 32
DEFAULT_FINBERT_MAX_LENGTH = 512  # BERT-base üst sınırı; doğruluğu korumak için varsayılan
MIN_FINBERT_MAX_LENGTH = 16


def _env_int(name: str, default: int | None) -> int | None:
    """Pozitif tam sayı ortam değişkeni; boş/geçersiz ise varsayılan (uyarıyla)."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        log.warning("%s tam sayı olmalı (%r); varsayılan kullanılıyor.", name, raw)
        return default
    if value < 1:
        log.warning("%s pozitif olmalı (%r); varsayılan kullanılıyor.", name, raw)
        return default
    return value


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def normalize_sentence(sentence: str) -> str:
    """Önbellek anahtarı: boşluklar tek boşluğa indirgenir, baş/son boşluk atılır.

    BERT belirteçleyicisi metni boşluklardan böldüğü için boşluk farkı modele aynı girdiyi
    verir; harf, noktalama ve Unicode biçimi DEĞİŞTİRİLMEZ (aynı anahtar = aynı model girdisi).
    """
    return " ".join(sentence.split())


def finbert_variant_name(base: str, max_length: int, quantized: bool) -> str:
    """Model adı + doğruluğu etkileyebilecek ayarların izi.

    Varsayılan ayarlarda (512, kuantizasyonsuz) ad değişmez ("ProsusAI/finbert"). int8 veya
    kısaltılmış max_length sonuçları ayrı model adıyla kaydedilir; böylece cümle önbelleği ve
    "aynı modelle analiz edildi" denetimi farklı ayarların sonuçlarını karıştırmaz.
    """
    name = base
    if quantized:
        name += "+int8"
    if max_length != DEFAULT_FINBERT_MAX_LENGTH:
        name += f"+len{max_length}"
    return name


def finbert_max_length_from_env() -> int:
    value = _env_int("FINSIGHT_FINBERT_MAX_LENGTH", DEFAULT_FINBERT_MAX_LENGTH)
    assert value is not None
    return max(MIN_FINBERT_MAX_LENGTH, min(DEFAULT_FINBERT_MAX_LENGTH, value))


def configured_finbert_name(base: str = FINBERT_NAME) -> str:
    """Ortam değişkenlerine göre FinBERT'in kaydedileceği ad (modeli YÜKLEMEDEN, /health)."""
    return finbert_variant_name(base, finbert_max_length_from_env(),
                                _env_flag("FINSIGHT_FINBERT_QUANTIZE"))


def length_sorted_batches(lengths: Sequence[int], batch_size: int) -> list[list[int]]:
    """Cümle indekslerini belirteç uzunluğuna göre (uzundan kısaya, eşitlikte orijinal sıra)
    sıralayıp ``batch_size``'lık gruplara böler.

    Benzer uzunluktaki cümleler aynı gruba düştüğü için ``padding="longest"`` ile grup içi
    dolgu (padding) en aza iner. En uzun grup başta işlenir: bellek sorunu varsa hemen görülür.
    """
    if batch_size < 1:
        raise ValueError("batch_size en az 1 olmalı")
    order = sorted(range(len(lengths)), key=lambda i: (-int(lengths[i]), i))
    return [order[i:i + batch_size] for i in range(0, len(order), batch_size)]


def restore_order(batches: Sequence[Sequence[int]], batch_outputs: Sequence[Sequence],
                  n: int) -> list:
    """``length_sorted_batches`` gruplarının çıktılarını orijinal cümle sırasına yerleştirir."""
    if len(batches) != len(batch_outputs):
        raise ValueError("Grup ve çıktı sayıları eşit olmalı")
    out: list = [None] * n
    filled = [False] * n
    for indices, outputs in zip(batches, batch_outputs):
        if len(indices) != len(outputs):
            raise ValueError("Grup çıktısı grup boyutuyla eşleşmiyor")
        for i, value in zip(indices, outputs):
            if filled[i]:
                raise ValueError(f"İndeks iki kez dolduruldu: {i}")
            out[i] = value
            filled[i] = True
    if not all(filled):
        raise ValueError("Bazı cümlelerin tahmini eksik")
    return out


def _label_list(config) -> list[str]:
    """model.config.id2label → indeks sırasıyla proje etiketleri (bilinmeyen → neutral)."""
    id2label = dict(getattr(config, "id2label", None) or {})
    n = len(id2label) or int(getattr(config, "num_labels", 0) or 3)
    labels = []
    for i in range(n):
        raw = id2label.get(i, id2label.get(str(i), f"LABEL_{i}"))
        labels.append(_FINBERT_LABEL_MAP.get(str(raw).lower(), "neutral"))
    return labels


def _to_numpy(logits) -> np.ndarray:
    if hasattr(logits, "detach"):  # torch.Tensor
        logits = logits.detach().float().cpu().numpy()
    return np.asarray(logits, dtype=np.float64)


def _softmax_top(logits, labels: Sequence[str]) -> list[dict]:
    arr = _to_numpy(logits)
    if arr.ndim == 1:
        arr = arr[None, :]
    arr = arr - arr.max(axis=1, keepdims=True)
    probs = np.exp(arr)
    probs /= probs.sum(axis=1, keepdims=True)
    best = probs.argmax(axis=1)
    return [{"label": labels[b] if b < len(labels) else "neutral", "score": float(probs[k, b])}
            for k, b in enumerate(best)]


def _quantize_dynamic(torch, model):
    """nn.Linear katmanlarını int8 dinamik kuantize eder (CPU). Hata yükseltebilir."""
    ao = getattr(getattr(torch, "ao", None), "quantization", None)
    fn = getattr(ao, "quantize_dynamic", None)
    if fn is None:  # eski torch sürümleri
        fn = torch.quantization.quantize_dynamic
    return fn(model, {torch.nn.Linear}, dtype=torch.qint8)


class FinBertModel:
    """FinBERT (ProsusAI/finbert) — CPU, doğrudan AutoTokenizer + AutoModel ile.

    Hız iyileştirmeleri (doğruluğu değiştirmez, varsayılan açık):
    - ``torch.inference_mode()`` (otomatik türev kaydı yok),
    - cümleler belirteç uzunluğuna göre sıralanıp ``padding="longest"`` ile gruplanır
      (kısa cümleler 512'ye kadar doldurulmaz), sonuçlar orijinal sıraya geri yerleştirilir,
    - grup boyutu 32 (``FINSIGHT_FINBERT_BATCH``), iş parçacığı sayısı
      ``FINSIGHT_TORCH_THREADS`` (verilmezse torch varsayılanı).

    Doğruluğu etkileyebilecek ayarlar (varsayılan KAPALI; ``bench_sentiment`` ile ölçün):
    - ``FINSIGHT_FINBERT_MAX_LENGTH`` (varsayılan 512),
    - ``FINSIGHT_FINBERT_QUANTIZE=1``: nn.Linear int8 dinamik kuantizasyon; hata olursa
      uyarıyla normal modele düşülür.
    Bu ikisi değiştirilirse model adına "+int8" / "+len256" eklenir (bkz. finbert_variant_name).
    """

    name = FINBERT_NAME

    def __init__(self, model_name: str = FINBERT_NAME, batch_size: int | None = None, *,
                 max_length: int | None = None, quantize: bool | None = None,
                 threads: int | None = None, tokenizer=None, model=None) -> None:
        import torch  # ağır bağımlılık: lazy import

        self._torch = torch
        self.base_name = model_name
        self.batch_size = batch_size or _env_int("FINSIGHT_FINBERT_BATCH", DEFAULT_FINBERT_BATCH) \
            or DEFAULT_FINBERT_BATCH
        if max_length is None:
            max_length = finbert_max_length_from_env()
        self.max_length = max(MIN_FINBERT_MAX_LENGTH, min(DEFAULT_FINBERT_MAX_LENGTH,
                                                          int(max_length)))
        threads = threads if threads is not None else _env_int("FINSIGHT_TORCH_THREADS", None)
        if threads:
            torch.set_num_threads(int(threads))
        if tokenizer is None or model is None:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            if tokenizer is None:
                tokenizer = AutoTokenizer.from_pretrained(model_name)
            if model is None:
                model = AutoModelForSequenceClassification.from_pretrained(model_name)
        model.eval()
        self.quantized = False
        if quantize if quantize is not None else _env_flag("FINSIGHT_FINBERT_QUANTIZE"):
            try:
                model = _quantize_dynamic(torch, model)
                self.quantized = True
            except Exception as exc:  # desteklenmeyen platform / torch sürümü
                log.warning("FinBERT int8 kuantizasyonu yapılamadı (%s: %s); normal model "
                            "kullanılıyor.", type(exc).__name__, exc)
        self._tokenizer = tokenizer
        self._model = model
        self._labels = _label_list(getattr(model, "config", None))
        self.name = finbert_variant_name(model_name, self.max_length, self.quantized)

    def token_lengths(self, sentences: Sequence[str]) -> list[int]:
        """Kesme (truncation) sonrası özel belirteçler dahil uzunluklar."""
        enc = self._tokenizer(list(sentences), truncation=True, max_length=self.max_length)
        return [len(ids) for ids in enc["input_ids"]]

    def predict(self, sentences: Sequence[str]) -> list[dict]:
        if not sentences:
            return []
        texts = list(sentences)
        batches = length_sorted_batches(self.token_lengths(texts), self.batch_size)
        outputs = []
        with self._torch.inference_mode():
            for indices in batches:
                enc = self._tokenizer([texts[i] for i in indices], truncation=True,
                                      max_length=self.max_length, padding="longest",
                                      return_tensors="pt")
                outputs.append(_softmax_top(self._model(**enc).logits, self._labels))
        return restore_order(batches, outputs, len(texts))


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
NEGATION_WINDOW = 4
# Belirsizlik/güvence vermeme kalıpları ("there can be no assurance", "we cannot assure"):
# ifade olumsuz sayılır (−1) ve ardından gelen olumlu kelimeler varsayımsaldır (sayılmaz).
UNCERTAINTY_PHRASES: tuple[tuple[str, ...], ...] = (
    ("no", "assurance"), ("no", "assurances"), ("cannot", "assure"), ("can", "not", "assure"),
    ("can't", "assure"), ("cannot", "guarantee"), ("no", "guarantee"),
)
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

    Kurallar: olumlu kelime +1, olumsuz −1; önceki 4 kelimede "not/no/never" (ve
    "cannot", "...n't") varsa olumlu kelime −1 olur, olumsuz kelime ise nötrleşir
    ("did not experience any material disruption" olumlu sayılmaz). "no assurance",
    "cannot assure", "there can be no assurance" kalıpları −1'dir ve kalıptan sonraki olumlu
    kelimeler sayılmaz. net > 0 → positive, net < 0 → negative,
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
        phrase_tokens: set[int] = set()
        first_phrase: int | None = None
        for i in range(len(tokens)):
            for phrase in UNCERTAINTY_PHRASES:
                if tuple(tokens[i:i + len(phrase)]) == phrase:
                    phrase_tokens.update(range(i, i + len(phrase)))
                    if first_phrase is None or i < first_phrase:
                        first_phrase = i
                    break
        if first_phrase is not None:
            net -= 1
            hits += 1
        for i, tok in enumerate(tokens):
            if i in phrase_tokens:
                continue
            pol = self._polarity(tok)
            if not pol:
                continue
            if pol > 0 and first_phrase is not None and i > first_phrase:
                continue  # "no assurance that we will achieve profitability": varsayımsal
            window = [w for j, w in enumerate(tokens[max(0, i - NEGATION_WINDOW):i],
                                              start=max(0, i - NEGATION_WINDOW))
                      if j not in phrase_tokens]
            if any(_is_negation(w) for w in window):
                if pol < 0:
                    # "did not experience any material disruption": olumsuzlanmış olumsuz
                    # olumluya çevrilmez, nötrdür.
                    continue
                pol = -pol
            hits += 1
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
