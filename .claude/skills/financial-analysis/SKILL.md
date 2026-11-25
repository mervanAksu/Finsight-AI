---
name: financial-analysis
description: FinSight AI'de XBRL kalemleri, finansal oranlar, dönemsel değişim ve 100 puanlık açıklanabilir skor hesaplanırken okunur. Etiket eşlemeleri, türetme kuralları, oran formülleri ve skor eşikleri bu dosyadaki tanımla birebir uygulanır.
---

# Finansal analiz kuralları

Bu dosya hesaplamaların **tek doğruluk kaynağıdır**. Kod ve testler bununla tutarlı olmalı.
Değişiklik gerekiyorsa önce bu dosya güncellenir.

## 1. Kalemler ve XBRL etiketleri (us-gaap, birim USD)

| metric | Tür | Öncelik sıralı etiketler | Yedek türetme |
| --- | --- | --- | --- |
| revenue | süreli | RevenueFromContractWithCustomerExcludingAssessedTax, Revenues, SalesRevenueNet, RevenueFromContractWithCustomerIncludingAssessedTax | — |
| cost_of_revenue | süreli | CostOfGoodsAndServicesSold, CostOfRevenue, CostOfGoodsSold | — |
| gross_profit | süreli | GrossProfit | revenue − cost_of_revenue |
| operating_income | süreli | OperatingIncomeLoss | — |
| net_income | süreli | NetIncomeLoss, ProfitLoss | — |
| operating_cash_flow | süreli | NetCashProvidedByUsedInOperatingActivities, NetCashProvidedByUsedInOperatingActivitiesContinuingOperations | — |
| capex | süreli | PaymentsToAcquirePropertyPlantAndEquipment, PaymentsToAcquireProductiveAssets | — |
| total_assets | anlık | Assets | — |
| current_assets | anlık | AssetsCurrent | — |
| current_liabilities | anlık | LiabilitiesCurrent | — |
| total_liabilities | anlık | Liabilities | LiabilitiesAndStockholdersEquity − (StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest, yoksa StockholdersEquity) |
| stockholders_equity | anlık | StockholdersEquity, StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest | — |

- Yalnızca `form ∈ {10-K, 10-K/A}` kayıtları; süreli kalemde 330–400 gün; anlık kalemde `start` yok.
- **Birincil etiket kuralı:** her kalem için birincil etiket, adaylar arasında en son
  `period_end`'e sahip etikettir (eşitlikte tablodaki öncelik sırası). Önce birincil etiketin
  tüm dönemleri alınır; birincilde olmayan dönemler için öncelik sırasıyla diğer etiketlere
  düşülür. (Gerekçe: etiket geçişinde şirket eski yılı yeni etiketle yeniden düzenlenmiş
  karşılaştırma değeri olarak raporlar; eski etiketteki farklı tanımlı değer yerine aynı
  tanımlı değer kullanılır ve YoY tutarlı olur.)
- Aynı dönem için: önce birincil etiket, sonra öncelik sırası, aynı etikette en son `filed`.
- Türetilmiş değerler `tag = "derived"` ve `derived_from` (kullanılan etiketler) ile işaretlenir;
  kaynak accession, türetmede kullanılan **en son** başvurununkidir.
- Ortalama hesapları için bilanço kalemleri bir yıl fazla (son 4 dönem sonu) çekilir.

## 2. Oranlar

Değerler **ham oran** olarak saklanır (0.253 = %25,3). `unit`: `"percent"` veya `"times"`.
Dönemler: revenue ∪ net_income ∪ total_assets mali yıl sonlarının birleşimi; birbirine 30
günden yakın tarihler tek dönemdir (en son tarih tutulur), son `periods` dönem alınır.
Her kalem dönemin `period_end`'ine ±7 gün toleransla (en yakın) eşlenir (52–53 haftalık yıl,
gelir 28 Aralık / bilanço 31 Aralık). Ortalama = (dönem sonu + bir önceki mali yıl sonu) / 2
(önceki = 330–400 gün önce); önceki dönem yoksa dönem sonu değeri kullanılır ve `note`
alanına "ortalama yerine dönem sonu değeri" yazılır. ROE'de önceki dönem özkaynağı ≤ 0 ise
ortalama yerine dönem sonu değeri kullanılır; `note`: "önceki dönem özkaynağı negatif;
ortalama yerine dönem sonu değeri".

