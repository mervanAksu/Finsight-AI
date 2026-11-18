---
name: sec-edgar
description: SEC EDGAR'a (submissions, companyfacts, belge arşivi) erişen veya 10-K/10-Q/8-K yapısını ayrıştıran FinSight AI kodu yazılırken okunur. Erişim kuralları, URL kalıpları, XBRL tuzakları ve rapor bölüm yapısını içerir.
---

# SEC EDGAR ile çalışma

## Erişim kuralları (değişmez)
- Tüm istekler `finsight.sec_client.SecClient` üzerinden yapılır. Doğrudan `httpx`/`requests` yasak.
- Tanımlayıcı User-Agent: `"<Uygulama> <Ad Soyad> <e-posta>"` (`SEC_USER_AGENT` ortam değişkeni).
- SEC adil erişim kuralı: kullanıcı başına en fazla 10 istek/sn. Proje sınırı 8 (`RateLimiter`).
- 429/5xx → 1, 2, 4 sn bekleyerek en fazla 3 yeniden deneme; 404 yeniden denenmez.
- data.sec.gov CORS desteklemez: tarayıcıdan doğrudan çağrılmaz, hep backend'den.
- Kaynaklar: https://www.sec.gov/about/developer-resources ,
  https://www.sec.gov/search-filings/edgar-application-programming-interfaces

## URL kalıpları
| Amaç | URL |
| --- | --- |
| Ticker → CIK | `https://www.sec.gov/files/company_tickers.json` |
| Başvuru listesi | `https://data.sec.gov/submissions/CIK{10 hane}.json` |
| XBRL değerleri | `https://data.sec.gov/api/xbrl/companyfacts/CIK{10 hane}.json` |
| Belge | `https://www.sec.gov/Archives/edgar/data/{CIK sıfırsız}/{accession tiresiz}/{primaryDocument}` |

`filings.recent` sütunlu dizilerdir (aynı indeks = aynı başvuru); daha eski başvurular
`filings.files` altındaki ek JSON dosyalarındadır.

## companyfacts tuzakları
1. Aynı değer sonraki raporlarda karşılaştırma sütunu olarak tekrar görünür → aynı
   (start, end) için en son `filed` tarihli kayıt seçilir.
2. `fy` alanı değerin değil, onu raporlayan başvurunun mali yılıdır → dönem `end` ile belirlenir.
3. 10-K içinde çeyreklik değerler de vardır → süreli kalemlerde 330–400 günlük dönem.
4. Bilanço (anlık) kalemlerinde `start` yoktur.
5. Şirketler farklı/eski etiket kullanabilir → öncelik sıralı yedek etiket listesi
   (bkz. `financial-analysis` becerisi).
6. Mali yıl takvim yılı olmayabilir (ör. Microsoft Haziran, Apple Eylül) → arayüzde
   dönem bitiş tarihi gösterilir.

## Rapor bölüm yapısı
| Form | Risk faktörleri | Yönetim değerlendirmesi (MD&A) |
| --- | --- | --- |
| 10-K | Part I, Item 1A ("Risk Factors"), Item 1B'ye kadar | Part II, Item 7, Item 7A'ya (yoksa Item 8'e) kadar |
| 10-Q | Part II, Item 1A | Part I, Item 2, Item 3'e kadar |
| 8-K | — | `items` alanındaki madde numaraları (2.02 faaliyet sonuçları, 5.02 yönetici değişikliği …) |

Başlıklar içindekiler tablosunda da geçer: ilk eşleşme genelde içindekilerdir. Kural
(`text_extract.py`): yalnızca **başlık benzeri** satırlar aday olur (≤ 150 karakter, başlıktan
sonra virgül/küçük harfle devam eden cümle yok, önceki satırın devamı değil — "see Item 1A of
this report" gibi çapraz atıflar elenir). Bitişine 50 kelimeden az olan aday içindekiler
sayılır; kalanlardan **ilki** seçilir, hiçbiri yoksa en uzun adaya düşülür. Küçük şirketler (smaller reporting company) Item 1A'yı
"Not applicable" diye geçebilir; bu durumda bölüm boş kabul edilir.
