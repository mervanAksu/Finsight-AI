# FinSight AI — API Sözleşmesi (v1, son ek v1.4)

Backend ile frontend arasındaki tek doğruluk kaynağıdır. Alan eklemek serbesttir; alan adı
değiştirmek veya silmek bu dosya güncellenmeden yapılmaz.

- Tüm uç noktalar `/api` önekiyle sunulur. Derlenmiş arayüz (`frontend/dist`) aynı sunucuda `/` altından sunulur.
- Tarihler ISO 8601 (`"2025-06-30"`), parasal değerler USD cinsinden ham sayı (ölçeklenmemiş).
- Oranlar ham orandır (`0.253` = %25,3); `unit` alanı `"percent"` veya `"times"`.
- Eksik değer `null` ve mümkünse `reason` alanıyla döner; hiçbir zaman `0` ile doldurulmaz.
- Analiz içeren her yanıtta `disclaimer` (string) bulunur.
- Hata yanıtı: HTTP 4xx/5xx ve `{"detail": "Türkçe açıklama"}`.
- Bilinmeyen şirket: 404. Şirket var ama analiz yapılmamışsa ilgili alanlar `null` / boş liste.

Ortak nesneler:

```jsonc
// Company
{"ticker": "MSFT", "cik": "0000789019", "name": "MICROSOFT CORP", "sic": "7372",
 "sic_description": "Services-Prepackaged Software", "fiscal_year_end": "0630",
 "exchanges": ["Nasdaq"]}

// FilingRef
{"accession_no": "0000950170-25-100235", "form": "10-K", "filing_date": "2025-07-30",
 "report_date": "2025-06-30", "url": "https://www.sec.gov/Archives/edgar/data/789019/..."}

// Source (sayısal değerin kaynağı)
{"xbrl_tag": "us-gaap:NetIncomeLoss", "form": "10-K", "accession_no": "0000950170-25-100235",
 "filed": "2025-07-30", "derived_from": null,   // türetilmişse xbrl_tag "derived" ve etiket listesi
 "url": "https://www.sec.gov/Archives/edgar/data/789019/000095017025100235/"}  // başvuru klasörü
```

### Netleştirmeler (v1.1)
- Company alanlarından `sic`, `sic_description`, `fiscal_year_end` `null` olabilir; `exchanges` boş liste olabilir.
- `/overview` yanıtında `analyzed: bool` alanı vardır (metin analizi yapılmış mı). Arama
  sonuçlarındaki `ingested` = finansal veri çekilmiş; `analyzed` = metin analizi de yapılmış.
- `/facts`: değeri olmayan dönem listede yer almaz (`value: null` gönderilmez).
- `/score` hesaplanamamışsa: `total`, `band`, `as_of_period`, `explanation` `null`; `components` boş.
  Gösterge anahtarları: oran anahtarları + `revenue_yoy`, `net_income_yoy`, `mdna_net_tone`,
  `risk_net_tone`, `risk_added`. Göstergelerde ek alanlar: `reason`, `threshold`.
- `/ask`: `model` LLM modunda LLM model adı, diğer modlarda gömme modeli adıdır.
  Soru gövdesi geçersizse 422 (FastAPI doğrulama hatası, `detail` bir dizi olabilir).
- `/compare`: finansal verisi çekilmiş her şirket karşılaştırılabilir (metin analizi gerekmez);
  bilinmeyen ticker 404, 2–4 dışı sayıda ticker 400. Şirketlerin `period_end` değerleri farklı olabilir.
- `/risk-changes`: karşılaştırma yoksa `old_filing` ve `new_filing` `null`, `counts` değerleri 0, `changes` boş.
- `/export.csv`: bilinmeyen şirket 404; analiz yoksa yalnızca `fact` ve `ratio` satırları.
- `/jobs`: `status = "error"` iken `progress` hatanın oluştuğu andaki değerdir.
- Sunucu tarafı metinler (etiketler, açıklamalar, uyarı) Türkçedir; arayüz bilinen anahtarları kendi çevirir.

### Netleştirmeler (v1.2)
- Aynı CIK'e bağlı birden fazla ticker (ör. GOOGL / GOOG) aynı şirketi gösterir: `{ticker}` alan
  tüm uç noktalar şirketin bilinen her ticker'ıyla çalışır. `company.ticker` şirketin ilk
  kaydedildiği birincil ticker'dır (istekteki ticker'dan farklı olabilir).
