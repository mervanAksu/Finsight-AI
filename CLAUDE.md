# FinSight AI — Proje Talimatları (Claude Code)

FinSight AI, seçilen bir ABD şirketinin SEC 10-K / 10-Q / 8-K raporlarından finansal verileri
çıkaran, dönemsel karşılaştırma yapan, risk ve ton (duygu) analizi yürüten, rapor hakkındaki
soruları kaynak göstererek cevaplayan ve açıklanabilir bir finansal sağlık skoru üreten bir
üniversite tasarım projesidir. **Yatırım tavsiyesi üretmez**; her analiz yanıtı ve ekranı
bu uyarıyı taşır.

Tasarımın tamamı: `docs/raporlar/00-Asama1-Analiz-ve-Tasarim-Planlama.md`
(FR-01…FR-16, NFR-01…NFR-12, tablolar, oranlar, skor ağırlıkları).
API sözleşmesi (backend ile frontend arasındaki tek doğruluk kaynağı): `docs/api-contract.md`.
Aşama 4 (güncel/TTM veri, tazelik, önbellek politikası): `docs/asama4-sozlesme.md`.

## Çalışma modeli: orkestra şefi + alt ajanlar

Ana oturum **orkestra şefidir**. Ağır işi kendisi yapmaz; işi böler, alt ajanlara verir,
çıktılarını kontrol eder, çelişkileri giderir ve birleştirir.

1. **Planla.** İşi bağımsız parçalara böl. Her parçanın dokunacağı dosyaları önceden belirle;
   iki ajan aynı dosyayı aynı anda düzenlemesin.
2. **Sözleşmeyi önce yaz.** Birden fazla ajanı ilgilendiren arayüz (API JSON şekli, fonksiyon
   imzası, tablo şeması) ajanlar başlamadan `docs/` altında yazılır.
3. **Görevlendir.** Her alt ajana `.claude/agents/<rol>.md` dosyasını okumasını, hangi
   dosyalara dokunabileceğini, kabul ölçütlerini ve çalıştırması gereken doğrulama komutlarını
   açıkça söyle. Alt ajanlar soğuk başlar: gereken bağlamı istemde ver.
4. **Doğrula.** Ajanın "bitti" demesi yeterli değildir. Şef testleri kendisi tekrar çalıştırır,
   diff'i okur ve gerekirse `code-reviewer` ajanına bağımsız inceleme yaptırır. Yüksek riskli
   değişikliklerde (finansal hesaplar, SEC erişimi, RAG kaynak gösterme) en az iki inceleme turu.
5. **Birleştir ve kaydet.** Doğrulanmış değişiklikler küçük, anlamlı commit'lerle kaydedilir.

| Rol (`.claude/agents/`) | Sorumluluk |
| --- | --- |
| `backend-engineer` | SEC/XBRL veri katmanı, oranlar, skor, veritabanı, FastAPI |
| `nlp-engineer` | Bölüm ayıklama, FinBERT, risk karşılaştırma, gömme, RAG, LLM istemcisi |
| `frontend-engineer` | React + TypeScript web arayüzü, grafikler, tasarım sistemi |
| `test-engineer` | Test yazma, sahte veri (fixture), uçtan uca doğrulama |
| `code-reviewer` | Salt okunur bağımsız inceleme; bulgu raporu üretir, kod değiştirmez |
| `researcher` | SEC belgeleri ve akademik kaynaklardan bilgi doğrulama, künye kontrolü |
| `security-auditor` | Salt okunur güvenlik denetimi: tehdit modeli, OWASP/CWE bulguları (`docs/guvenlik/`) |
| `red-team` | Saldırı simülasyonu: bulguları çalıştırılabilir saldırı testlerine dönüştürür |
| `security-engineer` | Savunma: bulguları kapatır, her düzeltmeyi saldırı testiyle kanıtlar |

Beceriler (`.claude/skills/`): `sec-edgar`, `financial-analysis`, `verify-changes`,
`ui-design-system`, `academic-report`. İlgili iş başlamadan önce SKILL.md okunur.

## Depo yapısı

```
backend/finsight/      Python paketi (FastAPI uygulaması, analiz modülleri)
backend/tests/         pytest testleri; tests/fixtures/ sentetik SEC verisi
frontend/              React 19 + TypeScript arayüzü (esbuild ile derlenir)
docs/                  tasarım, API sözleşmesi, raporlar, ekran görüntüleri
.claude/               alt ajan tanımları ve beceriler
```

## Komutlar

```bash
# Backend
cd backend && python -m pytest -q            # tüm testler
ruff check backend/finsight backend/tests     # lint (depo kökünden)
python -m finsight.ingest AAPL --analyze      # SEC'ten çek + analiz et (SEC_USER_AGENT gerekli)
uvicorn finsight.api:app --reload             # API: http://127.0.0.1:8000/docs

# Frontend
cd frontend && npm install && npm run build   # dist/ üretir; API aynı sunucudan sunar
npm run dev                                    # geliştirme sunucusu (http://127.0.0.1:5173)
npm run typecheck
```

## Değişmez kurallar

- **SEC erişimi yalnızca `finsight.sec_client.SecClient` üzerinden.** Tanımlayıcı User-Agent
  zorunlu, en fazla 8 istek/sn (SEC sınırı 10), 429/5xx'te üstel geri çekilme (NFR-01…03).
- **Her sayı ve her cevap kaynağına izlenebilir olmalı** (accession no, XBRL etiketi, dönem,
  rapor bölümü). Kaynaksız değer veya cevap üretilmez (NFR-06, NFR-07).
- **Eksik veri sıfır değildir.** Hesaplanamayan oran `None` + neden olarak döner.
- **Yatırım tavsiyesi yok.** "al / sat / tut" dili kullanılmaz; uyarı metni her yanıtta.
- **Gizli bilgi koda yazılmaz.** API anahtarları `.env`'den okunur; `.env` git'e girmez.
- **Ağır ML bağımlılıkları isteğe bağlıdır.** `transformers`, `torch`, `sentence-transformers`
  yoksa sistem yedek (sözlük / hashing) modellerle çalışmaya devam eder ve hangi modeli
  kullandığını yanıtta belirtir. Testler bu kütüphaneler olmadan geçmelidir.
- **Testler ağa çıkmaz.** SEC ve LLM çağrıları sahte (fake) istemcilerle test edilir.
- Kod yorumları ve kullanıcıya dönük metinler Türkçe; tanımlayıcılar (değişken, fonksiyon) İngilizce.

## Ortam notu (bulut oturumu)

Bulut çalışma ortamı PyPI, npm kayıt deposu ve SEC'e erişemeyebilir. Bu durumda:
- Saf Python modülleri burada pytest ile test edilir; FastAPI/SQLAlchemy testleri
  (`tests/test_api*.py`) `importorskip` ile atlanır ve kullanıcının bilgisayarında çalıştırılır.
- Frontend, `/opt/npm-tools/node_modules` altındaki React 19, d3 ve esbuild ile derlenip
  Playwright ile sahte API'ye karşı ekran görüntüsüyle kontrol edilir (bkz. `verify-changes`).
- Gerçek SEC verisiyle doğrulama kullanıcının bilgisayarında yapılır; sonuç raporda belirtilir.

## Tamamlanma tanımı

Bir iş ancak şunlar sağlandığında bitmiş sayılır: testler geçiyor, `ruff` temiz, yeni
davranışın testi var, API değiştiyse `docs/api-contract.md` güncel, arayüz değiştiyse ekran
görüntüsüyle kontrol edildi, ilgili FR/NFR kimliği kodda veya testte anılıyor.
