# FinSight AI — Güvenlik Düzeltmeleri Raporu (Savunma / Mavi Takım)

- **Tarih:** 2026-11-25
- **Rol:** `security-engineer` (`.claude/agents/security-engineer.md`)
- **Girdi:** `docs/guvenlik/denetim-raporu.md` (SEC-01 … SEC-19),
  `docs/guvenlik/saldiri-simulasyonu.md` (A01 … A58, RT-01, RT-02; §6 düzeltme listesi)
- **İlke:** FinSight yerel, tek kullanıcılı bir masaüstü uygulamasıdır. Kimlik doğrulama
  eklenmedi; bunun yerine orantılı önlemler alındı: yalnızca yerel `Host`, başka siteden gelen
  durum değiştiren isteğin reddi, güvenlik başlıkları, kaynak (iş / soru / LLM / SEC hızı)
  sınırları ve LLM çıktısının doğrulanması.

## 1. Sonuç

| | Başlangıç | Şimdi |
| --- | --- | --- |
| `cd backend && python -m pytest -q` (bulut) | 659 passed, 9 skipped, 50 xfailed | **780 passed, 10 skipped, 8 xfailed** |
| `ruff check backend/finsight backend/tests` | temiz | temiz |

Buluttaki 8 `xfail`: CSV-DID (6 parametre), SEC-11 (1), SEC-15 kilit dosyası (1) — bu ajanın
dosya sahipliği dışında (bkz. §4). Kullanıcının bilgisayarında bunlara SEC-01/A03 (1, bilinçli)
eklenir; bulutta atlanan HTTP dosyasındadır.

Bulgu bazında: **19 denetim bulgusu + 2 kırmızı takım varyantı (RT-01, RT-02) + CSV-DID.**
Kapatılan: SEC-01 (A03 hariç), 02, 03, 04, 05, 06, 07, 08, 09, 10, 12, 13, 14, 17, 18, 19,
RT-01, RT-02; SEC-15 kısmen. Açık: SEC-11 (arayüz ajanında), SEC-15'in kilit dosyası kısmı,
CSV-DID (`views.py` sahipliği dışında), SEC-16 (zaten Aşama 4 koduyla kapalıydı; kullanıcıda
doğrulanacak).

## 2. Önce / sonra özeti (öğrenci raporu için)

