# Aşama 4 — Analiz hızı: araştırma, uygulanan değişiklikler ve ölçüm planı

Hazırlayan: nlp-engineer alt ajanı, 23 Kasım 2026. İlgili gereksinimler: FR-07 (risk bölümü),
FR-08 (FinBERT duygu), FR-09 (risk farkı), FR-10 (gömme / soru-cevap), NFR (performans).

**İlke:** doğruluk > hız. Etiketleri değiştirebilecek her ayar varsayılan olarak **kapalıdır**,
ayrı model adıyla kaydedilir ("+fp16", "+onnx") ve `bench_sentiment` ile ölçülmeden açılmaz.
int8 dinamik kuantizasyon (5x hızlı ama olumsuz cümlelerin %74'ünü nötre çevirdi) bu yüzden
reddedilmişti; aynı türden bir hataya düşebilecek yöntemler (int8 ONNX / OpenVINO int8) önerilmez.

## 1. Özet

| Değişiklik | Beklenen hız etkisi | Doğruluğa etkisi |
| --- | --- | --- |
| `html_to_text`: BeautifulSoup ağacı yerine doğrudan lxml ağacı (+ bs4 yolunda alt ağaç taraması hatası giderildi) | Bulutta HTML → metin **6–7x** (sentetik 8,3 MB: 2,04 → 0,34 sn) | Yok — çıktı bs4 yoluyla **birebir aynı** (62 girdi + parametreli testler) |
| `risk_diff.sparse_similarities` numpy ile vektörleştirildi | Risk adımı **1,9x** (ölçek 1) – **4,6x** (ölçek 3, ~54 bin kelimelik bölüm) | Yok — saf Python başvurusuyla aynı çiftler, fark 0 |
| Gömme: yinelenen parçalar bir kez gömülür; hashing kova önbelleği | Hashing gömme 1,3–1,6x; ST'de yinelenen parça oranı kadar | Yok — aynı vektörler (test) |
| FinBERT `FINSIGHT_DEVICE=auto\|cpu\|cuda` (varsayılan auto: kullanılabilir CUDA varsa GPU, **fp32**) | Kullanıcının GPU'sunda **ölçülmeli**; tahmini 1,5–3x (aşağıda gerekçe) | fp32 aynı ağırlık + aynı aritmetik; yalnızca toplama sırası farkı (~1e-6). Uyum `--configs be --reference b` ile ölçülür |
| GPU belleği yetmezse grup ikiye bölünür | 2 GB VRAM'de çökme yerine devam | Yok (dikkat maskesi; test) |
| `FINSIGHT_FP16=0\|1\|auto` (varsayılan **0**) | Yalnızca tensör çekirdekli GPU'da (cc ≥ 7.0) anlamlı | Değişebilir → ad "+fp16", ölçülmeden açılmaz |
| `FINSIGHT_FINBERT_BACKEND=onnx` (ONNX Runtime, CPU fp32, varsayılan kapalı) | Donanıma bağlı; ölçülmeli (`--configs bg --reference b`) | fp32, ama farklı çekirdekler → ad "+onnx", ölçülmeden açılmaz |
| Gömme modeli de `FINSIGHT_DEVICE` ile GPU'ya geçer (her zaman fp32) | Gömme zaten birkaç saniye; küçük | Yok (fp16 kullanılmaz: veritabanındaki vektörlerle aynı uzay) |
| Yeni `bench_pipeline` aracı (ağsız, adım adım) | — | — |
| `bench_sentiment`: (e) CUDA fp32, (f) CUDA fp16, (g) ONNX; `--reference`; GPU yapılandırmaları ağırlık kopyası kullanır | — | Uyum yüzdesi + karışıklık tablosu |

Kullanıcının 193,7 sn'lik önbelleksiz MSFT analizinin büyük kısmı FinBERT'tir (2.155 cümle /
15 cümle/sn ≈ 144 sn). Bu yüzden asıl kazanç GPU'dan gelebilir; metin hattındaki iyileştirmeler
(HTML → metin, risk farkı) birkaç saniyelik ama doğruluk riski sıfır kazançlardır.

## 2. Donanım tespiti (kontrol edin)

ASUS X515JF varyantları satıcı sayfalarında **NVIDIA GeForce MX130 2 GB GDDR5** ile listeleniyor
([quenotebookcomprar, X515JF-EJ383W](https://quenotebookcomprar.com.br/?p=121978)); ASUS'un X515
teknik sayfası ise X515JP için **MX330 2 GB** gösteriyor ([asus.com X515 techspec](https://www.asus.com/eg-en/Laptops/For-Home/All-series/ASUS-X515/techspec/)).

| GPU | Mimari / çip | Compute capability | FP16 | Not |
| --- | --- | --- | --- | --- |
| MX130 | Maxwell, GM108 ([TechPowerUp](https://techpowerup.com/gpudb/3043/geforce-mx130)) | 5.0 (Maxwell GM108 için genel bilgi; NVIDIA listesinde MX130 yok — **kontrol edin**) | Yerel FP16 aritmetiği yok (sm_53 öncesi) | 384 çekirdek, 1189 MHz → kuramsal ~0,9 TFLOPS FP32 (hesap, sayfada yazmıyor) |
| MX330 | Pascal, GP108 ([TechPowerUp](https://techpowerup.com/gpu-specs/geforce-mx330.c3493)) | 6.1 | 19,13 GFLOPS, FP32'nin **1/64'ü** | 1,224 TFLOPS FP32 |

Sonuç: her iki kartta da **fp16 hız kazandırmaz** (Maxwell'de yerel değil, Pascal tüketici
kartında 1:64). Kullanılacaksa GPU **fp32** çalışmalıdır; `FINSIGHT_FP16=auto` bu yüzden yalnızca
cc ≥ 7.0'da (PyTorch: karışık duyarlık "Volta and newer" tensör çekirdeklerinde hızlanır —
[PyTorch Performance Tuning Guide](https://docs.pytorch.org/tutorials/recipes/recipes/tuning_guide.html)) açılır.
VRAM: FinBERT fp32 ağırlıkları ~440 MB (requirements-ml.txt), MiniLM ~470 MB; batch 32 × en
fazla 512 belirteçle 2 GB'a sığması beklenir; sığmazsa grup otomatik bölünür.

**PyTorch sürüm kısıtı (önemli):** PyTorch 2.8'den itibaren CUDA 12.8/12.9 derlemelerinde Maxwell
ve Pascal desteği kaldırıldı ([PyTorch dev-discuss](https://dev-discuss.pytorch.org/t/cuda-toolkit-version-and-architecture-support-update-maxwell-and-pascal-architecture-support-removed-in-cuda-12-8-and-12-9-builds/3128));
PyTorch 2.15'ten itibaren CUDA 12.6 wheel'leri yayımlanmıyor, CUDA 13.x Turing (sm_75) altını
desteklemiyor ve "PyTorch 2.14 is the last release with a prebuilt wheel" Maxwell/Pascal için
([PyTorch dev-discuss, 2.15 duyurusu](https://dev-discuss.pytorch.org/t/notice-cuda-12-6-wheels-will-no-longer-be-published-from-pytorch-2-15-drops-maxwell-pascal-volta/3432)).
Kullanıcıda torch 2.15.0+cpu kurulu: GPU için **torch 2.14 + cu126**'ya inmek gerekir (bölüm 8).
`FINSIGHT_DEVICE=auto` bu durumu kendisi yakalar: `torch.cuda.is_available()` True olsa bile
kurulu derlemenin mimari listesinde GPU'nun `sm_XY`'si yoksa veya küçük deneme işlemi
("no kernel image is available") başarısızsa CPU'ya düşer ve nedenini günlüğe yazar.

**Tahmini GPU kazancı (ölçülmedi, yalnızca kaba hesap):** i3-1005G1 / i5-1035G1 (Ice Lake,
AVX-512, çekirdek başına tek 512-bit FMA) kuramsal tepe ≈ çekirdek × ~3,4 GHz × 32 FLOP ≈
0,2–0,4 TFLOPS; MX130 ~0,9, MX330 ~1,2 TFLOPS. Bellek bant genişliği (64-bit GDDR5) ve PCIe
aktarımı yüzünden gerçek kazanç tepe oranından düşük olur; **1,5–3x aralığı bir tahmindir**,
`bench_sentiment --configs be` ile ölçülmeden README'ye yazılmamalıdır.

## 3. Araştırma özeti

| Seçenek | Kaynak | Doğruluk | Bu proje için karar |
| --- | --- | --- | --- |
| CUDA fp32 | PyTorch belgeleri (yukarıda) | Aynı (toplama sırası farkı) | **Uygulandı**, auto varsayılan; MX130/MX330 için torch ≤ 2.14 cu126 gerekir |
| CUDA fp16 | Tuning guide: hızlanma Volta+ tensör çekirdeklerinde; SBERT: GPU'da FP16 medyan 2,92x, "without reducing average task quality" ([sbert.net efficiency](https://sbert.net/docs/sentence_transformer/usage/efficiency.html)) | Küçük sapma olası (ölçülmeli) | **Uygulandı, varsayılan kapalı**; kullanıcının kartında yararsız (bkz. bölüm 2) |
| `torch.inference_mode` | — | Aynı | Zaten vardı (Aşama 3) |
| SDPA | transformers BERT belgesi: torch ≥ 2.1.1'de SDPA uygun olduğunda **varsayılan**; A100 fp16 çıkarımda belirteç başı gecikme %15–25 daha düşük ([HF BERT docs](https://huggingface.co/docs/transformers/main/ko/model_doc/bert)) | Aynı | Ek kod gerekmez (kullanıcının torch/transformers sürümünde zaten etkin) |
| BetterTransformer | Optimum: SDPA destekleyen modeller için **kullanımdan kaldırıldı**, doğrudan transformers + SDPA öneriliyor ([Optimum docs](https://huggingface.co/docs/optimum/bettertransformer/overview)) | — | **Reddedildi** (gereksiz) |
| `torch.set_num_threads` | Intra-op iş parçacıkları GEMM gibi tek işlem içini paralelleştirir; aşırı abonelikten kaçının ([PyTorch CPU threading](https://docs.pytorch.org/docs/2.3/notes/cpu_threading_torchscript_inference.html)) | Aynı | Zaten `FINSIGHT_TORCH_THREADS`; 2 ve 4 değeri `--threads` ile ölçülmeli |
| `set_num_interop_threads` | Inter-op havuzu TorchScript `fork` görevleri içindir (aynı kaynak) | Aynı | **Uygulanmadı**: eager BERT ileri geçişi inter-op kullanmaz |
| ONNX Runtime fp32 | SBERT: ONNX O1–O3 fp32, O4 fp16; Intel CPU'da OpenVINO, diğerlerinde ONNX öneriliyor; sıralamalar donanıma bağlı, kendi donanımınızda ölçün (sbert.net). Microsoft'un 17x CPU sonucu 3 katmanlı damıtılmış BERT içindir, buraya aktarılamaz ([Microsoft blog](https://opensource.microsoft.com/blog/2020/01/21/microsoft-onnx-open-source-optimizations-transformer-inference-gpu-cpu/)) | fp32: çok küçük sayısal fark | **Uygulandı, varsayılan kapalı**, bench (g) ile ölçülecek |
| ONNX/OpenVINO int8 | sbert.net: küçük doğruluk kaybı kabulse openvino-qint8 | Kayıp var | **Reddedildi** (int8 deneyimi) |
| Uzunluk sıralı gruplama | — | Aynı | FinBERT'te zaten var; sentence-transformers `encode` kendi içinde uzunluğa göre sıralar (kaynak kodda `length_sorted_idx` — kontrol komutu bölüm 7'de) |
| lxml | BeautifulSoup belgeleri: "Beautiful Soup will never be as fast as the parsers it sits on top of … work directly atop lxml" ([bs4 docs](https://www.crummy.com/software/BeautifulSoup/bs4/doc/)) | Aynı (eşitlik testleri) | **Uygulandı** |
| İndirme + çıkarım örtüştürme | — | Aynı | **Öneri** (jobs.py, şef entegre edecek; bölüm 6) |

## 4. Uygulanan değişiklikler (dosya bazında)

- `backend/finsight/text_extract.py`
  - `html_to_text(html, backend=None)`: lxml kuruluysa `_walk_lxml` (bs4 nesne ağacı kurulmaz),
    değilse `_walk_bs4`. İki yol aynı metin kurallarını (`_separators`) paylaşır.
  - **Bulunan hata:** bs4 4.13+ sürümünde `Tag.decomposed` özelliği `getattr(self, "_decomposed")`
    çağırır; öznitelik yoksa `Tag.__getattr__` bunu alt öğe adı sanıp `find()` ile tüm alt ağacı
    tarar. Eski döngü her öğe için bunu yapıyordu (profilde sürenin ~%35'i). Artık atılacak
    öğeler `decompose` edilmeden yürüyüşte atlanıyor (`test_bs4_path_does_not_scan_subtrees`).
  - **libxml2 derinlik sınırı:** ağaç kuran HTML ayrıştırıcısı ~254 iç içe düzeyden sonrasını
    *hata vermeden* atıyor (ölçüldü: varsayılan 254, `huge_tree` ile 2046). Yürüyüş 240 düzeye
    ulaşırsa bs4 yoluna düşülür (olay tabanlı bs4 oluşturucusu etkilenmez). Gerçek EDGAR
    belgeleri tipik olarak bu derinliğin çok altındadır; kapanmamış `<font>` dizili eski
    başvurular yavaş ama doğru yoldan geçer.
- `backend/finsight/risk_diff.py`: `sparse_similarities` numpy (CSC benzeri sıralı diziler +
  `np.bincount`); `sparse_similarities_reference` saf Python başvurusu olarak korundu.
  Eşikler (0,90 / 0,50) değişmedi.
- `backend/finsight/embeddings.py`: `unique_texts`; `HashingEmbedder` ve
  `SentenceTransformerEmbedder` yinelenen metni bir kez gömer; hashing kova önbelleği
  (`BUCKET_CACHE_MAX = 200_000`, dolunca sıfırlanır); ST cihazı `FINSIGHT_DEVICE` ile (fp32).
- `backend/finsight/sentiment.py`: `resolve_device`, `_cuda_usable` (mimari listesi + deneme
  işlemi), `device_from_env`, `fp16_from_env`, `finbert_backend_from_env`;
  `FinBertModel(device=, fp16=, backend=)`, `device_info`, `oom_splits`; OOM'da grup bölme;
  int8 yalnızca CPU torch yolunda; ad sonekleri "+onnx", "+int8", "+fp16", "+lenN".
- `backend/finsight/bench_sentiment.py`: (e) CUDA fp32, (f) CUDA fp16, (g) ONNX; `--reference`;
  varsayılan `--configs abde` (reddedilen c artık varsayılan değil, istenirse eklenir).
- `backend/finsight/bench_pipeline.py` (yeni): sentetik EDGAR benzeri belgeler (gizli
  `ix:header`, satır içi stiller, içindekiler tuzağı, finansal tablolar, eski/yeni 10-K'da
  risk paragraflarının ~%30'u değişik) veya `--dir` / `--file` ile diskteki belgeler; adım adım
  süre, `--repeat`, `--json`, `--models fallback|configured`.
- `backend/requirements-ml.txt`: CUDA ve ONNX kurulum notları.
- Testler: `backend/tests/test_perf_asama4.py` (yeni, 70 test),
  `backend/tests/test_sentiment_perf.py` (uçtan uca bench testi yeni yapılandırmalara uyarlandı).

Yeni ortam değişkenleri: `FINSIGHT_DEVICE` (auto|cpu|cuda, varsayılan auto), `FINSIGHT_FP16`
(0|1|auto, varsayılan 0), `FINSIGHT_FINBERT_BACKEND` (torch|onnx, varsayılan torch). Hepsi
`sentiment.py` içinde okunur (config.py'ye dokunulmadı).

## 5. Bulut ölçümleri (önce / sonra)

Ortam: bulut kapsayıcısı, Python 3.13, lxml 6.1.3 (libxml2 2.14.6), bs4 4.15.0; torch YOK.
Girdi: `bench_pipeline` sentetik şirketi (eski 10-K, yeni 10-K, 10-Q); yedek modeller
(sözlük + hashing). "Önce" = aynı süreçte eski uygulamalar yamalanarak; 3 tekrarın her adım
için en iyisi. **Sentetik veridir; kullanıcının gerçek MSFT sürelerinin yerini tutmaz.**

Ölçek 1 (8,3 MB HTML, 3.583 cümle, 761 parça):

| Adım | Önce (sn) | Sonra (sn) | Hız |
| --- | --- | --- | --- |
| HTML → metin | 2,037 | 0,335 | 6,1x |
| Bölüm ayıklama | 0,027 | 0,030 | — |
| Cümle bölme + başlık eleme | 0,046 | 0,044 | — |
| Duygu (sözlük) | 0,084 | 0,086 | — |
| Parçalama | 0,038 | 0,040 | — |
| Gömme (hashing) | 0,107 | 0,079 | 1,3x |
| Risk karşılaştırması | 0,093 | 0,050 | 1,9x |
| **Toplam** | **2,433** | **0,664** | **3,7x** |

Ölçek 3 (24,9 MB, 10.692 cümle, 2.254 parça; büyük bankalar gibi ~54 bin kelimelik risk bölümü):

| Adım | Önce (sn) | Sonra (sn) | Hız |
| --- | --- | --- | --- |
| HTML → metin | 6,710 | 0,975 | 6,9x |
| Gömme (hashing) | 0,354 | 0,225 | 1,6x |
| Risk karşılaştırması | 0,823 | 0,181 | 4,6x |
| **Toplam** | **8,439** | **1,972** | **4,3x** |

Her iki ölçekte etiket dağılımı ve risk sayıları (ölçek 1: eklendi 30 / çıkarıldı 30 /
değişti 18 / aynı 112) önce ve sonra **aynı**. Ayrıca sabit işaretli eşitlik: HTML → metin
62 girdide (fixture'lar, test dosyalarındaki tüm HTML dizgileri, sentetik belgeler, uç durumlar)
eski uygulamayla birebir aynı; `sparse_similarities` rastgele ve gerçekçi vektörlerde başvuruyla
aynı anahtarlar, en büyük fark 0.

## 6. Şefin analyze.py / jobs.py'ye entegre etmesi önerilenler (kod yazılmadı)

### 6.1 İndirmeyi çıkarımla örtüştürme — `jobs.run_analysis_for_company`

Bugün döngü her başvuruda sırayla: indir → HTML/duygu → gömme → kaydet. Bir sonraki başvurunun
indirilmesi, öncekinin FinBERT çıkarımı sırasında arka planda yapılabilir (httpx G/Ç ve torch
çekirdekleri GIL'i bırakır). Ayrıca ilk belge model yüklenirken indirilebilir. Doğruluğa
etkisi yoktur. **SEC kuralı korunur:** tüm indirmeler tek işçili havuzdan geçer (aynı anda en
fazla bir SEC isteği; `RateLimiter` zaten kilitli); `Session` işçi iş parçacığında
KULLANILMAZ. Kazanç ≈ min(indirme, çıkarım) × (başvuru − 1) + min(indirme₁, model yükleme).

```python
# jobs.py başına
from concurrent.futures import Future, ThreadPoolExecutor

# run_analysis_for_company içinde, report("Modeller yükleniyor", 0.0) satırından ÖNCE:
all_filings = repo.load_filings(session, company_id)            # (aşağıdan yukarı taşınır)
include_quarterly = analyze_quarterly_enabled() if analyze_quarterly is None \
    else analyze_quarterly
selected = select_filings_for_analysis(all_filings, include_quarterly=include_quarterly)
prefetch_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sec-prefetch")
fetches: dict[int, Future] = {}

def prefetch(filing: dict) -> None:
    if filing["id"] not in fetches:
        fetches[filing["id"]] = prefetch_pool.submit(
            client.document, company["cik"], filing["accession_no"],
            filing["primary_document"])

# Modeller yüklenirken ilk başvuruyu indir. Adlar modeli yüklemeden bilinir; FinBERT yüklenemezse
# ad farklı çıkar ve en kötü ihtimalle bir belge gereksiz indirilir.
names = model_status()
pending = [f for f in selected if force or not repo.is_filing_analyzed(
    session, f["id"], names["sentiment"], names["embedding"])]
if pending:
    prefetch(pending[0])
try:
    ... # mevcut model yükleme ve döngü
    # döngüde, "client.document(...)" çağrısının YERİNE:
    #     prefetch(filing)
    #     body = fetches.pop(filing["id"]).result()   # "download" süresi = yalnızca bekleme
    #     nxt = next((f for f in selected[i + 1:] if force or not repo.is_filing_analyzed(
    #         session, f["id"], sm.name, emb.name)), None)
    #     if nxt is not None:
    #         prefetch(nxt)                               # FinBERT çalışırken iner
    #     document = store_document(body, ...)            # (değişmez)
finally:
    prefetch_pool.shutdown(wait=True, cancel_futures=True)
```

Not: `fetches.pop(...).result()` indirme hatasını aynı istisna olarak yükseltir; mevcut
`except Exception` bloğu başvuruyu "error" olarak işaretlemeye devam eder. Mevcut testlerde sahte
`client.document` iş parçacığından çağrılacağı için sahte istemcinin iş parçacığı güvenli olduğu
kontrol edilmeli.

### 6.2 Cihaz bilgisini özete ekleme (isteğe bağlı)

`summary` sözlüğüne `"sentiment_device": getattr(sm, "device_info", None)` ve ingest CLI
özetine bir satır ("Duygu cihazı: CUDA GeForce MX130 (sm_50) fp32" / "CPU (...)") eklenirse
kullanıcı GPU'nun gerçekten kullanıldığını görür. `/health` için `model_status()`'e
`"device": os.getenv("FINSIGHT_DEVICE", "auto")` eklenebilir (API değişirse
`docs/api-contract.md` güncellenmeli).

### 6.3 README "Performans" tablosu

Ölçümler kullanıcı bilgisayarında alındıktan sonra `FINSIGHT_DEVICE`, `FINSIGHT_FP16`,
`FINSIGHT_FINBERT_BACKEND` satırları ve `bench_pipeline` komutu eklenmeli. analyze.py'de
değişiklik gerekmiyor (yeni hızlı yollar aynı fonksiyon adlarının arkasında).

## 7. Kullanıcı bilgisayarında çalıştırılacak ölçüm komutları (PowerShell)

```powershell
cd backend

# 0) Donanım ve kurulu torch (compute_cap sorgusu eski sürücülerde yoksa satırdan çıkarın)
nvidia-smi --query-gpu=name,compute_cap,driver_version,memory.total --format=csv
python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_arch_list(), torch.get_num_threads())"
python -c "import inspect, sentence_transformers as s; print('length_sorted_idx' in inspect.getsource(s.SentenceTransformer.encode))"

# 1) Ağsız boru hattı, yedek modeller (bulut tablosuyla karşılaştırma için)
python -m finsight.bench_pipeline --repeat 3

# 2) Diskteki MSFT belgeleriyle (CIK 789019), gerçek modellerle, adım adım
$docs = python -c "from finsight.config import get_documents_dir; print(get_documents_dir())"
python -m finsight.bench_pipeline --dir (Join-Path $docs 789019) --models configured --json bench_cpu.json

# 3) FinBERT: CPU iş parçacığı sayısı (doğruluğu etkilemez)
python -m finsight.bench_sentiment MSFT --configs b --threads 2
python -m finsight.bench_sentiment MSFT --configs b --threads 4

# 4) GPU (CUDA torch kurulduktan sonra, bölüm 8): fp32 hız + uyum, fp16 yalnızca bilgi için
python -m finsight.bench_sentiment MSFT --configs bef --reference b --limit 1000

# 5) ONNX Runtime CPU fp32 (pip install "optimum[onnxruntime]" sonrası)
python -m finsight.bench_sentiment MSFT --configs bg --reference b --limit 1000

# 6) Uçtan uca: önbelleksiz tam analiz, önce CPU sonra auto (GPU)
$env:FINSIGHT_DEVICE="cpu";  python -m finsight.ingest MSFT --analyze --force --no-sentence-cache
$env:FINSIGHT_DEVICE="auto"; python -m finsight.ingest MSFT --analyze --force --no-sentence-cache
Remove-Item Env:FINSIGHT_DEVICE
```

Karar ölçütü (bench aracının "Öneri" satırı): uyum ≥ %97 **ve** (b)'ye göre ≥ 1,2x. GPU fp32
için beklenen uyum ~%100'dür; %99'un altında çıkarsa (ör. kararsız sürücü) GPU kullanılmamalı ve
`FINSIGHT_DEVICE=cpu` yazılmalıdır. Skor (83,9) ve risk farkı (25/8/25/52) uçtan uca
çalıştırmada değişmemelidir. Dizüstünde prize takılı, yüksek performans modunda, ikinci sonuç.

## 8. CUDA torch kurulumu (MX130 / MX330 için; kontrol edin)

1. GPU'yu doğrulayın: `nvidia-smi` çıktısında kart adı (MX130 / MX330) ve "CUDA Version"
   görünmeli. NVIDIA sürücüsü yoksa NVIDIA'nın sitesinden dizüstü (Notebook) GeForce sürücüsünü
   kurun. Maxwell/Pascal sürücü desteğinin "dondurulacağı" duyuruldu ([Tom's Hardware, 24 Ocak 2025](https://www.tomshardware.com/pc-components/gpu-drivers/nvidia-starts-phasing-out-maxwell-pascal-and-volta-gpus-geforce-driver-support-status-unclear));
   kartınızı destekleyen en son sürücüyü kullanın. "CUDA Version" değeri 12.6 veya üstü
   olmalıdır (**kontrol edin**).
2. Sanal ortamda CPU torch'u kaldırıp CUDA 12.6 derlemesini kurun (PyTorch 2.15+ bu kartlar
   için CUDA wheel'i yayımlamıyor):
   ```powershell
   pip uninstall -y torch
   pip install "torch==2.14.*" --index-url https://download.pytorch.org/whl/cu126
   python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_arch_list())"
   ```
   Beklenen: `2.14.x+cu126 True [... 'sm_50' ... 'sm_61' ...]` (**kontrol edin**: listede
   kartınızın `sm_50` / `sm_61` değeri yoksa bu sürüm de kartı desteklemiyordur; `FINSIGHT_DEVICE=auto`
   o durumda CPU'da kalır ve nedenini yazar).
3. `transformers` / `sentence-transformers` sürümünüzün torch 2.14 ile uyumlu olduğunu kontrol
   edin (`python -m pytest -q` ve bölüm 7 adım 4). Uyumsuzlukta bu paketlerin bir önceki sürümüne
   inmek gerekebilir.
4. Geri dönüş: `pip uninstall -y torch; pip install torch --index-url https://download.pytorch.org/whl/cpu`.
5. 2 GB VRAM'de "out of memory" uyarısı görülürse: `$env:FINSIGHT_FINBERT_BATCH="16"`.

## 9. Reddedilen veya uygulanmayan seçenekler

- **int8 dinamik kuantizasyon** (torch / ONNX / OpenVINO): olumsuz → nötr kayması (Aşama 3); aynı
  risk taşıdığı için yeniden önerilmez. CUDA'da zaten uygulanmaz.
- **fp16'nın varsayılan açılması:** kullanıcının kartında kazanç yok (Maxwell yerel FP16 yok,
  Pascal 1:64) ve etiket uyumu ölçülmeden açılmamalı.
- **`max_length` 256:** Aşama 3 ölçümünde kazanç yok (256'yı aşan cümle yok).
- **BetterTransformer:** kullanımdan kaldırıldı; SDPA zaten varsayılan.
- **`set_num_interop_threads`:** eager BERT'te etkisiz; yanlış zamanda çağrılırsa hata riski.
- **Gömmede fp16:** veritabanındaki fp32 vektörlerle uzay kayması riski; gömme zaten kısa sürüyor.
- **Embedder'da ek uzunluk sıralaması:** sentence-transformers `encode` bunu kendisi yapıyor
  (kontrol komutu bölüm 7); çift sıralama kazanç getirmez.
- **torch.compile:** değerlendirilmedi (Windows desteği ve değişken uzunluklu grupların yeniden
  derleme maliyeti ayrıca araştırılmalı).
- **ONNX Runtime GPU / OpenVINO fp32:** değerlendirilmedi; OpenVINO fp32 Intel CPU'da bir sonraki
  aday olabilir (sbert.net akış şeması), ancak yeni bağımlılık ve ölçüm gerektirir.

## 10. Gerçek veriyle doğrulanmamış kısımlar

- GPU, fp16 ve ONNX yolları bulutta yalnızca **sahte** torch / CUDA / optimum nesneleriyle test
  edildi; gerçek hız ve uyum kullanıcı bilgisayarında ölçülmeli (bölüm 7).
- `_cuda_usable` mimari denetimi `torch.cuda.get_arch_list()` biçimine (`sm_50`, `compute_80`)
  dayanır; deneme işlemi ikinci güvencedir.
- X515JF'nin GPU'su (MX130 mı MX330 mı) ve MX130'un compute capability değeri satıcı / genel
  kaynaklardan; `nvidia-smi` ile doğrulanmalı. torch 2.14 cu126'nın `sm_50` içerdiği PyTorch
  duyurusundan çıkarımdır.
- HTML → metin hızlanması sentetik belgelerde ölçüldü; gerçek MSFT iXBRL belgelerinde
  `bench_pipeline --dir` ile doğrulanmalı (beklenen: aynı metin, daha kısa süre). Eşitlik iki
  yolun aynı libxml2 ayrıştırıcısını kullanmasına dayanır; testler fixture'lar, sentetik
  belgeler ve uç durumlarla birebir eşitliği doğruluyor.
- `optimum[onnxruntime]` paket adı ve `ORTModelForSequenceClassification.from_pretrained(...,
  export=True)` API'si sürüme göre değişebilir (kontrol edin).

FinSight AI eğitim ve araştırma amaçlı bir prototiptir; bu belge ve ölçümler yatırım tavsiyesi
değildir.