| key | Ad | Formül | unit | Yüksek iyi mi |
| --- | --- | --- | --- | --- |
| gross_margin | Brüt kâr marjı | gross_profit / revenue | percent | evet |
| operating_margin | Faaliyet kâr marjı | operating_income / revenue | percent | evet |
| net_margin | Net kâr marjı | net_income / revenue | percent | evet |
| roa | Aktif kârlılığı (ROA) | net_income / ort. total_assets | percent | evet |
| roe | Özkaynak kârlılığı (ROE) | net_income / ort. stockholders_equity | percent | evet |
| current_ratio | Cari oran | current_assets / current_liabilities | times | evet |
| debt_to_equity | Borç / özkaynak | total_liabilities / stockholders_equity | times | hayır |
| liabilities_to_assets | Yükümlülük / varlık | total_liabilities / total_assets | percent | hayır |
| ocf_to_net_income | Nakit akışı kalitesi | operating_cash_flow / net_income | times | evet |
| fcf_margin | Serbest nakit akışı marjı | (operating_cash_flow − capex) / revenue | percent | evet |

Hesaplanamama kuralları (değer `None`, `status = "missing"`, `reason` dolu):
- Girdi eksik → "eksik veri: <metric>".
- Payda 0 → "payda sıfır".
- roe ve debt_to_equity'de özkaynak ≤ 0 → "özkaynak negatif veya sıfır; oran anlamlı değil".
- ocf_to_net_income'da net_income ≤ 0 → "net kâr sıfır veya negatif; oran anlamlı değil".
- revenue = 0 → "payda sıfır"; revenue < 0 → "gelir negatif; marj oranı anlamlı değil".
- Birden fazla girdi eksikse neden virgülle birleşir: "eksik veri: operating_cash_flow, capex".
- Eksik oranlarda da bulunan girdiler `inputs` içinde listelenir (izlenebilirlik).

