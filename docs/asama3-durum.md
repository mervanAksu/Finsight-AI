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
