# FinSight AI — tek imaj: derlenmiş React arayüzü + FastAPI backend.
#
#   docker build -t finsight-ai .
#   docker run --env-file .env -p 127.0.0.1:8000:8000 finsight-ai   → http://localhost:8000
#
# SEC-14: port YALNIZCA bu bilgisayara (127.0.0.1) yayınlanır; adres önekisiz yayın API'yi aynı
# ağdaki herkese açar (kimlik doğrulama yok). Kapsayıcı root olmayan kullanıcıyla çalışır.
# Kapsayıcı içinde uvicorn 0.0.0.0'a bağlanır (Docker port yönlendirmesi için gerekli); Host
# denetimi için kapsayıcının adı gerekirse FINSIGHT_ALLOWED_HOSTS ile eklenir.
#
# Ağır ML paketleri (FinBERT, sentence-transformers) varsayılan olarak KURULMAZ; sistem yedek
# modellerle (lexicon-fallback, hashing-fallback) çalışır. Kurmak için:
#   docker build --build-arg INSTALL_ML=1 -t finsight-ai .

# ---- 1. aşama: arayüzü derle ---------------------------------------------------------------
FROM node:22-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund
COPY frontend/build.mjs frontend/index.html frontend/tsconfig.json ./
COPY frontend/src ./src
# Üretimde API aynı sunucudadır (göreli /api yolları).
ENV FINSIGHT_API_BASE=""
RUN npm run build

# ---- 2. aşama: Python backend + derlenmiş arayüz -------------------------------------------
FROM python:3.12-slim
ARG INSTALL_ML=0
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FINSIGHT_FRONTEND_DIST=/app/frontend_dist \
    SEC_CACHE_DIR=/app/.sec_cache \
    HF_HOME=/app/.hf_cache

WORKDIR /app
COPY backend/requirements.txt backend/requirements-ml.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && if [ "$INSTALL_ML" = "1" ]; then \
      pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
      && pip install --no-cache-dir -r requirements-ml.txt; \
    fi
COPY backend/finsight ./finsight
COPY --from=frontend /build/frontend/dist ./frontend_dist

RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin finsight \
 && mkdir --parents /app/.sec_cache /app/.hf_cache \
 && chown -R finsight:finsight /app/.sec_cache /app/.hf_cache
USER finsight

EXPOSE 8000
CMD ["uvicorn", "finsight.api:app", "--host", "0.0.0.0", "--port", "8000"]
