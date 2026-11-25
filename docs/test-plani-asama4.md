# Aşama 4 test planı: fonksiyonel, performans/yük ve kullanıcı testleri

4. ara rapor (Test ve değerlendirme) için çalışma belgesi. Şablondaki maddeler:
"Kapsamlı sistem testleri (fonksiyonel testler, kullanıcı testleri, performans ve yük testleri
vb.)", "Test sonuçlarının yorumlanması ve değerlendirilmesi", "Proje hedeflerinin gerçekleşme
oranı". Gereksinim kimlikleri ve kabul ölçütleri tasarım belgesinden alınmıştır
(`docs/raporlar/00-Asama1-Analiz-ve-Tasarim-Planlama.md`, Bölüm 4–5).

Belge 23 Kasım 2026 tarihli depo durumunu yansıtır. Aynı gün başka ajanlar TTM / tazelik
(`docs/asama4-sozlesme.md`) ve güvenlik değişiklikleri üzerinde çalışıyordu; sayılar bu
değişikliklerle birlikte yeniden alınmalıdır (bkz. §6).

## 1. Test katmanları ve nerede koştuğu

| Katman | Araç | Bulut oturumu | Kullanıcı bilgisayarı |
| --- | --- | --- | --- |
| Birim testleri (saf modüller) | pytest | Koşar | Koşar |
| Entegrasyon (veritabanı, API, arka plan işi) | pytest + FastAPI TestClient + bellek içi SQLite | Atlanır (`importorskip`: fastapi/sqlalchemy yok) | Koşar |
| Uçtan uca fonksiyonel (saf zincir) | `tests/test_e2e_pipeline.py` | Koşar | Koşar |
| NFR denetimleri | `tests/test_nfr_*.py` | Koşar | Koşar |
| Performans / yük | `python -m finsight.bench_api` (çalışan sunucuya karşı) | Yalnızca sahte sunucuyla araç denemesi | **Asıl ölçüm** |
| Analiz hattı süresi | `python -m finsight.bench_pipeline`, `bench_sentiment` | Yedek modellerle | FinBERT ile |
| Arayüz | esbuild + Playwright ekran görüntüsü (sahte API) | Koşar | `npm run typecheck` |
| Kullanıcı testi | Görev listesi + SUS + gözlem formu (§5) | — | 5–8 katılımcı |

Sabitler: testler ağa çıkmaz (SEC ve LLM sahte istemcilerle), tarihler sabit (`date(2026, 11, 23)`
veya bugüne göre üretilen fixture), rastgelelik sabit tohumlu. Sentetik şirketler (Example Corp,
Example June Corp, "Kestrel Dynamics" belge metinleri) gerçek şirket değildir.

## 2. Uçtan uca fonksiyonel senaryolar (FR)

