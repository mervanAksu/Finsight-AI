"""Başvuru metinlerinden bölüm, cümle duygusu, parça ve gömme üreten SAF analiz katmanı.

FR-07 (risk bölümü), FR-08 (duygu), FR-09 (risk farkı), FR-10 (soru-cevap için parçalar),
FR-11 (skorun metin sinyalleri). Veritabanı ve ağ erişimi yoktur; ``jobs.py`` bu fonksiyonları
çağırır ve sonuçları ``repository.py`` ile yazar. Hesap kuralları: docs/entegrasyon-plani.md.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime

import numpy as np

from .embeddings import HASHING_NAME, Embedder
from .ratios import compute_ratios
from .risk_diff import compare_risk_sections
from .scoring import compute_score
from .sentiment import SentimentModel, summarize
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


def select_filings_for_analysis(filings: list[dict]) -> list[dict]:
    """Analiz edilecek başvurular: en son iki 10-K + en son 10-Q (eskiden yeniye).

    - Orijinal başvuru tercih edilir; ``10-K/A`` / ``10-Q/A`` yalnızca aynı dönem için orijinal
      yoksa seçilir (değişiklik başvuruları çoğu zaman yalnızca Part III içerir).
    - ``primary_document`` alanı olup boş olan başvurular atlanır.
    """
    selected = _pick(filings, ANNUAL_FORM, 2) + _pick(filings, QUARTERLY_FORM, 1)
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


def analyze_sections(html: bytes | str, form: str, *, sentiment_model: SentimentModel) -> list[dict]:
    """HTML → bölümler (belge sırasıyla) + cümle duyguları + özet. Gömme yapılmaz.

    Duygu tahmini bölüm başına tek toplu (batch) çağrıyla yapılır.
    """
    text = html_to_text(html)
    sections = sorted(extract_sections(text, form).values(), key=lambda s: s["char_start"])
    out = []
    for sec in sections:
        # Alt başlıklar ("Risks Related to Our Business") duygu cümlesi sayılmaz (R2).
        sentences = content_sentences(sec["text"])
        predictions = sentiment_model.predict(sentences) if sentences else []
        if len(predictions) != len(sentences):
            raise ValueError("Duygu modeli cümle sayısı kadar tahmin döndürmedi")
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
                     embedder: Embedder, chunk_words: int | None = None) -> dict:
    """Bir başvuru belgesinin tam analizi.

    Dönüş: ``{"sections": [{item_code, title, text, char_start, char_end, word_count,
    sentences: [{index, sentence, label, score}], summary, chunks: [{index, text, word_count,
    embedding}]}], "sentiment_model", "embedding_model", "embedding_dim", "chunk_words"}``.
    """
    words = chunk_words or default_chunk_words(embedder)
    sections = analyze_sections(html, form, sentiment_model=sentiment_model)
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
