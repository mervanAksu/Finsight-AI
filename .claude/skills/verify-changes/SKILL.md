---
name: verify-changes
description: FinSight AI'de bir değişiklik bittiğinde nasıl doğrulanacağını anlatır - backend testleri, lint, tip kontrolü, frontend derleme ve sahte API ile tarayıcıda ekran görüntüsü alma. Bir işi "bitti" saymadan önce okunur.
---

# Değişiklikleri doğrulama

## Backend
```bash
cd backend && python -m pytest -q          # tamamı geçmeli
cd .. && ruff check backend/finsight backend/tests
pyright backend/finsight                   # varsa; fastapi/sqlalchemy eksikse ilgili uyarılar beklenir
```
- FastAPI/SQLAlchemy kurulu değilse `tests/test_api*.py` atlanır (`importorskip`). Bu durumu
  raporda "atlandı, kullanıcı bilgisayarında çalıştırılacak" diye açıkça yaz.
- Yeni saf modüller fastapi/sqlalchemy içe aktarmamalı; aksi halde bulutta test edilemez.

## Frontend (bulut ortamı, npm erişimi yoksa)
`/opt/npm-tools/node_modules` içinde react 19, react-dom, d3, react-icons ve esbuild bulunur.
```bash
cd frontend
mkdir -p .verify && ln -sfn /opt/npm-tools/node_modules .verify/node_modules
NODE_PATH=/opt/npm-tools/node_modules /opt/npm-tools/node_modules/.bin/esbuild src/main.tsx \
  --bundle --outfile=dist/assets/app.js --loader:.tsx=tsx --jsx=automatic \
  --define:process.env.NODE_ENV='"production"' --minify --sourcemap
```
`@types/react` bulutta yoktur; `tsc` tam tip kontrolü kullanıcı bilgisayarında
`npm run typecheck` ile yapılır. Bulutta esbuild sözdizimi ve modül çözümlemesini doğrular.

## Arayüzü gözle kontrol (Playwright + sahte API)
1. `frontend/dist` klasörünü ve `docs/api-contract.md` şekline uyan sahte JSON yanıtlarını
   sunan küçük bir Python sunucusu başlat (yalnızca geçici çalışma klasöründe; depoya eklenmez).
2. Playwright (Python, Chromium `/opt/pw-browsers`) ile her ana ekranı 1440×900 ve 820×1180
   boyutlarında aç, `page.screenshot(full_page=True)` al, konsol hatalarını topla.
3. Görüntüleri `Read` ile aç ve bak: taşan metin, üst üste binen eleman, boş grafik,
   okunmayan kontrast, yatay kaydırma. Sorun varsa düzelt, tekrar çek.

## Kullanıcı bilgisayarında son kontrol (bulutta yapılamayanlar)
```powershell
cd backend; python -m pip install -r requirements.txt; python -m pytest -q
$env:SEC_USER_AGENT="FinSight-AI Ad Soyad eposta@ornek.com"
python -m finsight.ingest MSFT --analyze
cd ..\frontend; npm install; npm run typecheck; npm run build
```