Dönemsel değişim (YoY): `(cur − prev) / |prev|`; `prev` 0 veya yoksa `None`. İki öğenin
`period_end` farkı 330–400 gün değilse (eksik yıl, mali yıl sonu değişimi) de `None`.
(Not: mevcut `xbrl.yoy_changes` yüzde döndürür ve negatif tabanda `None` verir; yeni kod
oran döndürür ve mutlak değerle böler. Bu fark API'de belgelenir.)

## 3. Skor (100 puan, sürüm `v1-2026-11`)

Her bileşen 0–100 arası puanlanır; toplam = Σ(ağırlık × puan) / Σ(kullanılabilen ağırlık).
Hesaplanamayan bileşen dışarıda kalır, kalanların ağırlığı oranlanır ve bu durum
`missing_components` listesinde belirtilir. Kullanılabilen ağırlık toplamı 50'nin altındaysa
toplam skor üretilmez (`total = None`).

Doğrusal eşleme `lin(x, a, b)`: x ≤ a → 0, x ≥ b → 100, arası doğrusal (a > b ise ters yön).
Bileşen puanı, içindeki kullanılabilen göstergelerin ortalamasıdır. Tüm göstergeler son
mali yıl içindir.

| Bileşen (key) | Ağırlık | Göstergeler ve eşleme |
| --- | --- | --- |
| profitability (Kârlılık) | 25 | net_margin lin(0, 0.20); operating_margin lin(0, 0.25); roa lin(0, 0.15); roe lin(0, 0.25) |
| leverage (Kaldıraç ve borç) | 20 | debt_to_equity lin(3.0, 0.5) (ters); liabilities_to_assets lin(0.9, 0.3) (ters); özkaynak ≤ 0 ise bileşen puanı 0 |
| liquidity (Likidite) | 15 | current_ratio lin(0.5, 2.0) |
| cash_quality (Nakit akışı kalitesi) | 15 | ocf_to_net_income lin(0, 1.2) — net kâr ≤ 0 ise: OCF > 0 → 50, değilse 0; fcf_margin lin(−0.05, 0.15) |
| growth (Büyüme) | 10 | revenue YoY lin(−0.10, 0.15); net_income YoY lin(−0.20, 0.20) — önceki yıl net kârı ≤ 0 ise net kâr büyüme göstergesi yok (zarardan kâra geçişte yüzde anlamlı değil; `reason` dolu, bileşen yalnızca gelir büyümesiyle hesaplanır) |
| text_tone (Metin tonu) | 10 | son 10-K MD&A net tonu lin(−0.40, 0.20); MD&A yoksa risk bölümü net tonu lin(−0.80, −0.20) |
| risk_change (Risk değişimi) | 5 | son iki 10-K arasında eklenen risk paragrafı sayısı lin(15, 0) (ters) |

Net ton = (olumlu cümle − olumsuz cümle) / toplam cümle.

Bant (gösterim için, derecelendirme değildir), 1 ondalığa **yuvarlanmış** toplamdan:
≥ 75 "Güçlü", 60 ≤ x < 75 "Dengeli", 45 ≤ x < 60 "İzlenmeli", < 45 "Zayıf".
Toplam, yuvarlanmamış bileşen puanlarından hesaplanır; gösterge nesnelerinde ek olarak
`reason` ve `threshold` (ör. "%0–%20") alanları bulunur.

Her bileşen için `explanation` Türkçe tek cümle: hangi gösterge, değeri ve puana etkisi.
Örnek: "Net kâr marjı %24,3 (eşik %0–%20 → 100 puan)." Bu ağırlık ve eşikler doğrulanmış
bir model değildir; başlangıç önerisidir ve 4. aşamada duyarlılık analizine tabi tutulur.

## 4. Son on iki ay (TTM) — dinamik seri (Aşama 4, yöntem sürümü `v2-2026-11-ttm`)

Kaynak: `docs/asama4-sozlesme.md` §2–3. Kod: `xbrl.extract_ttm`, `xbrl.build_dynamic`,
`ratios.compute_ratios(..., dynamic=...)`, `scoring.compute_score(..., dynamic=...)`.
Tüm tarih eşleşmeleri ±7 gün toleranslıdır (52–53 haftalık yıllar).

- `E_A`: revenue ∪ net_income ∪ total_assets yıllık serisinin en son dönem sonu.
- `E_Q`: aynı kalemlerin etiketlerinde `form ∈ {10-Q, 10-Q/A}` olup `E_A`'dan 30–300 gün
  sonra biten en son dönem sonu. Yoksa dayanak yıllıktır (`basis: "annual"`).
- **Süreli kalem:** `TTM = A + YTD_cur − YTD_prior`, üç parça da **yıllık serinin birincil
  etiketinden** (§1 birincil etiket kuralı). Birincil etikette parça eksikse o kalem için TTM
  yoktur; başka etikete GEÇİLMEZ (A ile YTD farklı tanımlı olabilir). Neden: "TTM parçaları
  birincil etiket (X) altında eksik; Y etiketine geçilmedi" (Y'de parçalar varsa), aksi
  halde parça nedeni + "(etiket X)".
  - `A`: `E_A`'da biten 330–400 günlük 10-K değeri.
  - `YTD_cur`: başlangıcı ≈ `E_A + 1 gün`, bitişi ≈ `E_Q`. 2./3. çeyrekte 3 aylık değer
    (başlangıcı `E_Q − 3 ay`) bu süzgece takılır; 1. çeyrekte 3 ay = YTD.
  - `YTD_prior`: başlangıcı ≈ `A.start`, bitişi ≈ `YTD_cur.end − 1 yıl`.
  - Aynı dönemi raporlayan birden çok başvuru (10-Q/A, sonraki 10-Q'daki yeniden düzenlenmiş
    karşılaştırma) → en son `filed`. Gerekçe: yaygın LTM uygulaması en son raporlanan
    (yeniden düzenlenmiş, karşılaştırılabilir) değerleri kullanır; şirket önceki dönemi yeni
    muhasebe politikası / sınıflandırmayla yeniden sunduğunda TTM'nin üç parçası aynı tanımla
    hesaplanmış olur. Aynı dönem için daha önce FARKLI değer raporlanmışsa parçada
    `restated: true` ve `original_value` (ilk raporlanan değer) gösterilir.
  - Parça eksikse TTM üretilmez (neden: "yıllık değer (A) yok", "mali yıl başından bugüne
    (YTD) değeri yok", "önceki yılın aynı YTD dönemi yok"); o kalem için yıllık değer kullanılır
    ve neden `reasons` / `basis_notes`'a yazılır. Oranlama, yıllıklaştırma, uydurma yok.
- **Anlık kalem:** `E_Q` tarihli 10-Q bilançosu; ortalama için önceki değer `E_Q − 1 yıl`
  tarihli bilanço (önceki yılın aynı çeyreği; 330–400 gün kuralı). İkisi de birincil
  etiketten; önceki değer yoksa None ve oranda "ortalama yerine dönem sonu değeri" notu.
- **Dayanak:** `basis = "ttm"` ancak gelir veya net kârdan en az biri gerçekten TTM ise.
  Yalnızca bilanço kalemleri E_Q'ya geçtiyse dayanak yıllık kalır (skor / oran sütunu E_A)
  ve `note` yazılır.
- **Önceki TTM** (büyüme): `A_önceki + YTD_prior − YTD_prior2`. Yoksa büyüme yıllık YoY'a
  düşer, göstergenin `note` alanı: "TTM büyümesi hesaplanamadı; yıllık YoY kullanıldı".
- **Türetme:** brüt kâr TTM yoksa gelir TTM − maliyet TTM (`tag = "derived"`); toplam
  yükümlülük `E_Q`'da yoksa `LiabilitiesAndStockholdersEquity − özkaynak` (10-Q; her bileşen
  kendi yıllık birincil etiketinden). Türetilmiş TTM'de `derived_from` tam bileşen
  sözlükleridir: `{role: "component", metric, tag, accession_no, form, period_start,
  period_end, value, parts?}` (NFR-06).
- **İzlenebilirlik (NFR-06):** TTM noktası `basis: "ttm"`, `form`/`accession_no` YTD_cur'un
  başvurusu, `derived_from: [{role: annual|ytd_current|ytd_prior, accession_no, form, tag,
  period_start, period_end, value}]`. Yıllık noktalar `basis: "annual"`.
- **Oranlar:** yıllık dönemler değişmez; `periods` sonuna `E_Q` sütunu eklenir (etiket
  "TTM 2026-09-30"). TTM sütununda yıllığa düşen girdi varsa `note`: "TTM yok, yıllık değer
  kullanıldı: <kalem>".
- **Skor:** ağırlık ve eşikler §3 ile aynı (`weights_version` v1-2026-11); son dönem `E_Q`.
  Metin tonu: en son analiz edilen başvurunun MD&A'sı (10-K Item 7 veya daha yeni 10-Q Part I
  Item 2), risk tonu en son 10-K'nın Item 1A'sı; risk farkı iki son 10-K arasında.
  Yanıtta `basis`, `as_of_filing`, `data_through`, `method_version`.
- Veritabanında TTM noktaları `financial_fact.basis = "ttm"` ile, TTM üretilemeyen kalemlerin
  nedenleri `company.ttm_reasons` (JSON) ile saklanır. `/facts` `values` yıllık seridir; son
  nokta ayrı `latest` alanındadır. Artık üretilmeyen TTM satırları yeniden çekmede silinir.
