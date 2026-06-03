"""Pydantic schemas matching the Apex Retail hiring challenge spec exactly."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class EventIn(BaseModel):
    """Challenge event schema — accepts both zone_id and legacy zone key."""

    event_id: str
    event_type: str
    timestamp: str
    store_id: Optional[str] = None
    camera_id: Optional[str] = None
    camera: Optional[str] = None  # legacy alias
    visitor_id: Optional[str] = None
    zone_id: Optional[str] = None
    zone: Optional[str] = None  # legacy alias
    dwell_ms: int = 0
    is_staff: bool = False
    confidence: float = 0.85
    metadata: Dict[str, Any] = Field(default_factory=dict)
    track_id: Optional[int] = None
    queue_depth: Optional[int] = None  # top-level alias for metadata.queue_depth


class IngestResponse(BaseModel):
    accepted: int
    rejected: int
    duplicates: int
    errors: List[str] = Field(default_factory=list)


class MetricsResponse(BaseModel):
    """GET /stores/{id}/metrics — real-time store analytics."""

    store_id: str
    visitors: int
    conversion_rate: float  # %
    avg_dwell_seconds: float  # store-wide average
    avg_dwell_per_zone: Dict[str, float] = Field(default_factory=dict)  # per-zone breakdown
    queue_depth: int
    abandonment_rate: float = 0.0  # %
    visitors_per_staff: float = 0.0
    revenue_leakage_est: float = 0.0


class FunnelStage(BaseModel):
    stage: str
    count: int
    drop_off_pct: float = 0.0


class FunnelResponse(BaseModel):
    """GET /stores/{id}/funnel — session-level conversion funnel."""

    store_id: str
    stages: List[FunnelStage]


class HeatmapZone(BaseModel):
    score: int  # 0–100, normalised visit frequency
    visits: int
    avg_dwell_seconds: float


class HeatmapResponse(BaseModel):
    """GET /stores/{id}/heatmap — zone visit frequency + dwell, normalised 0–100."""

    store_id: str
    zones: Dict[str, HeatmapZone]
    data_confidence: str = "HIGH"  # HIGH / LOW (LOW if < 20 sessions)


class AnomalyItem(BaseModel):
    """Single anomaly: severity is INFO / WARN / CRITICAL."""

    type: str
    severity: str
    message: str = ""
    suggested_action: str = ""


class StoreHealth(BaseModel):
    last_event_at: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    """GET /health — service status + per-store STALE_FEED detection."""

    status: str = "healthy"
    stores: Dict[str, StoreHealth] = Field(default_factory=dict)


class DashboardResponse(BaseModel):
    store_id: str
    occupancy: int
    occupancy_level: str
    store_vibe: str
    conversion_rate: float
    queue_depth: int
    active_alerts: int
    uptime_seconds: int
    staff_needed: int
    queue_label: str
    visitors_per_staff: float = 0.0
    revenue_leakage_est: float = 0.0


class SystemStatusResponse(BaseModel):
    yolo_pipeline: str
    fastapi_backend: str
    vibe_engine: str
    anomaly_detector: str
    last_inference: Optional[str] = None
    detection_running: bool = False


class DetectStartRequest(BaseModel):
    source_type: str
    source_path: Optional[str] = None
    webcam_index: int = 0
    role: str = "floor"
    camera_id: Optional[str] = None
    store_id: str = "ST1008"
    realtime: bool = True
    max_frames: Optional[int] = None
    fps_skip: int = 2


class SpatialVisitor(BaseModel):
    visitor_id: str
    display_id: str
    x: float
    y: float
    zone: Optional[str] = None
    status: str = "active"
    is_active: bool = True
    last_event_type: Optional[str] = None
    last_seen: Optional[str] = None
    trail: List[dict] = Field(default_factory=list)


class SpatialZone(BaseModel):
    polygon: Optional[List[List[float]]] = None
    line: Optional[dict] = None
    centroid: dict
    color: str = "#64748b"


class SpatialResponse(BaseModel):
    """GET /stores/{id}/spatial — live customer positions on floor plan."""

    store_id: str
    canvas: dict
    zones: Dict[str, SpatialZone]
    visitors: List[SpatialVisitor]
    active_visitors: int
    total_tracked: int
