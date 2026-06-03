"""Real-time metrics, funnel, heatmap, anomalies — challenge-aligned.

All analytics exclude is_staff=True events from customer counts.
POS correlation: visitor in BILLING zone within ±5 min of a transaction = purchased.
"""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from backend.models import StoredEvent
from pipeline.config import POS_CORRELATION_WINDOW_MS
from pipeline.dataset_loader import load_pos_transactions, resolve_store_id, store_id_matches

VALID_TYPES = {
    "ENTRY", "EXIT", "REENTRY",
    "ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL",
    "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON",
    "PURCHASE_CORRELATED",
}

# Anomaly thresholds
QUEUE_SPIKE_THRESHOLD = 5         # queue_depth >= this → WARN
QUEUE_CRITICAL_THRESHOLD = 8      # queue_depth >= this → CRITICAL
DEAD_ZONE_WINDOW_MIN = 30         # no zone visits in this many minutes → DEAD_ZONE
CONVERSION_DROP_THRESHOLD = 0.5   # conversion < 50% of historical avg → anomaly
STALE_FEED_MINUTES = 10           # last event older than this → STALE_FEED


def _parse_ts(ts: str) -> datetime:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt
    except (ValueError, AttributeError):
        return datetime.utcnow()


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
    """Filter out staff events and unknown types."""
    return [e for e in events if not e.is_staff and e.event_type in VALID_TYPES]


def count_visitors(events: list[StoredEvent]) -> int:
    """Unique customer visitor_ids from ENTRY or REENTRY events.
    REENTRY reuses the same visitor_id so it does NOT inflate the count.
    """
    return len({e.visitor_id for e in events
                if e.event_type in ("ENTRY", "REENTRY") and e.visitor_id})


# ---------------------------------------------------------------------------
# /stores/{id}/metrics
# ---------------------------------------------------------------------------

