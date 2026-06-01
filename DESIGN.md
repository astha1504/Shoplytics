# Design — Apex Store Intelligence

## Overview

End-to-end pipeline: **hackathon CCTV clips (CAM 1–5)** → YOLOv8 person detection → ByteTrack IDs → behavioural events → FastAPI ingest → live metrics and dashboard.

```
dataset/ (read-only)
  CCTV Footage/CAM *.mp4
  Brigade layout .xlsx
  Brigade POS .csv
        ↓
pipeline/  (detect + track + emit)
        ↓
events.jsonl
        ↓
POST /events/ingest
        ↓
SQLite + analytics
        ↓
GET /stores/{id}/metrics|funnel|heatmap|anomalies
        ↓
React dashboard
```

## Detection layer

- **Model**: YOLOv8n (class 0 = person), chosen for CPU-friendly speed on 1080p/15fps clips.
- **Tracking**: `supervision.ByteTrack()` for stable `track_id` per frame.
- **Cameras**: Mapped in `config/camera_map.json` — CAM 1 entry, CAM 2–3 floor, CAM 4–5 billing. Dataset folder is never modified.
- **Entry/exit**: Virtual line from layout config; direction-aware crossing → `ENTRY` / `EXIT`.
- **Re-entry**: HSV histogram + bbox size match against recent exits (30 min window).
- **Staff**: Rule-based — presence > 15 minutes → `is_staff=true` (still emitted, excluded in API).
- **Zones**: Polygons in `config/store_layout.json`, names enriched from hackathon `.xlsx` when present.
- **Timestamps**: ISO-8601 UTC; production would use clip start time + frame offset (hook ready in processor).

## Event stream

Events follow the challenge schema: `event_id`, `store_id`, `camera_id`, `visitor_id`, `event_type`, `zone_id`, `dwell_ms`, `confidence`, `metadata` (e.g. `queue_depth`, `session_seq`).

## Intelligence API

- FastAPI + SQLAlchemy + SQLite
- Idempotent ingest by `event_id` (batch ≤ 500)
- Store-scoped routes: `/stores/{store_id}/…`
- POS correlation: Brigade CSV `order_id` + date/time → unique transactions; billing queue within ±5 min = converted visitor
- Health: per-store `last_event_at`, `STALE_FEED` if > 10 min lag

## AI-Assisted Decisions

1. **Camera mapping for CAM 1–5** — AI suggested renaming files to `entry.mp4`; we kept hackathon names and added `camera_map.json` so the dataset ZIP stays untouched.
2. **Excel layout** — AI proposed manual JSON only; we parse `.xlsx` for zone names but keep editable polygons in `config/` because the spreadsheet is not pixel-accurate.
3. **API paths** — AI initially used `/metrics`; challenge spec requires `/stores/{id}/metrics`; we implemented spec routes and kept legacy aliases for the dashboard migration.

## Production notes

- Structured request logging (`trace_id`, `latency_ms`)
- DB errors → HTTP 503 JSON body
- `docker compose up` runs API + optional dashboard
- Tests cover empty store, staff-only, duplicates, re-entry, zero conversion