| Bulgu | Saldırı (önce) | Önlem (sonra) | Neden bu önlem |
| --- | --- | --- | --- |
| SEC-01 CSRF | Herhangi bir site `fetch(no-cors)` ile `POST /ingest` açabiliyordu | `Origin` / `Sec-Fetch-Site` başka siteyi gösterirse 403 | Tarayıcılar bu başlıkları sahteleyemez; curl/betikler (Origin'siz) etkilenmez |
| SEC-03 DNS rebinding | `Host: rebind.kotu.example` kabul ediliyordu → API okunabilir, LLM kotası harcanabilir | Yalnızca `127.0.0.1`, `localhost`, `::1` Host'u (+ `FINSIGHT_ALLOWED_HOSTS`) | Rebinding'in tek imzası yabancı Host'tur; kimlik doğrulamaya gerek kalmaz |
| SEC-03 ek | `/ask` sınırsız ücretli LLM çağrısı | Dakikada 20 soru (429) + günlük 300 LLM'li soru (sonra LLM'siz cevap) | Saldırı başarılı olsa bile maliyet tavanı |
| SEC-02 DoS | 40 ingest isteği tüm sunucuyu (health dahil) kilitliyordu | Ayrı iş havuzu: 2 çalışan + 8 kuyruk, fazlası 429 | İstek havuzu ile iş havuzu ayrıldı; kaynak tüketimi sınırlı |
| SEC-10 | Yavaş SEC'te arama istekleri kilitte birikiyordu | Tek uçuş: ağ çağrısı kilit dışında, diğerleri beklemez | SEC-02 ile aynı tükenme yolunu kapatır |
| SEC-04 tavsiye | "Portföyüme eklemeli miyim?", "alım fırsatı [1]" filtreden geçiyordu | TR/EN sözlük genişletildi, yanlış pozitif testleriyle | Projenin değişmez kuralı: yatırım tavsiyesi yok |
| SEC-05 uydurma sayı | "Hasılat 999 milyar [1]" kaynakta olmasa da gösteriliyordu | Atıflı cümledeki her sayı atıf yapılan kaynakta aranır (biçim normalize) | NFR-06: her sayı kaynağına izlenebilir |
| SEC-06 istem enjeksiyonu | 10-K içindeki "Ignore previous instructions" + itaatkâr LLM | Soru da etkisizleştirilir; talimatlı kaynak işaretlenir, sayı doğrulamasında ve alıntıda kullanılmaz | Asıl savunma çıktı doğrulaması (SEC-04/05) |
| SEC-07 SEC hız sınırı | API + CLI + refresh toplamda 16–24 istek/sn; 429 sonrası diğer işler devam | Önbellek klasöründe dosya kilitli ortak bütçe (8/sn toplam) + 429/403'te 10 sn küresel duraklama; yönlendirme adımları da sayılır | SEC IP'yi engellemesin (NFR-01…03) |
| SEC-08 ReDoS | 100 000 nokta → dakikalarca | Noktalama dizileri bölmeden önce 3 karaktere kısaltılır → < 1 ms | Doğrusal zaman, cümle sınırı kararı değişmez |
| SEC-09 önbellek zehirlenmesi | 200 + HTML gövde JSON önbelleğine yazılıyordu | JSON ayrıştırılamazsa yazılmaz; bozuk eski kayıt yok sayılır; TTL sınıfı yol + host ile | Bozuk veri kalıcılaşmaz, kendiliğinden iyileşir |
| RT-02 | `accession_no="../../.."` belgeyi klasör dışına yazabilirdi | CIK/accession biçim denetimi + `realpath` kapsama denetimi | Derinlemesine savunma |
| SEC-12 clickjacking | Başlık yoktu | CSP (`frame-ancestors 'none'`, satır içi betik yalnızca SHA-256 ile), XFO DENY, nosniff, no-referrer | Çerçeveleme engellenir; olası XSS'in etkisi sınırlanır |
| SEC-13 | `*.db-wal`, `.env.local` git'e girebilirdi | `.gitignore` genişletildi (`!.env.example` korunur) | Sorular / anahtar sızmaz |
| SEC-14 Docker | `-p 8000:8000`, root, varsayılan parola | `-p 127.0.0.1:8000:8000`, `USER finsight` (uid 10001), `POSTGRES_PASSWORD:?` | Ağ maruziyeti ve yetki en aza |
| SEC-15 | Starlette alt sınırı yok | `starlette>=0.49.1` (CVE-2025-62727) | Eski sanal ortamda açıklı sürüm kalmasın |
| SEC-17 | `FINSIGHT_CORS_ORIGINS=*` kabul; anahtar `http://` uzağa gidebilirdi | `*`/`null`/şemasız köken yok sayılır; uzak `http://` + anahtar → LLM kapalı | Yanlış yapılandırma güvenliği delmesin |
| SEC-18 / RT-01 | `..`, `-`, `AAPL\n` ticker'ları geçiyordu | `TICKER_RE.fullmatch` + harf/rakamla başlama | Çöp iş kaydı ve günlük sahteciliği yok |
| SEC-19 | Sorular süresiz saklanıyordu, kapatılamıyordu | `FINSIGHT_QA_LOG=0` | Gizlilik tercihi kullanıcıda |

## 3. Bulgu bazında ayrıntı

Durum sütunu: **Bulutta kanıtlandı** = test bu ortamda gerçekten koştu ve geçti.
**Kullanıcıda doğrulanacak** = FastAPI/SQLAlchemy gerektiren test; xfail işareti kod okumasıyla
kaldırıldı, kesin sonuç kullanıcının bilgisayarındaki koşudur. Bu maddelerin çekirdek mantığı
ayrıca `tests/security/test_defense_cloud.py` içinde (ilgili sınıflar `api.py` / `jobs.py`
kaynağından AST ile alınıp Starlette `TestClient` ile) bulutta koşturuldu.

### SEC-01 — CSRF (Orta) — kapatıldı (A03 hariç, bilinçli)

