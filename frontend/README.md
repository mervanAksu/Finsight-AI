# FinSight AI — Web arayüzü

React 19 + TypeScript arayüzü. esbuild ile derlenir; grafikler d3 ölçekleriyle elle çizilen
SVG bileşenleridir. Veri şekilleri `docs/api-contract.md` ile birebir aynıdır (`src/types.ts`).

> Yatırım tavsiyesi değildir. Arayüzdeki her analiz ekranı bu uyarıyı gösterir.

## Kurulum

```bash
cd frontend
npm install
npm run typecheck   # tsc --noEmit
```

## Geliştirme

```bash
# 1) Backend'i başlat (depo kökünden): uvicorn finsight.api:app --reload  → http://127.0.0.1:8000
# 2) Arayüz (izleme + otomatik yenileme):
npm run dev          # http://127.0.0.1:5173, API varsayılanı http://127.0.0.1:8000
```

Farklı bir API adresi için: `FINSIGHT_API_BASE=http://host:port npm run dev`.

## Üretim derlemesi

```bash
npm run build        # dist/ (index.html + assets/app.js + assets/app.css)
```

Üretimde API adresi varsayılan olarak boştur (aynı sunucu); backend `dist/` klasörünü `/`
altından sunar. Gerekirse `FINSIGHT_API_BASE` ile derleme anında değiştirilir.

## Demo / sunum modu (backend olmadan)

`scripts/mock_server.py` yalnızca Python standart kütüphanesiyle çalışır; `dist/` klasörünü ve
`/api/*` uç noktalarını `mock/*.json` içindeki **kurgusal** verilerle sunar (EXMP – Example Corp,
NVLT – Novalight Systems, HRBR – Harbor Freight Lines, QNTA – Quanta Ridge Foods, CDRW – Cedarway
Outdoor). Ağa, SEC'e veya LLM'e bağlanmaz; yanıtlar `docs/api-contract.md` (v1.4) şeklindedir.

```bash
npm run build
python3 scripts/mock_server.py 8765          # http://127.0.0.1:8765
python3 scripts/mock_server.py 8765 --latency 0   # yapay gecikme olmadan
python3 scripts/mock_server.py 8765 --empty       # hiç işlenmiş şirket yok (boş ana sayfa)
```

Demo akışları:
- **İşlenmemiş şirket:** `#/company/CDRW/overview` (veya aramada CDRW) → sunucu gerçek backend gibi
  404 döner; sayfa "Bu şirket henüz işlenmedi" ve "SEC'ten verileri çek ve analiz et" düğmesini
  gösterir. ~9 sn süren sahte iş (adım uyarısı dahil) bitince sayfa dolar.
- **Metin analizi yapılmamış şirket:** QNTA yalnızca finansal veriyle başlar (`analyzed: false`;
  ton/risk özeti yok, skor metin bileşenleri olmadan). Başlıktaki "Metin analizini çalıştır" ile
  analiz tamamlanır.
- **Hata durumu:** LMNR, ORVB, PLSD veya EXMR için analiz işi hata ile biter (şirket kaydı boş kalır).
- **Soru-Cevap:** soruda "bulunamadı" (veya "kripto") geçerse `not_found`, "alıntı" geçerse
  `extractive`, "gömme" geçerse `warning` alanlı yanıt döner; "almalı mıyım" gibi tavsiye
  sorularında `advice_refused: true` ve cevabın başında sabit ret cümlesi bulunur.
- **Eksik oran:** NVLT'de 2023 cari oranı hesaplanamaz ("—", nedeni ipucunda).
- **TTM (Aşama 4):** skor, temel kalemler ve oranların son sütunu son on iki aya dayanır
  ("TTM · 30 Eyl 2026 · 10-Q"). QNTA'da işletme nakit akışının TTM'si yoktur; yıllık değere
  düşer ve nedeni (`ttm_reason`, `basis_notes`, oran `note`) gösterilir.
- **Tazelik (`/freshness`):** HRBR'de analizden sonra yayımlanmış bir 10-Q vardır → şirket
  sayfasında "Yeni rapor yayımlandı" bandı ve Güncelle; NVLT'de SEC'e ulaşılamamış gibi
  davranılır ("SEC kontrol edilemedi"). `--no-freshness`: uç nokta yok (eski sunucu).

Sunucu durumu bellektedir; CDRW / QNTA'yı yeniden başlangıç durumunda görmek için sunucuyu yeniden başlatın.
Sahte veriyi yeniden üretmek için: `python3 scripts/gen_mock_data.py` (oranlar ve skor,
`.claude/skills/financial-analysis` kurallarıyla bu betikte hesaplanır).

## Yapı

```
src/main.tsx, App.tsx      giriş, dil/tema durumu, hash yönlendirme (#/company/EXMP/overview)
src/api.ts, types.ts       API istemcisi ve sözleşme tipleri
src/format.ts, i18n.ts     Intl biçimlendirme (tr-TR / en-US), TR/EN sözlük
src/components/            başlık, arama, analiz ilerlemesi, tazelik bandı, ortak bileşenler
src/fonts/                 Bitter ve Fira Sans (WOFF alt kümeleri, SIL OFL 1.1; lisanslar yanında)
src/charts/                skor cetveli, çubuk grafik (TTM taralı), sparkline, ton çubukları
src/pages/                 ana sayfa, şirket sekmeleri (özet, finansallar, oranlar, metin tonu,
                           risk değişimi, soru-cevap, başvurular), karşılaştırma

Tasarım dili ve tokenlar: `.claude/skills/ui-design-system/SKILL.md`.
src/lib/                   veri kancası, boyut izleme, kelime farkı (LCS), localStorage
```