Önkoşul (tüm otomatik E2E senaryoları için): `integration_support.FakeSec` sahte SEC'i
(ticker listesi, submissions, companyfacts, iki 10-K + bir 10-Q HTML'i); sözlük duygu modeli
(`lexicon-fallback`), hashing gömme, LLM yok. Manuel senaryolar için önkoşul: kullanıcı
bilgisayarında `SEC_USER_AGENT` tanımlı, `python -m finsight.ingest MSFT --analyze` ve
`... AAPL --analyze` çalıştırılmış, `uvicorn finsight.api:app` açık, arayüz `npm run build` ile derlenmiş.

"Durum" sütunu: **Otomatik** = bulutta ve kullanıcı bilgisayarında pytest ile koşar;
**Otomatik (yerel)** = yalnızca fastapi/sqlalchemy kurulu ortamda koşar; **Manuel** = elle / ekran görüntüsüyle.

| No | FR | Adımlar | Beklenen sonuç (kabul ölçütü) | Otomatik test karşılığı | Durum |
| --- | --- | --- | --- | --- | --- |
| E2E-01 | FR-01 | "AAPL", "aapl ", "320193", "example", "0000000001" ile ara | AAPL → CIK 0000320193; ad ve CIK ile EXMP bulunur; olmayan sorgu boş liste | `test_e2e_pipeline::test_e2e01_search_by_ticker_name_and_cik`; `test_edgar::test_resolve_by_ticker_and_cik`; `test_views::test_search_companies_ranking`; `test_api::test_search_*` (4 test) | Otomatik + Otomatik (yerel) |
| E2E-02 | FR-02 | Şirketi topla (`collect`), başvuru listesini görünüme çevir | Yalnızca 10-K/10-Q/8-K (ve /A); her satırda form, tarih, accession; yeniden eskiye | `test_e2e02_filing_list_forms_dates_accessions`; `test_edgar::test_parse_filings_*`, `test_older_files_only_when_needed`; `test_pipeline::test_collect_end_to_end_with_fake_sec`; `test_api::test_filings_filter_by_form` | Otomatik |
| E2E-03 | FR-03 | Ana belgeyi indir, aynı URL'yi ikinci kez iste | Belge saklanır; ikinci istekte SEC'e gidilmez | `test_e2e03_document_downloaded_once_and_cached`; `test_sec_client::test_cache_prevents_second_request`; `test_jobs::test_second_run_skips_analyzed_filings_and_creates_no_duplicates`, `test_store_document_write_failure_keeps_hash` | Otomatik + Otomatik (yerel) |
| E2E-04 | FR-04 | companyfacts'ten kalemleri çıkar, `/facts` görünümü | Her değer accession, form, dönem ve XBRL etiketiyle (türetilmişse etiket listesi); değersiz dönem listelenmez | `test_e2e04_facts_traceable_to_accession_tag_and_period`; `test_xbrl` (19 test); `test_views::test_facts_view_*`; `test_api::test_facts_have_source_and_no_duplicates` | Otomatik |
| E2E-05 | FR-05 | Gelir serisinin YoY değerlerini elle hesapla | YoY = (cari − önceki)/\|önceki\|; ilk dönem `null` | `test_e2e05_yoy_matches_hand_calculation`; `test_xbrl::test_yoy_*` (5 test, 52–53 hafta ve eksik yıl) | Otomatik (YoY). **QoQ: kapsanmıyor** (bkz. §4) |
| E2E-06 | FR-06 | Oranları hesapla, net kâr marjını bağımsız hesapla | 8–10 oran, formül ve birim; eksik veride `null` + neden, 0 yazılmaz | `test_e2e06_*` (2 test); `test_ratios` (20 test: sıfır payda, negatif özkaynak, eksik girdi) | Otomatik |
| E2E-07 | FR-07 | 10-K ve 10-Q'yu analiz et | 10-K'da 1A ve 7, 10-Q'da Part I Item 2 | `test_e2e07_sections_extracted_for_10k_and_10q`; `test_text_extract` (29 test) | Otomatik (sentetik). **"10 şirketin 8'inde elle kontrol" ölçütü: Manuel, yapılmadı** |
| E2E-08 | FR-08 | Bölüm cümlelerini sınıflandır | Her cümle etiket + olasılık; bölüm özeti net ton; model adı yanıtta | `test_e2e08_every_sentence_has_label_and_probability`; `test_sentiment` (15), `test_sentiment_perf` (27) | Otomatik (yedek model). FinBERT: kullanıcı bilgisayarı |
| E2E-09 | FR-09 | Eski ve yeni 10-K risk bölümünü karşılaştır | Eklenen paragraf "added"; gruplar ayrı ve sıralı; değişmeyenler sayılır | `test_e2e09_new_risk_paragraph_listed_as_added`; `test_risk_diff` (11) | Otomatik |
| E2E-10 | FR-10 | İlgili soru, ilgisiz soru, tavsiye sorusu | Kaynaklı cevap (accession, bölüm, bağlantı); ilgisizde "bulunamadı" + boş kaynak; tavsiye reddi | `test_e2e10_*` (3 test); `test_rag` (30); `test_api::test_ask_*` (3) | Otomatik; gerçek LLM ile manuel (anahtar gerekli) |
| E2E-11 | FR-11 | Skoru hesapla | Ağırlık toplamı 100; her bileşen puan + gerekçe; toplam = kullanılabilen bileşenlerin ağırlıklı ortalaması | `test_e2e11_*` (2 test); `test_scoring` (17) | Otomatik |
| E2E-12 | FR-12 | Finansallar sekmesini aç (1440 ve 820 px) | Gelir, net kâr, borç grafikleri çizilir; konsol hatası 0 | — (yalnızca `test_views::test_facts_view_*` veri şekli) | **Manuel** (Playwright ekran görüntüsü) |
| E2E-13 | FR-13 | İki şirketi karşılaştır | Oran tablosu tek yanıtta; eksik oran `null` | `test_e2e13_compare_two_companies_side_by_side`; `test_views::test_compare_view_shape`; `test_api::test_compare` | Otomatik + ekran görüntüsü |
| E2E-14 | FR-14 | 8-K listesini aç | Madde numaraları ve Türkçe etiketleri görünür | `test_e2e14_8k_items_listed_with_labels`; `test_views::test_filings_view_items_list_and_labels`, `test_edgar_item_helpers` | Otomatik |
| E2E-15 | FR-15 | `POST /ingest`, `GET /jobs/{id}` ile izle | queued → running → done/error; ikinci istek aynı iş kimliği | `test_jobs` (22); `test_api::test_ingest_*` (5) | **Otomatik (yerel)** — bulutta atlanır |
| E2E-16 | FR-16 | CSV dışa aktar | UTF-8 BOM; tüm oranlar ve kaynak accession'lar; eksik hücre boş | `test_e2e16_csv_contains_all_ratios_and_source_accessions`; `test_views::test_export_*`; `test_api::test_export_csv*` | Otomatik (CSV). **PDF: uygulanmadı** |

### Manuel senaryo adımları (kullanıcı bilgisayarı)

- **M-01 (FR-12, NFR-11):** Şirket sayfası → "Finansallar" sekmesi; dil TR/EN değiştir;
  1440×900 ve 820×1180 genişlikte ekran görüntüsü. Beklenen: üç grafik dolu, eksenler okunur,
  yatay kaydırma yok, İngilizcede Türkçe etiket kalmaz.
- **M-02 (FR-07):** 10 şirketin (ör. MSFT, AAPL, NVDA, JNJ, XOM, KO, JPM, WMT, PFE, CAT) son
  10-K'sı için ayıklanan Item 1A ve Item 7'nin ilk ve son cümlesini EDGAR'daki belgeyle karşılaştır.
  Beklenen: en az 8'inde sınırlar doğru. Sonuç tablosu rapora eklenir.
- **M-03 (FR-10, NFR-05, NFR-07):** LLM anahtarıyla 20 test sorusu (10 cevaplanabilir, 5 raporda
  olmayan, 5 tavsiye niyetli); her cevap için süre, kaynak sayısı, kaynak sadakati (cevaptaki her
  iddia alıntılanan parçada var mı: evet/kısmen/hayır).
- **M-04 (FR-15):** Arayüzden yeni şirket ekle; iş durumunun ilerlediğini ve hata durumunda
  Türkçe mesaj göründüğünü gözle.

## 3. Fonksiyonel olmayan gereksinimler: ölçüm yöntemi ve hedef

| NFR | Hedef / kabul ölçütü (tasarım) | Ölçüm yöntemi | Otomatik test | Durum |
| --- | --- | --- | --- | --- |
| NFR-01 | User-Agent'sız SEC isteği kod seviyesinde engellenir | Birim testi | `test_sec_client::test_every_request_carries_user_agent`, `test_client_refuses_missing_or_anonymous_user_agent`, `test_config_requires_user_agent`; `test_e2e_sec_requests_all_identified_and_counted`; `test_api::test_ingest_without_user_agent_is_503` | Kapsanıyor |
| NFR-02 | ≤ 8 istek/sn; 100 istek ≥ 12,5 sn | Sahte saatle hız sınırlayıcı | `test_rate_limiter` (2); `test_jobs::test_shared_limiter_is_process_wide` | Kapsanıyor. Not: test 100 isteğin ilk ve sonuncusu arasında 99/8 = 12,375 sn ölçer; "12,5 sn" ölçütü 100 aralık (100. isteğin ardından bir aralık daha) olarak okunmalıdır |
| NFR-03 | 429/5xx'te üstel geri çekilme, en fazla 3 yeniden deneme, sonra hata | Sahte fetcher | `test_sec_client::test_retries_then_succeeds`, `test_gives_up_after_three_retries`, `test_404_is_not_retried`; `test_jobs::test_unknown_ticker_marks_job_error` | Kapsanıyor |
| NFR-04 | Önbellekteki şirketin özet ekranı p95 < 2 sn | `bench_api`: "ekran: şirket özeti" (overview + score art arda; üst sınır) ve `GET /overview` p95 | `test_nfr_performance::test_nfr04_overview_request_compute_share_within_budget` (saf hesap payı < 200 ms) | Araç hazır; **gerçek ölçüm kullanıcı bilgisayarında** |
| NFR-05 | Soru-cevap p95 < 10 sn (LLM dahil) | `bench_api --allow-llm` (LLM'li) ve LLM'siz; M-03'te 20 soru | `test_nfr_performance::test_nfr05_*` (erişim payı < 1 sn; `latency_ms` doğruluğu) | Araç hazır; **gerçek ölçüm kullanıcı bilgisayarında** |
| NFR-06 | Her sayı kaynağına izlenebilir | Görünüm testleri | `test_e2e04_*`, `test_e2e_nfr06_every_overview_number_has_source`, `test_e2e16_*`; `test_ratios::test_inputs_trace_accessions_including_average`; `test_views::test_source_view_direct_and_derived` | Kapsanıyor |
| NFR-07 | Cevap yalnızca getirilen parçalara dayanır | Atıf temizleme / atıfsız sayı ayıklama testleri; M-03 sadakat değerlendirmesi | `test_rag::test_invalid_citations_are_removed`, `test_llm_advice_sentences_and_uncited_numbers_removed`, `test_not_found_does_not_call_llm`; `test_e2e10_unrelated_question_says_not_found` | Mekanizma kapsanıyor; **sadakat ölçümü manuel (M-03)** |
| NFR-08 | `docker compose up` ile tek komut; temiz makinede 15 dk | Statik denetim + temiz makinede süre ölçümü | `test_nfr_static::test_nfr08_single_command_compose_has_api_and_db` | Kısmen (statik); **15 dk ölçümü manuel** |
| NFR-09 | Satır kapsamı ≥ %70 (pytest-cov) | `python -m pytest --cov=finsight -q` | — | Bulutta pytest-cov yok; yaklaşık ölçüm §6 |
| NFR-10 | Depoda gizli bilgi yok | Desen taraması + `.env` dışlama | `test_nfr_static::test_nfr10_*` (4 test); `test_llm::test_api_key_never_appears_in_errors_or_repr`; `test_jobs::test_safe_error_hides_secrets` | Kapsanıyor |
| NFR-11 | TR + EN etiketler; masaüstü ve tablet; 3 kullanıcı yardımsız | i18n anahtar eşitliği; ekran görüntüsü; kullanıcı testi (§5) | `test_nfr_static::test_nfr11_*` (2 test) | Kısmen; **kullanıcı testi manuel** |
| NFR-12 | Her analiz ekranında "yatırım tavsiyesi değildir" | Her görünümde `disclaimer`; her ekran bileşeninde `<Disclaimer` | `test_e2e_nfr12_every_analysis_view_carries_disclaimer`, `test_e2e_score_explanations_use_no_buy_sell_language`, `test_nfr_static::test_nfr12_*` (7 test); `test_rag::test_advice_*` | Kapsanıyor |

## 4. Kapsam eşlemesi özeti ve açıklar

| Kimlik | Otomatik test sayısı (yaklaşık, doğrudan ilgili) | Bulutta koşuyor mu | Açık |
| --- | --- | --- | --- |
| FR-01 | 8 | Kısmen (API testleri yerel) | — |
| FR-02 | 6 | Evet | — |
| FR-03 | 4 | Kısmen | — |
| FR-04 | 25 | Evet | — |
| FR-05 | 7 | Evet | **QoQ (çeyrekten çeyreğe) değişim yok**; Aşama 4 TTM çalışması YoY'u TTM'ye taşıyor, QoQ tasarımda kalıyor |
| FR-06 | 22 | Evet | — |
| FR-07 | 31 | Evet | **Gerçek EDGAR belgesiyle 10 şirket kabul testi yapılmadı** (M-02) |
| FR-08 | 43 | Evet (yedek model) | FinBERT doğruluğu etiketli bir veri kümesiyle ölçülmedi (yalnızca eski/yeni yöntem uyumu, bench_sentiment) |
| FR-09 | 12 | Evet | — |
| FR-10 | 36 | Evet | Gerçek LLM ile uçtan uca test yok (M-03) |
| FR-11 | 19 | Evet | Skor duyarlılık analizi yok (planlanan Hafta 12) |
| FR-12 | 0 otomatik | — | **Otomatik grafik testi yok**; ekran görüntüsüyle manuel |
| FR-13 | 3 | Kısmen | — |
| FR-14 | 3 | Evet | — |
| FR-15 | 27 | **Hayır** (yalnızca yerel) | Bulutta hiç koşmuyor |
| FR-16 | 6 | Evet | **PDF dışa aktarma uygulanmadı** |
| NFR-04/05 | 3 bütçe testi + yük aracı (56 test) | Evet (araç) | Gerçek sunucu ölçümü yapılmadı |
| NFR-07 | 4 mekanizma testi | Evet | Sadakat (faithfulness) ölçümü yok |
| NFR-08 | 1 statik | Evet | Temiz makinede kurulum süresi ölçülmedi |
| NFR-09 | — | — | pytest-cov ile ölçülmedi |
| NFR-11 | 2 statik | Evet | Kullanıcı testi yapılmadı |

Bu belgeyle eklenen otomatik testler (hepsi yeni dosyalarda; mevcut test dosyaları değişmedi):

| Dosya | Test | Kapsadığı |
| --- | --- | --- |
| `backend/tests/test_e2e_pipeline.py` | 23 | FR-01…FR-11, FR-13, FR-14, FR-16, NFR-01, NFR-06, NFR-12 (saf uçtan uca zincir) |
| `backend/tests/test_nfr_static.py` | 14 | NFR-08, NFR-10, NFR-11, NFR-12 (depo / arayüz kaynağı denetimi) |
| `backend/tests/test_nfr_performance.py` | 3 | NFR-04, NFR-05 (hesaplama payı bütçeleri) |
| `backend/tests/test_nfr_bench_api.py` | 56 | Yük testi aracının doğruluğu (adres kısıtı, karışım, yüzdelik, CLI) |

## 5. Performans ve yük testi

### 5.1 Araç: `python -m finsight.bench_api`

- Yalnızca yerel sunucu (`127.0.0.1`, `localhost`, `::1`); başka adres 2 koduyla reddedilir.
- Isınma turu (her eylem bir kez, ölçüme katılmaz), ardından `--users` iş parçacığı `--duration`
  saniye boyunca ağırlıklı karışımdan sabit tohumla seçer.
- Karışım (ağırlık): şirket özeti ekranı = overview + score (4), finansallar = facts + ratios (2),
  risk sekmesi = sentiment + risk-changes (1), filings (1), companies (1), search (1),
  ask (1), compare (1, `--compare-with` verilirse), export.csv (0,5), health (0,5).
- **SEC'e istek atan uçlar karışımda yoktur**: `POST /ingest` yalnızca `--include-ingest` ile ve
  bir kez; `/freshness` hiç. Sunucuda LLM etkinse `/ask` ücretli olduğu için yalnızca `--allow-llm` ile.
- Çıktı: uç nokta başına istek, hata, hata %, p50/p95/p99/en büyük (ms, yalnızca başarılı
  isteklerden; doğrusal aradeğerleme), RPS; toplam satırı; NFR-04 / NFR-05 karşılaştırması
  (SAĞLANDI / SAĞLANMADI / DEĞERLENDİRİLEMEDİ; hata oranı > %1 ise "sağlandı" denmez); `--json`
  ile makine okunur sonuç (sunucu `/health` bilgisi ve `--label` dahil).

### 5.2 Kullanıcı bilgisayarında çalıştırılacak ölçüm

```powershell
cd backend
$env:SEC_USER_AGENT = "FinSight-AI Ad Soyad eposta@ornek.com"
python -m finsight.ingest MSFT --analyze
python -m finsight.ingest AAPL --analyze
uvicorn finsight.api:app --port 8000          # ayrı pencerede; --reload KULLANMAYIN
New-Item -ItemType Directory -Force ..\docs\olcumler | Out-Null

# 1) Tek kullanıcı taban çizgisi (LLM'siz /ask dahil)
python -m finsight.bench_api --ticker MSFT --compare-with AAPL --users 1 --duration 60 `
  --json ..\docs\olcumler\yuk-1kullanici.json --label "<makine>, SQLite, <modeller>"
# 2) Eşzamanlı yük: 5, 10, 20 kullanıcı
python -m finsight.bench_api --ticker MSFT --compare-with AAPL --users 10 --duration 120 `
  --json ..\docs\olcumler\yuk-10kullanici.json --label "<makine>, SQLite, <modeller>"
# 3) NFR-05 (LLM dahil; her /ask ücretli bir çağrıdır, kısa tutun)
python -m finsight.bench_api --ticker MSFT --users 2 --duration 60 --allow-llm `
  --json ..\docs\olcumler\yuk-llm.json
```

Rapora yazılacaklar: makine (CPU, RAM), veritabanı (SQLite / PostgreSQL), duygu/gömme modeli,
LLM açık/kapalı, her kullanıcı sayısı için toplam RPS, özet ekranı p95, `/ask` p95, hata oranı.
Yorumlarken: kullanıcı sayısı arttıkça p95'in ne zaman 2 sn'yi aştığı (doyum noktası) ve
hangi uç noktanın en yavaş olduğu.

### 5.3 Bulutta araç denemesi (gerçek sonuç DEĞİLDİR)

`python frontend/scripts/mock_server.py 8790 --latency 0` (sabit JSON döndüren sahte sunucu)
karşısında 8 kullanıcı, 8 sn: 8.479 istek, 0 hata, ~996 RPS; overview p95 12,8 ms, özet ekranı
p95 24,2 ms. Bu değerler yalnızca aracın çalıştığını gösterir; gerçek backend veritabanı
okur ve oranları hesaplar, sahte sunucu bunların hiçbirini yapmaz. Rapora sistem başarımı olarak
**yazılmaz**. Sahte sunucuda en büyük gecikmelerin ~1 sn olması, Python `ThreadingHTTPServer`'ın
küçük bağlantı kuyruğundan (TCP yeniden deneme) kaynaklanır; FinSight backend'ine (uvicorn) ait değildir.

## 6. Kapsam ölçümü (NFR-09)

`python -m pytest --cov=finsight -q` bulut oturumunda çalışmadı: `pytest-cov` ve `coverage`
kurulu değil (PyPI erişimi yok). Yaklaşık değer, standart kütüphanenin `sys.monitoring` satır
olaylarıyla (depo dışındaki geçici bir betikle) alındı; `coverage.py` ile birebir aynı değildir
(çalıştırılabilir satır tanımı farklı), yalnızca yön göstericidir:

| Kapsam | Satır | Çalışan | Oran |
| --- | --- | --- | --- |
| Tüm paket (bulut; fastapi/sqlalchemy gerektiren 6 modül hiç çalışmadı) | 5.964 | 3.580 | %60,0 |
| Yalnızca bulutta test edilebilen saf modüller (api, db, ingest, jobs, models, repository ve aynı gün eklenen bench_pipeline hariç) | 4.012 | 3.580 | %89,2 |

En düşük saf modüller: `xbrl.py` %63,9 (Aşama 4 TTM kodu aynı gün ekleniyordu, testleri
henüz yazılmamış olabilir), `config.py` %67,0, `ratios.py` %80,6. Asıl NFR-09 ölçümü kullanıcı
bilgisayarında, atlanan entegrasyon testleriyle birlikte yapılmalıdır:

```powershell
cd backend
python -m pip install -r requirements.txt pytest-cov
python -m pytest --cov=finsight --cov-report=term-missing -q
```

## 7. Kullanıcı testi planı (NFR-11, kullanılabilirlik)

### 7.1 Katılımcılar ve ortam

- 5–8 katılımcı: en az 2 finans/işletme öğrencisi, en az 2 finans dışı (ör. mühendislik)
  öğrencisi, en az 1 bireysel yatırım deneyimi olan kişi. Projeyi geliştiren kişiler katılmaz.
- Ortam: kullanıcı bilgisayarında çalışan sistem (MSFT ve AAPL önceden işlenmiş), Chrome,
  1440 px ekran; 2 katılımcı tablet genişliğinde (820 px) yapar.
- Süre: katılımcı başına ~30 dk (5 dk tanıtım, 15–20 dk görevler, 5 dk SUS + görüşme).
- Yöntem: sesli düşünme (think-aloud). Gözlemci yardım etmez; katılımcı 3 dk takılırsa
  "yardımla tamamlandı" olarak işaretlenir ve ipucu verilir.
- Etik: katılımcıya çalışmanın amacı, verinin anonim tutulacağı ve istediği an bırakabileceği
  söylenir; ad yerine K1…K8 kodu kullanılır; ekran kaydı yalnızca onayla alınır.
  Sistemin yatırım tavsiyesi vermediği açıkça belirtilir.

### 7.2 Görevler

| No | Görev (katılımcıya okunan) | Başarı ölçütü | Hedef süre | İlgili |
| --- | --- | --- | --- | --- |
| G1 | "Microsoft'u bulun ve finansal sağlık skorunu söyleyin; skorun hangi rapora (form ve dönem) dayandığını gösterin." | Doğru skor + bant ve doğru form/dönem (ör. 10-K, 2025-06-30) söylenir | 2 dk | FR-01, FR-11, NFR-06 |
| G2 | "Skoru en çok aşağı çeken bileşeni bulun ve nedenini kendi cümlenizle açıklayın." | En düşük puanlı bileşen doğru seçilir; gerekçe metnindeki gösterge/eşik anılır | 3 dk | FR-11 |
| G3 | "Son üç yılda gelirin nasıl değiştiğini grafikte gösterin; son yılın gelir değerinin hangi başvurudan geldiğini bulun." | Grafik açılır; accession numarası veya başvuru bağlantısı gösterilir | 3 dk | FR-04, FR-05, FR-12, NFR-06 |
| G4 | "Son 10-K'da bir önceki yıla göre eklenen risklerden birini bulun." | "Eklenen" grubundan bir paragraf okunur | 3 dk | FR-09 |
| G5 | "Sisteme şirketin tedarik zinciri riskleri hakkında bir soru sorun; cevabın hangi bölümden geldiğini gösterin." | Cevap alınır; en az bir kaynağın bölümü (ör. Risk Faktörleri) söylenir | 3 dk | FR-10 |
| G6 | "Microsoft ile Apple'ın net kâr marjını yan yana karşılaştırın." | Karşılaştırma ekranında iki şirket ve doğru oran | 2 dk | FR-13 |
| G7 | "Son 8-K başvurusunun hangi olayları bildirdiğini söyleyin." | Madde numarası veya etiketi söylenir | 2 dk | FR-14 |
| G8 | "Bu şirketin verilerini Excel'de açılabilecek bir dosya olarak indirin." | CSV indirilir | 1 dk | FR-16 |
| G9 | "Arayüz dilini İngilizceye çevirin ve skor ekranına geri dönün." | Dil değişir, skor ekranı görünür | 1 dk | NFR-11 |
| G10 | "Bu sistem size Microsoft hissesini almanızı öneriyor mu? Ekranda buna dair ne yazıyor?" | Uyarı metni gösterilir / "tavsiye vermiyor" denir | 1 dk | NFR-12 |

Ölçülenler (görev başına): tamamlanma (yardımsız / yardımla / tamamlanamadı), süre (sn),
hata sayısı (yanlış sekme, geri dönüş), katılımcı yorumu. Özet ölçütler: görev başarı oranı
(yardımsız tamamlanan / toplam), ortalama süre, hedef süreyi aşan görev oranı.
**NFR-11 kabul ölçütü:** G1, G3, G4, G5, G9 (temel görevler) her katılımcıda yardımsız
tamamlanır (tasarımdaki "3 kullanıcı" eşiği en az 3 katılımcıda sağlanmalıdır).

### 7.3 SUS (System Usability Scale) anketi

Kaynak: Brooke, J. (1996). SUS: A "quick and dirty" usability scale. P. W. Jordan, B. Thomas,
B. A. Weerdmeester ve I. L. McClelland (Ed.), *Usability evaluation in industry* içinde
(s. 189–194). Taylor & Francis.

Her madde 1 (Kesinlikle katılmıyorum) … 5 (Kesinlikle katılıyorum) arasında işaretlenir.
Aşağıdaki Türkçe metin bu proje için yapılmış çeviridir; rapora konmadan önce yayımlanmış
geçerlenmiş bir Türkçe SUS uyarlaması `researcher` ajanınca aranıp varsa o kullanılmalıdır.

| No | Özgün madde (Brooke, 1996) | Türkçe |
| --- | --- | --- |
| 1 | I think that I would like to use this system frequently. | Bu sistemi sık sık kullanmak isteyeceğimi düşünüyorum. |
| 2 | I found the system unnecessarily complex. | Sistemi gereksiz yere karmaşık buldum. |
| 3 | I thought the system was easy to use. | Sistemin kullanımının kolay olduğunu düşündüm. |
| 4 | I think that I would need the support of a technical person to be able to use this system. | Bu sistemi kullanabilmek için teknik bir kişinin desteğine ihtiyaç duyacağımı düşünüyorum. |
| 5 | I found the various functions in this system were well integrated. | Sistemdeki çeşitli işlevlerin birbiriyle iyi bütünleştiğini düşündüm. |
| 6 | I thought there was too much inconsistency in this system. | Sistemde çok fazla tutarsızlık olduğunu düşündüm. |
| 7 | I would imagine that most people would learn to use this system very quickly. | Çoğu insanın bu sistemi çok çabuk öğreneceğini tahmin ediyorum. |
| 8 | I found the system very cumbersome to use. | Sistemi kullanmayı çok zahmetli buldum. |
| 9 | I felt very confident using the system. | Sistemi kullanırken kendimden çok emin hissettim. |
| 10 | I needed to learn a lot of things before I could get going with this system. | Bu sistemi kullanmaya başlamadan önce birçok şey öğrenmem gerekti. |

**Puanlama:** tek numaralı maddeler (olumlu) için katkı = işaret − 1; çift numaralı maddeler
(olumsuz) için katkı = 5 − işaret. Katılımcının SUS puanı = (10 katkının toplamı) × 2,5; aralık
0–100. Boş bırakılan madde varsa o maddeye 3 (orta) verilir ve raporda belirtilir.

Örnek: işaretler 4,2,4,1,4,2,5,2,4,2 → katkılar 3,3,3,4,3,3,4,3,3,3 = 32 → 32 × 2,5 = **80**.

Yorum: SUS puanı yüzde değildir. Literatürde ortalama ~68 kabul edilir; Bangor, Kortum ve
Miller'in sıfat ölçeği yorum için kullanılabilir. Raporda katılımcı başına puan, ortalama,
standart sapma ve (n küçük olduğu için) %95 güven aralığı verilir. Künyeler (rapordan önce
`researcher` ile doğrulanacak):
- Bangor, A., Kortum, P. T. ve Miller, J. T. (2008). An empirical evaluation of the System
  Usability Scale. *International Journal of Human–Computer Interaction, 24*(6), 574–594.
- Bangor, A., Kortum, P. ve Miller, J. (2009). Determining what individual SUS scores mean:
  Adding an adjective rating scale. *Journal of Usability Studies, 4*(3), 114–123.

### 7.4 Gözlem formu (katılımcı başına bir sayfa)

```
Katılımcı kodu: K__    Tarih/saat: ________    Ekran: 1440 / 820 px    Gözlemci: ________
Finans geçmişi: yok / ders / iş    Yatırım deneyimi: yok / az / var    Dil: TR / EN

Görev | Başladı | Bitti | Süre(sn) | Sonuç (Y=yardımsız, D=destekle, T=tamamlanamadı) | Hata sayısı | Not
G1    |         |       |          |                                                  |             |
G2    |         |       |          |                                                  |             |
...   (G1–G10)

Gözlenen sorunlar (ekran / öğe / ne oldu / önem: kritik-orta-düşük):
1.
2.

Katılımcı alıntıları (sesli düşünme):
-

Görüşme soruları:
1. Sistemde en çok neyi faydalı buldunuz?
2. En çok nerede zorlandınız?
3. Skorun nasıl hesaplandığını anladınız mı? Neye dayandığını açıklayabilir misiniz?
4. Sistemin size yatırım önerisi verdiğini düşündünüz mü? Neden?
5. Eklenmesini istediğiniz bir özellik var mı?

SUS puanı: ____
```

### 7.5 Sonuçların raporlanması

Görev × katılımcı matrisi (Y/D/T), görev başına başarı oranı ve ortalama süre, SUS dağılımı,
önem derecesine göre sıralı sorun listesi ve her sorun için karar (düzeltildi / sonraki sürüm /
kapsam dışı). Hedeflerin gerçekleşme oranı tablosunda NFR-11 bu sonuçlarla doldurulur.

## 8. Hedeflerin gerçekleşme oranı için şablon

Rapordaki "Proje hedeflerinin gerçekleşme oranı" bölümü şu sütunlarla doldurulur: Kimlik |
Hedef | Ölçülen | Durum (Karşılandı / Kısmen / Karşılanmadı / Ölçülmedi) | Kanıt (test adı,
ölçüm dosyası, ekran görüntüsü). Oran = karşılanan / toplam; "kısmen" yarım sayılmaz, ayrı
gösterilir. 23 Kasım itibarıyla ölçülmemiş olanlar: NFR-04, NFR-05 (gerçek sunucu), NFR-07
(sadakat), NFR-08 (15 dk kurulum), NFR-09 (pytest-cov), NFR-11 (kullanıcı testi), FR-07 kabul
ölçütü (10 şirket), FR-16 PDF, FR-05 QoQ.
