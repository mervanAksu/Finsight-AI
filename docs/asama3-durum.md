# Aşama 3 durum raporu (18 Kasım 2026)

3. ara rapor (Uygulama ve Geliştirme) için çalışma notu. Ne yapıldı, nerede doğrulandı,
neler henüz doğrulanmadı ve tasarımdan hangi noktalarda ayrılındı.

## Gereksinim karşılama

| Kimlik | Gereksinim | Durum | Doğrulama |
| --- | --- | --- | --- |
| FR-01 | Ticker / ad / CIK ile arama | Tamamlandı | Birim testleri; arayüzde otomatik tamamlama |
| FR-02 | 10-K, 10-Q, 8-K listesi | Tamamlandı | Birim testleri; gerçek SEC ile MSFT (Aşama 1) |
| FR-03 | Belge indirme ve saklama | Tamamlandı | Sahte SEC ile uçtan uca (kullanıcı bilgisayarında koşacak) |
| FR-04 | XBRL kalemleri (12 kalem, türetmeler) | Tamamlandı | Birim testleri + bağımsız hesap |
| FR-05 | Dönemsel değişim (YoY) | Tamamlandı | 52–53 haftalık yıl ve eksik yıl testleri |
| FR-06 | 10 finansal oran + formül | Tamamlandı | Bağımsız hesapla birebir |
| FR-07 | Item 1A / 7 ayıklama | Tamamlandı | Sentetik EDGAR HTML + çapraz atıf senaryoları |
| FR-08 | FinBERT duygu analizi | Tamamlandı (yedek sözlük modeliyle) | Bulutta yedek model; FinBERT kullanıcı bilgisayarında |
| FR-09 | Risk bölümü karşılaştırma | Tamamlandı | Birim testleri |
| FR-10 | Kaynak gösteren soru-cevap | Tamamlandı | Sahte LLM ile; gerçek LLM anahtarla denenecek |
| FR-11 | 100 puanlık açıklanabilir skor | Tamamlandı | Bağımsız hesapla birebir |
| FR-12 | Zaman serisi grafikleri | Tamamlandı | Ekran görüntüleri (`docs/ekran-goruntuleri/asama3-demo`) |
| FR-13 | Şirket karşılaştırma | Tamamlandı | Ekran görüntüsü |
| FR-14 | 8-K madde etiketleri | Tamamlandı | Birim testi |
| FR-15 | Arka plan veri çekme işi | Tamamlandı | Sahte SEC ile (kullanıcı bilgisayarında koşacak) |
| FR-16 | Dışa aktarma | CSV tamamlandı, PDF yok | Birim testi |

## Test durumu

- Bulut ortamında: **349 test geçti, 3 dosya atlandı** (`test_api.py`, `test_jobs.py`,
  `test_repository.py`: fastapi/sqlalchemy bu ortamda kurulamadı). `ruff` temiz.
- Arayüz: esbuild derlemesi hatasız; React tip şimiyle `tsc --strict` 0 hata; Playwright ile
  1440 ve 820 px'te konsol/sayfa hatası 0. Backend'in gerçek saf kodunun ürettiği JSON ile
  sözleşme denetimi: 38 tip / 26 yanıt, 0 uyumsuzluk.
- İki bağımsız kod inceleme turu yapıldı; 1. turda 2 yüksek, 7 orta, 11 düşük; 2. turda 9
  kesin, 13 olası bulgu raporlandı ve kapatıldı.

## Kullanıcı bilgisayarında yapılacak doğrulamalar

```powershell
cd backend
python -m pip install -r requirements.txt
python -m pytest -q                       # atlanan 3 dosya ilk kez burada koşacak
$env:SEC_USER_AGENT = "FinSight-AI Ad Soyad eposta@ornek.com"
python -m finsight.ingest MSFT --analyze  # gerçek SEC verisiyle analiz
cd ..\frontend; npm install; npm run typecheck; npm run build
cd ..\backend; uvicorn finsight.api:app   # http://127.0.0.1:8000
```
İsteğe bağlı: `pip install -r requirements-ml.txt` (FinBERT ve çok dilli gömme modeli, ~1 GB
indirme) ve `.env` içinde `ANTHROPIC_API_KEY` (soru-cevapta LLM cevabı).

