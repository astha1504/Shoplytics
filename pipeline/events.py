import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Challenge event catalogue (+ internal purchase marker for tests)
VALID_EVENT_TYPES = {
    "ENTRY",
    "EXIT",
    "REENTRY",
    "ZONE_ENTER",
    "ZONE_EXIT",
    "ZONE_DWELL",
    "BILLING_QUEUE_JOIN",
    "BILLING_QUEUE_ABANDON",
    "PURCHASE_CORRELATED",
}


@dataclass
class Event:
    event_type: str
    timestamp: str
    store_id: str
    camera_id: str
    visitor_id: Optional[str] = None
    zone_id: Optional[str] = None
    dwell_ms: int = 0
    is_staff: bool = False
    confidence: float = 0.85
    track_id: Optional[int] = None
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: dict[str, Any] = field(default_factory=dict)
    session_seq: int = 0

    def to_dict(self) -> dict[str, Any]:
        meta = dict(self.metadata)
        if self.session_seq:
            meta.setdefault("session_seq", self.session_seq)
        out: dict[str, Any] = {
            "event_id": self.event_id,
            "store_id": self.store_id,
            "camera_id": self.camera_id,
            "visitor_id": self.visitor_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "zone_id": self.zone_id,
            "dwell_ms": self.dwell_ms,
            "is_staff": self.is_staff,
            "confidence": round(self.confidence, 3),
            "metadata": meta,
        }
        if self.zone_id is None and self.event_type in ("ENTRY", "EXIT", "REENTRY"):
            out["zone_id"] = None
        qd = meta.get("queue_depth")
        if qd is not None:
            out["metadata"]["queue_depth"] = qd
        return out

    def validate(self) -> bool:
        if self.event_type not in VALID_EVENT_TYPES:
            return False
        if self.event_type in ("ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL") and not self.zone_id:
            return False
        if not self.store_id or not self.camera_id:
            return False
        return True


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_ingest_event(raw: dict[str, Any]) -> dict[str, Any]:
    """Accept legacy (zone, camera) and challenge (zone_id, camera_id) keys."""
    out = dict(raw)
    if "zone_id" not in out and "zone" in out:
        out["zone_id"] = out.pop("zone")
    if "camera_id" not in out and "camera" in out:
        out["camera_id"] = out.pop("camera")
    if "queue_depth" in out and "metadata" not in out:
        out["metadata"] = {"queue_depth": out.pop("queue_depth")}
    return out


class EventWriter:
    def __init__(self, path: Path):
        self.path = path
        self._seen: set[str] = set()

    def emit(self, event: Event) -> bool:
        if not event.validate():
            return False
        if event.event_id in self._seen:
            return False
        self._seen.add(event.event_id)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_dict()) + "\n")
        return True


def load_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events
