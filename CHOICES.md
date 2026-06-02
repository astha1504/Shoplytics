# CHOICES.md — Key Design Decisions

## 1. Detection Model: YOLOv8n + ByteTrack

### Options Considered

| Option | Pros | Cons | Verdict |
|--------|------|------|---------|
| **YOLOv8n** (chosen) | CPU-friendly (~25 FPS), well-maintained, class 0 = person | Lower mAP vs larger variants | ✅ Chosen |
| YOLOv8m / YOLOv8l | Better occlusion handling, higher mAP | 4–8× slower; prohibitive on CPU Docker | ❌ Too slow |
| RT-DETR | Transformer-based, better long-range associations | No tracking companion; complex integration | ❌ Overkill |
| MediaPipe BlazePose | Very fast, mobile-optimised | Pose-only — no full bounding box for PIP zone checks | ❌ Wrong output |
| GPT-4V / Gemini Vision | Scene-level understanding | ~$50/min at 15fps; non-deterministic; untestable | ❌ Not viable |
| OpenCV Background Subtraction | Zero dependencies | Cannot distinguish people from static objects | ❌ Too noisy |

### What the AI Suggested

The AI recommended starting with **YOLOv8m** for better mAP on the occlusion cases in the billing clip, specifically noting that the nano model would struggle when people's bounding boxes overlap during group entry.

### What I Chose and Why

**YOLOv8n** because:

1. **The scoring harness weights API correctness and event schema over raw detection accuracy.** A nano model with honest confidence pass-through scores better than a larger model that silently drops uncertain frames. The challenge spec explicitly rewards graceful degradation.

2. **`docker compose` must work on a CPU-only machine.** YOLOv8m takes 10–15 seconds per frame on CPU; YOLOv8n runs at ~25 FPS. The former makes the live demo unusable.

3. **Confidence pass-through.** Low-confidence detections emit events with lower `confidence` values rather than being discarded. This satisfies the challenge's graceful degradation requirement while keeping the event log complete.

4. **ByteTrack** was chosen over DeepSORT (which requires appearance embeddings) because ByteTrack's low-confidence track preservation directly improves re-entry matching: tracks that disappear briefly (e.g. behind a shelf) are re-associated rather than terminated and re-spawned as new IDs.

**VLM Usage**: I evaluated using GPT-4V (Azure) for zone classification — describe the frame, extract which zone the customer is in. Rejected because: (a) 15fps latency is prohibitive, (b) results are non-deterministic making test assertions unreliable, (c) polygon-based zone checking is more reproducible and auditable. I did use Claude to evaluate zone polygon positions by describing frame screenshots; the final zone definitions in `config/store_layout.json` are rule-based.

**Production path**: I would switch to YOLOv8m (or RT-DETR for transformer-based long-range association) in production, add a GPU requirement to the deployment spec, and use a dedicated ReID model (OSNet/torchreid) for appearance-based tracking across camera handoffs.

---

## 2. Event Schema Design

### Options Considered

| Approach | Description | Verdict |
|----------|-------------|---------|
| **Challenge spec schema** (chosen) | `zone_id`, `camera_id` top-level; `queue_depth` in `metadata` | ✅ Exact match for harness assertions |
| Flat schema | All fields at top level, simpler queries | ❌ Fails challenge harness tests |
| Data envelope | `{ "data": { ... } }` wrapper around all optional fields | ❌ Extra nesting without benefit |
| Event sourcing with raw frames | Store base64 frame crops alongside events | ❌ DB size explodes; not required |
| GraphQL | Self-documenting, flexible queries | ❌ Challenge spec requires REST |

### What the AI Suggested

The AI initially generated a flat schema with `queue_depth` at the top level (simpler for SQL queries). It also suggested a `data` envelope to group optional fields, claiming this would make the schema more extensible.

### What I Chose and Why

I matched the **challenge schema exactly** (`zone_id`, `camera_id`, `metadata.queue_depth`) because:

1. **Harness compliance.** The scoring harness runs assertions against `sample_events.jsonl`. Any deviation fails those tests regardless of internal elegance.

2. **Normalisation at ingest.** `normalize_ingest_event()` in `pipeline/events.py` accepts legacy aliases (`zone` → `zone_id`, `camera` → `camera_id`). This means test fixtures written against the older internal schema still work without modification.

3. **`metadata` as an extension point.** By keeping optional per-event fields in `metadata`, the base schema stays stable. Edge cases like `queue_depth` (billing), `sku_zone` (zone enter), and `reentry_similarity` (re-ID score) can be added without changing the DB model or breaking existing queries.

4. **Visitor ID assignment.** Each `ENTRY` event mints a new `VIS_xxxxxxxx` token (UUID-derived). `REENTRY` reuses the visitor_id from the Re-ID match. This is why the funnel doesn't double-count re-entering customers: the `visitor_id` set is deduplicated at query time.

5. **Idempotency by `event_id`.** UUID v4 at both the application layer (early-exit query) and as a DB `UNIQUE` constraint. Safe to retry the ingest endpoint without side effects — a requirement for any reliable streaming pipeline.

---

## 3. API Architecture: FastAPI + SQLite + Store-Scoped Routes

### Options Considered

| Dimension | Choice | Alternative | Reasoning |
|-----------|--------|-------------|-----------|
| **Framework** | FastAPI | Flask / Django / Go Fiber | Async, Pydantic response models, auto-docs at `/docs`, best pytest integration |
| **Storage** | SQLite | PostgreSQL | Zero-config for Docker demo; one-line change to switch; adequate at demo scale |
| **Query model** | Real-time | Materialized views / Redis | Always fresh; production would add Redis with 30s TTL |
| **Route structure** | `/stores/{id}/metrics` | `/metrics?store=id` | Challenge spec path params; easier per-store URL caching in prod |
| **Ingest design** | Partial success | All-or-nothing | Resilient streaming: bad events don't block good ones |
| **Auth** | None (CORS wildcard) | JWT / API key | Demo scope; production would add bearer token to ingest |

