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
NVLT – Novalight Systems, HRBR – Harbor Freight Lines, QNTA – Quanta Ridge Foods). Ağa, SEC'e
veya LLM'e bağlanmaz; sunumda veya backend hazır değilken arayüzü göstermek için uygundur.

```bash
npm run build
python3 scripts/mock_server.py 8765          # http://127.0.0.1:8765
python3 scripts/mock_server.py 8765 --latency 0   # yapay gecikme olmadan
```

Demo akışları:
- **Analiz et:** Arama kutusuna `quanta` yazıp QNTA'yı seçin ve "Analiz et"e basın; ~9 sn süren
  sahte iş ilerleme çubuğuyla izlenir, bitince şirket sayfası açılır.
- **Hata durumu:** LMNR, ORVB, PLSD veya EXMR için analiz işi hata ile biter.
- **Soru-Cevap:** soruda "bulunamadı" (veya "kripto") geçerse `not_found`, "alıntı" geçerse
  `extractive` (LLM yok) yanıtı döner; diğer sorular iki kaynaklı LLM cevabı alır.
- **Eksik oran:** NVLT'de 2023 cari oranı hesaplanamaz ("—", nedeni ipucunda); NVLT'de risk
  değişimi bileşeni "hesaplanamadı" olarak görünür.

Sunucu durumu bellektedir; QNTA'yı yeniden "işlenmemiş" görmek için sunucuyu yeniden başlatın.
Sahte veriyi yeniden üretmek için: `python3 scripts/gen_mock_data.py` (oranlar ve skor,
`.claude/skills/financial-analysis` kurallarıyla bu betikte hesaplanır).

## Yapı

```
src/main.tsx, App.tsx      giriş, dil/tema durumu, hash yönlendirme (#/company/EXMP/overview)
src/api.ts, types.ts       API istemcisi ve sözleşme tipleri
src/format.ts, i18n.ts     Intl biçimlendirme (tr-TR / en-US), TR/EN sözlük
src/components/            başlık, arama, analiz ilerlemesi, ortak durum bileşenleri
src/charts/                skor göstergesi, çubuk grafik, sparkline, ton çubukları
src/pages/                 ana sayfa, şirket sekmeleri, karşılaştırma
src/lib/                   veri kancası, boyut izleme, kelime farkı (LCS), localStorage
```
