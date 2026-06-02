"""Store Intelligence API — Apex Retail hiring challenge.

Endpoints:
  POST /events/ingest          — batch ingest (≤500), idempotent by event_id
  GET  /stores/{id}/metrics    — unique visitors, conversion, dwell, queue
  GET  /stores/{id}/funnel     — Entry→Zone→Billing→Purchase with drop-off %
  GET  /stores/{id}/heatmap    — zone visit frequency + dwell, normalised 0–100
  GET  /stores/{id}/anomalies  — queue spike, conversion drop, dead zone
  GET  /health                 — per-store STALE_FEED detection
  POST /admin/reload-from-file — load events.jsonl for demo/testing
  POST /admin/clear-db         — wipe DB (demo / live-streaming reset)
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import List

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend import analytics, models
from backend.database import get_db, init_db
from backend.schemas import (
    AnomalyItem,
    EventIn,
    FunnelResponse,
    FunnelStage,
    HealthResponse,
    HeatmapResponse,
    HeatmapZone,
    IngestResponse,
    MetricsResponse,
    StoreHealth,
)
from pipeline.dataset_loader import resolve_store_id
from pipeline.events import VALID_EVENT_TYPES, normalize_ingest_event

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("store_intelligence")

app = FastAPI(
    title="Apex Store Intelligence API",
    version="1.0.0",
    description="Real-time retail analytics from CCTV events.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DATASET_DIR = Path(__file__).resolve().parent.parent / "dataset"
MAX_INGEST_BATCH = 500


# ---------------------------------------------------------------------------
# Middleware — structured request logging (trace_id, latency, status)
# ---------------------------------------------------------------------------

@app.middleware("http")
async def request_logging(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-Id", str(uuid.uuid4())[:8])
    start = time.perf_counter()
    response = await call_next(request)
    latency_ms = int((time.perf_counter() - start) * 1000)
    store_id = request.path_params.get("store_id", "-")
    logger.info(
        json.dumps({
            "trace_id": trace_id,
            "store_id": store_id,
            "endpoint": request.url.path,
            "method": request.method,
            "latency_ms": latency_ms,
            "status_code": response.status_code,
        })
    )
    response.headers["X-Trace-Id"] = trace_id
    return response


# ---------------------------------------------------------------------------
# Exception handlers — no raw stack traces in responses
# ---------------------------------------------------------------------------

@app.exception_handler(SQLAlchemyError)
async def db_unavailable(_request: Request, _exc: SQLAlchemyError):
    return JSONResponse(
        status_code=503,
        content={"error": "database_unavailable", "message": "Storage layer unavailable — retry shortly."},
    )


@app.exception_handler(Exception)
async def generic_error(_request: Request, exc: Exception):
    logger.error("Unhandled error: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "message": "An unexpected error occurred."},
    )


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
def startup():
    init_db()


# ---------------------------------------------------------------------------
# Event validation
# ---------------------------------------------------------------------------

def _validate_event(raw: dict) -> EventIn | None:
    raw = normalize_ingest_event(raw)
    try:
        ev = EventIn(**raw)
    except Exception:
        return None
    zone = ev.zone_id or ev.zone
    if ev.event_type not in VALID_EVENT_TYPES:
        return None
    if ev.event_type in ("ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL") and not zone:
        return None
    return ev


# ---------------------------------------------------------------------------
# Core ingest logic (called by both endpoint and seed script)
# ---------------------------------------------------------------------------

def _ingest_batch(payload: List[dict], db: Session) -> IngestResponse:
    if len(payload) > MAX_INGEST_BATCH:
        raise HTTPException(400, f"Batch limit is {MAX_INGEST_BATCH} events per request")

    accepted = rejected = duplicates = 0
    errors: list[str] = []
    # Track event_ids already seen in THIS batch to prevent within-batch dupes
    # from causing a UniqueConstraint violation on commit.
    seen_in_batch: set[str] = set()

    for i, raw in enumerate(payload):
        ev = _validate_event(raw)
        if not ev:
            rejected += 1
            evt = raw.get("event_type", "?")
            errors.append(f"row {i}: invalid event (type={evt})")
            continue

        # Check within-batch duplicates first (avoids DB UNIQUE constraint error)
        if ev.event_id in seen_in_batch:
            duplicates += 1
            continue

        exists = (
            db.query(models.StoredEvent)
            .filter(models.StoredEvent.event_id == ev.event_id)
            .first()
        )
        if exists:
            duplicates += 1
            continue

        seen_in_batch.add(ev.event_id)

        zone = ev.zone_id or ev.zone
        camera = ev.camera_id or ev.camera
        meta = dict(ev.metadata)
        qd = ev.queue_depth if ev.queue_depth is not None else meta.get("queue_depth")

        row = models.StoredEvent(
            event_id=ev.event_id,
            visitor_id=ev.visitor_id,
            event_type=ev.event_type,
            timestamp=ev.timestamp,
            zone=zone,
            dwell_ms=ev.dwell_ms,
            queue_depth=int(qd) if qd is not None else None,
            store_id=ev.store_id or resolve_store_id("ST1008"),
            camera=camera,
            is_staff=ev.is_staff,
            confidence=ev.confidence,
            raw_json=json.dumps(raw),
        )
        db.add(row)
        accepted += 1

    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise

    # Run POS correlation after every successful ingest
    if accepted:
        try:
            analytics.correlate_pos_purchases(db, DATASET_DIR)
        except Exception as exc:
            logger.warning("POS correlation skipped: %s", exc)

    logger.info(
        json.dumps({
            "endpoint": "/events/ingest",
            "event_count": len(payload),
            "accepted": accepted,
            "rejected": rejected,
            "duplicates": duplicates,
        })
    )
    return IngestResponse(
        accepted=accepted, rejected=rejected, duplicates=duplicates, errors=errors[:20]
    )


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@app.post("/events/ingest", response_model=IngestResponse, summary="Batch ingest events (idempotent)")
def ingest_events(payload: List[dict], db: Session = Depends(get_db)):
    """Accept up to 500 events per batch. Idempotent by event_id.
    Returns partial success even if some events are malformed.
    """
    return _ingest_batch(payload, db)


@app.get("/stores/{store_id}/metrics", response_model=MetricsResponse, summary="Real-time store metrics")
def store_metrics(store_id: str, db: Session = Depends(get_db)):
    """Unique visitors, conversion rate, avg dwell per zone, queue depth, abandonment rate.
    Excludes is_staff=True events. Real-time — not cached.
    """
    return MetricsResponse(**analytics.compute_metrics(db, DATASET_DIR, store_id))


@app.get("/stores/{store_id}/funnel", response_model=FunnelResponse, summary="Conversion funnel")
def store_funnel(store_id: str, db: Session = Depends(get_db)):
    """Entry → Zone Visit → Billing Queue → Purchase with drop-off %.
    Session is the unit. Re-entries do not double-count a visitor.
    """
    data = analytics.compute_funnel(db, DATASET_DIR, store_id)
    return FunnelResponse(
        store_id=data["store_id"],
        stages=[FunnelStage(**s) for s in data["stages"]],
    )


@app.get("/stores/{store_id}/heatmap", response_model=HeatmapResponse, summary="Zone traffic heatmap")
def store_heatmap(store_id: str, db: Session = Depends(get_db)):
    """Zone visit frequency + avg dwell, normalised 0–100.
    data_confidence=LOW when fewer than 20 sessions in window.
    """
    data = analytics.compute_heatmap(db, store_id)
    zones = {k: HeatmapZone(**v) for k, v in data["zones"].items()}
    return HeatmapResponse(
        store_id=data["store_id"], zones=zones, data_confidence=data["data_confidence"]
    )


@app.get("/stores/{store_id}/anomalies", response_model=List[AnomalyItem], summary="Active anomalies")
def store_anomalies(store_id: str, db: Session = Depends(get_db)):
    """Active anomalies: queue spike, conversion drop, dead zone, high abandonment.
    Severity: INFO / WARN / CRITICAL. Includes suggested_action per anomaly.
    """
    return [AnomalyItem(**a) for a in analytics.compute_anomalies(db, store_id)]


@app.get("/health", response_model=HealthResponse, summary="Service health")
def health(db: Session = Depends(get_db)):
    """Service status + last event timestamp per store.
    STALE_FEED warning when last event is >10 minutes ago.
    """
    h = analytics.compute_health(db)
    stores = {k: StoreHealth(**v) for k, v in h["stores"].items()}
    return HealthResponse(status=h["status"], stores=stores)


# ---------------------------------------------------------------------------
# Admin / demo endpoints
# ---------------------------------------------------------------------------

@app.post("/admin/reload-from-file", summary="Load events from a JSONL file (demo)")
def reload_from_file(path: str = "events.jsonl", db: Session = Depends(get_db)):
    """Read a JSONL file and ingest all events. Useful for demo and after clear-db."""
    p = Path(path)
    if not p.exists():
        raise HTTPException(404, f"File not found: {path}")
    events = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))
    if not events:
        raise HTTPException(400, "File is empty")
    return _ingest_batch(events, db)


@app.post("/admin/clear-db", summary="Wipe all events from the database (demo/testing)")
def clear_db(db: Session = Depends(get_db)):
    """Delete all stored events. Used to reset state for live-streaming demo."""
    try:
        count = db.query(models.StoredEvent).delete()
        db.commit()
        logger.info("admin/clear-db: deleted %d events", count)
        return {"deleted": count, "message": "Database cleared. Ready for live stream."}
    except SQLAlchemyError:
        db.rollback()
        raise


# ---------------------------------------------------------------------------
# Legacy aliases (backward compat for dashboard / earlier tests)
# ---------------------------------------------------------------------------

@app.get("/metrics")
def metrics_legacy(db: Session = Depends(get_db)):
    return store_metrics("ST1008", db)


@app.get("/funnel")
def funnel_legacy(db: Session = Depends(get_db)):
    return store_funnel("ST1008", db)


@app.get("/heatmap")
def heatmap_legacy(db: Session = Depends(get_db)):
    return store_heatmap("ST1008", db)


@app.get("/anomalies")
def anomalies_legacy(db: Session = Depends(get_db)):
    return store_anomalies("ST1008", db)
