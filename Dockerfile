# --- 1. build the React frontend -------------------------------------------------
FROM node:22-alpine AS frontend
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- 2. Python app (API + scheduler) ---------------------------------------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Kolkata \
    HF_HOME=/app/data/hf-cache \
    TT_FRONTEND_DIST=/app/frontend/dist

WORKDIR /app

COPY requirements.txt requirements-ml.txt ./
RUN pip install -r requirements.txt

# Semantic scoring is optional (adds ~1 GB). Build with: INSTALL_ML=true docker compose build
ARG INSTALL_ML=false
RUN if [ "$INSTALL_ML" = "true" ]; then \
      pip install torch --index-url https://download.pytorch.org/whl/cpu && \
      pip install -r requirements-ml.txt ; \
    fi

COPY tender_tracker ./tender_tracker
COPY --from=frontend /ui/dist ./frontend/dist

RUN useradd --create-home --uid 1000 tracker && mkdir -p /app/data && chown -R tracker /app
USER tracker

EXPOSE 8000
CMD ["python", "-m", "tender_tracker", "serve", "--host", "0.0.0.0", "--port", "8000"]
