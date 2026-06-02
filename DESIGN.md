# DESIGN.md — Apex Store Intelligence

## Overview

An end-to-end pipeline that converts raw anonymised CCTV footage into live retail analytics. Every architectural decision was made to maximise accuracy of the **North Star Metric**: offline store conversion rate. The system is built so that a reviewer can run a single command (`docker compose up --build`) and see the full pipeline — from YOLO-detected bounding boxes to a live React dashboard — within minutes.

```
dataset/ (read-only CCTV clips, POS CSV, layout XLSX)
  │
  ▼ pipeline/ — YOLOv8n + ByteTrack
  │   processor.py  →  entry_exit.py  →  zones.py
  │   billing.py    →  reentry.py     →  events.py
  │            emit → events.jsonl
  │
  ▼ POST /events/ingest — FastAPI + SQLite + SQLAlchemy
  │   Idempotent by event_id · Batch ≤500 · Partial success
  │
  ├── GET /stores/{id}/metrics    → KPIs (visitors, conversion, dwell, queue)
  ├── GET /stores/{id}/funnel     → Entry→Zone→Billing→Purchase + drop-off %
  ├── GET /stores/{id}/heatmap    → Zone traffic scores, normalised 0–100
  ├── GET /stores/{id}/anomalies  → BILLING_QUEUE_SPIKE / CONVERSION_DROP / DEAD_ZONE
  └── GET /health                 → STALE_FEED detection per store
         │
         ▼ React + Vite dashboard (3-second live polling)
             Tab 1: Dashboard  — KPIs, Charts, Store Layout, Live Anomalies
             Tab 2: Anomalies  — Full sortable table (Severity, Action)
             Tab 3: Camera Feed — Simulated YOLOv8 bounding boxes + ByteTrack IDs
```

---

## Detection Layer (`pipeline/`)

### Model — YOLOv8n (class 0 = person)

The `ultralytics` YOLOv8n model was chosen over YOLOv8m/l or RT-DETR for CPU-friendly throughput (~25 FPS on 1080p). At the take-home demo scale, inference speed matters more than marginal mAP gains. Confidence scores are passed through to emitted events — no silent drops — which directly satisfies the challenge spec's graceful-degradation requirement.

### Tracking — ByteTrack via `supervision`

`supervision.ByteTrack()` assigns stable `track_id`s across frames. It maintains track history during short occlusions (e.g. two customers crossing paths in the billing aisle), which single-frame IoU matching cannot. A stable `track_id` is the foundation for every downstream event: zone dwell, re-entry matching, and billing queue depth all depend on track continuity.

### Entry / Exit Detection (`pipeline/entry_exit.py`)

A virtual crossing line (coordinates in `config/store_layout.json`) with direction-aware detection. Direction is computed from the Y-delta (or X-delta for horizontal lines) of the bounding-box centroid across frames. An inbound crossing emits `ENTRY`; outbound emits `EXIT`. The line position is configurable per camera, handled by `config/camera_map.json`.

### Zone Tracking (`pipeline/zones.py`)

Point-in-polygon (PIP) test using NumPy against zone polygon vertices from `config/store_layout.json`. `ZONE_ENTER` is emitted on the first frame a centroid falls inside a polygon. `ZONE_DWELL` fires every 30 seconds of continuous presence. `ZONE_EXIT` fires on departure. This gives the heatmap both visit frequency and dwell time from a single zone definition file.

### Staff Detection (`pipeline/staff.py`)

Rule-based, zero ML. A track is flagged `is_staff=true` when:
- Total presence exceeds **15 minutes**, OR
- The track has moved through **≥ 3 distinct zones** (staff restocking pattern)

Staff events are still emitted to the event stream and stored in the database, but are excluded from all analytics queries via `WHERE is_staff = false`. This means the event log remains a complete audit trail while all metrics reflect customer behaviour only.

### Re-entry Detection (`pipeline/reentry.py`)

When a track crosses the entry line inbound, its bounding-box profile (height/width ratio + HSV colour histogram of the upper-body crop) is compared against a 30-minute buffer of recent exits using cosine similarity. A score ≥ 0.55 triggers a `REENTRY` event reusing the original `visitor_id`, preventing count inflation during the conversion funnel. The threshold was tuned on the test clip: 0.7 was too strict (same person in same clothing missed), 0.5 had false positives between people in similar colours.

### Billing Queue Depth (`pipeline/billing.py`)

A polygon around the billing zone tracks the simultaneous count of `track_id`s inside. `BILLING_QUEUE_JOIN` is emitted when a new ID enters a non-empty zone (`queue_depth > 0`). `BILLING_QUEUE_ABANDON` fires when an ID leaves the billing zone without a subsequent `PURCHASE_CORRELATED` within ±5 minutes. The queue depth is written into `metadata.queue_depth` exactly as the challenge schema specifies.

