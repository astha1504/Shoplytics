"""Process a single video with YOLO + ByteTrack and emit events."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from pipeline.billing import BillingQueueTracker
from pipeline.entry_exit import LineCrossingDetector
from pipeline.events import Event, EventWriter, utc_now_iso
from pipeline.reentry import ReentryMatcher
from pipeline.staff import StaffDetector
from pipeline.visitor import VisitorRegistry
from pipeline.zones import ZoneTracker

logger = logging.getLogger(__name__)


class VideoProcessor:
    def __init__(
        self,
        layout: dict[str, Any],
        writer: EventWriter,
        role: str,
        store_id: str,
        camera_id: str,
        fps_skip: int = 2,
    ):
        self.writer = writer
        self.camera = role
        self.camera_id = camera_id
        self.store_id = store_id
        self.fps_skip = fps_skip
        self.visitors = VisitorRegistry()
        self.staff = StaffDetector()
        self.reentry = ReentryMatcher()
        self.line: LineCrossingDetector | None = None
        self.zones: ZoneTracker | None = None
        self.billing: BillingQueueTracker | None = None

        if role == "entry":
            el = layout.get("entry_line", {})
            self.line = LineCrossingDetector(
                tuple(el["start"]),
                tuple(el["end"]),
                el.get("in_direction", "down"),
            )
        if role == "floor":
            self.zones = ZoneTracker(layout.get("zones", {}))
        if role == "billing":
            qz = layout.get("billing_queue_zone", [])
            if qz:
                self.billing = BillingQueueTracker(qz)

        self._model = None
        self._tracker = None

    def _init_model(self):
        if self._model is not None:
            return
        from ultralytics import YOLO
        import supervision as sv

        self._model = YOLO("yolov8n.pt")
        self._tracker = sv.ByteTrack()

    def _emit(
        self,
        event_type: str,
        visitor_id: str | None = None,
        track_id: int | None = None,
        zone_id: str | None = None,
        dwell_ms: int = 0,
        confidence: float = 0.85,
        metadata: dict | None = None,
        is_staff: bool = False,
    ) -> None:
        ev = Event(
            event_type=event_type,
            timestamp=utc_now_iso(),
            store_id=self.store_id,
            camera_id=self.camera_id,
            visitor_id=visitor_id,
            zone_id=zone_id,
            dwell_ms=dwell_ms or 0,
            confidence=confidence,
            track_id=track_id,
            metadata=metadata or {},
            is_staff=is_staff,
        )
        self.writer.emit(ev)

    def process(self, video_path: Path) -> int:
        if not video_path.exists():
            logger.warning("Video not found: %s", video_path)
            return 0

        self._init_model()
        import supervision as sv

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            logger.error("Cannot open video: %s", video_path)
            return 0

        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        frame_idx = 0
        count = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break
            frame_idx += 1
            if frame_idx % self.fps_skip != 0:
                continue

            timestamp_ms = int((frame_idx / fps) * 1000)
            results = self._model(frame, classes=[0], verbose=False)[0]
            detections = sv.Detections.from_ultralytics(results)
            detections = self._tracker.update_with_detections(detections)

            queue_tracks = []
            if self.billing and len(detections) > 0:
                for i in range(len(detections)):
                    xyxy = detections.xyxy[i]
                    cx = (xyxy[0] + xyxy[2]) / 2
                    cy = (xyxy[1] + xyxy[3]) / 2
                    if self.billing._in_poly(cx, cy, self.billing.queue_polygon):
                        queue_tracks.append(int(detections.tracker_id[i]))

            queue_depth = len(queue_tracks)

            if detections.tracker_id is None:
                continue

            for i in range(len(detections)):
                tid = int(detections.tracker_id[i])
                if tid < 0:
                    continue
                xyxy = detections.xyxy[i].astype(int)
                x1, y1, x2, y2 = xyxy.tolist()
                cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                bbox = (x1, y1, x2, y2)

                conf = float(detections.confidence[i]) if detections.confidence is not None else 0.75

                staff_flag = self.staff.update(tid, timestamp_ms)

                if self.line:
                    crossing = self.line.update(tid, cx, cy)
                    if crossing == "ENTRY":
                        match = self.reentry.match_reentry(timestamp_ms, bbox, frame)
                        if match and match.visitor_id:
                            vid = self.visitors.assign(tid, match.visitor_id)
                            self._emit(
                                "REENTRY", visitor_id=vid, track_id=tid, confidence=conf, is_staff=staff_flag
                            )
                            count += 1
                        else:
                            vid = self.visitors.assign(tid)
                            self._emit(
                                "ENTRY", visitor_id=vid, track_id=tid, confidence=conf, is_staff=staff_flag
                            )
                            count += 1
                    elif crossing == "EXIT":
                        vid = self.visitors.get(tid)
                        self.reentry.record_exit(tid, timestamp_ms, bbox, frame, vid)
                        self._emit("EXIT", visitor_id=vid, track_id=tid, confidence=conf, is_staff=staff_flag)
                        count += 1

                if self.zones:
                    for zev in self.zones.update(tid, cx, cy, timestamp_ms):
                        vid = self.visitors.get(tid)
                        self._emit(
                            zev["event_type"],
                            visitor_id=vid,
                            track_id=tid,
                            zone_id=zev.get("zone"),
                            dwell_ms=zev.get("dwell_ms") or 0,
                            confidence=conf,
                            is_staff=staff_flag,
                        )
                        count += 1

                if self.billing:
                    vid = self.visitors.get(tid)
                    if vid:
                        self.billing.set_visitor(tid, vid)
                    for bev in self.billing.update(tid, cx, cy, timestamp_ms, queue_depth):
                        self._emit(
                            bev["event_type"],
                            visitor_id=self.visitors.get(tid),
                            track_id=tid,
                            confidence=conf,
                            metadata={"queue_depth": bev.get("queue_depth")},
                            is_staff=staff_flag,
                        )
                        count += 1

        cap.release()
        return count
