"""Export store analytics as CSV, JSON, or HTML."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from backend import analytics, models, timeline
from pipeline.dataset_loader import resolve_store_id, store_id_matches

DATASET = Path(__file__).resolve().parent.parent / "dataset"


def export_json(db: Session, store_id: str) -> dict:
    sid = resolve_store_id(store_id)
    metrics = analytics.compute_metrics(db, DATASET, store_id)
    funnel = analytics.compute_funnel(db, DATASET, store_id)
    heatmap = analytics.compute_heatmap(db, store_id)
    anomalies = analytics.compute_anomalies(db, store_id)
    events = [e for e in db.query(models.StoredEvent).all() if store_id_matches(e.store_id, sid)]
    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "store_id": store_id,
        "metrics": metrics,
        "funnel": funnel,
        "heatmap": heatmap,
        "anomalies": anomalies,
        "occupancy_trend": timeline.get_occupancy_trend(store_id),
        "events_count": len(events),
        "events_sample": [
            {
                "event_id": e.event_id,
                "event_type": e.event_type,
                "timestamp": e.timestamp,
                "visitor_id": e.visitor_id,
                "zone": e.zone,
            }
            for e in events[:200]
        ],
    }


def export_csv(db: Session, store_id: str) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["timestamp", "occupancy", "queue_depth", "conversion_rate"])
    for row in timeline.get_occupancy_trend(store_id):
        w.writerow([row["timestamp"], row["occupancy"], row["queue_depth"], row.get("conversion_rate", 0)])
    if not timeline.get_occupancy_trend(store_id):
        m = analytics.compute_metrics(db, DATASET, store_id)
        w.writerow([datetime.now(timezone.utc).isoformat(), m["visitors"], m["queue_depth"], m["conversion_rate"]])
    return buf.getvalue()


def export_html(db: Session, store_id: str) -> str:
    m = analytics.compute_metrics(db, DATASET, store_id)
    anomalies = analytics.compute_anomalies(db, store_id)
    vibe = timeline.vibe_label(m["visitors"])
    anom_rows = "".join(
        f"<tr><td>{a['type']}</td><td>{a['severity']}</td><td>{a['message']}</td></tr>"
        for a in anomalies
    ) or "<tr><td colspan=3>No active anomalies</td></tr>"
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Store Report — {store_id}</title>
<style>body{{font-family:sans-serif;background:#0f172a;color:#e2e8f0;padding:2rem}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #334155;padding:8px}}</style></head>
<body>
<h1>Apex Store Intelligence Report</h1>
<p>Store: <strong>{store_id}</strong> · Generated: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}</p>
<h2>Metrics</h2>
<ul>
<li>Visitors: {m['visitors']}</li>
<li>Conversion: {m['conversion_rate']}%</li>
<li>Queue depth: {m['queue_depth']}</li>
<li>Store vibe: {vibe}</li>
<li>Abandonment: {m['abandonment_rate']}%</li>
</ul>
<h2>Active Anomalies</h2>
<table><tr><th>Type</th><th>Severity</th><th>Message</th></tr>{anom_rows}</table>
</body></html>"""