def compute_metrics(db: Session, dataset_dir: Path | None, store_id: str) -> dict[str, Any]:
    store_id = resolve_store_id(store_id)
    all_ev = customer_events(_filter_store(db.query(StoredEvent).all(), store_id))
    visitors = count_visitors(all_ev)

    purchases = _count_purchases(all_ev, dataset_dir, store_id)
    conversion = (purchases / visitors * 100) if visitors else 0.0

    # Store-wide average dwell
    dwells = [e.dwell_ms for e in all_ev if e.event_type == "ZONE_DWELL" and e.dwell_ms]
    avg_dwell = (sum(dwells) / len(dwells) / 1000) if dwells else 0.0

    # Per-zone average dwell (dict[zone, avg_seconds])
    zone_dwell_sums: dict[str, list[float]] = defaultdict(list)
    for e in all_ev:
        if e.event_type == "ZONE_DWELL" and e.dwell_ms and e.zone:
            zone_dwell_sums[e.zone].append(e.dwell_ms / 1000)
    avg_dwell_per_zone = {
        z: round(sum(vals) / len(vals), 1)
        for z, vals in zone_dwell_sums.items()
    }

    queue_events = [e for e in all_ev if e.event_type == "BILLING_QUEUE_JOIN"]
    queue_depth = max((_queue_depth(e) for e in queue_events), default=0)

    joins = len(queue_events)
    abandons = len([e for e in all_ev if e.event_type == "BILLING_QUEUE_ABANDON"])
    abandonment_rate = (abandons / joins * 100) if joins else 0.0

    # Staffing Efficiency: visitors per staff member (assuming at least 1 staff if occupancy > 0)
    staff_count = max(1, queue_depth // 3 + (1 if visitors > 10 else 0))
    visitors_per_staff = round(visitors / staff_count, 1) if staff_count else 0

    return {
        "store_id": store_id,
        "visitors": visitors,
        "conversion_rate": round(conversion, 1),
        "avg_dwell_seconds": round(avg_dwell, 1),
        "avg_dwell_per_zone": avg_dwell_per_zone,
        "queue_depth": queue_depth,
        "abandonment_rate": round(abandonment_rate, 1),
        "visitors_per_staff": visitors_per_staff,
        "revenue_leakage_est": round(abandons * 450.0, 2), # Assuming avg basket value 450
    }


# ---------------------------------------------------------------------------
# /stores/{id}/funnel
# ---------------------------------------------------------------------------

def compute_funnel(db: Session, dataset_dir: Path | None, store_id: str) -> dict[str, Any]:
    """Session is the unit — re-entries do NOT double-count a visitor."""
    store_id = resolve_store_id(store_id)
    events = customer_events(_filter_store(db.query(StoredEvent).all(), store_id))

    # Unique visitor sets per funnel stage
    entry_vis = {e.visitor_id for e in events
                 if e.event_type in ("ENTRY", "REENTRY") and e.visitor_id}
    zone_vis = {e.visitor_id for e in events
                if e.event_type == "ZONE_ENTER" and e.visitor_id}
    queue_vis = {e.visitor_id for e in events
                 if e.event_type == "BILLING_QUEUE_JOIN" and e.visitor_id}
    purchase = _count_purchases(events, dataset_dir, store_id)

    entry = len(entry_vis)
    zone = len(zone_vis & entry_vis)   # only visitors who actually entered
    queue = len(queue_vis & entry_vis)

    counts = [("Entry", entry), ("Zone Visit", zone), ("Billing Queue", queue), ("Purchase", purchase)]
    stages = []
    prev = entry or 1
    for name, count in counts:
        drop = round((1 - count / prev) * 100, 1) if prev else 0.0
        stages.append({"stage": name, "count": count, "drop_off_pct": max(0.0, drop)})
        prev = count or prev
    return {"store_id": store_id, "stages": stages}


# ---------------------------------------------------------------------------
# /stores/{id}/heatmap
# ---------------------------------------------------------------------------

def compute_heatmap(db: Session, store_id: str) -> dict[str, Any]:
    """Zone visit frequency + avg dwell, normalised 0–100.
    data_confidence = LOW when fewer than 20 sessions in window.
    """
    store_id = resolve_store_id(store_id)
    events = customer_events(_filter_store(db.query(StoredEvent).all(), store_id))
    sessions = count_visitors(events)

    heat: dict[str, dict[str, Any]] = {}
    for e in events:
        z = e.zone
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

    max_v = max(v["visits"] for v in heat.values()) or 1
    zones_out = {}
    for z, v in heat.items():
        avg_dwell = (v["dwell_sum_ms"] / v["dwell_n"] / 1000) if v["dwell_n"] else 0.0
        zones_out[z] = {
            "score": int(100 * v["visits"] / max_v),
            "visits": v["visits"],
            "avg_dwell_seconds": round(avg_dwell, 1),
        }

    confidence = "HIGH" if sessions >= 20 else "LOW"
    return {"store_id": store_id, "zones": zones_out, "data_confidence": confidence}


# ---------------------------------------------------------------------------
# /stores/{id}/anomalies
# ---------------------------------------------------------------------------

def compute_anomalies(db: Session, store_id: str) -> list[dict[str, str]]:
    """Detect operational anomalies.

    Types: BILLING_QUEUE_SPIKE, CONVERSION_DROP, DEAD_ZONE
    Severity: INFO / WARN / CRITICAL
    """
    store_id = resolve_store_id(store_id)
    events = customer_events(_filter_store(db.query(StoredEvent).all(), store_id))
    anomalies: list[dict[str, str]] = []
    now = datetime.utcnow()

    # 1. BILLING_QUEUE_SPIKE ------------------------------------------------
    queue_depths = [_queue_depth(e) for e in events
                    if e.event_type == "BILLING_QUEUE_JOIN"]
    if queue_depths:
        max_q = max(queue_depths)
        if max_q >= QUEUE_CRITICAL_THRESHOLD:
            anomalies.append({
                "type": "BILLING_QUEUE_SPIKE",
                "severity": "CRITICAL",
                "message": f"Queue depth reached {max_q} — immediate action needed.",
                "suggested_action": (
                    "Open all available billing counters immediately; "
                    "deploy floor staff to queue to manage customer expectations."
                ),
            })
        elif max_q >= QUEUE_SPIKE_THRESHOLD:
            anomalies.append({
                "type": "BILLING_QUEUE_SPIKE",
                "severity": "WARN",
                "message": f"Queue depth reached {max_q}.",
                "suggested_action": (
                    "Open additional billing counter or redirect staff to billing area."
                ),
            })

    # 2. CONVERSION_DROP ----------------------------------------------------
    visitors = count_visitors(events)
    purchases = _count_purchases(events, None, store_id)
    if visitors >= 5:
        conversion = purchases / visitors * 100
        if purchases == 0:
            anomalies.append({
                "type": "CONVERSION_DROP",
                "severity": "CRITICAL",
                "message": f"{visitors} visitors detected but zero purchases correlated.",
                "suggested_action": (
                    "Check billing camera feed and POS sync; "
                    "verify billing zone polygon in config/store_layout.json."
                ),
            })
        elif conversion < 15.0:
            anomalies.append({
                "type": "CONVERSION_DROP",
                "severity": "WARN",
                "message": f"Conversion rate is {round(conversion, 1)}% — below 15% threshold.",
                "suggested_action": (
                    "Review product placement in high-traffic zones; "
                    "consider floor staff engagement to improve conversion."
                ),
            })

    # 3. DEAD_ZONE ----------------------------------------------------------
    # No zone visits in the last DEAD_ZONE_WINDOW_MIN minutes (even with traffic)
    window_start = now - timedelta(minutes=DEAD_ZONE_WINDOW_MIN)
    zone_events_in_window = [
        e for e in events
        if e.event_type == "ZONE_ENTER" and _parse_ts(e.timestamp) >= window_start
    ]
    entry_events_in_window = [
        e for e in events
        if e.event_type in ("ENTRY", "REENTRY") and _parse_ts(e.timestamp) >= window_start
    ]

    # Only flag DEAD_ZONE if there were visitors in the window but no zone traffic
    if entry_events_in_window and not zone_events_in_window:
        anomalies.append({
            "type": "DEAD_ZONE",
            "severity": "WARN",
            "message": (
                f"No zone visits detected in the last {DEAD_ZONE_WINDOW_MIN} minutes "
                "despite active entry traffic."
            ),
            "suggested_action": (
                "Recalibrate floor zone polygons in config/store_layout.json. "
                "Check floor camera feed is active."
            ),
        })

    # 4. HIGH ABANDONMENT RATE ----------------------------------------------
    joins = sum(1 for e in events if e.event_type == "BILLING_QUEUE_JOIN")
    abandons = sum(1 for e in events if e.event_type == "BILLING_QUEUE_ABANDON")
    if joins >= 5:
        abandon_rate = abandons / joins * 100
        if abandon_rate >= 40.0:
            anomalies.append({
                "type": "HIGH_ABANDONMENT",
                "severity": "WARN",
                "message": f"Queue abandonment rate is {round(abandon_rate, 1)}%.",
                "suggested_action": (
                    "Reduce queue wait time by opening additional counters or "
                    "introducing queue management system."
                ),
            })

    return anomalies


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

def last_event_time(db: Session, store_id: str) -> datetime | None:
    store_id = resolve_store_id(store_id)
    events = _filter_store(db.query(StoredEvent).all(), store_id)
    if not events:
        return None
    return max(_parse_ts(e.timestamp) for e in events)


def compute_health(db: Session) -> dict[str, Any]:
    """Per-store health with STALE_FEED if last event > 10 minutes ago."""
    store_ids = {
        resolve_store_id(e.store_id)
        for e in db.query(StoredEvent).all()
        if e.store_id
    }
    if not store_ids:
        store_ids = {resolve_store_id("ST1008")}

    now = datetime.utcnow()
    status = "healthy"
    stores: dict[str, Any] = {}

    for sid in sorted(store_ids):
        last = last_event_time(db, sid)
        warnings: list[str] = []
        if last is None:
            warnings.append("NO_EVENTS")
        elif (now - last) > timedelta(minutes=STALE_FEED_MINUTES):
            warnings.append("STALE_FEED")
            status = "degraded"
        stores[sid] = {
            "last_event_at": last.isoformat() + "Z" if last else None,
            "warnings": warnings,
        }

    return {"status": status, "stores": stores}


# ---------------------------------------------------------------------------
# POS correlation helpers
# ---------------------------------------------------------------------------

def _load_pos(dataset_dir: Path | None, store_id: str) -> list[tuple[datetime, str]]:
    if not dataset_dir:
        return []
    rows = []
    for ts, txn_id, sid, _basket in load_pos_transactions(dataset_dir):
        if store_id_matches(sid, store_id):
            t = ts.replace(tzinfo=None) if ts.tzinfo else ts
            rows.append((t, txn_id))
    return rows


def _count_purchases(
    events: list[StoredEvent], dataset_dir: Path | None, store_id: str
) -> int:
    """Count distinct visitors who purchased.

    Priority: explicit PURCHASE_CORRELATED events → POS time-window correlation.
    """
    explicit = {
        e.visitor_id for e in events
        if e.event_type == "PURCHASE_CORRELATED" and e.visitor_id
    }
    if explicit:
        return len(explicit)

    pos = _load_pos(dataset_dir, store_id)
    if not pos:
        return 0

    billing_visits: list[tuple[str, datetime]] = [
        (e.visitor_id, _parse_ts(e.timestamp))
        for e in events
        if e.event_type == "BILLING_QUEUE_JOIN" and e.visitor_id
    ]

    window = timedelta(milliseconds=POS_CORRELATION_WINDOW_MS)
    purchasers: set[str] = set()
    for txn_ts, _ in pos:
        for vid, visit_ts in billing_visits:
            if abs((txn_ts - visit_ts).total_seconds()) <= window.total_seconds():
                purchasers.add(vid)
    return len(purchasers)


def correlate_pos_purchases(db: Session, dataset_dir: Path, store_id: str | None = None) -> int:
    """Write PURCHASE_CORRELATED events to DB for billing visitors matched to POS."""
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
    existing_ids = {e.event_id for e in events}
    added = 0

    for txn_ts, txn_id in pos:
        for vid, visit_ts in billing:
            if abs((txn_ts - visit_ts).total_seconds()) <= window.total_seconds():
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
                existing_ids.add(eid)
                added += 1

    db.commit()
    return added
