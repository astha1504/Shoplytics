"""Re-entry detection via appearance matching (no extra ML models)."""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass

from pipeline.config import REENTRY_SIMILARITY_THRESHOLD, REENTRY_WINDOW_MS


@dataclass
class ExitSnapshot:
    track_id: int
    timestamp_ms: int
    height: float
    width: float
    color_hist: np.ndarray
    visitor_id: str | None = None


class ReentryMatcher:
    def __init__(
        self,
        window_ms: int = REENTRY_WINDOW_MS,
        threshold: float = REENTRY_SIMILARITY_THRESHOLD,
    ):
        self.window_ms = window_ms
        self.threshold = threshold
        self._recent_exits: list[ExitSnapshot] = []

    @staticmethod
    def color_histogram(frame: np.ndarray, bbox: tuple[int, int, int, int]) -> np.ndarray:
        x1, y1, x2, y2 = bbox
        crop = frame[max(0, y1) : y2, max(0, x1) : x2]
        if crop.size == 0:
            return np.zeros(48, dtype=np.float32)
        hsv = crop
        if len(crop.shape) == 3 and crop.shape[2] == 3:
            import cv2

            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        hist_h = np.histogram(hsv[:, :, 0], bins=16, range=(0, 180))[0]
        hist_s = np.histogram(hsv[:, :, 1], bins=16, range=(0, 256))[0]
        hist_v = np.histogram(hsv[:, :, 2], bins=16, range=(0, 256))[0]
        hist = np.concatenate([hist_h, hist_s, hist_v]).astype(np.float32)
        norm = np.linalg.norm(hist)
        return hist / norm if norm > 0 else hist

    @staticmethod
    def similarity(a: ExitSnapshot, height: float, width: float, hist: np.ndarray) -> float:
        h_sim = 1 - min(abs(a.height - height) / max(a.height, height, 1), 1.0)
        w_sim = 1 - min(abs(a.width - width) / max(a.width, width, 1), 1.0)
        hist_sim = float(np.dot(a.color_hist, hist) / (np.linalg.norm(a.color_hist) * np.linalg.norm(hist) + 1e-6))
        return 0.3 * h_sim + 0.2 * w_sim + 0.5 * max(hist_sim, 0)

    def record_exit(
        self,
        track_id: int,
        timestamp_ms: int,
        bbox: tuple[int, int, int, int],
        frame: np.ndarray,
        visitor_id: str | None,
    ) -> None:
        x1, y1, x2, y2 = bbox
        h, w = y2 - y1, x2 - x1
        hist = self.color_histogram(frame, bbox)
        self._recent_exits.append(
            ExitSnapshot(track_id, timestamp_ms, float(h), float(w), hist, visitor_id)
        )
        cutoff = timestamp_ms - self.window_ms
        self._recent_exits = [e for e in self._recent_exits if e.timestamp_ms >= cutoff]

    def match_reentry(
        self,
        timestamp_ms: int,
        bbox: tuple[int, int, int, int],
        frame: np.ndarray,
    ) -> ExitSnapshot | None:
        x1, y1, x2, y2 = bbox
        h, w = y2 - y1, x2 - x1
        hist = self.color_histogram(frame, bbox)
        cutoff = timestamp_ms - self.window_ms
        best: ExitSnapshot | None = None
        best_score = 0.0
        for snap in self._recent_exits:
            if snap.timestamp_ms < cutoff:
                continue
            score = self.similarity(snap, float(h), float(w), hist)
            if score > best_score and score >= self.threshold:
                best_score = score
                best = snap
        return best
