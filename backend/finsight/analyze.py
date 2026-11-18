"""Başvuru metinlerinden bölüm, cümle duygusu, parça ve gömme üreten SAF analiz katmanı.

FR-07 (risk bölümü), FR-08 (duygu), FR-09 (risk farkı), FR-10 (soru-cevap için parçalar),
FR-11 (skorun metin sinyalleri). Veritabanı ve ağ erişimi yoktur; ``jobs.py`` bu fonksiyonları
çağırır ve sonuçları ``repository.py`` ile yazar. Hesap kuralları: docs/entegrasyon-plani.md.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, MutableMapping, Sequence
from datetime import date, datetime

import numpy as np

from .embeddings import HASHING_NAME, Embedder
from .ratios import compute_ratios
from .risk_diff import compare_risk_sections
from .scoring import compute_score
from .sentiment import SentimentModel, normalize_sentence, summarize
from .text_extract import chunk_text, content_sentences, extract_sections, html_to_text

# NLP ölçümü: hashing gömücüsünde benzerlik sinyali parça uzunluğunun kareköküyle düşer;
# ~150 kelimelik parçalar geri çağırmayı korur. Anlamsal (ST) modellerde 350 kelime yeterli.
HASHING_CHUNK_WORDS = 150
DEFAULT_CHUNK_WORDS = 350
CHUNK_OVERLAP_WORDS = 40

ANNUAL_FORM = "10-K"
QUARTERLY_FORM = "10-Q"
RISK_CODE = "1A"
MDNA_CODES = ("7", "2")  # 10-K: Item 7, 10-Q: Item 2


# --------------------------------------------------------------------------- yardımcılar


def _as_date(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _base_form(form: str | None) -> str:
    form = (form or "").strip().upper()
    return form[:-2] if form.endswith("/A") else form


def _is_amendment(filing: dict) -> bool:
    if "is_amendment" in filing and filing["is_amendment"] is not None:
        return bool(filing["is_amendment"])
    return str(filing.get("form") or "").upper().endswith("/A")


def _sort_key(filing: dict) -> tuple:
    return (_as_date(filing.get("filing_date")) or date.min,
            _as_date(filing.get("report_date")) or date.min,
            str(filing.get("accession_no") or ""))


# --------------------------------------------------------------------------- başvuru seçimi


def _pick(filings: Iterable[dict], base: str, count: int) -> list[dict]:
    """Bir form türünden en son `count` başvuru; değişiklik başvurusu (…/A) yalnızca aynı
    dönem (report_date) için orijinal başvuru yoksa aday olur."""
    group = [f for f in filings if _base_form(f.get("form")) == base]
    original_periods = {_as_date(f.get("report_date")) for f in group if not _is_amendment(f)}
    candidates = []
    for f in group:
        if "primary_document" in f and not f["primary_document"]:
            continue  # belge adı olmayan başvuru indirilemez
        if _is_amendment(f) and _as_date(f.get("report_date")) in original_periods:
            continue
        candidates.append(f)
    candidates.sort(key=_sort_key, reverse=True)
    return candidates[:count]


def select_filings_for_analysis(filings: list[dict], *,
                                include_quarterly: bool = True) -> list[dict]:
    """Analiz edilecek başvurular: en son iki 10-K + en son 10-Q (eskiden yeniye).

    - ``include_quarterly=False`` ("hızlı mod", ``FINSIGHT_ANALYZE_10Q=0``): 10-Q seçilmez;
      başvuru listesinde görünür ama metni indirilip analiz edilmez.
    - Orijinal başvuru tercih edilir; ``10-K/A`` / ``10-Q/A`` yalnızca aynı dönem için orijinal
      yoksa seçilir (değişiklik başvuruları çoğu zaman yalnızca Part III içerir).
    - ``primary_document`` alanı olup boş olan başvurular atlanır.
    """
    selected = _pick(filings, ANNUAL_FORM, 2)
    if include_quarterly:
        selected += _pick(filings, QUARTERLY_FORM, 1)
    return sorted(selected, key=_sort_key)


def latest_two_annual(filings: list[dict]) -> tuple[dict | None, dict | None]:
    """Seçilen başvurulardan risk karşılaştırması için (eski 10-K, yeni 10-K)."""
    annual = sorted((f for f in filings if _base_form(f.get("form")) == ANNUAL_FORM),
                    key=_sort_key)
    if len(annual) < 2:
        return None, (annual[-1] if annual else None)
    return annual[-2], annual[-1]


# --------------------------------------------------------------------------- belge analizi


def default_chunk_words(embedder: Embedder) -> int:
    """Gömücüye göre parça uzunluğu: hashing-fallback 150, diğerleri 350 kelime."""
    return HASHING_CHUNK_WORDS if getattr(embedder, "name", "") == HASHING_NAME \
        else DEFAULT_CHUNK_WORDS


PredictionCache = MutableMapping[str, dict]


def _add(stats: dict | None, key: str, value: float) -> None:
    if stats is not None:
        stats[key] = stats.get(key, 0) + value


def predict_with_cache(model: SentimentModel, sentences: Sequence[str], *,
                       cache: PredictionCache | None = None,
                       stats: dict | None = None) -> list[dict]:
    """Cümle duygu tahmini; yinelenen ve önceden tahmin edilmiş cümleler modele gitmez.

    - Anahtar ``normalize_sentence`` (yalnızca boşluk normalizasyonu): aynı anahtar modele
      aynı girdiyi verir, dolayısıyla sonucu değiştirmez.
    - ``cache`` (anahtar → {label, score}) verilirse önce ona bakılır ve yeni tahminler ona
      yazılır (aynı şirketin önceki başvuruları / aynı çalıştırmadaki önceki başvurular).
      Önbellek tek bir duygu modeline aittir; çağıran farklı modellerin sonuçlarını karıştırmaz.
    - Bir çağrıdaki yinelenen cümleler her durumda bir kez tahmin edilir.
    - Modele tek toplu çağrı gider (sıralı gruplama modelin içinde yapılır).

    ``stats`` sayaçları: sentences, predicted, cache_hits, duplicates, sentiment_s.
    """
    started = time.perf_counter()
    known: PredictionCache = cache if cache is not None else {}
    keys = [normalize_sentence(s) for s in sentences]
    todo_keys: list[str] = []
    todo_texts: list[str] = []
    seen: set[str] = set()
    hits = duplicates = 0
    for key, sentence in zip(keys, sentences):
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        if key in known:
            hits += 1
        else:
            todo_keys.append(key)
            todo_texts.append(sentence)
    if todo_texts:
        predictions = model.predict(todo_texts)
        if len(predictions) != len(todo_texts):
            raise ValueError("Duygu modeli cümle sayısı kadar tahmin döndürmedi")
        for key, p in zip(todo_keys, predictions):
            known[key] = {"label": p["label"], "score": float(p["score"])}
    _add(stats, "sentences", len(keys))
    _add(stats, "predicted", len(todo_texts))
    _add(stats, "cache_hits", hits)
    _add(stats, "duplicates", duplicates)
    _add(stats, "sentiment_s", time.perf_counter() - started)
    return [dict(known[k]) for k in keys]


# Adım süreleri (sn) — ingest CLI özetinde bu sırayla ve bu adlarla yazılır.
TIMING_STEPS: tuple[tuple[str, str], ...] = (
    ("model_load", "Model yükleme"),
    ("download", "Belge indirme"),
    ("html_to_text", "HTML → metin"),
    ("sections", "Bölüm ayıklama"),
    ("cache_load", "Cümle önbelleği yükleme"),
    ("sentiment", "Duygu analizi"),
    ("embedding", "Gömme"),
    ("save", "Veritabanına yazma"),
    ("risk", "Risk karşılaştırması"),
    ("score", "Skor"),
)


def _rate(count: float, seconds: float) -> float | None:
    return round(count / seconds, 2) if seconds > 0 and count else None


def sentiment_stats(stats: dict, *, cache_enabled: bool, cache_preloaded: int = 0) -> dict:
    """analyze.predict_with_cache sayaçlarından duygu özeti (cümle/sn dahil)."""
    seconds = float(stats.get("sentiment_s", 0.0))
    sentences = int(stats.get("sentences", 0))
    predicted = int(stats.get("predicted", 0))
    return {
        "sentences": sentences,
        "predicted": predicted,
        "cache_hits": int(stats.get("cache_hits", 0)),
        "duplicates": int(stats.get("duplicates", 0)),
        "seconds": round(seconds, 3),
        # Etkin hız: tüm cümleler / süre; model hızı: modele giden cümleler / süre.
        "sentences_per_second": _rate(sentences, seconds),
        "model_sentences_per_second": _rate(predicted, seconds),
        "cache_enabled": cache_enabled,
        "cache_preloaded": cache_preloaded,
    }


def analyze_sections(html: bytes | str, form: str, *, sentiment_model: SentimentModel,
                     prediction_cache: PredictionCache | None = None,
                     stats: dict | None = None) -> list[dict]:
    """HTML → bölümler (belge sırasıyla) + cümle duyguları + özet. Gömme yapılmaz.

    Duygu tahmini belge başına tek toplu (batch) çağrıyla yapılır; yinelenen cümleler ve
    ``prediction_cache``'te bulunanlar modele gönderilmez (bkz. predict_with_cache).
    ``stats`` verilirse adım süreleri (html_to_text_s, sections_s, sentiment_s) ve duygu
    sayaçları eklenir.
    """
    t0 = time.perf_counter()
    text = html_to_text(html)
    t1 = time.perf_counter()
    _add(stats, "html_to_text_s", t1 - t0)
    sections = sorted(extract_sections(text, form).values(), key=lambda s: s["char_start"])
    # Alt başlıklar ("Risks Related to Our Business") duygu cümlesi sayılmaz (R2).
    per_section = [content_sentences(sec["text"]) for sec in sections]
    _add(stats, "sections_s", time.perf_counter() - t1)
    flat = [s for sentences in per_section for s in sentences]
    flat_predictions = predict_with_cache(sentiment_model, flat, cache=prediction_cache,
                                          stats=stats) if flat else []
    out = []
    offset = 0
    for sec, sentences in zip(sections, per_section):
        predictions = flat_predictions[offset:offset + len(sentences)]
        offset += len(sentences)
        out.append({
            "item_code": sec["item_code"],
            "title": sec["title"],
            "text": sec["text"],
            "char_start": int(sec["char_start"]),
            "char_end": int(sec["char_end"]),
            "word_count": len(sec["text"].split()),
            "sentences": [
                {"index": i, "sentence": s, "label": p["label"], "score": float(p["score"])}
                for i, (s, p) in enumerate(zip(sentences, predictions))
            ],
            "summary": summarize(sentences, predictions),
        })
    return out


def embed_sections(sections: list[dict], *, embedder: Embedder,
                   chunk_words: int | None = None) -> list[dict]:
    """Her bölüme ``chunks`` ekler: [{index, text, word_count, embedding}] (yerinde ve dönüş).

    Gömme bölüm başına tek toplu çağrıyla yapılır; vektörler float32.
    """
    words = chunk_words or default_chunk_words(embedder)
    overlap = CHUNK_OVERLAP_WORDS if CHUNK_OVERLAP_WORDS < words else max(0, words // 4)
    for sec in sections:
        pieces = chunk_text(sec["text"], target_words=words, overlap_words=overlap)
        vectors = embedder.embed(pieces) if pieces else np.zeros((0, embedder.dim), np.float32)
        sec["chunks"] = [
            {"index": i, "text": t, "word_count": len(t.split()),
             "embedding": np.asarray(vectors[i], dtype=np.float32)}
            for i, t in enumerate(pieces)
        ]
    return sections


def analyze_document(html: bytes | str, form: str, *, sentiment_model: SentimentModel,
                     embedder: Embedder, chunk_words: int | None = None,
                     prediction_cache: PredictionCache | None = None) -> dict:
    """Bir başvuru belgesinin tam analizi.

    Dönüş: ``{"sections": [{item_code, title, text, char_start, char_end, word_count,
    sentences: [{index, sentence, label, score}], summary, chunks: [{index, text, word_count,
    embedding}]}], "sentiment_model", "embedding_model", "embedding_dim", "chunk_words"}``.
    """
    words = chunk_words or default_chunk_words(embedder)
    sections = analyze_sections(html, form, sentiment_model=sentiment_model,
                                prediction_cache=prediction_cache)
    embed_sections(sections, embedder=embedder, chunk_words=words)
    return {
        "sections": sections,
        "sentiment_model": sentiment_model.name,
        "embedding_model": embedder.name,
        "embedding_dim": int(embedder.dim),
        "chunk_words": words,
    }


# --------------------------------------------------------------------------- risk ve sinyaller


def compare_risk(old_text: str | None, new_text: str | None) -> dict | None:
    """İki 10-K'nın 1A metinleri varsa ``compare_risk_sections`` sonucu, yoksa None."""
    if not old_text or not new_text:
        return None
    return compare_risk_sections(old_text, new_text)


