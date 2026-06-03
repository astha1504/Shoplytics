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

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend import analytics, models
from backend import spatial as spatial_mod
from backend.database import get_db, init_db
from backend import export as export_mod
from backend import timeline
from backend.live_detect import UPLOAD_DIR, get_detection_manager
from backend.schemas import (
    AnomalyItem,
    DashboardResponse,
    DetectStartRequest,
    EventIn,
    FunnelResponse,
    FunnelStage,
    HealthResponse,
    HeatmapResponse,
    HeatmapZone,
    IngestResponse,
    MetricsResponse,
    SpatialResponse,
    SpatialVisitor,
    SpatialZone,
    StoreHealth,
    SystemStatusResponse,
)
from pipeline.dataset_loader import resolve_store_id
from pipeline.events import VALID_EVENT_TYPES, normalize_ingest_event

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("store_intelligence")

app = FastAPI(
    title="Store Intelligence System",
    version="1.0.0",
    description="Real-time retail analytics from CCTV events — Apex Retail challenge.",
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
    mgr = get_detection_manager()

    def _ingest(batch: list[dict]) -> None:
        from backend.database import SessionLocal

        db = SessionLocal()
        try:
            _ingest_batch(batch, db)
        finally:
            db.close()

    mgr.set_ingest_handler(_ingest)


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

@app.get("/", summary="Root")
def root():
    return {"service": "Store Intelligence System", "docs": "/docs", "health": "/health"}


@app.post("/events/ingest", response_model=IngestResponse, summary="Ingest")
def ingest_events(payload: List[dict], db: Session = Depends(get_db)):
    """Accept up to 500 events per batch. Idempotent by event_id.
    Returns partial success even if some events are malformed.
    """
    return _ingest_batch(payload, db)


@app.get("/stores/{store_id}/metrics", response_model=MetricsResponse, summary="Store Metrics")
def store_metrics(store_id: str, db: Session = Depends(get_db)):
    data = analytics.compute_metrics(db, DATASET_DIR, store_id)
    timeline.record_metrics(store_id, data["visitors"], data["queue_depth"], data["conversion_rate"])
    return MetricsResponse(**data)


@app.get("/stores/{store_id}/dashboard", response_model=DashboardResponse, summary="Shoplytics dashboard bundle")
def store_dashboard(store_id: str, db: Session = Depends(get_db)):
    m = analytics.compute_metrics(db, DATASET_DIR, store_id)
    timeline.record_metrics(store_id, m["visitors"], m["queue_depth"], m["conversion_rate"])
    anomalies = analytics.compute_anomalies(db, store_id)
    occ = m["visitors"]
    level = "LOW" if occ <= 5 else "MODERATE" if occ <= 10 else "HIGH"
    q = m["queue_depth"]
    return DashboardResponse(
        store_id=m["store_id"],
        occupancy=occ,
        occupancy_level=level,
        store_vibe=timeline.vibe_label(occ),
        conversion_rate=m["conversion_rate"],
        queue_depth=q,
        active_alerts=len(anomalies),
        uptime_seconds=timeline.uptime_seconds(),
        staff_needed=max(1, q // 3 + (1 if occ > 10 else 0)),
        queue_label="Short" if q <= 2 else "Moderate" if q <= 5 else "Long",
        visitors_per_staff=m.get("visitors_per_staff", 0.0),
        revenue_leakage_est=m.get("revenue_leakage_est", 0.0),
    )


@app.get("/stores/{store_id}/occupancy-trend", summary="Occupancy trend (last 30 readings)")
def occupancy_trend(store_id: str):
    return {"store_id": store_id, "readings": timeline.get_occupancy_trend(store_id), "threshold": timeline.OCCUPANCY_THRESHOLD}


@app.get("/stores/{store_id}/vibe-history", summary="Vibe history")
def vibe_history(store_id: str):
    return {"store_id": store_id, "history": timeline.get_vibe_history(store_id), "breakdown": timeline.get_vibe_breakdown(store_id)}


@app.get("/system/status", response_model=SystemStatusResponse, summary="System health panel")
def system_status(db: Session = Depends(get_db)):
    mgr = get_detection_manager()
    anomalies = analytics.compute_anomalies(db, resolve_store_id("ST1008"))
    last = timeline.LAST_INFERENCE
    last_str = None
    if last:
        from datetime import datetime, timezone
        last_str = datetime.fromtimestamp(last, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return SystemStatusResponse(
        yolo_pipeline="HEALTHY" if not mgr.status.error else "ERROR",
        fastapi_backend="ONLINE",
        vibe_engine="RUNNING",
        anomaly_detector="ALERT" if anomalies else "CLEAR",
        last_inference=last_str,
        detection_running=mgr.status.running,
    )


@app.get("/stores/{store_id}/export/json", summary="Export JSON report")
def export_json(store_id: str, db: Session = Depends(get_db)):
    return export_mod.export_json(db, store_id)


@app.get("/stores/{store_id}/export/csv", summary="Export CSV occupancy log")
def export_csv(store_id: str, db: Session = Depends(get_db)):
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(export_mod.export_csv(db, store_id), media_type="text/csv")


@app.get("/stores/{store_id}/export/html", summary="Export HTML report")
def export_html_report(store_id: str, db: Session = Depends(get_db)):
    return HTMLResponse(export_mod.export_html(db, store_id))


@app.get("/stores/{store_id}/funnel", response_model=FunnelResponse, summary="Store Funnel")
def store_funnel(store_id: str, db: Session = Depends(get_db)):
    """Entry → Zone Visit → Billing Queue → Purchase with drop-off %.
    Session is the unit. Re-entries do not double-count a visitor.
    """
    data = analytics.compute_funnel(db, DATASET_DIR, store_id)
    return FunnelResponse(
        store_id=data["store_id"],
        stages=[FunnelStage(**s) for s in data["stages"]],
    )


@app.get("/stores/{store_id}/spatial", response_model=SpatialResponse, summary="Live spatial analytics")
def store_spatial(store_id: str, db: Session = Depends(get_db)):
    """Customer positions and movement trails on the store floor plan.
    Positions update as ZONE_ENTER / ZONE_DWELL events flow in from the detection pipeline.
    """
    data = spatial_mod.compute_spatial(db, store_id)
    zones = {k: SpatialZone(**v) for k, v in data["zones"].items()}
    visitors = [SpatialVisitor(**v) for v in data["visitors"]]
    return SpatialResponse(
        store_id=data["store_id"],
        canvas=data["canvas"],
        zones=zones,
        visitors=visitors,
        active_visitors=data["active_visitors"],
        total_tracked=data["total_tracked"],
    )


@app.get("/stores/{store_id}/heatmap", response_model=HeatmapResponse, summary="Store Heatmap")
def store_heatmap(store_id: str, db: Session = Depends(get_db)):
    """Zone visit frequency + avg dwell, normalised 0–100.
    data_confidence=LOW when fewer than 20 sessions in window.
    """
    data = analytics.compute_heatmap(db, store_id)
    zones = {k: HeatmapZone(**v) for k, v in data["zones"].items()}
    return HeatmapResponse(
        store_id=data["store_id"], zones=zones, data_confidence=data["data_confidence"]
    )


@app.get("/stores/{store_id}/anomalies", response_model=List[AnomalyItem], summary="Store Anomalies")
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


_ROLE_CAM = {"entry": "CAM_ENTRY_01", "floor": "CAM_FLOOR_01", "billing": "CAM_BILLING_01"}


@app.post("/detect/start", summary="Start YOLOv8 detection")
def detect_start(body: DetectStartRequest):
    if body.source_type not in ("webcam", "file", "rtsp"):
        raise HTTPException(400, "source_type: webcam | file | rtsp")
    if body.source_type in ("file", "rtsp") and not body.source_path:
        raise HTTPException(400, "source_path required")
    cam = body.camera_id or _ROLE_CAM.get(body.role, "CAM_FLOOR_01")
    r = get_detection_manager().start(
        source_type=body.source_type,
        source_path=body.source_path,
        webcam_index=body.webcam_index,
        role=body.role,
        camera_id=cam,
        store_id=body.store_id,
        realtime=body.realtime,
        max_frames=body.max_frames,
        fps_skip=body.fps_skip,
    )
    if not r.get("ok"):
        raise HTTPException(409, r.get("error", "busy"))
    return r


@app.post("/detect/stop", summary="Stop detection")
def detect_stop():
    return get_detection_manager().stop()


@app.get("/detect/status", summary="Detection status")
def detect_status():
    s = get_detection_manager().status
    return {
        "running": s.running,
        "source": s.source,
        "role": s.role,
        "camera_id": s.camera_id,
        "frames_processed": s.frames_processed,
        "events_emitted": s.events_emitted,
        "persons_tracked": s.persons_tracked,
        "fps": s.fps,
        "error": s.error,
        "last_event_types": s.last_event_types,
    }


@app.get("/detect/frame", summary="Latest annotated frame")
def detect_frame():
    jpeg = get_detection_manager().get_frame_jpeg()
    if not jpeg:
        raise HTTPException(404, "No frame yet")
    return Response(content=jpeg, media_type="image/jpeg")


@app.post("/detect/upload", summary="Upload clip and run YOLOv8")
async def detect_upload(
    file: UploadFile = File(...),
    role: str = "floor",
    store_id: str = "ST1008",
    realtime: bool = True,
):
    if not file.filename or not file.filename.lower().endswith((".mp4", ".avi", ".mov", ".mkv")):
        raise HTTPException(400, "Upload a video file")
    dest = UPLOAD_DIR / file.filename
    dest.write_bytes(await file.read())
    cam = _ROLE_CAM.get(role, "CAM_FLOOR_01")
    r = get_detection_manager().start(source_type="file", source_path=str(dest), role=role, camera_id=cam, store_id=store_id, realtime=realtime)
    if not r.get("ok"):
        raise HTTPException(409, r.get("error", "busy"))
    return {"ok": True, "file": file.filename, **r}


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

@app.get("/metrics", summary="Metrics Legacy")
def metrics_legacy(db: Session = Depends(get_db)):
    return store_metrics("ST1008", db)


@app.get("/funnel", summary="Funnel Legacy")
def funnel_legacy(db: Session = Depends(get_db)):
    return store_funnel("ST1008", db)


@app.get("/heatmap", summary="Heatmap Legacy")
def heatmap_legacy(db: Session = Depends(get_db)):
    return store_heatmap("ST1008", db)


@app.get("/anomalies", summary="Anomalies Legacy")
def anomalies_legacy(db: Session = Depends(get_db)):
    return store_anomalies("ST1008", db)
