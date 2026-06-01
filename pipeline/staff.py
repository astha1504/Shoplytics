"""Rule-based staff detection — no ML training."""

from pipeline.config import STAFF_PRESENCE_MS


class StaffDetector:
    """
    Staff heuristic: track present >15 minutes OR visits many zones repeatedly.
    Customers: typical entry → browse → exit pattern with shorter presence.
    """

    def __init__(self, threshold_ms: int = STAFF_PRESENCE_MS):
        self.threshold_ms = threshold_ms
        self._first_seen: dict[int, int] = {}
        self._last_seen: dict[int, int] = {}
        self._zone_hits: dict[int, set[str]] = {}
        self._staff_tracks: set[int] = set()

    def update(self, track_id: int, timestamp_ms: int, zone: str | None = None) -> bool:
        if track_id not in self._first_seen:
            self._first_seen[track_id] = timestamp_ms
        self._last_seen[track_id] = timestamp_ms
        if zone:
            self._zone_hits.setdefault(track_id, set()).add(zone)

        presence = timestamp_ms - self._first_seen[track_id]
        many_zones = len(self._zone_hits.get(track_id, set())) >= 3

        if presence >= self.threshold_ms or (presence >= 600_000 and many_zones):
            self._staff_tracks.add(track_id)
            return True
        return track_id in self._staff_tracks

    def is_staff(self, track_id: int) -> bool:
        return track_id in self._staff_tracks
