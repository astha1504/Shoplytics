"""Visitor ID assignment on ENTRY / REENTRY."""

from __future__ import annotations


class VisitorRegistry:
    def __init__(self):
        self._counter = 0
        self._track_to_visitor: dict[int, str] = {}

    def next_id(self) -> str:
        self._counter += 1
        return f"VIS_{self._counter:03d}"

    def assign(self, track_id: int, visitor_id: str | None = None) -> str:
        if visitor_id:
            vid = visitor_id
        else:
            vid = self.next_id()
        self._track_to_visitor[track_id] = vid
        return vid

    def get(self, track_id: int) -> str | None:
        return self._track_to_visitor.get(track_id)

    def clear_track(self, track_id: int) -> str | None:
        return self._track_to_visitor.pop(track_id, None)