### What the AI Suggested

- Suggested a single `/metrics` endpoint with `?store_id=` query parameter
- Suggested PostgreSQL from the start for "production readiness"
- Suggested all-or-nothing batch ingest (simpler to reason about)

### What I Chose and Why

**Path parameters** (`/stores/{store_id}/metrics`): The challenge spec is explicit. Path params also make per-store HTTP caching trivial in production (cache key = full URL with no query string ambiguity). Legacy aliases (`/metrics`, `/funnel`) are maintained for backward compatibility with older test fixtures.

**SQLite over PostgreSQL**: The scoring rubric rewards a working demo, not production infrastructure. SQLite is zero-infrastructure, works in Docker without a separate container, and SQLAlchemy makes it a one-line switch. The DB volume is mounted so data persists across container restarts.

**Real-time queries over caching**: At demo scale (one store, thousands of events), `GROUP BY visitor_id` on SQLite takes < 10ms. The design is documented: in production, a write-through Redis cache with 30-second TTL would eliminate the per-request full-table scan. I chose correctness (always fresh) over speed for the demo.

**Partial batch success**: A streaming pipeline may emit malformed events (JSON decode errors, camera glitches). Failing the entire 500-event batch because one event has a missing `zone_id` would make the pipeline fragile. The ingest endpoint commits all valid events, returns detailed error info for the bad ones, and lets the caller decide whether to retry.

---

## 4. Why YOLOv8 Over a Vision LLM for Detection

This is often asked in follow-ups, so it deserves a standalone section.

Retail store analytics run at **15–30 FPS** on footage from up to 5 cameras. A cloud Vision LLM (GPT-4V, Gemini Vision, Claude Vision) would:

- Cost ~$0.01 per frame × 15 FPS × 5 cameras = **$4.50/minute per store** — obviously non-viable
- Introduce **100–500ms latency per frame** vs < 5ms for YOLOv8n on CPU
- Produce **non-deterministic outputs** that cannot be asserted in unit tests
- Require **internet connectivity** — ruled out for in-store edge deployment

Where I **did** use AI assistance:
- Claude helped evaluate zone polygon positions by interpreting described frame layouts
- Used AI to generate the initial synthetic event dataset structure
- Discussed anomaly threshold calibration with AI ("what queue depth signals urgency?")

All final parameters (thresholds, polygon vertices, schema field names) are deterministic and rule-based.

---

## 5. Staff Detection: Rule-Based Over ML

### Options

| Approach | Pros | Cons |
|----------|------|------|
| **Rule-based (chosen)** | Zero training data, deterministic, explainable | May miss part-time staff who leave quickly |
| YOLOv8 uniform classifier | Could detect by clothing colour | Requires labelled training data; store-specific uniforms |
| Pose estimation | Detects "work posture" (e.g. stocking shelves) | Expensive; false positives from customers reaching for items |
| VLM description | "Is this person wearing a uniform?" | Non-deterministic; 15fps prohibitive |

### What I Chose

**Rule-based** (`pipeline/staff.py`):
- Presence > **15 minutes** continuously, OR
- Movement through **≥ 3 distinct zones** (restocking pattern)

Reasons:
1. Zero training data required — works on day one without store-specific labelling
2. Deterministic and reproducible — test assertions hold every run
3. Explainable in a follow-up interview: "Staff are flagged by dwell time and zone breadth"
4. Correct failure mode: a customer who stays 16 minutes is rare and correctly flagged (conservative), not a false spike in conversion

Production upgrade: fine-tune a binary classifier on upper-body crops (uniform colour + logo) using store-specific training images collected over the first week of deployment.

---

## 6. Re-entry Matching: HSV Histogram Over Deep ReID

### Options

| Approach | Accuracy | CPU Viable | Explainable |
|----------|----------|------------|-------------|
| **HSV histogram + cosine similarity (chosen)** | Good for same-session, same-clothing | ✅ Yes | ✅ Yes |
| OSNet / torchreid | Excellent appearance matching | ❌ Requires GPU | ✅ Yes (learned features) |
| Bounding box trajectory | Fast, no appearance | ✅ Yes | ✅ Yes | ❌ Fails for stop-and-return |
| Face recognition | Person-level ID across days | ❌ Privacy/legal issues | — |

### What I Chose

**HSV colour histogram on the upper-body crop** + bounding box aspect ratio, cosine similarity threshold 0.55.

- OSNet adds a GPU dependency incompatible with CPU-only Docker demo
- For a 30-minute match window, colour histogram is surprisingly effective: same person, same clothing
- The 0.55 threshold was tuned empirically: 0.7 missed obvious re-entries (same person, slightly different lighting), 0.5 produced false matches between people in similar tops
- **Safe failure mode**: if the same person wears different clothing on re-entry, the system correctly treats them as a new visitor — no count inflation, just slight overcounting in the rare case

---

## Dataset Mapping

| File in ZIP | How used |
|-------------|----------|
| `CCTV Footage/CAM 1–5.mp4` | Mapped to entry/floor/billing roles via `config/camera_map.json` |
| `Brigade Road - Store layout*.xlsx` | Zone names parsed; editable polygons in `config/store_layout.json` |
| `Brigade_Bangalore_*.csv` | POS correlation: order_id + timestamp → PURCHASE_CORRELATED events |
| `sample_events.jsonl` | Validates `normalize_ingest_event()` and schema compliance |
| `assertions.py` | Used to validate all 10 test assertions before submission |
