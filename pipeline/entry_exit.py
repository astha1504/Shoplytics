"""Virtual line crossing for ENTRY / EXIT."""

from __future__ import annotations

from enum import Enum


class Side(Enum):
    OUTSIDE = -1
    INSIDE = 1
    UNKNOWN = 0


class LineCrossingDetector:
    def __init__(self, start: tuple[int, int], end: tuple[int, int], in_direction: str = "down"):
        self.start = start
        self.end = end
        self.in_direction = in_direction
        self._last_side: dict[int, Side] = {}

    def _side_of_line(self, px: float, py: float) -> Side:
        x1, y1 = self.start
        x2, y2 = self.end
        cross = (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)
        if abs(cross) < 5:
            return Side.UNKNOWN
        if self.in_direction == "down":
            return Side.INSIDE if cross > 0 else Side.OUTSIDE
        return Side.INSIDE if cross < 0 else Side.OUTSIDE

    def update(self, track_id: int, cx: float, cy: float) -> str | None:
        side = self._side_of_line(cx, cy)
        if side == Side.UNKNOWN:
            return None
        prev = self._last_side.get(track_id)
        self._last_side[track_id] = side
        if prev is None or prev == side:
            return None
        if prev == Side.OUTSIDE and side == Side.INSIDE:
            return "ENTRY"
        if prev == Side.INSIDE and side == Side.OUTSIDE:
            return "EXIT"
        return None
