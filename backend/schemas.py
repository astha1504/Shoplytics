from typing import Any, Optional

from pydantic import BaseModel, Field


class EventIn(BaseModel):
    event_id: str
    event_type: str
    timestamp: str
    store_id: Optional[str] = None
    camera_id: Optional[str] = None
    camera: Optional[str] = None
    visitor_id: Optional[str] = None
    zone_id: Optional[str] = None
    zone: Optional[str] = None
    dwell_ms: int = 0
    is_staff: bool = False
    confidence: float = 0.85
    metadata: dict[str, Any] = Field(default_factory=dict)
    track_id: Optional[int] = None
    queue_depth: Optional[int] = None


class IngestResponse(BaseModel):
    accepted: int
    rejected: int
    duplicates: int
    errors: list[str] = Field(default_factory=list)


class MetricsResponse(BaseModel):
    store_id: str
    visitors: int
    conversion_rate: float
    avg_dwell_seconds: float
    queue_depth: int
    abandonment_rate: float = 0.0


class FunnelStage(BaseModel):
    stage: str
    count: int
    drop_off_pct: float = 0.0


class FunnelResponse(BaseModel):
    store_id: str
    stages: list[FunnelStage]


class HeatmapZone(BaseModel):
    score: int
    visits: int
    avg_dwell_seconds: float


class HeatmapResponse(BaseModel):
    store_id: str
    zones: dict[str, HeatmapZone]
    data_confidence: str = "HIGH"


class AnomalyItem(BaseModel):
    type: str
    severity: str
    message: str = ""
    suggested_action: str = ""


class StoreHealth(BaseModel):
    last_event_at: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str = "healthy"
    stores: dict[str, StoreHealth] = Field(default_factory=dict)