- `POST /ingest`: aynı şirket (CIK) için başka bir ticker'la başlatılmış çalışan iş varsa yeni iş
  açılmaz, onun kimliği döner.
- `/jobs`: hiçbir başvuru analiz edilemezse (ör. belgeler indirilemedi) iş `done` olur, uyarı
  eklenir; skor ve `analysis_updated_at` güncellenmez.
- `/ask`: `sources` içinde metni birebir aynı parçalar (eski ve yeni 10-K'da değişmeyen paragraf)
  tekrarlanmaz; yalnızca en yeni başvurununki yer alır. Çıkarımsal cevapta aynı cümle bir kez
  alıntılanır, bölüm alt başlıkları alıntılanmaz.
- `/ask`: şirketin parçaları yalnızca başka bir gömme modeliyle oluşturulmuşsa `mode: "not_found"`
  ile birlikte `warning` döner (yeniden analiz önerisi).
- `/ask`: `advice_refused: true` ve `mode: "not_found"` birlikteyse `answer` tam olarak
  "FinSight AI yatırım tavsiyesi vermez. Bu soruyla ilgili işlenen raporlarda bilgi bulunamadı."
  olur. Diğer modlarda ret cümlesi cevabın başına eklenir.
- `/export.csv`: CSV'de uyarı yanıt başlığındadır: `X-FinSight-Disclaimer` (URL kodlu UTF-8,
  `decodeURIComponent` ile çözülür). CSV gövdesine yorum satırı eklenmez (Excel'de ilk satır
  sütun başlıklarıdır). Başlık CORS ile tarayıcıya açılmıştır (`Access-Control-Expose-Headers`).

## GET /api/health
```json
{"status": "ok", "version": "0.3.0",
 "models": {"sentiment": "ProsusAI/finbert", "embedding": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            "llm": {"provider": "anthropic", "model": "claude-haiku-5-5", "enabled": true}}}
```
v1.3: yanıtta `"sec": {"user_agent_configured": true}` alanı da bulunur. `false` ise
`SEC_USER_AGENT` tanımlı değildir; arama yalnızca işlenmiş şirketlerde çalışır ve yeni şirket
çekilemez.
Yedek modeller kullanılıyorsa adlar `"lexicon-fallback"`, `"hashing-fallback"`; LLM yoksa `{"provider": "none", "model": null, "enabled": false}`.
FinBERT doğruluğu etkileyebilecek bir ayarla çalışıyorsa duygu modeli adına ek gelir: `"ProsusAI/finbert+int8"` (int8 kuantizasyon), `"ProsusAI/finbert+len256"` (max_length 256). Varsayılan ayarlarda ad `"ProsusAI/finbert"`dir. Bu, `/sentiment` ve `sentiment_summary` içindeki `model` alanı için de geçerlidir.

## GET /api/companies
İşlenmiş şirketler, ticker sırasıyla.
```json
[{"ticker": "MSFT", "cik": "0000789019", "name": "MICROSOFT CORP", "sic_description": "...",
  "analyzed": true, "last_filing_date": "2025-07-30",
  "score": {"total": 78.4, "band": "Güçlü"}}]
```
`score` analiz yoksa `null`.

## GET /api/companies/search?q=micro&limit=10
SEC ticker listesinde (önbellekteki `company_tickers.json`) ticker, ad veya CIK araması (FR-01).
```json
[{"ticker": "MSFT", "cik": "0000789019", "name": "MICROSOFT CORP", "ingested": true}]
```
Liste henüz indirilmemişse ve SEC'e erişilemiyorsa yalnızca işlenmiş şirketlerde arar.

v1.3: aramanın kapsamı yanıt başlıklarında bildirilir (gövde değişmez, CORS ile açıktır):

| Başlık | Değer |
| --- | --- |
| `X-FinSight-Search-Scope` | `sec` (SEC ticker listesi + işlenmiş şirketler) veya `local` (yalnızca işlenmiş şirketler) |
| `X-FinSight-Search-Reason` | Yalnızca `local` iken: `sec_user_agent_missing` (`SEC_USER_AGENT` tanımlı değil) veya `sec_unavailable` (ağ / SEC hatası; 5 dk sonra yeniden denenir) |

