"""Metrics, funnel, heatmap, anomalies — challenge-aligned, staff excluded."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from backend.models import StoredEvent
from pipeline.config import POS_CORRELATION_WINDOW_MS
from pipeline.dataset_loader import load_pos_transactions, resolve_store_id, store_id_matches

VALID_TYPES = {
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


def _parse_ts(ts: str) -> datetime:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt
    except ValueError:
        return datetime.utcnow()


def _zone_name(e: StoredEvent) -> str | None:
    return e.zone


def _queue_depth(e: StoredEvent) -> int:
    if e.queue_depth:
        return e.queue_depth
    if e.raw_json:
        try:
            meta = json.loads(e.raw_json).get("metadata") or {}
            q = meta.get("queue_depth")
            return int(q) if q is not None else 0
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return 0


def _filter_store(events: list[StoredEvent], store_id: str) -> list[StoredEvent]:
    return [e for e in events if store_id_matches(e.store_id, store_id)]


def customer_events(events: list[StoredEvent]) -> list[StoredEvent]:
    return [e for e in events if not e.is_staff and e.event_type in VALID_TYPES]


def count_visitors(events: list[StoredEvent]) -> int:
    visitors = set()
    for e in events:
        if e.event_type in ("ENTRY", "REENTRY") and e.visitor_id:
            visitors.add(e.visitor_id)
    return len(visitors)


def compute_metrics(
    db: Session, dataset_dir: Path | None, store_id: str
) -> dict[str, Any]:
    store_id = resolve_store_id(store_id)
    all_events = customer_events(_filter_store(db.query(StoredEvent).all(), store_id))
    visitors = count_visitors(all_events)

    purchases = _count_purchases(all_events, dataset_dir, store_id)
    conversion = (purchases / visitors * 100) if visitors else 0.0

    dwells = [e.dwell_ms for e in all_events if e.event_type == "ZONE_DWELL" and e.dwell_ms]
    avg_dwell = (sum(dwells) / len(dwells) / 1000) if dwells else 0.0

    queue_events = [e for e in all_events if e.event_type == "BILLING_QUEUE_JOIN"]
    queue_depth = max((_queue_depth(e) for e in queue_events), default=0)

    joins = len(queue_events)
    abandons = len([e for e in all_events if e.event_type == "BILLING_QUEUE_ABANDON"])
    abandonment_rate = (abandons / joins * 100) if joins else 0.0

    return {
        "store_id": store_id,
        "visitors": visitors,
        "conversion_rate": round(conversion, 1),
        "avg_dwell_seconds": round(avg_dwell, 1),
        "queue_depth": queue_depth,
        "abandonment_rate": round(abandonment_rate, 1),
    }


def compute_funnel(
    db: Session, dataset_dir: Path | None, store_id: str
) -> dict[str, Any]:
    store_id = resolve_store_id(store_id)
    events = customer_events(_filter_store(db.query(StoredEvent).all(), store_id))
    entry = len({e.visitor_id for e in events if e.event_type in ("ENTRY", "REENTRY") and e.visitor_id})
    zone = len({e.visitor_id for e in events if e.event_type == "ZONE_ENTER" and e.visitor_id})
    queue = len({e.visitor_id for e in events if e.event_type == "BILLING_QUEUE_JOIN" and e.visitor_id})
    purchase = _count_purchases(events, dataset_dir, store_id)

    counts = [("Entry", entry), ("Zone Visit", zone), ("Billing Queue", queue), ("Purchase", purchase)]
    stages = []
    prev = entry or 1
    for name, count in counts:
        drop = round((1 - count / prev) * 100, 1) if prev else 0.0
        stages.append({"stage": name, "count": count, "drop_off_pct": max(0.0, drop)})
        prev = count or prev
    return {"store_id": store_id, "stages": stages}


def compute_heatmap(db: Session, store_id: str) -> dict[str, Any]:
    store_id = resolve_store_id(store_id)
    events = customer_events(_filter_store(db.query(StoredEvent).all(), store_id))
    sessions = count_visitors(events)
    heat: dict[str, dict[str, Any]] = {}
    for e in events:
        z = _zone_name(e)
        if not z:
            continue
        if z not in heat:
            heat[z] = {"visits": 0, "dwell_sum_ms": 0, "dwell_n": 0}
        if e.event_type in ("ZONE_ENTER", "ZONE_DWELL"):
            heat[z]["visits"] += 1
        if e.event_type == "ZONE_DWELL" and e.dwell_ms:
            heat[z]["dwell_sum_ms"] += e.dwell_ms
            heat[z]["dwell_n"] += 1

    if not heat:
        return {"store_id": store_id, "zones": {}, "data_confidence": "LOW"}

    max_visits = max(v["visits"] for v in heat.values()) or 1
    zones_out = {}
    for z, v in heat.items():
        avg_dwell = (v["dwell_sum_ms"] / v["dwell_n"] / 1000) if v["dwell_n"] else 0.0
        score = int(100 * v["visits"] / max_visits)
        zones_out[z] = {
            "score": score,
            "visits": v["visits"],
            "avg_dwell_seconds": round(avg_dwell, 1),
        }

    confidence = "HIGH" if sessions >= 20 else "LOW"
    return {"store_id": store_id, "zones": zones_out, "data_confidence": confidence}


def compute_anomalies(db: Session, store_id: str) -> list[dict[str, str]]:
    store_id = resolve_store_id(store_id)
    events = customer_events(_filter_store(db.query(StoredEvent).all(), store_id))
    anomalies: list[dict[str, str]] = []

    queue_depths = [_queue_depth(e) for e in events if e.event_type == "BILLING_QUEUE_JOIN"]
    if queue_depths and max(queue_depths) >= 5:
        anomalies.append(
            {
                "type": "BILLING_QUEUE_SPIKE",
                "severity": "WARN",
                "message": f"Queue depth reached {max(queue_depths)}",
                "suggested_action": "Open additional billing counter or deploy floor staff to queue.",
            }
        )

    visitors = count_visitors(events)
    purchases = _count_purchases(events, None, store_id)
    if visitors >= 5 and purchases == 0:
        anomalies.append(
            {
                "type": "CONVERSION_DROP",
                "severity": "CRITICAL",
                "message": "Visitors present but no POS-correlated purchases",
                "suggested_action": "Check billing camera feed and POS sync; verify queue zone polygon.",
            }
        )

    zone_visitors = {e.visitor_id for e in events if e.event_type == "ZONE_ENTER"}
    if visitors >= 3 and not zone_visitors:
        anomalies.append(
            {
                "type": "DEAD_ZONE",
                "severity": "WARN",
                "message": "No zone visits detected despite store entries",
                "suggested_action": "Recalibrate floor zone polygons in config/store_layout.json",
            }
        )

    return anomalies


def last_event_time(db: Session, store_id: str) -> datetime | None:
    store_id = resolve_store_id(store_id)
    events = _filter_store(db.query(StoredEvent).all(), store_id)
    if not events:
        return None
    return max(_parse_ts(e.timestamp) for e in events)


def compute_health(db: Session) -> dict[str, Any]:
    stores: dict[str, Any] = {}
    store_ids = {resolve_store_id(e.store_id) for e in db.query(StoredEvent).all() if e.store_id}
    if not store_ids:
        store_ids = {resolve_store_id("ST1008")}

    status = "healthy"
    for sid in store_ids:
        last = last_event_time(db, sid)
        warnings: list[str] = []
        if last is None:
            warnings.append("NO_EVENTS")
        elif datetime.utcnow() - last > timedelta(minutes=10):
            warnings.append("STALE_FEED")
            status = "degraded"
        stores[sid] = {
            "last_event_at": last.isoformat() + "Z" if last else None,
            "warnings": warnings,
        }
    return {"status": status, "stores": stores}


def _load_pos(dataset_dir: Path | None, store_id: str) -> list[tuple[datetime, str]]:
    if not dataset_dir:
        return []
    from pipeline.dataset_loader import load_pos_transactions

    rows = []
    for ts, txn_id, sid, _basket in load_pos_transactions(dataset_dir):
        if store_id_matches(sid, store_id):
            t = ts.replace(tzinfo=None) if ts.tzinfo else ts
            rows.append((t, txn_id))
    return rows


def _count_purchases(
    events: list[StoredEvent], dataset_dir: Path | None, store_id: str
) -> int:
    explicit = {e.visitor_id for e in events if e.event_type == "PURCHASE_CORRELATED" and e.visitor_id}
    if explicit:
        return len(explicit)

    pos = _load_pos(dataset_dir, store_id)
    if not pos:
        return 0

    billing_visits: list[tuple[str, datetime]] = []
    for e in events:
        if e.event_type == "BILLING_QUEUE_JOIN" and e.visitor_id:
            billing_visits.append((e.visitor_id, _parse_ts(e.timestamp)))

    window = timedelta(milliseconds=POS_CORRELATION_WINDOW_MS)
    purchasers: set[str] = set()
    for txn_ts, _ in pos:
        for vid, visit_ts in billing_visits:
            if abs((txn_ts - visit_ts).total_seconds()) <= window.total_seconds():
                purchasers.add(vid)
    return len(purchasers)


def correlate_pos_purchases(db: Session, dataset_dir: Path, store_id: str | None = None) -> int:
    events = db.query(StoredEvent).all()
    sid = resolve_store_id(store_id or "ST1008")
    pos = _load_pos(dataset_dir, sid)
    if not pos:
        return 0

    billing = [
        (e.visitor_id, _parse_ts(e.timestamp))
        for e in events
        if e.event_type == "BILLING_QUEUE_JOIN"
        and e.visitor_id
        and not e.is_staff
        and store_id_matches(e.store_id, sid)
    ]
    window = timedelta(milliseconds=POS_CORRELATION_WINDOW_MS)
    added = 0
    existing = {e.event_id for e in events}

    for txn_ts, txn_id in pos:
        for vid, visit_ts in billing:
            if abs((txn_ts - visit_ts).total_seconds()) <= window.total_seconds():
                import uuid

                eid = str(uuid.uuid4())
                ev = StoredEvent(
                    event_id=eid,
                    visitor_id=vid,
                    event_type="PURCHASE_CORRELATED",
                    timestamp=txn_ts.isoformat().replace("+00:00", "Z"),
                    store_id=sid,
                    raw_json=json.dumps({"transaction_id": txn_id}),
                )
                db.add(ev)
                existing.add(eid)
                added += 1
    db.commit()
    return added
