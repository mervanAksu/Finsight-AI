# Aşama 4 durum raporu (25 Kasım 2026)

4\. ara rapor (Sonuçlandırma ve Değerlendirme) için çalışma notu. Bulutta doğrulananlar ile
kullanıcı bilgisayarında doğrulanması gerekenler ayrı tutulmuştur; ikincisi rapora ancak
ölçüldükten sonra sonuç olarak yazılır.

## Bu turda yapılanlar

| Alan | Değişiklik | Belge |
| --- | --- | --- |
| Güncel veri | Skor ve oranlar SEC'teki en son 10-Q'ya dayanıyor: TTM = son yıllık + bu yılın YTD − geçen yılın YTD; her TTM parçası (başvuru no, etiket, dönem) izlenebilir; yeniden düzenlenmiş karşılaştırmalar işaretleniyor; yalnızca birincil XBRL etiketi kullanılıyor | `asama4-sozlesme.md`, `api-contract.md` v1.4 |
| Tazelik | `/freshness` (SEC'te analiz edilmemiş yeni rapor var mı), arayüzde "Yeni rapor yayımlandı — Güncelle" bandı, `python -m finsight.refresh`, isteğe bağlı otomatik yenileme, atomik iş kilidi, SEC JSON önbelleği 24 saat → 1 saat, belgeler süresiz | `asama4-sozlesme.md` §4–5 |
| Performans | lxml ile metin ayıklama (6–7x), numpy risk benzerliği, tekil gömme, indirme ile FinBERT'in örtüşmesi, `FINSIGHT_DEVICE` (GPU), ONNX seçeneği, `bench_pipeline` | `performans-asama4.md` |
| Güvenlik | 19 denetim bulgusu + 2 kırmızı takım bulgusu; saldırı testleri; iki tur düzeltme ve bağımsız inceleme | `guvenlik/` |
| Arayüz | "Dipnotlu tablo" tasarım dili; 7 sekme (Oranlar ve Metin tonu yeni); kaynak ipuçları; TTM/tazelik gösterimi; hata sınırı ve 429/503/403 mesajları | `ekran-goruntuleri/asama4-tasarim/` (kurgusal veri) |
| Test | Yük testi aracı (`bench_api`), uçtan uca ve NFR testleri, test planı ve SUS'lu kullanıcı testi planı | `test-plani-asama4.md` |

## Test durumu (bulut)

`878 passed, 13 skipped, 1 xfailed`; `ruff` temiz. Atlanan dosyalar FastAPI/SQLAlchemy gerektiriyor
(bulutta kurulamıyor). Kalan xfail: frontend `package-lock.json` yok (SEC-15, kısmen açık).

Bulutta yapılan performans ölçümleri sentetik belgeler ve yedek modellerle yapılmıştır
(metin hattı 3,7–4,3x); gerçek MSFT süresi kullanıcı bilgisayarında ölçülecek.

## Güvenlik özeti

- Denetim: 0 kritik, 0 yüksek, 7 orta, 9 düşük, 3 bilgi (+ RT-01, RT-02).
- Kapatılan: SEC-11 ve CSV-DID dahil tümü; istisnalar: SEC-01/A03 (Origin başlığı olmayan gövdesiz
  POST curl ile aynıdır, bilinçli olarak açık) ve SEC-15 (frontend kilit dosyası; npm erişimi
  gerekiyor).
- HTTP katmanı testleri (CSRF, Host, eşzamanlı ingest, 503) kod okumasına göre işaretlendi;
  kullanıcı bilgisayarında koşacak.

## Kullanıcı bilgisayarında yapılacaklar (sıra önemli)

```powershell
cd C:\Users\mrvnn\Finsight\Finsight-AI
git pull
cd backend
python -m pip install -U -r requirements.txt
python -m pytest -q                              # atlanan API/DB testleri ilk kez burada koşar
python -m pytest tests\security -v -rxXs         # beklenen tek XFAIL: test_sec01_bodiless_ingest_requires_json
python -m finsight.ingest MSFT --analyze         # skor dayanağı "TTM ... 2026-09-30 · 10-Q" olmalı
cd ..\frontend; npm run typecheck; npm run build
cd ..\backend; uvicorn finsight.api:app          # http://127.0.0.1:8000
```

Ölçümler (4. rapora girecek sayılar):

```powershell
python -m finsight.bench_pipeline --repeat 3
python -m finsight.bench_api --ticker MSFT --users 10 --duration 120 --json ..\docs\olcumler\yuk-10kullanici.json
python backend\tests\security\attack_sim.py --base-url http://127.0.0.1:8000 --json saldiri-sonuc.json
```

GPU (isteğe bağlı; ayrıntı `performans-asama4.md` §7–8): önce `nvidia-smi` ile kart modeli
doğrulanmalı; torch 2.15 bu kart sınıfı için CUDA paketi yayımlamıyor.

## 4. rapor için açık kalanlar

- FR-05 çeyrekten çeyreğe (QoQ) değişim ve FR-16 PDF dışa aktarma uygulanmadı.
- Gerçek ölçümler: yük testi (NFR-04/05), kurulum süresi (NFR-08), kapsam (`pytest --cov`).
- Kullanıcı testi (5–8 katılımcı, SUS) yapılmadı.
- XBRL değerlerinin 10-K tablolarıyla elle karşılaştırılması ve bölüm ayıklamanın 10 şirkette
  doğrulanması (FR-07 kabul ölçütü) yapılmadı.
- LLM anahtarı bağlanmadı; soru-cevap çıkarımsal modda.