Arayüz `local` kapsamında sonuç listesinin altında nedenini gösterir; sessiz düşüş olmaz.

## POST /api/companies/{ticker}/ingest
Gövde: `{"analyze": true}` (varsayılan true). Arka planda veri çekme + analiz başlatır (FR-15).
Yanıt 202: `{"job_id": 7, "status": "queued"}`. Aynı şirket için çalışan iş varsa onun kimliği döner.

## GET /api/jobs/{job_id}
```json
{"id": 7, "ticker": "MSFT", "status": "running", "step": "Duygu analizi (10-K 2025)",
 "progress": 0.6, "error": null, "started_at": "2026-11-18T13:00:00Z", "finished_at": null,
 "request_count": 14}
```
`status`: `queued | running | done | error`. `progress` 0–1.

## GET /api/companies/{ticker}/overview
```json
{"company": {Company},
 "score": {"total": 78.4, "band": "Güçlü", "as_of_period": "2025-06-30"},
 "key_metrics": [
   {"metric": "revenue", "label": "Gelir", "value": 281724000000, "period_end": "2025-06-30",
    "yoy": 0.149, "source": {Source}}
 ],
 "latest_filings": [{FilingRef}],
 "sentiment_summary": {"filing": {FilingRef}, "mdna_net_tone": 0.08, "risk_net_tone": -0.52, "model": "ProsusAI/finbert"},
 "risk_summary": {"added": 4, "removed": 1, "modified": 9, "unchanged": 120},
 "analysis_updated_at": "2026-11-18T13:05:00Z",
 "disclaimer": "..."}
```
`key_metrics` sırası: revenue, net_income, operating_cash_flow, total_assets, total_liabilities, stockholders_equity.
`sentiment_summary`, `risk_summary` analiz yoksa `null`.

## GET /api/companies/{ticker}/facts?metric=revenue
```json
{"company": {Company},
 "metrics": [
   {"metric": "revenue", "label": "Gelir", "kind": "duration",
    "values": [{"period_start": "2024-07-01", "period_end": "2025-06-30", "value": 281724000000,
                "yoy": 0.149, "source": {Source}}]}
 ],
 "disclaimer": "..."}
```
`metric` verilmezse tüm kalemler (bkz. `.claude/skills/financial-analysis`). `values` eskiden yeniye. `kind`: `duration | instant`.

## GET /api/companies/{ticker}/ratios
```json
{"company": {Company},
 "periods": ["2023-06-30", "2024-06-30", "2025-06-30"],
 "ratios": [
   {"key": "net_margin", "label": "Net kâr marjı", "formula": "Net kâr / Gelir",
    "unit": "percent", "higher_is_better": true,
    "values": [{"period_end": "2025-06-30", "value": 0.361, "status": "ok", "reason": null, "note": null,
                "inputs": [{"metric": "net_income", "value": 101832000000, "accession_no": "..."}]}]}
 ],
 "disclaimer": "..."}
```
`status`: `ok | missing`. `values` her dönem için bir öğe, `periods` sırasıyla.

## GET /api/companies/{ticker}/score
```json
{"total": 78.4, "band": "Güçlü", "as_of_period": "2025-06-30", "weights_version": "v1-2026-11",
 "components": [
   {"key": "profitability", "label": "Kârlılık", "weight": 25, "score": 92.5, "available": true,
    "indicators": [{"key": "net_margin", "label": "Net kâr marjı", "value": 0.361, "display": "%36,1", "points": 100.0}],
    "explanation": "Net kâr marjı %36,1 (eşik %0–%20 → 100 puan) ..."}
 ],
 "missing_components": [],
 "explanation": "Toplam skor 7 bileşenin ağırlıklı ortalamasıdır ...",
 "disclaimer": "..."}
```
Skor hesaplanamamışsa 200 ile `{"total": null, "components": [], ...}`.

## GET /api/companies/{ticker}/filings?form=10-K&limit=50
```json
{"company": {Company},
 "filings": [{"accession_no": "...", "form": "8-K", "filing_date": "2025-07-30", "report_date": "2025-07-30",
              "items": ["2.02", "9.01"], "item_labels": ["Faaliyet sonuçları ve finansal durum", "Finansal tablolar ve ekler"],
              "url": "...", "analyzed": false}],
 "disclaimer": "..."}
```
`form` verildiğinde değişiklik başvuruları (`10-K/A`) da dahildir. Yeniden eskiye sıralı.

