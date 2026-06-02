FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY pipeline ./pipeline
COPY backend ./backend
COPY config ./config
COPY dataset ./dataset
COPY scripts ./scripts

RUN python -m pipeline.run --synthetic --output /app/events.jsonl || true

EXPOSE 8000

CMD ["sh", "-c", "python scripts/seed_db.py && uvicorn backend.main:app --host 0.0.0.0 --port 8000"]
