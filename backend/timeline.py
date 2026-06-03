"""In-memory occupancy / vibe timeline for live dashboard charts."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

SERVER_START = time.time()
LAST_INFERENCE: float | None = None
OCCUPANCY_CAP = 30
VIBE_CAP = 20

_history: dict[str, deque] = defaultdict(lambda: deque(maxlen=OCCUPANCY_CAP))
_vibe_history: dict[str, deque] = defaultdict(lambda: deque(maxlen=VIBE_CAP))
OCCUPANCY_THRESHOLD = 10


def vibe_from_occupancy(n: int) -> str:
    if n <= 3:
        return "cozy"
    if n <= 10:
        return "moderate"
    return "energetic"


def vibe_label(n: int) -> str:
    v = vibe_from_occupancy(n)
    return {
        "cozy": "Cozy & Calm",
        "moderate": "Moderate & Buzzing",
        "energetic": "Energetic & Crowded",
    }[v]


def record_metrics(store_id: str, visitors: int, queue_depth: int, conversion_rate: float) -> None:
    global LAST_INFERENCE
    LAST_INFERENCE = time.time()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    vibe = vibe_from_occupancy(visitors)
    _history[store_id].append(
        {"timestamp": ts, "occupancy": visitors, "queue_depth": queue_depth, "conversion_rate": conversion_rate}
    )
    _vibe_history[store_id].append({"timestamp": ts, "vibe": vibe, "occupancy": visitors})


def get_occupancy_trend(store_id: str) -> list[dict[str, Any]]:
    return list(_history.get(store_id, []))


def get_vibe_history(store_id: str) -> list[dict[str, Any]]:
    return list(_vibe_history.get(store_id, []))


def get_vibe_breakdown(store_id: str) -> dict[str, int]:
    counts = {"cozy": 0, "moderate": 0, "energetic": 0}
    for item in _vibe_history.get(store_id, []):
        counts[item["vibe"]] = counts.get(item["vibe"], 0) + 1
    total = sum(counts.values()) or 1
    return {k: round(v / total * 100) for k, v in counts.items()}


def uptime_seconds() -> int:
    return int(time.time() - SERVER_START)


def mark_inference() -> None:
    global LAST_INFERENCE
    LAST_INFERENCE = time.time()