## GET /api/companies/{ticker}/sentiment
```json
{"model": "ProsusAI/finbert",
 "sections": [
   {"filing": {FilingRef}, "item_code": "7", "title": "Yönetimin Değerlendirmesi (MD&A)",
    "sentence_count": 812, "positive": 210, "negative": 140, "neutral": 462,
    "net_tone": 0.086,
    "top_negative": [{"sentence": "...", "score": 0.97}],
    "top_positive": [{"sentence": "...", "score": 0.95}]}
 ],
 "disclaimer": "..."}
```
`item_code`: `"1A"` (risk faktörleri) veya `"7"` (MD&A); 10-Q için `"2"` (MD&A) ve `"1A"`.
Bölümler başvuru tarihine göre eskiden yeniye. `top_*` en fazla 5 cümle.

## GET /api/companies/{ticker}/risk-changes
```json
{"old_filing": {FilingRef}, "new_filing": {FilingRef},
 "counts": {"added": 4, "removed": 1, "modified": 9, "unchanged": 120},
 "changes": [{"change_type": "added", "old_text": null, "new_text": "...", "similarity": null},
             {"change_type": "modified", "old_text": "...", "new_text": "...", "similarity": 0.71}],
 "disclaimer": "..."}
```
`changes` yalnızca `added | removed | modified` içerir (önce added, sonra modified, sonra removed).
Karşılaştırılacak iki 10-K yoksa `old_filing: null`, `changes: []`.

## POST /api/companies/{ticker}/ask
Gövde: `{"question": "Tedarik zinciri riskleri nasıl değişti?"}` (1–500 karakter).
```json
{"question": "...", "answer": "Şirket ... belirtmektedir [1]. ... [2]",
 "found": true,
 "sources": [{"n": 1, "chunk_id": 412, "section": "1A", "section_title": "Risk Faktörleri",
              "form": "10-K", "filing_date": "2025-07-30", "accession_no": "...", "url": "...",
              "text": "alıntılanan parça ...", "similarity": 0.62}],
 "mode": "llm",
 "model": "claude-haiku-5-5", "latency_ms": 2140, "disclaimer": "..."}
```
`mode`: `llm` (LLM cevabı) | `extractive` (LLM yok; en ilgili alıntılar) | `not_found`.
`advice_refused` (bool, her zaman var): soru yatırım tavsiyesi niyeti taşıyorsa `true`; cevabın
başına sabit ret cümlesi eklenir, yalnızca rapor olguları verilir. `warning` (isteğe bağlı
string): ör. parçalar farklı bir gömme modeliyle oluşturulmuşsa yeniden analiz uyarısı.
LLM cevabındaki tavsiye cümleleri ve atıfsız sayısal cümleler sunucuda ayıklanır.
`found=false` iken `answer` "Bu bilgi işlenen raporlarda bulunamadı." anlamında bir cümledir ve `sources` boştur
(tavsiye niyetli soruda: "FinSight AI yatırım tavsiyesi vermez. Bu soruyla ilgili işlenen raporlarda bilgi bulunamadı.").

## GET /api/compare?tickers=AAPL,MSFT
2–4 şirket (FR-13).
```json
{"ratio_meta": [{"key": "net_margin", "label": "Net kâr marjı", "unit": "percent", "higher_is_better": true}],
 "companies": [{"ticker": "MSFT", "name": "...", "period_end": "2025-06-30",
                "score": {"total": 78.4, "band": "Güçlü"},
                "ratios": {"net_margin": 0.361, "roe": null}}],
 "disclaimer": "..."}
```

## GET /api/companies/{ticker}/export.csv
FR-16. `text/csv; charset=utf-8`, UTF-8 BOM ile (Excel uyumu). Sütunlar:
`ticker,cik,section,key,label,period_start,period_end,value,unit,status,xbrl_tag,form,accession_no,filed`.
`section`: `fact | ratio | score`. Uyarı metni `X-FinSight-Disclaimer` yanıt başlığındadır
(bkz. Netleştirmeler v1.2); CSV'nin ilk satırı her zaman sütun başlıklarıdır.