### POS Correlation (`analytics.py → correlate_pos_purchases`)

Brigade Road POS CSV rows are loaded once per `POST /events/ingest` batch via `correlate_pos_purchases()`. Visitors in the billing zone within ±5 minutes of a transaction timestamp receive a `PURCHASE_CORRELATED` event. This is the bridge between the CCTV pipeline and the till — the conversion rate metric depends entirely on this step.

---

## Event Stream Schema

Events follow the challenge schema exactly. Key fields:

| Field | Description |
|---|---|
| `event_id` | UUID v4 — primary deduplication key |
| `event_type` | One of 9 valid types (ENTRY, EXIT, REENTRY, ZONE_ENTER, ZONE_EXIT, ZONE_DWELL, BILLING_QUEUE_JOIN, BILLING_QUEUE_ABANDON, PURCHASE_CORRELATED) |
| `visitor_id` | Assigned on ENTRY; reused on REENTRY |
| `store_id` | Normalised to `ST1008` format |
| `camera_id` | Maps to `config/camera_map.json` roles |
| `zone_id` | Required for all ZONE_* events |
| `metadata.queue_depth` | Required for BILLING_QUEUE_JOIN |
| `is_staff` | Excluded from analytics (`WHERE is_staff = false`) |
| `confidence` | YOLOv8n detection confidence; passed through, never suppressed |

The `normalize_ingest_event()` function in `pipeline/events.py` accepts legacy aliases (`zone` → `zone_id`, `camera` → `camera_id`) for backward compatibility with test fixtures written against the older schema.

---

## Intelligence API (`backend/`)

### Framework — FastAPI + SQLAlchemy + SQLite

FastAPI provides async request handling, OpenAPI docs at `/docs`, and Pydantic response validation that doubles as a test contract. SQLAlchemy abstracts the DB layer — switching to PostgreSQL for production requires only changing one connection string. SQLite is adequate for the take-home demo: zero infrastructure, works in Docker without a separate service, persists across restarts via volume mount.

### Ingest Endpoint — `POST /events/ingest`

- Validates all events against the 9-type catalogue before any writes
- Deduplicates by `event_id` at both application layer (early exit) and DB (`UNIQUE` index)
- Supports **partial batch success**: valid events are committed even if some are malformed
- Returns `{ accepted, rejected, duplicates, errors[:20] }` for observability
- Batch limit: 500 events per request (returns HTTP 400 if exceeded)

### Real-time Metrics — `GET /stores/{id}/metrics`

Computed live from the DB on every request (no cache). Returns: unique visitors (ENTRY + REENTRY deduped by `visitor_id`), conversion rate (purchases / visitors × 100), avg dwell time per zone (dict), current queue depth (max of all BILLING_QUEUE_JOIN depths), abandonment rate.

### Funnel — `GET /stores/{id}/funnel`

Session is the unit. Re-entries do not double-count. Stages: Entry → Zone Visit → Billing Queue → Purchase, each with `drop_off_pct` to the next stage. This matches the exact format described in the challenge spec.

### Heatmap — `GET /stores/{id}/heatmap`

Zone visit frequency and avg dwell time, normalised to 0–100. `data_confidence = LOW` when fewer than 20 sessions have been recorded (prevents misleading scores on sparse data).

### Anomaly Detection — `GET /stores/{id}/anomalies`

| Type | Trigger | Severity |
|---|---|---|
| `BILLING_QUEUE_SPIKE` | queue_depth ≥ 5 | WARN |
| `BILLING_QUEUE_SPIKE` | queue_depth ≥ 8 | CRITICAL |
| `CONVERSION_DROP` | conversion < 15% (≥5 visitors) | WARN |
| `CONVERSION_DROP` | 0 purchases (≥5 visitors) | CRITICAL |
| `DEAD_ZONE` | Entry traffic but no zone visits in last 30 min | WARN |
| `HIGH_ABANDONMENT` | Abandonment ≥ 40% (≥5 queue joins) | WARN |

Every anomaly includes a `suggested_action` string for the operations team.

### Health — `GET /health`

Per-store `last_event_at` with `STALE_FEED` warning when the most recent event is > 10 minutes old. Used by the dashboard's always-visible Health Widget. The widget polls every 5 seconds independently of the main 3-second data refresh cycle.

---

## Dashboard (`frontend/`)

Built with React + Vite + Chart.js. Three tabs:

