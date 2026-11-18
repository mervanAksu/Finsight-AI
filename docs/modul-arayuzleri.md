# Modül arayüzleri (Aşama 3)

Bu belge, paralel çalışan alt ajanların ürettiği saf Python modüllerinin imzalarını tanımlar.
Entegrasyon katmanı (veritabanı, analiz hattı, API) yalnızca bu imzalara dayanır.
Saf modüller `fastapi` ve `sqlalchemy` içe aktarmaz.

Ortak kayıt biçimi — **FactItem** (`xbrl.extract_annual` çıktısıyla aynı, bir alan eklendi):
```python
{"metric": str, "tag": str, "period_start": date | None, "period_end": date, "value": float,
 "unit": "USD", "accession_no": str, "form": str, "filed": date, "fy": int | None,
 "fp": str | None, "derived_from": list[str] | None}
```

## Finans (backend-engineer)

`finsight/xbrl.py` (mevcut imzalar korunur)
- `METRICS: dict[str, tuple[list[str], bool]]` — skill tablosundaki tüm kalemler.
- `METRIC_LABELS: dict[str, str]` — Türkçe etiketler ("revenue" → "Gelir").
- `extract_annual(companyfacts, metric, years=3, unit="USD") -> list[FactItem]` — değişmedi.
- `extract_all(companyfacts, years=3) -> dict[str, list[FactItem]]` — türetme kurallarını
  (gross_profit, total_liabilities) uygular; türetilenlerde `tag="derived"`.
- `yoy_changes(series) -> list[float | None]` — mevcut davranış (yüzde) korunur.
- `yoy_ratio(series) -> list[float | None]` — yeni: ham oran, `|prev|` ile bölme.

`finsight/ratios.py`
- `RATIO_DEFS: list[dict]` — `key, label, formula, unit, higher_is_better`.
- `compute_ratios(facts: dict[str, list[FactItem]], periods: int = 3) -> dict`
  → `{"periods": [date], "ratios": [ {…, "values": [ {"period_end", "value", "status",
  "reason", "note", "inputs": [{"metric", "value", "accession_no"}]} ]} ]}`
  (API'deki `/ratios` şeklinin `company`/`disclaimer` hariç hali).

`finsight/scoring.py`
- `WEIGHTS_VERSION = "v1-2026-11"`, `COMPONENTS` tanımı.
- `compute_score(ratios: dict, facts: dict[str, list[FactItem]], text_signals: dict) -> dict`
  - `text_signals = {"mdna_net_tone": float | None, "risk_net_tone": float | None, "risk_added": int | None}`
  - çıktı: API `/score` şekli (`disclaimer` hariç).
- `band(total: float | None) -> str | None`

`finsight/formatting.py`
- `fmt_percent(x, digits=1) -> str` ("%36,1"), `fmt_times(x, digits=2) -> str` ("1,25x"),
  `fmt_usd_short(x) -> str` ("281,7 mr $").

## NLP (nlp-engineer)

`finsight/text_extract.py`
- `html_to_text(html: str | bytes) -> str`
- `extract_sections(text: str, form: str) -> dict[str, dict]` — anahtarlar 10-K: `"1A"`, `"7"`;
  10-Q: `"1A"`, `"2"`. Değer: `{"item_code", "title", "text", "char_start", "char_end"}`.
  Bulunamayan bölüm sözlükte yer almaz.
- `SECTION_TITLES: dict[str, str]` — Türkçe başlıklar.
- `split_sentences(text) -> list[str]`, `split_paragraphs(text) -> list[str]`
- `chunk_text(text, target_words=350, overlap_words=50) -> list[str]`

`finsight/sentiment.py`
- `class SentimentModel(Protocol)`: `name: str`; `predict(sentences) -> list[dict]`
  (her öğe `{"label": "positive"|"negative"|"neutral", "score": float}`).
- `FinBertModel`, `LexiconModel`, `get_sentiment_model(prefer: str = "finbert") -> SentimentModel`
- `summarize(sentences, predictions, top_n=5) -> dict` — `sentence_count, positive, negative,
  neutral, net_tone, top_negative, top_positive` (API `/sentiment` bölüm alanları).

`finsight/risk_diff.py`
- `compare_risk_sections(old_text: str, new_text: str) -> dict` → `{"counts": {...}, "changes": [...]}`
  (API `/risk-changes` şekli, başvuru alanları hariç).

`finsight/embeddings.py`
- `class Embedder(Protocol)`: `name`, `dim`, `embed(texts) -> np.ndarray` (float32, L2 normalize).
- `SentenceTransformerEmbedder`, `HashingEmbedder`, `get_embedder() -> Embedder`
- `to_bytes(vec) -> bytes`, `from_bytes(b, dim) -> np.ndarray`, `top_k(query, matrix, k) -> list[tuple[int, float]]`

`finsight/llm.py`
- `class LLMClient(Protocol)`: `provider`, `model`, `complete(system: str, user: str, max_tokens: int = 800) -> str`
- `AnthropicClient`, `OpenAICompatibleClient`, `get_llm_client() -> LLMClient | None`

`finsight/rag.py`
- `answer_question(question, chunks: list[dict], embedder, llm, top_k=5, min_similarity=None) -> dict`
  - `chunks` öğesi: `{"chunk_id", "text", "embedding" (np.ndarray), "section", "section_title",
    "form", "filing_date", "accession_no", "url"}`
  - çıktı: API `/ask` şekli (`question`, `disclaimer` hariç).
