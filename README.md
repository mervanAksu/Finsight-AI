# FinSight AI — SEC Veri Çekme Prototipi (Aşama 1)

Verilen ABD şirketleri için SEC EDGAR'dan 10-K, 10-Q ve 8-K başvuru listesini ve son 3 mali
yılın temel finansal kalemlerini (XBRL companyfacts) çeker, veritabanına yazar ve iki REST uç
noktasıyla sunar.

> FinSight AI eğitim ve araştırma amaçlı bir prototiptir. Sunulan analizler yatırım tavsiyesi değildir.

## Ne yapıyor

| Özellik | Gereksinim |
| --- | --- |
| Ticker veya CIK ile şirket bulma | FR-01 |
| 10-K / 10-Q / 8-K başvuru listesi, belge bağlantısı | FR-02 |
| Gelir, net kâr, işletme nakit akışı, toplam varlık, toplam yükümlülük, özkaynak (son 3 yıl) | FR-04 |
| Yıllık yüzde değişim (YoY) | FR-05 |
| Her istekte tanımlayıcı User-Agent; yoksa program başlamaz | NFR-01 |
| En fazla 8 istek/sn (SEC sınırı 10) | NFR-02 |
| 429/5xx yanıtlarında 3 yeniden deneme | NFR-03 |
| Her değerin kaynağı: XBRL etiketi, form, accession numarası | NFR-06 |
| 24 saatlik dosya önbelleği, tekrar çalıştırmada mükerrer kayıt yok | — |

## Kurulum A: Python ile (en hızlı, Docker gerekmez)

Python 3.11 veya üstü gerekir. Veritabanı olarak otomatik bir SQLite dosyası kullanılır.

**Windows (PowerShell):**

```powershell
cd finsight\backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:SEC_USER_AGENT = "FinSight-AI Ad Soyad eposta@ornek.com"
python -m finsight.ingest AAPL MSFT KO --csv sonuc.csv
uvicorn finsight.api:app --reload
```

**macOS / Linux:**

```bash
cd finsight/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export SEC_USER_AGENT="FinSight-AI Ad Soyad eposta@ornek.com"
python -m finsight.ingest AAPL MSFT KO --csv sonuc.csv
uvicorn finsight.api:app --reload
```

Sonra tarayıcıda: <http://127.0.0.1:8000/docs> (Swagger arayüzü).

## Kurulum B: Docker Compose ile (PostgreSQL + pgvector)

```bash
cd finsight
cp .env.example .env          # Windows: copy .env.example .env  → sonra SEC_USER_AGENT'ı düzenle
docker compose up -d --build
docker compose run --rm api python -m finsight.ingest AAPL MSFT KO
```

API: <http://localhost:8000/docs>

## Uç noktalar

| Yöntem | Yol | Açıklama |
| --- | --- | --- |
| GET | `/health` | Servis durumu |
| GET | `/companies` | İşlenmiş şirketler |
| GET | `/companies/{ticker}/filings?form=10-K&limit=50` | Başvuru listesi |
| GET | `/companies/{ticker}/facts?metric=revenue` | Finansal kalemler ve kaynakları |

`metric` değerleri: `revenue`, `net_income`, `operating_cash_flow`, `total_assets`,
`total_liabilities`, `stockholders_equity`.

## Testler

```bash
cd backend
pytest -v --cov=finsight
```

Testler SEC'e gerçek istek atmaz; `tests/fixtures/` altındaki **sentetik** "Example Corp"
verisini kullanır (gerçek bir şirket değildir). Test edilen kurallar: User-Agent zorunluluğu,
hız sınırı (her 1 sn'de en fazla 8 istek), yeniden deneme, önbellek, form süzme, mükerrer XBRL
değerinden en son raporlananın seçilmesi, 10-K içindeki çeyrek değerlerin dışlanması, etiket
değişikliğinde yedek etikete geçiş, API yanıtları.

## Klasör yapısı

```
finsight-ai/
├── docker-compose.yml
├── .env.example
├── docs/
│   ├── raporlar/            # planlama belgesi ve ara raporlar (.md + .docx)
│   ├── ekran-goruntuleri/   # prototip çalıştırma ve Swagger ekran görüntüleri
│   └── kaynak/              # bölümün ara rapor şablonu, ilk proje taslağı
└── backend/
    ├── finsight/
    │   ├── config.py        # ortam değişkenleri, User-Agent kontrolü
    │   ├── rate_limiter.py  # 8 istek/sn sınırı
    │   ├── sec_client.py    # SEC'e tek erişim noktası (retry, önbellek)
    │   ├── edgar.py         # CIK, ticker, başvuru ayrıştırma
    │   ├── xbrl.py          # companyfacts → yıllık kalemler
    │   ├── pipeline.py      # bir şirket için veri toplama
    │   ├── models.py, db.py # company, filing, financial_fact tabloları
    │   ├── ingest.py        # komut satırı aracı
    │   └── api.py           # FastAPI uç noktaları
    └── tests/
```

## Bilinen sınırlar (Aşama 2'de ele alınacak)

- Tablolar `create_all` ile oluşturuluyor; Alembic göçleri Aşama 2'de eklenecek.
- Bankalar ve sigorta şirketlerinde bazı kalemler farklı etiketlerle raporlanır, boş gelebilir.
- Mali yılı Aralık dışında biten şirketlerde "yıl" dönem bitiş tarihine göre gösterilir.
- Belge metni (Item 1A / 7) indirme ve ayıklama Aşama 3'te (hafta 7).

## Kaynaklar

- SEC, [EDGAR Application Programming Interfaces](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- SEC, [Developer Resources — Fair Access](https://www.sec.gov/about/developer-resources)
