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
