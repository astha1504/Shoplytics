# CHOICES.md

## 1. Detection model: YOLOv8n + ByteTrack

**Options considered**: YOLOv8/YOLOv9, RT-DETR, MediaPipe (pose-only).

**AI suggestion**: Start with YOLOv8n for speed; upgrade to `yolov8m` if recall on billing occlusion is poor.

**Choice**: YOLOv8n + ByteTrack via `ultralytics` + `supervision`.

**Why**: Hackathon clips are 1080p/15fps and reviewers weight **events and API** over marginal mAP. Nano runs on CPU for `docker compose` demos; confidence is passed through (not suppressed) per schema.

**Partial occlusion**: Low-confidence boxes still emit events with lower `confidence` — graceful degradation vs silent drop.

---

## 2. Event schema design

**Options**: Flat `zone` string vs `zone_id`; embed `queue_depth` top-level vs `metadata`.

**Choice**: Match challenge schema exactly (`zone_id`, `camera_id`, `metadata.queue_depth`). Ingest accepts legacy `zone`/`camera` keys and normalizes.

**Visitor IDs**: `VIS_xxx` per entry session; `REENTRY` reuses ID after appearance match.

**Staff**: Events emitted with `is_staff=true`; excluded only in analytics queries.

---

## 3. API architecture: store-scoped routes + read-only dataset

**Options**: Single global `/metrics` vs `/stores/{id}/metrics`; copy POS to `pos_transactions.csv` vs parse Brigade CSV in place.

**Choice**:

- Primary routes under `/stores/{store_id}/…` (accepts `ST1008` and alias `STORE_BLR_002`).
- **Never write to `dataset/`** — POS from `Brigade_*.csv`, layout from `.xlsx` + `config/store_layout.json`, videos from `CCTV Footage/CAM *.mp4`.
- SQLite for take-home; PostgreSQL would be a drop-in connection string change.

**POS correlation**: No `customer_id` in POS — count conversion when `BILLING_QUEUE_JOIN` is within 5 minutes of a transaction timestamp (challenge rule).

---

## Hackathon dataset mapping (Brigade bundle)

| Hackathon file | How we use it |
|----------------|---------------|
| `CCTV Footage/CAM 1.mp4` … `CAM 5.mp4` | Mapped in `config/camera_map.json` |
| `Brigade Road - Store layout*.xlsx` | Zone names parsed; polygons in `config/` |
| `Brigade_Bangalore_*.csv` | POS transactions (unique `order_id`) |

**VLM for zones**: Considered GPT-4V for polygon drawing; rejected for reproducibility and offline judging — rule-based polygons tunable per camera in config.

---

## Staff detection (no ML)

- Presence **> 15 minutes** OR long presence across **≥ 3 zones** → staff.
- Documented in `pipeline/staff.py`; excluded from visitor/funnel/conversion counts only.

---

## Re-entry

Store exit snapshots (height, width, color histogram). On inbound crossing, similarity ≥ 0.55 → `REENTRY` + same `visitor_id`.

---

## Dwell

`ZONE_DWELL` after 30s in zone, then every 30s while inside (per spec).