## v1.4 — Güncel (TTM) veri ve tazelik (Aşama 4, yalnızca ekleme)

Kaynak: `docs/asama4-sozlesme.md`. Mevcut alanların adı ve anlamı değişmez.

**Dayanak (`basis`).** Son 10-K'dan sonra yayımlanmış bir 10-Q varsa son dönem **TTM**'dir
(`"ttm"`): süreli kalemler `A + YTD_cur − YTD_prior`, anlık kalemler o 10-Q'nun bilançosu
(dönem sonu `E_Q`). Yoksa `"annual"`. Bir kalemin TTM parçası eksikse o kalem için yıllık
değer kullanılır ve nedeni yazılır (uydurma/oranlama yok).

Source nesnesine TTM noktalarında ek alanlar (NFR-06):
```jsonc
{"xbrl_tag": "us-gaap:Revenues", "form": "10-Q", "accession_no": "...", "filed": "2026-10-28",
 "derived_from": null, "url": "...",
 "basis": "ttm",
 "parts": [{"role": "annual", "metric": null, "xbrl_tag": "us-gaap:Revenues", "form": "10-K",
            "accession_no": "...", "period_start": "2025-07-01", "period_end": "2026-06-30",
            "value": 1200, "url": "..."},
           {"role": "ytd_current", ...}, {"role": "ytd_prior", ...}]}
```
`role`: `annual | ytd_current | ytd_prior | component`. Her parçada ayrıca `restated` (bool)
ve `original_value`: aynı dönem için daha önce farklı değer raporlanmışsa (yeniden düzenlenmiş
karşılaştırma, 10-Q/A) `restated: true` ve ilk raporlanan değer; hesapta en son raporlanan
değer kullanılır. Türetilmiş TTM'de (`xbrl_tag: "derived"`, ör. brüt kâr = gelir TTM − maliyet
TTM, toplam yükümlülük = LSE − özkaynak) `parts` öğeleri `role: "component"` ve `metric`
doludur; `derived_from` yine etiket listesidir. Anlık (bilanço) TTM noktasında `parts` boştur
(doğrudan 10-Q değeri). TTM yalnızca yıllık serinin birincil XBRL etiketinden üretilir.
`basis: "ttm"` ancak gelir veya net kâr TTM ise; yalnızca bilanço 10-Q'ya geçtiyse dayanak
`"annual"` kalır.

### /score ve /overview.score
Ek alanlar: `basis` (`"ttm" | "annual"`), `as_of_filing` (`{"form", "accession_no",
"filing_date"}` — skorun dayandığı başvuru), `data_through` (= `as_of_period`),
`method_version` (`"v2-2026-11-ttm"`; Aşama 4 öncesi kayıtlı skorlarda `"v1-2026-11"` ve
`basis: "annual"`). `/score`'da ayrıca `basis_notes` (TTM'si hesaplanamayıp yıllığa düşen
kalemler, Türkçe). `weights_version` `"v1-2026-11"` kalır (ağırlık ve eşikler değişmedi).
Göstergelerde ek `note` (ör. büyüme: "TTM büyümesi hesaplanamadı; yıllık YoY kullanıldı") ve
metin tonu göstergesinde `source` (`{form, accession_no, filing_date}`): MD&A tonu en son
analiz edilen başvurudan (10-K Item 7 veya daha yeni 10-Q Part I Item 2), risk tonu en son
10-K'dan. Büyüme göstergeleri TTM ↔ önceki TTM (`A_önceki + YTD_prior − YTD_prior2`).

### /overview
- `key_metrics[]` ek alanlar: `basis` (`"ttm" | "annual"`), `ttm` (`{"value", "period_start",
  "period_end", "yoy", "source"}` veya `null`), TTM şirketinde TTM'si olmayan kalemde
  `ttm_reason`. `value` / `period_end` / `yoy` son **yıllık** değer olarak kalır.
- `latest_filing`: en son analiz edilen 10-K/10-Q (FilingRef) veya `null`.

### /ratios
- `periods` listesinin sonuna TTM dönemi eklenir (`E_Q`, ör. `"2026-09-30"`).
- `period_info`: `[{"period_end", "basis", "label"}]` (`label` yıllıkta `"2026-06-30"`, TTM'de
  `"TTM 2026-09-30"`).
