"""Spatial analytics — live customer positions on store floor plan.

Derives x/y coordinates from event metadata (camera centroids) or zone polygon
centroids. Builds movement trails per visitor session for the dashboard.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from backend.analytics import _parse_ts, customer_events, _filter_store
from backend.models import StoredEvent
from pipeline.dataset_loader import resolve_store_id

# Layout canvas bounds (matches config/store_layout.json coordinate space)
CANVAS_W = 1100
CANVAS_H = 720

ZONE_COLORS = {
    "SKINCARE": "#60a5fa",
    "MAKEUP": "#f472b6",
    "FRAGRANCE": "#a78bfa",
    "HAIRCARE": "#34d399",
    "ACCESSORIES": "#fbbf24",
    "BILLING": "#fb923c",
    "ENTRY": "#38bdf8",
}


def _load_layout() -> dict[str, Any]:
    path = Path(__file__).resolve().parent.parent / "config" / "store_layout.json"
    if not path.exists():
        return {"zones": {}, "entry_line": {}, "billing_queue_zone": []}
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _zone_centroid(polygon: list[list[int]]) -> tuple[float, float]:
    if not polygon:
        return CANVAS_W / 2, CANVAS_H / 2
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _jitter(visitor_id: str, scale: float = 45.0) -> tuple[float, float]:
    """Deterministic offset so multiple visitors in one zone don't overlap."""
    h = hashlib.md5(visitor_id.encode()).hexdigest()
    dx = (int(h[:4], 16) / 65535 - 0.5) * scale * 2
    dy = (int(h[4:8], 16) / 65535 - 0.5) * scale * 2
    return dx, dy


def _norm(x: float, y: float) -> dict[str, float]:
    return {
        "x": round(x / CANVAS_W * 100, 2),
        "y": round(y / CANVAS_H * 100, 2),
        "x_raw": round(x, 1),
        "y_raw": round(y, 1),
    }


def _position_from_event(e: StoredEvent, layout: dict[str, Any]) -> tuple[float, float] | None:
    meta: dict[str, Any] = {}
    if e.raw_json:
        try:
            meta = json.loads(e.raw_json).get("metadata") or {}
        except (json.JSONDecodeError, TypeError):
            pass

    px = meta.get("position_x") or meta.get("zone_hotspot_x")
    py = meta.get("position_y") or meta.get("zone_hotspot_y")
    if px is not None and py is not None:
        return float(px), float(py)

    zone = e.zone
    zones = layout.get("zones", {})
    if zone and zone in zones:
        cx, cy = _zone_centroid(zones[zone])
        if e.visitor_id:
            dx, dy = _jitter(e.visitor_id)
            return cx + dx, cy + dy
        return cx, cy

    if e.event_type in ("ENTRY", "REENTRY", "EXIT"):
        el = layout.get("entry_line", {})
        start = el.get("start", [200, 540])
        end = el.get("end", [400, 540])
        cx = (start[0] + end[0]) / 2
        cy = (start[1] + end[1]) / 2
        if e.visitor_id:
            dx, dy = _jitter(e.visitor_id, 12)
            return cx + dx, cy + dy
        return cx, cy

    if e.event_type in ("BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON"):
        bz = layout.get("billing_queue_zone", [])
        if bz:
            return _zone_centroid(bz)

    return None


def _normalize_zones(layout: dict[str, Any]) -> dict[str, Any]:
    zones_out: dict[str, Any] = {}
    for name, poly in layout.get("zones", {}).items():
        cx, cy = _zone_centroid(poly)
        zones_out[name] = {
            "polygon": [[round(p[0] / CANVAS_W * 100, 2), round(p[1] / CANVAS_H * 100, 2)] for p in poly],
            "centroid": _norm(cx, cy),
            "color": ZONE_COLORS.get(name, "#64748b"),
        }

    bz = layout.get("billing_queue_zone", [])
    if bz:
        cx, cy = _zone_centroid(bz)
        zones_out["BILLING"] = {
            "polygon": [[round(p[0] / CANVAS_W * 100, 2), round(p[1] / CANVAS_H * 100, 2)] for p in bz],
            "centroid": _norm(cx, cy),
            "color": ZONE_COLORS["BILLING"],
        }

    el = layout.get("entry_line", {})
    if el.get("start") and el.get("end"):
        s, end = el["start"], el["end"]
        zones_out["ENTRY"] = {
            "line": {
                "start": _norm(s[0], s[1]),
                "end": _norm(end[0], end[1]),
            },
            "centroid": _norm((s[0] + end[0]) / 2, (s[1] + end[1]) / 2),
            "color": ZONE_COLORS["ENTRY"],
        }

    return zones_out


def compute_spatial(db: Session, store_id: str, trail_limit: int = 12) -> dict[str, Any]:
    """Return floor layout + active visitors with positions and movement trails."""
    store_id = resolve_store_id(store_id)
    layout = _load_layout()
    events = sorted(
        customer_events(_filter_store(db.query(StoredEvent).all(), store_id)),
        key=lambda e: e.timestamp,
    )

    # Per-visitor session state
    sessions: dict[str, dict[str, Any]] = {}
    exited: set[str] = set()

    for e in events:
        vid = e.visitor_id
        if not vid:
            continue

        if e.event_type == "EXIT":
            exited.add(vid)

        if e.event_type in ("ENTRY", "REENTRY"):
            exited.discard(vid)
            if vid not in sessions:
                sessions[vid] = {
                    "visitor_id": vid,
                    "entered_at": e.timestamp,
                    "trail": [],
                    "current_zone": None,
                    "last_event_type": e.event_type,
                    "status": "active",
                }

        if vid not in sessions:
            continue

        sess = sessions[vid]
        pos = _position_from_event(e, layout)
        if pos:
            point = {
                **_norm(pos[0], pos[1]),
                "timestamp": e.timestamp,
                "event_type": e.event_type,
                "zone": e.zone,
            }
            sess["trail"].append(point)
            if len(sess["trail"]) > trail_limit:
                sess["trail"] = sess["trail"][-trail_limit:]

        if e.zone:
            sess["current_zone"] = e.zone
        sess["last_event_type"] = e.event_type
        sess["last_seen"] = e.timestamp

        if e.event_type == "BILLING_QUEUE_JOIN":
            sess["status"] = "in_queue"
        elif e.event_type == "PURCHASE_CORRELATED":
            sess["status"] = "converted"
        elif e.event_type == "EXIT":
            sess["status"] = "exited"

    visitors_out: list[dict[str, Any]] = []
    active_count = 0

    for vid, sess in sessions.items():
        trail = sess.get("trail", [])
        if not trail:
            continue

        last = trail[-1]
        is_active = vid not in exited and sess.get("status") != "exited"
        if is_active:
            active_count += 1

        visitors_out.append({
            "visitor_id": vid,
            "display_id": vid.replace("VIS_", "#") if vid.startswith("VIS_") else vid,
            "x": last["x"],
            "y": last["y"],
            "zone": sess.get("current_zone"),
            "status": sess.get("status", "active"),
            "is_active": is_active,
            "last_event_type": sess.get("last_event_type"),
            "last_seen": sess.get("last_seen"),
            "trail": [{"x": p["x"], "y": p["y"]} for p in trail],
        })

    # Sort: active first, then by last_seen
    visitors_out.sort(key=lambda v: (not v["is_active"], v.get("last_seen") or ""))

    return {
        "store_id": store_id,
        "canvas": {"width": CANVAS_W, "height": CANVAS_H},
        "zones": _normalize_zones(layout),
        "visitors": visitors_out,
        "active_visitors": active_count,
        "total_tracked": len(visitors_out),
    }