def _net_tone(sections, code: str) -> float | None:
    if not sections:
        return None
    if isinstance(sections, dict):
        entry = sections.get(code)
    else:
        entry = next((s for s in sections if s.get("item_code") == code), None)
    if entry is None:
        return None
    summary = entry.get("summary", entry) if isinstance(entry, dict) else None
    tone = summary.get("net_tone") if isinstance(summary, dict) else None
    return float(tone) if tone is not None else None


def latest_annual_summaries(sections: Sequence[dict]) -> tuple[dict | None, dict[str, dict]]:
    """Duygu bölümlerinden (``{"filing": {...}, "item_code", "summary"|özet alanları}``) en son
    10-K başvurusunu ve onun bölüm özetlerini (item_code → özet) döndürür."""
    annual = [s for s in sections if _base_form((s.get("filing") or {}).get("form")) == ANNUAL_FORM]
    if not annual:
        return None, {}
    latest = max(annual, key=lambda s: _sort_key(s["filing"]))["filing"]
    acc = latest.get("accession_no")
    by_code = {s["item_code"]: s.get("summary", s) for s in annual
               if (s.get("filing") or {}).get("accession_no") == acc}
    return latest, by_code


def compute_text_signals(latest_10k_sections_summary, risk_comparison: dict | None) -> dict:
    """Skorun metin sinyalleri (scoring.compute_score girdisi).

    latest_10k_sections_summary: son 10-K'nın bölüm özetleri — ``{"7": summarize(...),
    "1A": ...}`` sözlüğü veya ``[{"item_code", "summary"}]`` listesi (None olabilir).
    risk_comparison: ``compare_risk_sections`` çıktısı (``counts`` alanı) veya None.
    """
    risk_added = None
    if risk_comparison and isinstance(risk_comparison.get("counts"), dict):
        added = risk_comparison["counts"].get("added")
        risk_added = int(added) if added is not None else None
    return {
        "mdna_net_tone": _net_tone(latest_10k_sections_summary, "7"),
        "risk_net_tone": _net_tone(latest_10k_sections_summary, RISK_CODE),
        "risk_added": risk_added,
    }


def score_company(facts: dict[str, list[dict]], text_signals: dict | None,
                  periods: int = 3) -> tuple[dict, dict]:
    """``compute_ratios(facts, periods)`` → ``compute_score``; (oranlar, skor) döndürür."""
    ratios = compute_ratios(facts, periods=periods)
    return ratios, compute_score(ratios, facts, text_signals or {})
