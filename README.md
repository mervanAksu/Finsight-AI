# FinSight AI — SEC Raporlarından Açıklanabilir Finansal Analiz (Aşama 3)

FinSight AI, seçilen bir ABD şirketinin SEC 10-K / 10-Q / 8-K raporlarından finansal verileri
çıkarır, dönemsel karşılaştırma yapar, risk ve ton (duygu) analizi yürütür, rapor hakkındaki
soruları kaynak göstererek cevaplar ve bileşenleri görünür bir finansal sağlık skoru üretir.
Backend (FastAPI) ve web arayüzü (React + TypeScript) tek sunucudan çalışır.

> FinSight AI eğitim ve araştırma amaçlı bir üniversite projesidir. Sunulan analizler
> **yatırım tavsiyesi değildir**; her yanıt ve ekran bu uyarıyı taşır.

## Özellikler

| Özellik | Gereksinim |
| --- | --- |
| Ticker, şirket adı veya CIK ile arama (aynı şirketin birden fazla ticker'ı, ör. GOOGL/GOOG, aynı kaydı açar) | FR-01 |
| 10-K / 10-Q / 8-K başvuru listesi ve belge bağlantıları | FR-02 |
| Son iki 10-K ve son 10-Q belgesinin indirilip saklanması (aynı belge tekrar indirilmez) | FR-03 |
| Gelir, net kâr, işletme nakit akışı, toplam varlık / yükümlülük, özkaynak — her değer XBRL etiketi, form ve accession numarasıyla | FR-04, NFR-06 |
| Yıllık yüzde değişim (YoY) | FR-05 |
| 10 finansal oran, formülüyle; eksik veri "hesaplanamadı" olarak döner, sıfır yazılmaz | FR-06 |
| Item 1A (Risk Faktörleri) ve Item 7 / 10-Q Item 2 (MD&A) bölüm ayıklama | FR-07 |
| Cümle düzeyinde duygu analizi (FinBERT; yoksa sözlük yedeği) | FR-08 |
| İki 10-K arasında eklenen / çıkarılan / değişen risk paragrafları | FR-09 |
| Kaynak gösteren soru-cevap (RAG); kaynak yoksa "raporlarda bulunamadı" | FR-10, NFR-07 |
| Bileşenleri ve ağırlıkları görünür 100 puanlık skor | FR-11 |
| Zaman serisi grafikleri (gelir, net kâr, borç, oranlar) | FR-12 |
| 2–4 şirketi yan yana karşılaştırma | FR-13 |
| 8-K olay maddeleri (ör. 2.02, 5.02) Türkçe etiketleriyle | FR-14 |
| Arayüzden arka plan veri çekme işi ve ilerleme takibi | FR-15 |
| CSV dışa aktarma (Excel uyumlu, UTF-8 BOM) | FR-16 |
| SEC kuralları: tanımlayıcı User-Agent zorunlu, en fazla 8 istek/sn, 429/5xx'te üstel geri çekilme | NFR-01…03 |
| Türkçe / İngilizce arayüz | NFR-11 |
| Yatırım tavsiyesi yok: al/sat/tut soruları reddedilir, yalnızca rapor olguları verilir | NFR-12 |

## Klasör yapısı

```
finsight-ai/
├── Dockerfile               # tek imaj: derlenmiş arayüz + FastAPI
├── docker-compose.yml       # api + PostgreSQL
├── .env.example             # tüm ayarlar (kopyalayıp .env yapın)
├── docs/
│   ├── api-contract.md      # API sözleşmesi (backend ↔ arayüz)
│   ├── entegrasyon-plani.md # katmanlar, tablolar, analiz hattı
│   ├── modul-arayuzleri.md  # saf modüllerin fonksiyon imzaları
│   ├── raporlar/            # planlama belgesi ve ara raporlar
│   └── ekran-goruntuleri/
├── backend/
│   ├── requirements.txt     # çekirdek bağımlılıklar
│   ├── requirements-ml.txt  # isteğe bağlı: torch, transformers, sentence-transformers
│   ├── finsight/
│   │   ├── config.py        # ortam değişkenleri + .env yükleme, User-Agent kontrolü
│   │   ├── rate_limiter.py  # 8 istek/sn sınırı
│   │   ├── sec_client.py    # SEC'e tek erişim noktası (yeniden deneme, dosya önbelleği)
│   │   ├── edgar.py         # CIK, ticker, başvuru ayrıştırma, 8-K maddeleri
│   │   ├── xbrl.py          # companyfacts → yıllık kalemler
│   │   ├── pipeline.py      # bir şirket için SEC verisi toplama
│   │   ├── ratios.py        # finansal oranlar
│   │   ├── scoring.py       # açıklanabilir skor
│   │   ├── text_extract.py  # HTML → metin, bölüm ayıklama, cümle / paragraf / parça
│   │   ├── sentiment.py     # FinBERT veya sözlük yedeği
│   │   ├── risk_diff.py     # risk bölümü karşılaştırması
│   │   ├── embeddings.py    # sentence-transformers veya hashing yedeği
│   │   ├── llm.py           # Anthropic / OpenAI uyumlu LLM istemcisi
│   │   ├── rag.py           # kaynak gösteren soru-cevap
│   │   ├── analyze.py       # saf analiz hattı (bölüm, duygu, gömme, risk, skor)
│   │   ├── models.py, db.py # tablolar, şema yükseltme
│   │   ├── repository.py    # veritabanı okuma / yazma
│   │   ├── views.py         # API JSON şekilleri (saf)
│   │   ├── jobs.py          # arka plan işi, model önbelleği
│   │   ├── ingest.py        # komut satırı aracı
│   │   └── api.py           # FastAPI uç noktaları + arayüzün sunulması
│   └── tests/               # pytest; fixtures/ altında sentetik SEC verisi
└── frontend/                # React 19 + TypeScript (esbuild), d3 grafikler
    ├── src/
    │   ├── pages/           # ana sayfa, şirket sayfası (sekmeler), karşılaştırma
    │   ├── components/      # arama, veri çekme paneli, ortak bileşenler
    │   ├── charts/          # çubuk grafik, skor göstergesi
    │   ├── api.ts, types.ts # API istemcisi ve sözleşme tipleri
    │   └── i18n.ts          # Türkçe / İngilizce metinler
    ├── mock/, scripts/      # sahte API verisi (arayüz testleri için)
    └── build.mjs            # npm run build → dist/
```

## Çalıştırma

Önce ayar dosyasını hazırlayın (her iki yöntem için):

```bash
cd finsight-ai
cp .env.example .env      # Windows: copy .env.example .env
```

`.env` içinde en az `SEC_USER_AGENT` satırını kendi adınız ve e-postanızla doldurun
(SEC bunu zorunlu tutar; yoksa veri çekme başlamaz). `.env` git'e eklenmez.
Python ile çalışırken `.env` otomatik okunur (önce komutu çalıştırdığınız klasördeki, sonra
depo kökündeki `.env`); kabukta zaten tanımlı bir değişken `.env` ile ezilmez.

### A. Python ile (Docker gerekmez)

Python 3.11 veya üstü gerekir. Veritabanı olarak çalışma klasöründe otomatik bir SQLite
dosyası (`finsight.db`) oluşturulur; önceki sürümden kalan dosya ilk açılışta yeni sütun ve
tablolarla yükseltilir.

**Windows (PowerShell):**

```powershell
cd finsight-ai\backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m finsight.ingest MSFT --analyze      # veri çek + metin analizi + skor
uvicorn finsight.api:app --reload
```

**macOS / Linux:**

```bash
cd finsight-ai/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m finsight.ingest MSFT --analyze      # veri çek + metin analizi + skor
uvicorn finsight.api:app --reload
```

- `python -m finsight.ingest AAPL MSFT` yalnızca finansal veriyi çeker; `--analyze` ayrıca son
  iki 10-K ile son 10-Q'yu indirir, bölümleri ayıklar, duygu analizi, gömme, risk
  karşılaştırması ve skor üretir. Çıktıda hangi modellerin kullanıldığı yazılır. `--force`
  önceden analiz edilmiş başvuruları yeniden işler. Bir şirketteki hata diğerlerini durdurmaz.
  Özetin sonunda adım başına süreler ve duygu analizi hızı (cümle/sn) yazılır; bkz.
  [Performans](#performans).
- Şirketler arayüzden de eklenebilir ("Analiz et" düğmesi → arka plan işi).
- Yeni rapor kontrolü ve artımlı güncelleme: `python -m finsight.refresh --all`
  (`--dry-run` yalnızca listeler). Windows Görev Zamanlayıcı ile çalıştırırken eylemin
  **Başlangıç konumu** `backend\` klasörü olmalı veya `.env`'de `DATABASE_URL` ve
  `SEC_CACHE_DIR` mutlak yol verilmelidir (varsayılanlar göreli yoldur); CLI başlarken
  kullandığı veritabanını ve önbellek klasörünü yazdırır. Ayrıntı: `docs/asama4-sozlesme.md` §4.

**Arayüz:** `cd frontend && npm install && npm run build` ile `frontend/dist` üretildiyse
arayüz API ile aynı adresten sunulur: <http://127.0.0.1:8000>. Geliştirme için `npm run dev`
→ <http://127.0.0.1:5173>. Swagger: <http://127.0.0.1:8000/docs>.

### B. Docker Compose ile (PostgreSQL, arayüz dahil)

```bash
cd finsight-ai
docker compose up -d --build                 # ML paketleriyle: INSTALL_ML=1 docker compose up -d --build
docker compose run --rm api python -m finsight.ingest MSFT --analyze
```

Arayüz: <http://localhost:8000> · API: <http://localhost:8000/api/health> ·
Swagger: <http://localhost:8000/docs>

Portlar yalnızca bu bilgisayara (127.0.0.1) açılır. Veritabanı portu dışarı açılmaz; gerekirse
`docker-compose.yml` içindeki yorumlu satırı açın.

Güvenlik notları (`docs/guvenlik/duzeltmeler.md`):

- `.env` içinde `POSTGRES_PASSWORD` **zorunludur** (varsayılan parola yoktur).
- Tarayıcıda `http://0.0.0.0:8000` değil `http://127.0.0.1:8000` (veya `localhost`) açın;
  API yalnızca bu adlarla gelen istekleri kabul eder (DNS rebinding koruması). Başka bir ad
  gerekiyorsa `FINSIGHT_ALLOWED_HOSTS` ile ekleyin.
- Kapsayıcı root olmayan `finsight` (uid 10001) kullanıcısıyla çalışır. Önceki sürümde root ile
  oluşturulmuş birimler yazılamayabilir ("Permission denied"). Bu birimler yalnızca önbellek
  içerir; silip yeniden oluşturun: `docker compose down` →
  `docker volume rm finsight-ai_seccache finsight-ai_hfcache` → `docker compose up -d --build`.
  (Birim adlarını `docker volume ls` ile kontrol edin; veritabanı birimi `pgdata`'yı SİLMEYİN.)
- `docker run` ile tek başına çalıştırırken portu yalnızca bu bilgisayara açın ve SQLite için
  birim bağlayın: `docker run --env-file .env -p 127.0.0.1:8000:8000 -v finsight-data:/app/data
  finsight-ai`.

### İsteğe bağlı ML paketleri

Kurulmazsa sistem yedek modellerle çalışır: duygu için `lexicon-fallback` (sözlük), gömme için
`hashing-fallback`. Hangi modelin kullanıldığı `/api/health` ve her yanıtta belirtilir.

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-ml.txt     # ilk çalıştırmada ~0,9 GB model indirilir
```

Model değiştikten sonra şirketi yeniden analiz edin (arayüzde "Analiz et" veya
`python -m finsight.ingest MSFT --analyze`); eski gömmelerle soru-cevap uyarı verir.

### LLM anahtarı (isteğe bağlı)

Soru-cevap LLM olmadan da çalışır: ilgili rapor cümlelerini kaynak numarasıyla alıntılar.
Özet cevap için `.env` içinde `ANTHROPIC_API_KEY=...` (ya da `OPENAI_API_KEY=...`; yerel
OpenAI uyumlu sunucu için `OPENAI_BASE_URL`) tanımlayın. Model `FINSIGHT_LLM_MODEL` ile
değiştirilebilir. Anahtar koda yazılmaz, hata mesajlarında gizlenir.

## Uç noktalar (tümü `/api` önekli; şekiller: `docs/api-contract.md`)

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
| GET | `/api/companies/{ticker}/export.csv` | CSV dışa aktarım (uyarı metni `X-FinSight-Disclaimer` başlığında) |

## Testler

```bash
cd backend
python -m pytest -q                  # tüm testler
python -m pytest --cov=finsight      # kapsam raporu (pytest-cov)
cd .. && ruff check backend/finsight backend/tests     # lint

cd frontend && npm run typecheck && npm run build      # arayüz
```

Testler SEC'e ve LLM'e gerçek istek atmaz; `backend/tests/fixtures/` altındaki **sentetik**
şirket verisini ("Example Corp", "Kestrel Dynamics" — gerçek şirketler değildir) ve sahte
istemcileri kullanır. FastAPI / SQLAlchemy kurulu değilse ilgili testler (`test_api.py`,
`test_repository.py`, `test_jobs.py`) atlanır; tam doğrulama için `requirements.txt`
kurulu ortamda çalıştırın.

## Nasıl çalışır (kısaca)

- **Veri:** SEC `submissions` ve `companyfacts` JSON'ları + başvuruların ana HTML belgesi.
  Yanıtlar 24 saat dosya önbelleğinde tutulur.
- **Metin analizi:** bölümler ayıklanır, cümlelere ve ~150–350 kelimelik parçalara bölünür.
  Bölüm alt başlıkları ("Risks Related to Our Business") duygu analizinde ve alıntılarda
  kullanılmaz.
- **Soru-cevap:** parça vektörleri veritabanında ikili (float32) sütunda saklanır; benzerlik
  her soruda numpy ile hesaplanır (kosinüs). Veritabanında vektör araması (pgvector) Aşama 4
  seçeneğidir. Eski ve yeni 10-K'da birebir aynı paragraflar tek kaynak olarak gösterilir.
- **Skor:** oranlar + metin sinyalleri, ağırlıkları `scoring.py`'de; her bileşen gerekçesiyle.

## Performans

### Neden yavaş?

FinBERT (BERT-base, ~110 milyon parametre) GPU olmadan her cümle için tam bir transformer
çalıştırır. Örnek ölçüm (4 çekirdekli dizüstü, MSFT, `--analyze --force`, eski kod):
3 başvuruda ~2.155 cümle ve 213 parça, toplam ~349 sn. Gömme birkaç saniye sürer; zamanın
büyük kısmı FinBERT'tir. `--analyze` çıktısı artık bunu doğrudan gösterir:

```
  Adım süreleri:
    Model yükleme                   …  sn
    Belge indirme                   …
    HTML → metin / Bölüm ayıklama   …
    Duygu analizi                   …  <- en uzun
    Gömme / Veritabanı / Risk / Skor …
  Duygu        : 2155 cümle, … sn, … cümle/sn
                 modele giden …, önbellekten …, yinelenen …; model hızı … cümle/sn
```

### Ne yapıldı?

Doğruluğu değiştirmeyen iyileştirmeler (varsayılan **açık**):

| İyileştirme | Ne yapar | Beklenen etki |
| --- | --- | --- |
| Cümle önbelleği | Aynı şirketin aynı duygu modeliyle daha önce analiz edilmiş cümleleri (boşlukları normalize edilmiş metin → etiket, skor) ve aynı çalıştırmada önce işlenen başvuruların sonuçlarını yeniden kullanır. Belge içindeki yinelenen cümleler bir kez tahmin edilir. | Eski ve yeni 10-K'nın risk bölümleri büyük ölçüde aynıdır; ikinci 10-K'da tekrar eden cümle oranı kadar (tahmini %40–70) tasarruf. `--force` ile yeniden analizde duygu adımı neredeyse sıfırlanır. |
| Sıralı dinamik dolgu | Cümleler belirteç uzunluğuna göre sıralanıp `padding="longest"` ile gruplanır, sonuçlar orijinal sıraya geri yerleştirilir. | Eski yöntemde her grup içindeki en uzun cümleye kadar dolgu yapılıyordu; sıralama boşa giden hesabı azaltır (tipik 1,5–2,5x, ölçülecek). |
| `torch.inference_mode()` + doğrudan model | Hattın (pipeline) ek yükü ve otomatik türev kaydı yok. | Küçük (%5–15). |
| Grup boyutu 32 | `FINSIGHT_FINBERT_BATCH` ile değiştirilebilir. | Donanıma bağlı. |

Doğruluğu etkileyebilecek ayarlar (varsayılan **kapalı**, ölçüm aracıyla doğrulanınca açın):

| Ortam değişkeni | Varsayılan | Açıklama |
| --- | --- | --- |
| `FINSIGHT_FINBERT_QUANTIZE=1` | kapalı | `nn.Linear` katmanları int8 dinamik kuantizasyon (CPU'da tipik 1,5–2,5x). Hata olursa uyarıyla normal modele düşer. Model adı `ProsusAI/finbert+int8` olarak kaydedilir. |
| `FINSIGHT_FINBERT_MAX_LENGTH` | 512 | Daha kısa değer (ör. 256) uzun cümleleri keser. Sıralı dolgu kısa cümleleri zaten 512'ye doldurmadığı için kazanç yalnızca 256 token'ı aşan cümle oranı kadardır. Ad `…+len256` olur. |
| `FINSIGHT_TORCH_THREADS` | torch varsayılanı | CPU iş parçacığı sayısı; 4 çekirdekli işlemcide 4 ve 8'i deneyin. |
| `FINSIGHT_FINBERT_BATCH` | 32 | Grup boyutu. |
| `FINSIGHT_ANALYZE_10Q=0` | 1 | **Hızlı mod:** en son 10-Q başvuru listesinde görünür ama metni indirilip analiz edilmez (cümlelerin yaklaşık üçte biri). Risk karşılaştırması ve skor yalnızca 10-K'lara dayandığı için etkilenmez; 10-Q duygu bölümü güncellenmez. |

Kuantizasyon veya kısa `max_length` açıldığında model adı değiştiği için önceki sonuçlarla
karışmaz: başvurular yeni adla yeniden analiz edilir ve cümle önbelleği ayrı tutulur.

### Ölçüm

```powershell
cd backend
# 1) Adım süreleri: önbelleksiz (modelin gerçek hızı) ve önbellekli çalıştırma
python -m finsight.ingest MSFT --analyze --force --no-sentence-cache
python -m finsight.ingest MSFT --analyze --force
# 2) Yapılandırma karşılaştırması (veritabanındaki MSFT cümlelerinden 500 örnek)
python -m finsight.bench_sentiment MSFT --limit 500
python -m finsight.bench_sentiment MSFT --limit 500 --threads 4
```

`bench_sentiment` aynı cümle örneğinde şunları karşılaştırır: **(a)** eski yöntem (pipeline,
sırasız, batch 16), **(b)** yeni varsayılan, **(c)** yeni + int8, **(d)** yeni + max_length 256.
Her biri için toplam süre, cümle/sn, (a)'ya göre hızlanma, (a)'ya göre etiket uyumu (%) ve
sınıf bazında karışıklık tablosu ile token uzunluğu dağılımı (64/128/256/512'yi aşan cümle
sayısı) yazılır. torch/transformers kurulu değilse anlaşılır bir mesajla çıkar.

**Açma ölçütü:** (c) veya (d), (a) ile etiket uyumu **≥ %97** ve (b)'ye göre hızlanması
**≥ 1,2x** ise ilgili ayarın açılması önerilir (araç bu kararı "Öneri" satırında yazar). Ayrıca
karışıklık tablosunda olumsuz → olumlu gibi zıt yönlü değişimlerin çok az olduğuna bakın; skorun
metin sinyali net tona (olumlu − olumsuz) dayanır. Dizüstü bilgisayarda prize takılı ve yüksek
performans güç modunda ölçün; termal kısma nedeniyle iki kez çalıştırıp ikinci sonucu kullanın.

## Bilinen sınırlar

- **Hashing yedeğinde Türkçe sorular:** `hashing-fallback` gömücüsü kelime eşleşmesine
  dayanır ve diller arası eşleştiremez. LLM anahtarı yoksa Türkçe soru, İngilizce raporlarda
  karşılık bulamayabilir ("raporlarda bulunamadı"). Çözüm: soruyu İngilizce sorun, LLM anahtarı
  tanımlayın (soru otomatik İngilizceye çevrilip aranır) veya ML paketlerini kurun (çok dilli
  gömme modeli).
- **Sözlük duygu modeli:** `lexicon-fallback` kelime sayımıdır; olumsuzlamayı ("not
  significant"), bağlamı ve finansal ironiyi anlamaz, uzun cümlelerde nötre kayar. Ton
  değerlerini FinBERT kurulu ortamda yorumlayın. Loughran-McDonald sözlüğü
  (`LM_DICTIONARY_PATH`) verilirse kapsam artar.
- **Gerçek SEC verisiyle doğrulama** geliştirme ortamında ağ kısıtı nedeniyle sentetik veriyle
  yapıldı; gerçek şirketlerle (ör. MSFT, AAPL, GOOGL/GOOG) uçtan uca deneme kullanıcının
  bilgisayarında yapılır.
- Bölüm ayıklama başlık kalıplarına dayanır; alışılmadık biçimli belgelerde bölüm
  bulunamayabilir (iş uyarısında belirtilir).
- Bankalar ve sigorta şirketlerinde bazı kalemler farklı etiketlerle raporlanır, boş gelebilir.
- Mali yılı Aralık dışında biten şirketlerde "yıl" dönem bitiş tarihine göre gösterilir.
- Şema yükseltme basit `ALTER TABLE ... ADD COLUMN` ile yapılır; Alembic göçleri Aşama 4'te.
- Arka plan işleri tek süreç içindir (uvicorn tek worker önerilir).
- PDF dışa aktarma yok; yalnızca CSV.

## Kaynaklar

- SEC, [EDGAR Application Programming Interfaces](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- SEC, [Developer Resources — Fair Access](https://www.sec.gov/about/developer-resources)
