# Aşama 4 sözleşmesi: güncel (dinamik) veri, tazelik, önbellek

Bu belge Aşama 4'te birden fazla ajanın dokunduğu arayüzlerin tek doğruluk kaynağıdır.
`docs/api-contract.md` v1.4 bu belgeye göre güncellenir.

## 1. Sorun

Skor ve oranlar yalnızca 10-K'lardaki yıllık (330–400 gün) dönemlerden hesaplanıyor. Mali yılı
Haziran'da biten bir şirket için Kasım ayında skor hâlâ 30 Haziran verisine dayanıyor; o tarihten
sonra yayımlanan 10-Q'lar skora girmiyor. Hedef: skor, oranlar ve ton analizi **SEC'te yayımlanmış
en son rapora** dayansın ve yeni rapor yayımlandığında sistem bunu fark etsin.

## 2. TTM (son on iki ay) kuralları (FR-04, FR-06, FR-11)

Tanımlar (tüm tarih eşleşmeleri mevcut kural gibi ±7 gün toleranslı):

- `A`: en son yıllık değer (10-K, 330–400 gün), dönem sonu `E_A`.
- `Q`: `E_A`'dan sonra biten en son 10-Q; dönem sonu `E_Q`.
- `YTD_cur`: `Q`'nun dönem sonuna (`E_Q`) biten ve mali yıl başında (`E_A + 1 gün`) başlayan
  süreli değer (mali yıl başından bugüne). Birden fazla aday varsa en son başvurudaki.
- `YTD_prior`: bir yıl önceki aynı YTD dönemi (bitiş ≈ `E_Q − 1 yıl`, başlangıç ≈ önceki mali yıl
  başı). Genellikle aynı 10-Q'da karşılaştırmalı olarak bulunur.

**Süreli kalemler** (hasılat, satış maliyeti, brüt kâr, faaliyet kârı, net kâr, faaliyet nakit
akışı, yatırım harcaması vb.):
`TTM = A + YTD_cur − YTD_prior`. `YTD_cur` veya `YTD_prior` yoksa o kalem için TTM üretilmez
(`None` + neden) ve yıllık değer kullanılır; uydurma/oranlama yapılmaz.

**Anlık kalemler** (toplam varlık, özkaynak, nakit, borç, dönen varlık/borç): `E_Q` tarihli 10-Q
bilanço değeri. Ortalama gerektiren oranlar için önceki değer `E_Q − 1 yıl` tarihli bilançodur
(önceki yılın aynı çeyreği; `is_prior_fiscal_year` kuralı 330–400 gün aynen geçerli).

**Önceki TTM** (büyüme için): `TTM_önceki = A_önceki + YTD_prior − YTD_prior2`
(`YTD_prior2`: bitişi ≈ `E_Q − 2 yıl`). Hesaplanamazsa büyüme yıllık YoY'a düşer ve bu
`note` alanında söylenir.

En son başvuru 10-K ise (`E_A`'dan sonra 10-Q yok) TTM = yıllık değerdir (`basis: "annual"`).

**İzlenebilirlik (NFR-06):** Her TTM noktası mevcut fact sözlüğü biçimindedir ve ek alanlar taşır:

```python
{"period_start": date, "period_end": E_Q, "value": float, "unit": "USD",
 "form": "10-Q", "accession_no": "<Q'nun numarası>", "tag": "<birincil etiket>",
 "filed": date, "basis": "ttm",
 "derived_from": [  # hesaba giren her parça
   {"role": "annual" | "ytd_current" | "ytd_prior", "accession_no": ..., "tag": ...,
    "period_start": ..., "period_end": ..., "value": ...}]}
```

Yıllık noktalar `basis: "annual"` taşır (alan yoksa annual sayılır). Grafikler ve yıllık tablo
(`/facts`) yıllık seriyi göstermeye devam eder; TTM noktası ayrıca `latest` olarak verilir.

## 3. Skor ve analiz seçimi

- Skor (`compute_score`) dinamik seri üzerinde çalışır: en son nokta TTM (veya yalnızca yıllık
  varsa yıllık). Yanıtta: `basis` (`"ttm"`/`"annual"`), `as_of_period` (= `E_Q` veya `E_A`),
  `as_of_filing` (`{"form", "accession_no", "filing_date"}`), `data_through` (= `as_of_period`).
  Ağırlıklar ve eşikler değişmez; yöntem sürümü `v1-2026-11` → `v2-2026-11-ttm`.
- Metin tonu bileşeni: **en son** analiz edilmiş başvurunun MD&A'sı (10-K Item 7 veya daha yeni
  10-Q Part I Item 2) + en son 10-K'nın risk bölümü. Risk farkı iki son 10-K arasında kalır.
