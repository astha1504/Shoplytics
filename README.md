# Purplle CCTV Store Analytics

Convert CCTV footage into retail analytics: footfall, zone attention, queue behavior, conversion, and anomalies.

## Architecture

```
CCTV Videos → YOLOv8 → ByteTrack → Events (JSONL) → FastAPI/SQLite → React Dashboard
```

## Quick start

### 1. Install

```bash
pip install -r requirements.txt
```

### 2. Generate events

**`dataset/` is read-only** (hackathon ZIP). Do not edit it. The app reads:

| Hackathon file | Usage |
|----------------|--------|
| `dataset/CCTV Footage/CAM 1.mp4` … `CAM 5.mp4` | Mapped via `config/camera_map.json` |
| `dataset/Brigade Road - Store layout*.xlsx` | Zone names (polygons tuned in `config/`) |
| `dataset/Brigade_Bangalore_*.csv` | POS / conversion correlation |

Tune zones and entry line in **`config/store_layout.json`** only.

If videos are missing, synthetic events are generated:

```bash
python -m pipeline.run --synthetic --output events.jsonl
```

With videos:

```bash
python -m pipeline.run --dataset dataset --output events.jsonl
# or: sh pipeline/run.sh
```

### 3. Start API

```bash
python scripts/seed_db.py
uvicorn backend.main:app --reload --port 8000
```

### 4. Dashboard

```bash
cd frontend && npm install && npm run dev
```

Open http://localhost:5173 (proxies API to :8000).

### Docker

```bash
docker compose up --build
```

API: http://localhost:8000/docs

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/events/ingest` | Batch ingest events (validate + dedupe) |
| GET | `/stores/{store_id}/metrics` | visitors, conversion_rate, dwell, queue |
| GET | `/stores/{store_id}/funnel` | funnel stages + drop-off % |
| GET | `/stores/{store_id}/heatmap` | zone scores 0–100, data_confidence |
| GET | `/stores/{store_id}/anomalies` | queue spike, conversion drop, dead zone |
| POST | `/events/ingest` | batch ingest (≤500), idempotent |
| GET | `/health` | STALE_FEED per store |

## Business questions answered

| Question | Source |
|----------|--------|
| How many customers entered? | `ENTRY` + `REENTRY` (excl. staff) |
| How many purchased? | POS correlation + `PURCHASE_CORRELATED` |
| Which zone gets max attention? | `/heatmap` |
| Where do customers drop off? | `/funnel` stages |
| Queue problem? | `queue_depth`, `QUEUE_SPIKE` anomaly |
| Conversion falling? | `/metrics` conversion_rate + anomalies |

## Tests

```bash
pytest -v
```

Covers: empty store, staff-only, duplicate events, re-entry, zero conversion.

## Dataset (read-only)

| Location | Purpose |
|----------|---------|
| `dataset/*.csv` | Hackathon POS (Brigade export) — loaded at runtime |
| `dataset/**/entry.mp4` etc. | CCTV when provided by hackathon |
| `config/store_layout.json` | Zone polygons, entry line (you tune this) |

See [CHOICES.md](CHOICES.md) for staff, re-entry, and POS rules.
