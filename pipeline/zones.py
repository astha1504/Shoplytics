"""Zone enter / exit / dwell using polygon zones."""

from __future__ import annotations

from pipeline.config import DWELL_THRESHOLD_MS


class ZoneTracker:
    def __init__(self, zones: dict[str, list[list[int]]], dwell_ms: int = DWELL_THRESHOLD_MS):
        self.zones = zones
        self.dwell_ms = dwell_ms
        self._inside: dict[int, set[str]] = {}
        self._enter_time: dict[tuple[int, str], int] = {}
        self._last_dwell_emit: dict[tuple[int, str], int] = {}

    @staticmethod
    def point_in_polygon(px: float, py: float, polygon: list[list[int]]) -> bool:
        n = len(polygon)
        inside = False
        j = n - 1
        for i in range(n):
            xi, yi = polygon[i]
            xj, yj = polygon[j]
            if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi + 1e-9) + xi):
                inside = not inside
            j = i
        return inside

    def update(self, track_id: int, cx: float, cy: float, timestamp_ms: int) -> list[dict]:
        emitted: list[dict] = []
        current = set()
        for name, poly in self.zones.items():
            if self.point_in_polygon(cx, cy, poly):
                current.add(name)

        prev = self._inside.get(track_id, set())
        for z in current - prev:
            self._enter_time[(track_id, z)] = timestamp_ms
            emitted.append({"event_type": "ZONE_ENTER", "zone": z})

        for z in prev - current:
            key = (track_id, z)
            self._enter_time.pop(key, None)
            self._last_dwell_emit.pop(key, None)
            emitted.append({"event_type": "ZONE_EXIT", "zone": z})

        for z in current:
            key = (track_id, z)
            enter_t = self._enter_time.get(key, timestamp_ms)
            dwell = timestamp_ms - enter_t
            last_emit = self._last_dwell_emit.get(key, 0)
            if dwell >= self.dwell_ms and (timestamp_ms - last_emit) >= self.dwell_ms:
                self._last_dwell_emit[key] = timestamp_ms
                emitted.append(
                    {"event_type": "ZONE_DWELL", "zone": z, "dwell_ms": int(dwell)}
                )

        self._inside[track_id] = current
        return emitted