- Analize seçilen başvurular: son iki 10-K + son 10-K'dan sonra yayımlanmış **en son** 10-Q
  (mevcut kural; korunur). Hızlı mod (`FINSIGHT_ANALYZE_10Q=0`) TTM'yi kapatmaz (TTM yalnızca
  companyfacts'ten gelir), yalnızca 10-Q metin analizini kapatır.

## 4. Tazelik (dinamik yapı)

`GET /api/companies/{ticker}/freshness` (yeni, v1.4):

```json
{"checked_at": "2026-11-23T09:30:00Z",
 "sec_latest": {"form": "10-Q", "filing_date": "2026-10-28", "report_date": "2026-09-30",
                "accession_no": "0000950170-26-000123"},
 "analyzed_latest": {"form": "10-K", "filing_date": "2026-07-30", "report_date": "2026-06-30",
                     "accession_no": "..."},
 "new_filings": [{"form": "10-Q", "filing_date": "2026-10-28", "report_date": "2026-09-30",
                  "accession_no": "..."}],
 "stale": true, "error": null}
```

- Yalnızca 10-K, 10-Q (ve /A) dikkate alınır; 8-K listelenir ama `stale` yapmaz.
- SEC'e en fazla bir istek (submissions, önbelleği atlayarak `fresh=True`); aynı şirket için
  sonuç süreç içinde 15 dk tutulur. SEC'e ulaşılamazsa `stale: null`, `error: "<Türkçe neden>"`,
  HTTP 200.
- Arayüz `stale: true` ise şirket sayfasında "Yeni rapor yayımlandı: 10-Q (28 Eki 2026) —
  Güncelle" bandı gösterir; düğme mevcut `POST /ingest` (artımlı: yalnızca yeni başvurular analiz
  edilir) çağırır.
- `python -m finsight.refresh [--all | TICKER ...]`: kayıtlı şirketlerden yalnızca eskimiş olanları
  günceller (Windows Görev Zamanlayıcı ile periyodik çalıştırılabilir).
- İsteğe bağlı: `FINSIGHT_AUTO_REFRESH_MINUTES` (varsayılan kapalı/0) → API süreci içinde arka
  planda eskimiş şirketleri sırayla günceller (tek iş parçacığı, mevcut iş kilidini kullanır).

## 5. SEC önbellek politikası (NFR-01…03, performans)

`FileCache` URL sınıfına göre süre uygular:

| URL | Süre | Gerekçe |
| --- | --- | --- |
| `/Archives/edgar/data/...` (belgeler) | süresiz | Yayımlanmış belge değişmez; günlük yeniden indirme gereksizdi |
| `submissions/*.json`, `companyfacts/*.json` | `FINSIGHT_SEC_JSON_TTL` sn (varsayılan 3600) | Yeni başvurular saatler içinde görünsün |
| `company_tickers.json` | 24 saat | Nadiren değişir |

`SecClient.get_bytes/get_json(url, *, fresh: bool = False)`, `submissions(cik, *, fresh=False)`,
`companyfacts(cik, *, fresh=False)`: `fresh=True` önbelleği okumaz ama başarılı yanıtı yazar.
Testlerdeki sahte istemciler `fresh` anahtar argümanını kabul eder (yok sayabilir).

## 6. API değişiklikleri (v1.4, yalnızca ekleme)

- `/score` ve `/overview.score`: `basis`, `as_of_filing`, `data_through` alanları; `method_version`.
- `/overview`: `key_metrics` öğelerinde `basis` ve TTM değeri (varsa); `latest_filing` (en son
  analiz edilen başvuru).
- `/ratios`: her oran için en son nokta TTM ise `basis: "ttm"`; dönem listesi TTM dönemini de
  içerir (`period_end = E_Q`, etiket "TTM 2026-09-30").
- `/freshness`: yeni (bkz. 4).
- `/health`: `sec.json_ttl_seconds`, `auto_refresh_minutes`.
Mevcut alanların adı ve anlamı değişmez.