- **Değişiklik:** `backend/finsight/api.py:545` `LocalGuardMiddleware` (en dışta,
  `api.py:769`): POST/PUT/PATCH/DELETE için `Sec-Fetch-Site: cross-site` veya izinli olmayan
  `Origin` (`null` dahil) → 403. İzinli kökenler: `get_cors_origins()` + `SELF_ORIGINS`
  (`http://127.0.0.1:8000`, `http://localhost:8000`) + isteğin kendi kökeni (doğrulanmış
  Host'tan; başka portta çalıştırılsa da arayüz bozulmaz).
- **Testler:** `test_attack_csrf_host.py::test_sec01_cross_origin_bodiless_ingest_rejected`,
  `::test_sec01_sec_fetch_site_cross_site_rejected`, `::test_sec01_text_plain_form_post_rejected`
  (xfail kaldırıldı; kullanıcıda doğrulanacak), kontrol `::test_sec01_legit_ingest_still_works`.
  Bulutta: `test_defense_cloud.py::test_guard_rejects_cross_site_post[5]`,
  `::test_guard_allows_legit_post[6]`, `::test_guard_get_from_other_origin_not_blocked`.
- **Kalan risk / bilinçli karar (A03, `test_sec01_bodiless_ingest_requires_json` xfail kalır):**
  Kırmızı takım "ingest gövdesi zorunlu JSON olsun" önerdi. Origin'siz, gövdesiz bir POST
  curl'ün isteğiyle aynıdır ve sözleşmede geçerlidir (`test_api.py`'deki gövdesiz ingest ve 503
  testleri bunu korur; görev tanımında Origin'siz istemcilerin çalışmaya devam etmesi istendi).
  Tüm güncel tarayıcılar (Chrome 2019+, Firefox 70+, Safari) başka siteden gelen POST'ta
  `Origin` gönderir, bu yüzden tarayıcı kaynaklı CSRF 403 ile kapalıdır. Gövdeyi zorunlu yapmak
  istenirse: `IngestRequest | None` → `IngestRequest`, `test_api.py:243,260,268` ve sözleşme
  güncellenmeli.

### SEC-03 — DNS rebinding (Orta) — kapatıldı

- **Değişiklik:** `api.py:545` aynı ara katman: Host adı (port hariç) izinli listede değilse
  400. Liste `config.py:130` `get_allowed_hosts()` (`127.0.0.1`, `localhost`, `::1` +
  `FINSIGHT_ALLOWED_HOSTS`; `*` reddedilir) veya `create_app(allowed_hosts=...)`. Starlette
  `TrustedHostMiddleware` yerine tek ara katman seçildi: Host + Origin + başlıklar tek yerde,
  reddetme yanıtı da Türkçe JSON ve güvenlik başlıklı.
- **Test uyarlaması:** `tests/test_api.py` (`LOCAL_BASE`, 7 `TestClient`) ve
  `tests/test_api_freshness.py:63` artık `base_url="http://127.0.0.1:8000"` kullanır
  (`testserver` izinli listeye eklenmedi; tek etiketli ad yerel ağ arama alanıyla
  çözülebileceğinden).
- **Testler:** `test_attack_csrf_host.py::test_sec03_dns_rebinding_host_rejected`,
  `::test_sec03_dns_rebinding_cannot_reach_ask` (kullanıcıda), kontrol
  `::test_sec03_loopback_hosts_allowed`. Bulutta: `test_defense_cloud.py::test_guard_rejects_foreign_host[4]`
  (`testserver`, `192.168.1.20`, `127.0.0.1.nip.io` dahil), `::test_guard_allows_loopback_hosts[4]`.
- **Ek önlem (A12):** `api.py:422` `SlidingWindowLimiter` (`api.py:613`): dakikada
  `FINSIGHT_ASK_PER_MINUTE` (20) soru, aşılırsa 429 + `Retry-After`. `api.py:433`
  `DailyBudget` (`api.py:636`): LLM'li günlük soru `FINSIGHT_LLM_DAILY_LIMIT` (300); dolunca
  cevap LLM'siz üretilir ve `warning` döner (hata değil, uygulama kullanılabilir kalır).
  Test: `::test_sec03_ask_rate_limited` (kullanıcıda); bulutta
  `test_defense_cloud.py::test_ask_sliding_window_limit`, `::test_llm_daily_budget`.
- **Kalan risk:** Sınırlar süreç içidir (`uvicorn --workers N` ile N katı); tek kullanıcılı
  kullanımda yeterli.

### SEC-02 — Eşzamanlı ingest ile havuz tükenmesi (Orta) — kapatıldı

- **Değişiklik:** `backend/finsight/jobs.py:525` `JobRunner`: ayrı `ThreadPoolExecutor`
  (`FINSIGHT_MAX_CONCURRENT_JOBS`, varsayılan 2) + kuyruk (`FINSIGHT_MAX_QUEUED_JOBS`, 8);
  `config.py:154-176`. `api.py:256` ingest: çalışan iş varsa o döner; yoksa yer ayrılır
  (`api.py:283`), dolu ise **429 + `Retry-After: 30` + Türkçe mesaj**; iş `runner.submit` ile
  ayrı havuzda çalışır, yanıt sonrası `_await_job` (`api.py:321`) iş parçacığı tutmadan bekler
  (TestClient davranışı korunur). `create_job_if_idle` / JobLock mantığı aynen kullanılır;
  yer, iş oluşturulmazsa (`created=False`, 409, hata) geri bırakılır. Uygulama kapanırken
  kuyruktaki işler iptal edilir (lifespan).
- **Kod incelemesi ekleri:** `_ingest_lock` yerine şirket bazında 64 şeritli kilit
  (`api.py:76-81`, `api.py:278`): SQLite `busy_timeout` (15 sn) sırasında diğer şirketler
  beklemez. `create_job_if_idle` sırasında `OperationalError` ("database is locked") → **503 +
  `Retry-After: 5`** (`api.py:296`), ayrılan yer bırakılır. Kayıtsız şirkette de CIK tabanlı
  kilit: SEC ticker haritası önbellekteyse `cik=` geçilir (`api.py:266`, `api.py:293`).
- **Testler:** `test_attack_dos.py::test_sec02_ingest_flood_exhausts_threadpool` (xfail
  kaldırıldı; kullanıcıda), yeni `::test_ingest_capacity_full_returns_429`,
  `::test_ingest_db_locked_returns_503`, `::test_ingest_passes_sec_cik_to_job_lock`
  (kullanıcıda). Bulutta: `test_defense_cloud.py::test_job_flood_does_not_starve_request_pool`
  (42 eşzamanlı istek: en fazla 2 çalışan, 10 kabul / 32 × 429, `/health` < 1 sn),
  `::test_job_runner_capacity_and_release`.
- **Kalan risk:** Sınır süreç içidir; otomatik yenileyici (`refresh.AutoRefresher`) kendi tek
  iş parçacığında çalışır ve bu sayıma girmez (en fazla +1 iş).
- **Arayüz notu:** `frontend/src/api.ts` 429/503 yanıtındaki `detail` metnini kullanıcıya
  göstermeli (genel hata işleyicisi `detail` gösteriyorsa ek iş gerekmez).

### SEC-10 — Ticker haritası kilidi (Düşük) — kapatıldı

- **Değişiklik:** `api.py:136` `_TickerMapCache.get`: ağ çağrısı kilit dışında; aynı anda tek
  yenileyici, diğerleri mevcut haritayı / `None` (yerel arama) hemen alır.
- **Testler:** `test_attack_dos.py::test_sec10_slow_sec_does_not_block_other_searches`
  (kullanıcıda); bulutta `test_defense_cloud.py::test_ticker_map_single_flight`,
  `::test_ticker_map_failure_recorded`.

### SEC-04 — Tavsiye korumasının aşılması (Orta) — kapatıldı

- **Değişiklik:** `backend/finsight/rag.py:130-145` soru kalıpları (yatırayım, portföye ekle,
  "almak için doğru zaman", hisse ucuz mu, pozisyon alayım, purchase/accumulate/go long/short,
  good time to buy, undervalued …); `rag.py:166-186` cevap cümlesi kalıpları (alım fırsatı,
  portföye ekle, pozisyon almak, cazip + hisse/fiyat/değerleme, recommend purchasing,
  accumulate shares, investors may want to …, buying opportunity, undervalued …).
- **Testler (bulutta kanıtlandı):** `test_attack_rag.py::test_sec04_advice_question_paraphrase_is_detected[5]`,
  `::test_sec04_paraphrased_advice_question_end_to_end`, `::test_sec04_advice_sentence_is_filtered[5]`,
  `::test_sec04_llm_advice_sentence_removed_from_answer`. Yanlış pozitif kontrolleri:
  `::test_sec04_false_positive_control[3]`, `test_defense_cloud.py::test_advice_question_false_positives[6]`,
  `::test_advice_sentence_false_positives[4]` ("ürün portföyü", "uzun vadeli borç",
  "short-term investments", "hisse geri alımı" geçerli kalır).
- **Kalan risk:** Sözlük tabanlı filtre doğası gereği eksiktir (yeni ifade biçimleri). İleride:
  LLM ile ikinci sınıflandırma adımı. Tavsiye cümlesi filtrelendiğinde `advice_refused=true`
  yapılması önerisi uygulanmadı: mevcut `test_rag.py::test_llm_advice_sentences_and_uncited_numbers_removed`
  bu alanın yalnızca soru niyetini gösterdiğini sözleşme olarak sabitliyor.

### SEC-05 — Atıflı uydurma sayı (Orta) — kapatıldı

- **Değişiklik:** `rag.py:365` `_numbers_supported`, `rag.py:383` `filter_answer(answer,
  sources=None)`: atıflı her cümledeki sayılar (atıf numaraları, form adları ve "Item 1A"
  hariç) **atıf yapılan** kaynak(lar)ın sayı kümesinde aranır; yoksa cümle çıkarılır.
  Normalizasyon (`rag.py:210`, `number_set`): TR/EN ondalık-binlik (`412,5` ~ `412.5`,
  `1.234.567` ~ `1,234,567`), ölçek (`999 milyar` ~ `999 billion` ~ `999.000 milyon`), yüzde
  (`%34` ~ `34%`), İngilizce sayı kelimeleri (`two` ~ `2`). Kaynak metni = parça metni + üst
  veri (form, tarih, bölüm; `rag.py:498`). Parametresiz çağrı eski davranışı korur.
- **Testler (bulutta kanıtlandı):** `test_attack_rag.py::test_sec05_fabricated_number_with_citation_is_removed`,
  `::test_sec05_fabricated_percentage_with_citation_is_removed`; kontrol
  `::test_sec05_number_present_in_source_is_kept`, `test_defense_cloud.py::test_number_check_normalizes_formats[5]`,
  `::test_number_check_rejects_wrong_source`.
- **Kalan risk:** LLM kaynaktaki sayıyı farklı birime çevirir veya hesaplarsa (ör. "yaklaşık
  %6 büyüme") cümle çıkarılır (muhafazakâr; sistem istemi kural 7 zaten bunu yasaklıyor).
  Sayının doğru bağlamda kullanıldığı (ör. 2024 değeri 2025 diye) denetlenmez.

### SEC-06 — Dolaylı istem enjeksiyonu (Düşük) — kapatıldı

- **Değişiklik:** `rag.py:280` soru metni `_neutralize` edilir (`<source>` yapısı bozulamaz).
  `rag.py:194` `_INJECTION_MARKER` ("ignore/disregard previous instructions", büyük harf
  `SYSTEM:`/`ASSISTANT:`, `you are now a`, `<system>`, "önceki talimatları …"): işaretli
  kaynak istemde veri olarak bırakılır ama "talimat içeriyor, uygulama" uyarısı eklenir;
  `strip_injection` (`rag.py:246`) ile işaretten sonrası sayı doğrulamasında kaynak sayılmaz
  (`rag.py:503`) ve çıkarımsal modda alıntılanmaz (`rag.py:460`).
- **Testler (bulutta kanıtlandı):** `test_attack_rag.py::test_sec06_indirect_prompt_injection_from_filing_text`,
  `::test_sec06_question_cannot_break_source_structure`; kontrol
  `::test_sec06_source_tag_breakout_is_neutralized` ve `test_rag.py::test_source_tags_in_chunk_text_are_neutralized`
  (talimat metni veri olarak istemde korunur).
- **Kalan risk:** Kalıp dışı enjeksiyon metni LLM'e gider; savunma çıktı doğrulamasıdır
  (SEC-04/05). Renkle gizlenmiş HTML metni `html_to_text`'te ayıklanmıyor.

### SEC-07 — Süreçler arası SEC hız sınırı (Orta) — kapatıldı

- **Değişiklik:** `backend/finsight/rate_limiter.py:115` `FileRateLimiter`: durum
  `<SEC_CACHE_DIR>/.ratelimit`, erişim `.ratelimit.lock` üzerindeki işletim sistemi kilidiyle
  (POSIX `fcntl.flock`, Windows `msvcrt.locking` bloklamayan deneme + kısa bekleme;
  `refresh.ProcessLock` kalıbı). Duvar saati; bozuk / gelecekteki değer yok sayılır; dosyaya
  erişilemezse süreç içi sınırlayıcıya düşülür. `rate_limiter.py:220` `shared_file_limiter`:
  klasör başına tek örnek. `jobs.py:166` `shared_limiter()` (API + `finsight.refresh` +
  otomatik yenileyici) bunu kullanır. **CLI:** `finsight.ingest` kendi `RateLimiter(get_max_rps())`'ını
  veriyor; `sec_client.py:220` gerçek saatli `RateLimiter` + `FileCache` ile kurulan her
  istemciyi aynı klasörün paylaşılan sınırlayıcısına yükseltir (sahte saatli test
  sınırlayıcıları yükseltilmez). Böylece API, CLI ve refresh tek 8/sn bütçeyi paylaşır.
- **Küresel duraklama:** `sec_client.py:274` 429 veya 403'te `limiter.pause(10 sn)`
  (`rate_limiter.py:58`, `:193`): sınırlayıcıyı paylaşan tüm istekler ve süreçler bekler.
  10 sn seçildi (önerilen 60 sn yerine): SEC penceresi 1 sn'dir; `/freshness` gibi tek denemeli
  istekler istek iş parçacığını en fazla 10 sn tutar. Mevcut üstel geri çekilme aynen sürer.
- **Kod incelemesi eki — yönlendirmeler:** `sec_client.py:96` `follow_sec_redirects(...,
  before_hop=)`; `SecClient` (`sec_client.py:229-235`) her yönlendirme adımında
  `limiter.acquire()` + `request_count += 1` çalıştırır (tek `get_bytes` artık 6 isteği
  sınırsız yapamaz).
- **Testler:** `test_attack_files.py::test_sec07_429_pauses_other_clients_sharing_limiter`
  (bulutta kanıtlandı), `::test_sec07_two_processes_share_sec_rate_limit` (SQLAlchemy gerekir;
  kullanıcıda). Bulutta: `test_defense_cloud.py::test_api_and_cli_processes_share_rate_limit`
  (iki gerçek süreç: API yolu + CLI yolu, 1 sn'de ≤ 9), `::test_file_limiter_spacing_and_pause_shared_between_instances`,
  `::test_file_limiter_corrupt_state_is_bounded`, `::test_cli_sec_client_upgraded_to_shared_limiter`,
  `::test_redirect_hops_are_rate_limited_and_counted`.
- **Kalan risk:** Farklı `SEC_CACHE_DIR` kullanan süreçler bütçeyi paylaşmaz (belgelendi).
  Önerilen açık düzeltme (backend ajanı, `ingest.py:214`): `limiter=jobs.shared_limiter()`.
  `Retry-After` başlığı fetcher arayüzünde olmadığı için kullanılmıyor.

### SEC-08 — ReDoS (Düşük) — kapatıldı

- **Değişiklik:** `backend/finsight/text_extract.py:509` `_PUNCT_RUN` ve `:557`:
  `split_sentences` her satırda 4+ uzunluktaki `[.!?]` dizilerini ilk 3 karaktere kısaltır
  (doğrusal). lxml yolu ve derinlik koruması değiştirilmedi.
- **Testler (bulutta kanıtlandı):** `test_attack_parsing.py::test_sec08_redos_dot_run_in_filing_text`
  (100 000 nokta: 0.2 ms), `::test_sec08_redos_mixed_punctuation_run`; kontrol
  `::test_sec08_normal_text_still_split`, `tests/test_text_extract*.py`, `test_perf_asama4.py`
  geçiyor.

### SEC-09 + RT-02 — Önbellek zehirlenmesi, belge yolu (Düşük) — kapatıldı

- **Değişiklik:** `sec_client.py:241-247` `get_json` → `_fetch(parse=json)`: ayrıştırılamayan
  200 yanıtı önbelleğe **yazılmaz**, `SecError` yükselir; önbellekteki bozuk kayıt okunmaz,
  SEC'ten yeniden alınır (eski zehirli önbellek kendiliğinden iyileşir).
  Kod incelemesi eki: `sec_client.py:116` `cache_ttl_for` artık `urlsplit` ile yalnızca
  `https://*.sec.gov` + yol `/Archives/edgar/data/` önekini süresiz sayar (sorgu
  parametresindeki eşleşme sayılmaz); dizin sayfaları (`index.json`, `-index.htm`,
  `-index-headers.html`, klasör) `_MUTABLE_ARCHIVE_RE` (`sec_client.py:112`) ile JSON TTL alır.
  RT-02: `jobs.py:214-235` CIK (`\d{1,10}`) ve accession (tiresiz 18 rakam) doğrulanır,
  `realpath` ile belge klasörü içinde kalma denetlenir; aksi halde `local_path=None`
  (analiz sürer).
- **Testler:** `test_attack_files.py::test_sec09_html_error_page_not_cached_for_json_url`
  (bulutta kanıtlandı), kontrol `::test_sec09_valid_json_is_cached`;
  `::test_rt02_store_document_accession_traversal_blocked` (SQLAlchemy; kullanıcıda);
  bulutta `test_defense_cloud.py::test_cache_ttl_classes[8]`, `::test_poisoned_cache_entry_is_ignored`.
- **Kalan risk:** Yanıt boyutu üst sınırı ve belge önbelleği temizliği (`--prune`) yok.

### SEC-12 — Güvenlik başlıkları (Düşük) — kapatıldı

- **Değişiklik:** `api.py:545` ara katman tüm yanıtlara (400/403 dahil) CSP,
  `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
  `Cross-Origin-Opener-Policy: same-origin` ekler. Arayüz CSP'si `api.py:509`
  `build_app_csp`: `script-src 'self' 'sha256-…'` — `index.html`'deki satır içi tema betiğinin
  özeti başlangıçta `dist/index.html`'den hesaplanır (`api.py:518`), `'unsafe-inline'` yok;
  `style-src 'unsafe-inline'` (React `style` öznitelikleri). `/docs`, `/redoc`,
  `/openapi.json` için Swagger CDN'ine izin veren ayrı `DOCS_CSP`.
- **Testler:** `test_attack_csrf_host.py::test_sec12_security_headers_present[2]` (kullanıcıda);
  bulutta `test_defense_cloud.py::test_security_headers_and_docs_csp`,
  `::test_inline_script_hash_matches_built_index`. **Tarayıcı kontrolü (bulut):** derlenmiş
  `frontend/dist` aynı CSP başlığıyla sunulup headless Chromium (Playwright) ile açıldı: CSP
  ihlali 0, satır içi tema betiği çalıştı (`data-theme="light"`).
- **Kalan risk:** Arayüz ajanı `index.html`'deki satır içi betiği değiştirirse özet yeniden
  derlemede otomatik hesaplanır; yeni harici kaynak (CDN font vb.) eklenirse CSP'ye eklenmeli.

### SEC-13 — `.gitignore` (Düşük) — kapatıldı

- **Değişiklik:** `.gitignore:2-4` `.env.*` + `!.env.example`; `.gitignore:11-16` `*.db-wal`,
  `*.db-shm`, `*.db-journal`, `.ratelimit`, `.ratelimit.lock`.
- **Testler (bulutta kanıtlandı):** `test_attack_config.py::test_sec13_sensitive_files_are_git_ignored[6]`,
  kontrol `::test_sec13_existing_ignore_rules[3]` (`.env.example` izlenmeye devam ediyor).

### SEC-14 — Docker (Orta) — kapatıldı

- **Değişiklik:** `Dockerfile:4` `docker run ... -p 127.0.0.1:8000:8000`; `Dockerfile:45-48`
  `useradd --uid 10001 finsight` + `USER finsight`; `docker-compose.yml:9,31`
  `${POSTGRES_PASSWORD:?…}` (varsayılan parola yok); `.env.example` `POSTGRES_PASSWORD=` boş +
  açıklama, yeni güvenlik değişkenleri belgelendi.
- **Testler (bulutta kanıtlandı, dosya analizi):** `test_attack_config.py::test_sec14_documented_docker_run_binds_loopback_only`,
  `::test_sec14_container_runs_as_non_root`, `::test_sec14_compose_has_no_default_db_password`,
  kontrol `::test_sec14_compose_binds_loopback_and_hides_db`.
- **Kullanıcıda doğrulanacak:** `docker compose config` (POSTGRES_PASSWORD boşken hata vermeli),
  `docker run --rm finsight-ai id -u` → 10001. **Not:** Önceden root ile oluşturulmuş
  `seccache`/`hfcache` birimleri yazılamayabilir → `docker volume rm finsight-ai_seccache
  finsight-ai_hfcache` (yalnızca önbellek). Kapsayıcı içinde uvicorn `0.0.0.0`'a bağlanır
  (port yönlendirmesi için gerekli); Host denetimi `localhost:8000` ile gelen istekleri kabul eder.

### SEC-15 — Bağımlılıklar (Düşük) — kısmen

- **Değişiklik:** `backend/requirements.txt:3` `starlette>=0.49.1` (CVE-2025-62727).
- **Test:** `test_attack_config.py::test_sec15_requirements_pin_patched_starlette` (bulutta
  kanıtlandı); `::test_sec15_installed_starlette_not_vulnerable` (kurulu 1.7.0).
- **Açık:** `::test_sec15_frontend_lockfile_and_npm_ci` xfail kalır: `frontend/package-lock.json`
  arayüz ajanının alanında ve npm kayıt deposu bulutta erişilemez. Kilit dosyası üretildikten
  sonra `Dockerfile:18` `npm install` → `npm ci` yapılmalı (kilit olmadan `npm ci` derlemeyi
  bozar, bu yüzden şimdi değiştirilmedi). `pip-audit` / `requirements.lock` önerisi açık.

### SEC-17 — Yapılandırma (Bilgi) — kapatıldı

- **Değişiklik:** `config.py:99-122` `get_cors_origins()` yalnızca `http(s)://ana-makine[:port]`
  kabul eder; `*`, `null`, şemasız, yollu değer uyarıyla atlanır. `llm.py:224`
  `base_url_is_safe`, `llm.py:280`: `OPENAI_BASE_URL` uzak `http://` + anahtar → LLM kapalı
  (uyarı); `http://127.0.0.1|localhost` ve `https://` geçerli; anahtarsız uzak http uyarıyla
  kabul. Yönlendirme maddesi önceden kapatılmıştı.
- **Testler (bulutta kanıtlandı):** `test_attack_config.py::test_sec17_cors_wildcard_or_malformed_origin_rejected[4]`,
  `::test_sec17_api_key_not_sent_over_plain_http`; kontrol `::test_sec17_cors_explicit_origin_still_accepted`,
  `::test_sec17_local_http_and_https_llm_allowed[3]`, `test_llm.py` (tümü).

### SEC-18 + RT-01 — Ticker doğrulaması (Bilgi) — kapatıldı

- **Değişiklik:** `api.py:58` `TICKER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-]{0,9}")`,
  `api.py:108` `fullmatch`.
- **Testler:** `test_attack_parsing.py::test_sec18_dot_dash_only_ticker_rejected[5]`,
  `::test_sec18_trailing_newline_ticker_rejected[2]` (bulutta kanıtlandı, AST);
  kontrol `::test_sec18_legit_ticker_accepted[5]` (BRK.B, BF-B, CIK),
  `::test_sec18_malformed_ticker_rejected[7]`; HTTP `test_attack_csrf_host.py::test_sec18_dash_ticker_ingest_rejected`,
  `::test_sec18_newline_ticker_rejected` (kullanıcıda).

### SEC-19 — Soru günlüğü (Bilgi) — kapatıldı

- **Değişiklik:** `config.py:178` `qa_log_enabled()`; `api.py:444` `FINSIGHT_QA_LOG=0` iken
  `repo.log_qa` çağrılmaz (varsayılan açık, geriye uyumlu).
- **Test:** `test_attack_csrf_host.py::test_sec19_qa_log_can_be_disabled` (kullanıcıda);
  bulutta `test_defense_cloud.py::test_limit_env_defaults`.
- **Kalan risk:** Süreye dayalı silme yok (README'ye eklenmesi önerilir).

### SEC-16 — Aşama 4 ön koşulları — değişiklik yok

Aşama 4 koduyla karşılanmıştı (A40–A43). Ek: yönlendirme adımlarının sayılması ve süreçler
arası hız sınırı (SEC-07) madde 3–4'ü güçlendirir. A40–A42 kullanıcıda doğrulanacak.

### Ek: `GET /facts` `latest` alanı (şeften)

`api.py:357` `views.facts_view(..., dynamic=repo.load_dynamic(session, company["id"]))` —
v1.4 `latest` alanı API yanıtına girer. Test: `tests/test_api_facts_latest.py` (kullanıcıda).

## 4. Kapatılmayanlar ve gerekçe

| Bulgu | Test (xfail kalır) | Gerekçe / öneri |
| --- | --- | --- |
| SEC-01 / A03 | `test_sec01_bodiless_ingest_requires_json` | Bilinçli: Origin'siz gövdesiz POST = curl; sözleşmede geçerli. Tarayıcı CSRF'i Origin denetimiyle kapalı (§3). |
| SEC-11 | `test_sec11_malformed_hash_does_not_crash_router` | `frontend/` arayüz ajanında. Öneri aşağıda. |
| SEC-15 (kilit) | `test_sec15_frontend_lockfile_and_npm_ci` | `package-lock.json` arayüz alanında + npm bulutta erişilemez; sonra `npm ci`. |
| CSV-DID | `test_csv_formula_injection_neutralized[6]` | `views.py` bu ajanın dosya sahipliği dışında. Bugün sömürülebilir değil (serbest metin dışa aktarılmıyor). Öneri: `views.export_csv` içinde yalnızca **str** hücreler `= + - @ \t \r` ile başlıyorsa `"'" + değer`; sayılar (`-1250.0`) dokunulmadan (`test_csv_negative_numbers_not_mangled`). |

**SEC-11 önerisi (arayüz ajanı için, `frontend/src/router.ts`):**

```ts
function safeDecode(part: string): string | null {
  try { return decodeURIComponent(part); } catch { return null; }   // URIError: %E0, %
}
// parseHash içinde her decodeURIComponent(x) → safeDecode(x); null ise
// return { name: "notFound", path: raw };
```

Ek olarak `App.tsx` kökünde bir `ErrorBoundary` (render hatasında "Sayfa açılamadı — Ana
sayfaya dön" ekranı). Test: `test_attack_parsing.py::test_sec11_*` + Playwright ile
`#/company/%E0/overview` ekran görüntüsü.

## 5. Başka ajanlara notlar

- **Backend ajanı (`ingest.py`):** `ingest.py:214` `limiter=RateLimiter(get_max_rps())` yerine
  `limiter=jobs.shared_limiter()` daha açık olur (bugün `SecClient` bunu otomatik yükseltiyor).
- **Arayüz ajanı:** 429 (`/ingest`, `/ask`) ve 503 (`/ingest`) `detail` metni gösterilmeli;
  geliştirme sunucusu `127.0.0.1:5173` / `localhost:5173` dışındaki bir adresten API'ye POST
  atarsa 403 alır (`FINSIGHT_CORS_ORIGINS` ile eklenebilir).
- **README:** yeni ortam değişkenleri (`FINSIGHT_ALLOWED_HOSTS`, `FINSIGHT_MAX_CONCURRENT_JOBS`,
  `FINSIGHT_MAX_QUEUED_JOBS`, `FINSIGHT_ASK_PER_MINUTE`, `FINSIGHT_LLM_DAILY_LIMIT`,
  `FINSIGHT_QA_LOG`) `.env.example`'da belgelendi; README'ye kısa bir "Güvenlik" bölümü
  eklenebilir.

## 6. Kullanıcının bilgisayarında (PowerShell)

```powershell
pip install -U -r backend\requirements.txt          # starlette>=0.49.1
cd backend
python -m pytest -q                                  # beklenen: SKIPPED yalnızca ML/Node kaynaklı
python -m pytest tests\security -v -rxXs             # XPASS(strict) görülürse ilgili işaret kaldırılır
cd ..
ruff check backend\finsight backend\tests
# Gerçek sunucuya saldırı simülasyonu (başka pencerede: cd backend; uvicorn finsight.api:app --port 8000)
python backend\tests\security\attack_sim.py --base-url http://127.0.0.1:8000 --json saldiri-sonuc.json
# Docker (isteğe bağlı)
docker compose config        # POSTGRES_PASSWORD boşsa hata vermeli
docker build -t finsight-ai . ; docker run --rm finsight-ai id -u   # 10001
```

HTTP katmanında beklenen: `test_attack_csrf_host.py`, `test_attack_dos.py`,
`test_api.py`, `test_api_freshness.py`, `test_api_facts_latest.py` geçer; yalnızca
`test_sec01_bodiless_ingest_requires_json` XFAIL.
