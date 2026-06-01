"""Store Intelligence API — Apex Retail hiring challenge."""

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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("store_intelligence")

app = FastAPI(title="Apex Store Intelligence API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DATASET_DIR = Path(__file__).resolve().parent.parent / "dataset"
MAX_INGEST_BATCH = 500


@app.middleware("http")
async def request_logging(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-Id", str(uuid.uuid4())[:8])
    start = time.perf_counter()
    response = await call_next(request)
    latency_ms = int((time.perf_counter() - start) * 1000)
    logger.info(
        json.dumps(
            {
                "trace_id": trace_id,
                "path": request.url.path,
                "method": request.method,
                "latency_ms": latency_ms,
                "status_code": response.status_code,
            }
        )
    )
    response.headers["X-Trace-Id"] = trace_id
    return response


@app.exception_handler(SQLAlchemyError)
async def db_unavailable(_request: Request, _exc: SQLAlchemyError):
    return JSONResponse(
        status_code=503,
        content={"error": "database_unavailable", "message": "Storage layer unavailable"},
    )


@app.on_event("startup")
def startup():
    init_db()


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


def _ingest_batch(payload: List[dict], db: Session) -> IngestResponse:
    if len(payload) > MAX_INGEST_BATCH:
        raise HTTPException(400, f"Batch limit is {MAX_INGEST_BATCH} events")

    accepted = rejected = duplicates = 0
    errors: list[str] = []

    for i, raw in enumerate(payload):
        ev = _validate_event(raw)
        if not ev:
            rejected += 1
            errors.append(f"row {i}: invalid event")
            continue
        exists = (
            db.query(models.StoredEvent).filter(models.StoredEvent.event_id == ev.event_id).first()
        )
        if exists:
            duplicates += 1
            continue

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

    if accepted:
        analytics.correlate_pos_purchases(db, DATASET_DIR)

    logger.info(
        json.dumps(
            {"endpoint": "/events/ingest", "event_count": len(payload), "accepted": accepted}
        )
    )
    return IngestResponse(
        accepted=accepted, rejected=rejected, duplicates=duplicates, errors=errors[:20]
    )


@app.post("/events/ingest", response_model=IngestResponse)
def ingest_events(payload: List[dict], db: Session = Depends(get_db)):
    return _ingest_batch(payload, db)


@app.get("/stores/{store_id}/metrics", response_model=MetricsResponse)
def store_metrics(store_id: str, db: Session = Depends(get_db)):
    return MetricsResponse(**analytics.compute_metrics(db, DATASET_DIR, store_id))


@app.get("/stores/{store_id}/funnel", response_model=FunnelResponse)
def store_funnel(store_id: str, db: Session = Depends(get_db)):
    data = analytics.compute_funnel(db, DATASET_DIR, store_id)
    return FunnelResponse(
        store_id=data["store_id"],
        stages=[FunnelStage(**s) for s in data["stages"]],
    )


@app.get("/stores/{store_id}/heatmap", response_model=HeatmapResponse)
def store_heatmap(store_id: str, db: Session = Depends(get_db)):
    data = analytics.compute_heatmap(db, store_id)
    zones = {k: HeatmapZone(**v) for k, v in data["zones"].items()}
    return HeatmapResponse(
        store_id=data["store_id"], zones=zones, data_confidence=data["data_confidence"]
    )


@app.get("/stores/{store_id}/anomalies", response_model=List[AnomalyItem])
def store_anomalies(store_id: str, db: Session = Depends(get_db)):
    return [AnomalyItem(**a) for a in analytics.compute_anomalies(db, store_id)]


@app.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_db)):
    h = analytics.compute_health(db)
    stores = {k: StoreHealth(**v) for k, v in h["stores"].items()}
    return HealthResponse(status=h["status"], stores=stores)


# Legacy aliases (dashboard / earlier tests)
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


@app.post("/admin/reload-from-file")
def reload_from_file(path: str = "events.jsonl", db: Session = Depends(get_db)):
    p = Path(path)
    if not p.exists():
        raise HTTPException(404, f"File not found: {path}")
    events = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))
    return _ingest_batch(events, db)
