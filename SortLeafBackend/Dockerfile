FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ENVIRONMENT=production \
    MODEL_PATH=/app/models/SortLeafV2.tflite OPENBLAS_NUM_THREADS=1

RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    libmagic1 \
    dumb-init \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-api.txt .

RUN pip install --no-cache-dir -r requirements-api.txt numpy==1.26.4 tflite-runtime==2.14.0

COPY --chown=appuser:appuser . .

USER appuser

EXPOSE 10000

ENTRYPOINT ["/usr/bin/dumb-init", "--"]

CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port \"${PORT:-10000}\" --workers 1"]