1. **Dashboard** — 5 KPI cards, Zone Traffic Bar Chart, Conversion Funnel Line Chart, Store Layout Heatmap Grid (colour-coded by traffic intensity), Live Anomalies Card List
2. **Anomalies** — Full sortable table view of all active anomalies: Anomaly name, Severity badge (CRITICAL/WARN/INFO), Message, Suggested Action
3. **Camera Feed** — Simulated real-time detection preview showing animated bounding boxes with Track IDs, Zone labels, and Confidence scores. Demonstrates what the YOLO+ByteTrack output looks like at 30fps. Pipeline flow banner (CCTV → YOLO → ByteTrack → Event Stream → API → Dashboard) makes the architecture immediately legible.

The Health Widget is pinned to the tab bar and polls `GET /health` every 5 seconds. It displays `✅ Healthy · Last Event: 5s ago` or `⚠️ STALE FEED · No events for 12m` with pulsing amber animation.

---

## Production Features

| Feature | Implementation |
|---|---|
| Structured logging | Every request logs `trace_id`, `store_id`, `endpoint`, `latency_ms`, `status_code` as JSON |
| Graceful degradation | `SQLAlchemyError` → HTTP 503 with JSON body; generic errors → 500 with no stack trace |
| Containerised | `docker compose up --build` starts API (with DB seeding) and React dev server |
| Idempotency | `event_id` enforced at application layer + DB UNIQUE index |
| Partial success | `POST /events/ingest` commits valid events even when batch has malformed rows |
| CORS | Wildcard origin allow in dev; tighten to dashboard origin in production |
| Admin endpoints | `POST /admin/reload-from-file` seeds from `events.jsonl`; `POST /admin/clear-db` resets for live demo |

---

## AI-Assisted Decisions

**1. Camera mapping strategy**: The AI initially suggested renaming CCTV files to standardised names (`entry.mp4`, `floor.mp4`). I disagreed — dataset files are read-only in the challenge. Instead I added `config/camera_map.json` as a mapping layer that the pipeline reads at runtime. This keeps the dataset folder entirely untouched while still supporting any naming convention.

**2. Re-entry threshold**: When asked for a similarity threshold for HSV histogram re-entry matching, the AI suggested 0.7. Testing on the billing clip (same person re-entering 3 minutes later) showed 0.7 was too strict and produced false ENTRY events. I lowered it to 0.55, which correctly identified re-entries while avoiding false positives between different people in similar clothing.

**3. Anomaly detection scope**: The AI initially proposed a `DEAD_ZONE` check on total absence of zone events — which would fire spuriously on startup before any visitors arrived. I corrected this to a rolling 30-minute window that only fires when entry traffic exists but zone traffic is absent, making it a meaningful operational signal rather than a startup artefact.

**4. Dashboard architecture**: The AI suggested a single-page flat layout. I restructured into three tabs so that the Anomalies table and Camera Feed detection preview could each have dedicated screen space without visual clutter, which is especially important for reviewers who want to quickly verify each system component works.

**5. Health widget polling**: The AI suggested fetching health data in the same `Promise.all` as other endpoints (3-second cadence). I moved health to an independent 5-second poll so that a STALE_FEED warning remains visible even when the main data fetch fails — decoupling operational health visibility from analytics data availability.

---

## Project Structure

```
pipeline/
  run.py            # CLI: --dataset or --synthetic → events.jsonl
  processor.py      # Per-video YOLO+ByteTrack processing loop
  entry_exit.py     # Virtual line crossing with direction detection
  zones.py          # Point-in-polygon zone tracking + dwell timing
  billing.py        # Queue depth tracking + abandon detection
  staff.py          # Time+zone-count-based staff classification
  reentry.py        # HSV histogram re-ID matching (cosine similarity)
  events.py         # Event dataclass + writer + schema validation
  synthetic.py      # 200+ event synthetic dataset for demo/testing
  dataset_loader.py # POS CSV reader + camera video discovery

backend/
  main.py           # FastAPI app + all endpoints + middleware
  analytics.py      # compute_metrics / funnel / heatmap / anomalies / health
  models.py         # SQLAlchemy ORM model (StoredEvent)
  schemas.py        # Pydantic request/response models
  database.py       # SQLAlchemy engine + session factory

scripts/
  seed_db.py        # DB initialisation from events.jsonl
  stream_events.py  # Live-stream events.jsonl for Part E dashboard demo

frontend/
  src/App.jsx       # React dashboard — Dashboard / Anomalies / Camera Feed tabs
  src/index.css     # Glassmorphism design system

tests/
  test_analytics.py        # 34 API tests — metrics, funnel, heatmap, anomalies, health
  test_ingest_endpoint.py  # Ingest-specific edge cases — idempotency, malformed, re-entry
  test_pipeline.py         # Pipeline unit tests — schema validation, staff, writer, synthetic
  test_dataset_loader.py   # POS loader tests — parsing, uniqueness, ordering
  conftest.py              # In-memory SQLite fixture shared across all test modules
```
