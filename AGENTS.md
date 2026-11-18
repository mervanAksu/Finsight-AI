# AGENTS.md

Bu dosya Claude Code dışındaki kodlama ajanları (Codex, Cursor, Copilot vb.) için de ortak
talimatlardır. Ayrıntılı kurallar `CLAUDE.md` içindedir; çelişki olursa `CLAUDE.md` geçerlidir.

## Proje özeti

FinSight AI: SEC EDGAR raporlarından (10-K, 10-Q, 8-K) finansal veri çıkarma, oran analizi,
FinBERT ile ton analizi, risk bölümü karşılaştırma, kaynak gösteren soru-cevap (RAG) ve
açıklanabilir 100 puanlık skor. Backend Python/FastAPI, frontend React/TypeScript.

## Kurulum ve test

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt                     # çekirdek
pip install -r requirements-ml.txt                  # isteğe bağlı: FinBERT, sentence-transformers
python -m pytest -q

cd ../frontend && npm install && npm run build && npm run typecheck
```

## Kurallar (özet)

1. SEC'e yalnızca `SecClient` üzerinden, User-Agent ile ve ≤ 8 istek/sn erişilir.
2. Gösterilen her değer ve her cevap kaynağıyla (accession no, bölüm, XBRL etiketi) döner.
3. Eksik veri `None` ve gerekçeyle döner; asla 0 yazılmaz.
4. Yatırım tavsiyesi verilmez; yanıtlarda `disclaimer` alanı bulunur.
5. Testler ağa çıkmaz; dış servisler sahte istemcilerle test edilir.
6. API'yi değiştiren her iş `docs/api-contract.md` dosyasını da günceller.
7. Commit mesajları Türkçe, kısa ve ne yapıldığını anlatır.

## Roller

Çok ajanlı çalışmada roller ve sorumluluklar `.claude/agents/` altındadır. Her ajan yalnızca
kendisine verilen dosyalara dokunur, işi bitince çalıştırdığı doğrulama komutlarını ve
sonuçlarını raporlar.