- Her oranda `basis` (en son noktanın dayanağı); her değerde `basis`. TTM sütununda yıllık
  değere düşen girdi varsa `note`: `"TTM yok, yıllık değer kullanıldı: operating_cash_flow"`.
  Ortalamalı oranlarda (ROA/ROE) önceki değer `E_Q − 1 yıl` bilançosudur.
- `/compare` ve `/export.csv` aynı (TTM dahil) dönem listesini kullanır. `/compare`
  `companies[]` öğelerinde ek: `basis` (`"ttm" | "annual"`, son sütunun dayanağı) ve
  `as_of_period` (= `period_end`).

### /facts
`metrics[].values` yalnızca yıllık seridir (değişmedi). Ek alan `metrics[].latest`: dinamik
seriden TTM (süreli) veya E_Q 10-Q bilanço (anlık) noktası —
`{"period_start", "period_end", "value", "yoy", "source", "basis": "ttm"}`; TTM yoksa `null`.

### GET /api/companies/{ticker}/freshness (yeni)
```json
{"checked_at": "2026-11-23T09:30:00Z",
 "sec_latest": {"form": "10-Q", "filing_date": "2026-10-28", "report_date": "2026-09-30",
                "accession_no": "0000950170-26-000123"},
 "analyzed_latest": {"form": "10-K", "filing_date": "2026-07-30", "report_date": "2026-06-30",
                     "accession_no": "..."},
 "new_filings": [{"form": "10-Q", "filing_date": "2026-10-28", "report_date": "2026-09-30",
                  "accession_no": "..."}],
 "stale": true, "error": null, "disclaimer": "..."}
```
- Yalnızca kayıtlı şirketler: kayıtsız ticker 404 (SEC'e istek gitmez); geçersiz ticker 400.
- `stale`: SEC'te veritabanında olmayan, en son kayıtlı 10-K/10-Q'dan eski olmayan bir
  10-K/10-Q (/A) varsa `true`. 8-K'lar `new_filings`'te listelenir ama `stale` yapmaz.
- `analyzed_latest`: analiz edilmiş en son 10-K/10-Q (yoksa kayıtlı en son 10-K/10-Q).
- SEC sonucu şirket başına süreç içinde 15 dk tutulur (**hata sonuçları dahil**); küresel
  "fresh" istek bütçesi dakikada 10'dur. SEC'e ulaşılamazsa veya bütçe doluysa HTTP 200,
  `stale: null`, `error`: Türkçe neden.
- `xbrl_pending` (bool) ve `xbrl_note`: en son kayıtlı 10-Q (son 10-K'dan yeni) TTM
  noktalarında görünmüyorsa `true` + Türkçe açıklama (SEC companyfacts gecikmesi; skor
  şimdilik önceki döneme dayanır). `stale` hesabını etkilemez.
- "Güncelle" düğmesi `POST /ingest` çağırır (artımlı; submissions/companyfacts önbelleği
  atlanarak taze alınır). Aynı şirket (CIK) için etkin iş varsa — başka süreçte (refresh CLI,
  otomatik yenileyici) açılmış olsa da — **202** ve mevcut işin kimliği döner. Yalnızca başka
  bir süreç kilidi tam o anda almış ve işini henüz oluşturmamışsa (≤ 1 dk aralık) **409**
  (`detail`: "Bu şirket için başka bir süreç iş başlatıyor; biraz sonra tekrar deneyin.").

### /health
`sec.json_ttl_seconds` (submissions/companyfacts önbellek süresi, `FINSIGHT_SEC_JSON_TTL`,
varsayılan 3600, en az 300) ve üst düzeyde `auto_refresh_minutes` (`FINSIGHT_AUTO_REFRESH_MINUTES`;
0 = kapalı, açıksa en az 30). Geçersiz değerde uygulama `ConfigError` ile başlamaz.

## v1.5 — Güvenlik sertleştirmesi (yalnızca ekleme; `docs/guvenlik/duzeltmeler.md`)

Başarılı yanıtların JSON şekli değişmedi. Yeni hata durumları ve başlıklar:

| Durum | Ne zaman | Yanıt |
| --- | --- | --- |
| **400** | `Host` başlığı `127.0.0.1`, `localhost`, `::1` (+ `FINSIGHT_ALLOWED_HOSTS`) değil (DNS rebinding, SEC-03) — **tüm uç noktalar** | `{"detail": "Geçersiz Host başlığı: ..."}` |
| **403** | Durum değiştiren istek (POST/PUT/PATCH/DELETE) başka siteden: `Sec-Fetch-Site: cross-site` veya izinli olmayan `Origin` (`null` dahil). İzinli: API'nin kendi kökeni (Host'tan), `http://127.0.0.1:8000`, `http://localhost:8000`, geliştirme arayüzü `:5173` ve `FINSIGHT_CORS_ORIGINS`. `Origin` başlığı olmayan istemciler (curl, betikler) etkilenmez (SEC-01) | `{"detail": "Başka bir siteden gelen istek reddedildi (CSRF koruması)."}` |
| **400** | Ticker harf/rakamla başlamıyor (`..`, `-`) veya satır sonu içeriyor (`AAPL%0A`) (SEC-18) | mevcut "Geçersiz ticker" mesajı |
| **429** `POST /ingest` | Eşzamanlı iş kapasitesi dolu: en fazla `FINSIGHT_MAX_CONCURRENT_JOBS` (2) çalışan + `FINSIGHT_MAX_QUEUED_JOBS` (8) kuyrukta. Aynı şirket için çalışan iş varsa 429 yerine o iş döner (SEC-02) | `Retry-After: 30`, Türkçe `detail` |
| **503** `POST /ingest` | Veritabanı meşgul (SQLite "database is locked") | `Retry-After: 5`, `{"detail": "Veritabanı şu anda meşgul ..."}` |
| **429** `POST /ask` | Dakikada `FINSIGHT_ASK_PER_MINUTE` (20) sorudan fazlası (SEC-03) | `Retry-After: <sn>`, Türkçe `detail` |

- `POST /ask`: LLM'li günlük soru sınırı (`FINSIGHT_LLM_DAILY_LIMIT`, varsayılan 300, 0 = sınırsız)
  dolarsa hata yerine LLM'siz (`mode: "extractive"`) cevap döner ve `warning` alanında
  "Günlük LLM soru sınırına ulaşıldı ..." yazar.
- `POST /ask` `answer` (`mode: "llm"`): tavsiye cümleleri (genişletilmiş TR/EN sözlük) ve
  **atıf yaptığı kaynakta geçmeyen sayı içeren** cümleler çıkarılır (SEC-04/05). Kalan cümle
  yoksa çıkarımsal moda düşülür (mevcut davranış).
- `FINSIGHT_QA_LOG=0`: soru-cevaplar veritabanına yazılmaz (SEC-19).
- Tüm yanıtlarda: `Content-Security-Policy` (`frame-ancestors 'none'`; arayüzün satır içi
  betiğine SHA-256 özetiyle izin; `/docs`, `/redoc` için Swagger CDN'ine izin veren ayrı
  politika), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy: same-origin` (SEC-12).
- `POST /ingest` gövdesi isteğe bağlı kalır (geriye uyumluluk; tarayıcı kaynaklı CSRF yukarıdaki
  403 kuralıyla engellenir).

### v1.5.1 — 2. güvenlik inceleme turu (yalnızca davranış netleştirmesi; şekil aynı)

- `POST /ask` (`mode: "llm"`): atıf (`[n]`) içermeyen içerik cümleleri de çıkarılır; yalnızca
  kısa bağlaç / başlık ("Özet:") ve yatırım tavsiyesi / "bulunamadı" uyarı cümleleri atıfsız
  kalabilir. Sayı ve tarih doğrulaması biçim farklarını (TR/EN ondalık, ölçek ekleri, tarih
  biçimleri, gösterilen hassasiyete yuvarlama) tolere eder; ölçekli cevap sayısı yalnızca ölçekli
  kaynak sayısıyla eşleşir; başvuru tarihi yalnızca tam tarih olarak kaynak sayılır.
- Günlük LLM bütçesi (`FINSIGHT_LLM_DAILY_LIMIT`) yalnızca LLM gerçekten çağrıldığında, soru
  başına bir birim düşer (ilgili parça bulunamayan sorular bütçe harcamaz).
- Host reddi (400) mesajı `http://127.0.0.1:8000` adresini önerir (`0.0.0.0` tarayıcı adresi
  değildir).
