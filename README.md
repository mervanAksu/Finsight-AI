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

## Çalıştırma

### A. Python ile (Docker gerekmez)

Python 3.11 veya üstü gerekir. Veritabanı olarak otomatik bir SQLite dosyası (`finsight.db`)
kullanılır; önceki sürümden kalan dosya ilk açılışta yeni sütun ve tablolarla yükseltilir.

**Windows (PowerShell):**

```powershell
cd finsight-ai\backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:SEC_USER_AGENT = "FinSight-AI Ad Soyad eposta@ornek.com"
python -m finsight.ingest MSFT --analyze      # veri çek + metin analizi + skor
uvicorn finsight.api:app --reload
```

**macOS / Linux:**

```bash
cd finsight-ai/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export SEC_USER_AGENT="FinSight-AI Ad Soyad eposta@ornek.com"
python -m finsight.ingest MSFT --analyze      # veri çek + metin analizi + skor
uvicorn finsight.api:app --reload
```

- `python -m finsight.ingest AAPL MSFT` yalnızca finansal veriyi çeker; `--analyze` ayrıca son
  iki 10-K ile son 10-Q'yu indirir, risk / MD&A bölümlerini ayıklar, cümle duygu analizi,
  gömme (soru-cevap için), risk bölümü karşılaştırması ve skor üretir. Çıktıda hangi modellerin
  kullanıldığı (FinBERT mi yedek sözlük mü, LLM var mı) yazılır. `--force` önceden analiz
  edilmiş başvuruları yeniden işler.
- Şirketler arayüzden de eklenebilir (arka plan işi: `POST /api/companies/{ticker}/ingest`).

**Arayüz:** `cd frontend && npm install && npm run build` ile `frontend/dist` üretildiyse
arayüz API ile aynı adresten sunulur: <http://127.0.0.1:8000>. Geliştirme için
`npm run dev` → <http://127.0.0.1:5173> (API'ye `http://127.0.0.1:8000` üzerinden bağlanır).
Swagger: <http://127.0.0.1:8000/docs>.

**İsteğe bağlı ML paketleri (FinBERT, çok dilli gömme):** kurulmazsa sistem yedek modellerle
(`lexicon-fallback`, `hashing-fallback`) çalışır ve bunu `/api/health` yanıtında belirtir.

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-ml.txt     # ilk çalıştırmada ~0,9 GB model indirilir
```

**LLM anahtarı (isteğe bağlı):** soru-cevap LLM olmadan da çalışır (ilgili alıntıları kaynak
numarasıyla gösterir). LLM ile özet cevap için `.env` veya ortamda
`ANTHROPIC_API_KEY=...` (ya da `OPENAI_API_KEY=...`, yerel sunucu için `OPENAI_BASE_URL`)
tanımlayın; model `FINSIGHT_LLM_MODEL` ile değiştirilebilir. Tüm değişkenler: `.env.example`.

### B. Docker Compose ile (PostgreSQL + pgvector, arayüz dahil)

```bash
cd finsight-ai
cp .env.example .env          # Windows: copy .env.example .env  → SEC_USER_AGENT'ı düzenle
docker compose up -d --build  # ML paketleriyle: INSTALL_ML=1 docker compose up -d --build
docker compose run --rm api python -m finsight.ingest MSFT --analyze
```

Arayüz: <http://localhost:8000> · API: <http://localhost:8000/api/health> ·
Swagger: <http://localhost:8000/docs>

### Uç noktalar (tümü `/api` önekli; şekiller: `docs/api-contract.md`)

| Yöntem | Yol | Açıklama |
| --- | --- | --- |
| GET | `/api/health` | Durum, sürüm, kullanılan modeller |
| GET | `/api/companies` | İşlenmiş şirketler (skor özetiyle) |
| GET | `/api/companies/search?q=micro` | SEC ticker listesinde arama |
| POST | `/api/companies/{ticker}/ingest` | Arka planda veri çekme + analiz (202, iş kimliği) |
| GET | `/api/jobs/{job_id}` | İş durumu ve ilerleme |
| GET | `/api/companies/{ticker}/overview` | Özet: skor, ana kalemler, ton, risk değişimi |
| GET | `/api/companies/{ticker}/facts?metric=revenue` | Finansal kalemler ve kaynakları |
| GET | `/api/companies/{ticker}/ratios` | 10 finansal oran (son 3 mali yıl) |
| GET | `/api/companies/{ticker}/score` | Açıklanabilir 100 puanlık skor |
| GET | `/api/companies/{ticker}/filings?form=10-K` | Başvuru listesi (8-K madde etiketleriyle) |
| GET | `/api/companies/{ticker}/sentiment` | Risk ve MD&A bölümlerinin tonu |
| GET | `/api/companies/{ticker}/risk-changes` | İki 10-K arasında risk paragrafı farkı |
| POST | `/api/companies/{ticker}/ask` | Kaynak gösteren soru-cevap |
| GET | `/api/compare?tickers=AAPL,MSFT` | 2–4 şirket karşılaştırması |
| GET | `/api/companies/{ticker}/export.csv` | CSV dışa aktarım (UTF-8 BOM) |

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
