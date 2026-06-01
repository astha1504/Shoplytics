"""Billing queue join / abandon detection."""

from __future__ import annotations


class BillingQueueTracker:
    def __init__(self, queue_polygon: list[list[int]]):
        self.queue_polygon = queue_polygon
        self._in_queue: dict[int, int] = {}
        self._had_purchase: set[int] = set()
        self._visitor_by_track: dict[int, str] = {}

    def set_visitor(self, track_id: int, visitor_id: str) -> None:
        self._visitor_by_track[track_id] = visitor_id

    @staticmethod
    def _in_poly(cx: float, cy: float, polygon: list[list[int]]) -> bool:
        from pipeline.zones import ZoneTracker

        return ZoneTracker.point_in_polygon(cx, cy, polygon)

    def update(
        self,
        track_id: int,
        cx: float,
        cy: float,
        timestamp_ms: int,
        queue_count: int,
    ) -> list[dict]:
        emitted: list[dict] = []
        inside = self._in_poly(cx, cy, self.queue_polygon)
        was_inside = track_id in self._in_queue

        if inside and not was_inside:
            self._in_queue[track_id] = timestamp_ms
            emitted.append(
                {
                    "event_type": "BILLING_QUEUE_JOIN",
                    "queue_depth": queue_count,
                }
            )
        elif not inside and was_inside:
            join_time = self._in_queue.pop(track_id)
            if track_id not in self._had_purchase and (timestamp_ms - join_time) > 5000:
                emitted.append({"event_type": "BILLING_QUEUE_ABANDON"})
        return emitted

    def mark_purchase(self, track_id: int) -> None:
        self._had_purchase.add(track_id)