## Tasarımdan sapmalar ve gerekçeleri

| Tasarım | Uygulama | Gerekçe |
| --- | --- | --- |
| pgvector | Vektörler veritabanında ikili (float32) saklanıyor, benzerlik numpy ile | Docker'sız SQLite kurulumunda da çalışsın; şirket başına birkaç yüz parça için yeterli. pgvector Aşama 4 seçeneği. |
| Next.js + Recharts | React 19 + esbuild + d3 ile elle SVG grafikler | Daha az bağımlılık, tek komutla derleme; API ile aynı sunucudan sunuluyor |
| Alembic göçleri | `create_all` + küçük sütun ekleyici | Aşama 4'e bırakıldı |
| `financial_ratio` tablosu | Oranlar her istekte kalemlerden hesaplanıyor | Kalemlerle tutarsızlık riskini önlemek için |
| Soru-cevapta yalnız LLM | LLM yoksa çıkarımsal (alıntı) cevap | Anahtar olmadan da sistem çalışsın |
| Tek dilli gömme modeli | Çok dilli `paraphrase-multilingual-MiniLM-L12-v2` | Sorular Türkçe, raporlar İngilizce |

## Bilinen sınırlar

- ML paketleri kurulu değilse yedek modeller (sözlük duygu modeli, hashing gömme) kullanılır;
  hashing yedeğinde LLM yokken Türkçe sorular çoğunlukla "bulunamadı" döner.
- Skor ağırlıkları ve eşikleri doğrulanmış bir model değildir; Aşama 4'te duyarlılık analizi.
- Bölüm ayıklama gerçek EDGAR belgelerinin tüm varyasyonlarında denenmedi.
- PDF dışa aktarma yok; tek süreçli arka plan işi (birden çok worker için kuyruk gerekir).

## Performans ölçümleri (kullanıcı bilgisayarı, 19 Kasım 2026)

ASUS X515JF, CPU (4 iş parçacığı), torch 2.15.0+cpu, MSFT: iki 10-K + bir 10-Q, 2.155 cümle.

| Ölçüm | Önce | Sonra |
| --- | --- | --- |
| Tam analiz (önbelleksiz) | 349 sn | 193,7 sn (−%44) |
| Duygu analizi hızı | ~7 cümle/sn | 15,0 cümle/sn (2,1x) |
| Yeniden analiz (cümle önbelleği) | 349 sn | 60,9 sn (−%83), duygu 0 sn |

Skor (83,9), risk farkı (25/8/25/52) ve cümle sayıları iyileştirme öncesi ve sonrası aynı.

`bench_sentiment` (500 cümle; token uzunluğu medyan 30, en uzun 139):

| Yapılandırma | Cümle/sn | Hızlanma | Eski yöntemle uyum |
| --- | --- | --- | --- |
| (a) Eski: pipeline, sırasız, batch 16 | 7,2 | 1,00x | %100 |
| (b) Yeni: sıralı dinamik dolgu, batch 32 | 14,8 | 2,06x | %100 |
| (c) Yeni + int8 kuantizasyon | 35,9 | 5,01x | %70,6 |
| (d) Yeni + max_length 256 | 14,1 | 1,97x | %100 |

Karar: (b) varsayılan. int8 kuantizasyon 5 kat hızlı olmasına rağmen reddedildi: olumsuz
cümlelerin %74'ü (164'ün 121'i) nötr olarak etiketlendi. Bu sistematik kayma risk ve MD&A
tonunu olduğundan olumlu gösterir ve skordaki metin tonu bileşenini yukarı çeker. max_length
256 kazanç sağlamadı (256 token'ı aşan cümle yok; sıralı dolgu zaten gereksiz dolguyu önlüyor).
