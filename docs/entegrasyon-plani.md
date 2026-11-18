# Entegrasyon planı (Aşama 3)

Saf modüller (`xbrl`, `ratios`, `scoring`, `text_extract`, `sentiment`, `risk_diff`,
`embeddings`, `llm`, `rag`) veritabanından bağımsızdır. Bu belge onları veritabanı, analiz
hattı ve API ile birleştiren katmanı tanımlar.

## Katmanlar

```
api.py          FastAPI: yalnızca HTTP ↔ servis çağrısı, doğrulama, hata kodları
repository.py   SQLAlchemy: satırları düz sözlüklere çevirir / sözlükleri yazar
views.py        SAF: düz sözlüklerden API sözleşmesindeki JSON'u kurar (test edilebilir)
analyze.py      SAF: başvuru metinlerinden bölüm, parça, gömme, duygu, risk farkı üretir
jobs.py         Arka plan işi: collect → save → analyze → score, ilerleme kaydı
models.py, db.py
```

`views.py` ve `analyze.py` fastapi/sqlalchemy içe aktarmaz; bulut ortamında tam test edilir.
`repository.py`, `jobs.py`, `api.py` testleri `importorskip` ile kullanıcı bilgisayarında koşar.

## Veritabanı (models.py)

Mevcut: `company`, `filing`, `financial_fact`. Eklenecek sütunlar (hepsi NULL olabilir):
- `company.sic_description`, `company.exchanges` (JSON metni), `company.analysis_updated_at`
- `filing.analyzed` (bool, varsayılan false)
- `financial_fact.derived_from` (JSON metni)

Yeni tablolar:

| Tablo | Alanlar |
| --- | --- |
| `filing_document` | id, filing_id FK UNIQUE, local_path, sha256, size_bytes, downloaded_at |
| `filing_section` | id, filing_id FK, item_code, title, text, char_start, char_end, word_count; UNIQUE(filing_id, item_code) |
| `text_chunk` | id, section_id FK, chunk_index, text, word_count |
| `chunk_embedding` | chunk_id PK/FK, model_name, dim, vector (LargeBinary float32) |
| `sentiment_result` | id, section_id FK, sentence_index, sentence, label, score, model_name |
| `risk_comparison` | id, company_id FK, old_filing_id, new_filing_id, added, removed, modified, unchanged, created_at |
| `risk_change` | id, comparison_id FK, change_type, old_text, new_text, similarity |
| `company_score` | id, company_id FK, as_of_period, total, band, weights_version, payload (JSON), created_at |
| `qa_log` | id, company_id FK, question, answer, mode, model, source_chunk_ids (JSON), latency_ms, created_at |
| `ingestion_job` | id, ticker, status, step, progress, error, request_count, created_at, started_at, finished_at |

Şema yükseltme: `db.init_db` önce `create_all`, ardından mevcut tablolarda eksik NULL olabilir
sütunları `ALTER TABLE ... ADD COLUMN` ile ekler (önceki prototipten kalan `finsight.db`
dosyaları bozulmasın). Alembic geçişi Aşama 4'e bırakılmıştır.

Oranlar tabloya yazılmaz; her istekte `financial_fact` üzerinden hesaplanır (tutarlılık için).
Skorun anlık görüntüsü `company_score.payload` içinde saklanır.

## Analiz hattı (analyze.py)

1. Başvuru seçimi: en son iki 10-K (değişiklik başvurusu yalnızca orijinal yoksa) + en son 10-Q.
2. Her başvuru için: belge indir (`SecClient.document`, önbellekli) → `html_to_text` →
   `extract_sections` → bölüm başına `split_sentences` + `SentimentModel.predict` →
   `chunk_text` → `Embedder.embed`.
3. İki 10-K'nın "1A" bölümleri → `compare_risk_sections`.
4. Metin sinyalleri: son 10-K "7" net tonu, "1A" net tonu, eklenen risk sayısı.
5. `extract_all(companyfacts, years=4)` → `compute_ratios(periods=3)` → `compute_score`.

Aynı başvuru aynı duygu modeli ve gömme modeliyle daha önce işlendiyse atlanır.
Bir başvuruda hata olursa iş durmaz; hata adımı işe yazılır, diğer başvurulara geçilir.
