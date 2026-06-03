"""Process video/webcam/RTSP with YOLO + ByteTrack and emit events."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable, Union

import cv2

from pipeline.billing import BillingQueueTracker
from pipeline.entry_exit import LineCrossingDetector
from pipeline.events import Event, EventWriter, utc_now_iso
from pipeline.reentry import ReentryMatcher
from pipeline.staff import StaffDetector
from pipeline.visitor import VisitorRegistry
from pipeline.zones import ZoneTracker

logger = logging.getLogger(__name__)
FrameCallback = Callable[[Any, dict[str, Any]], None]
Source = Union[Path, str]


class VideoProcessor:
    def __init__(
        self,
        layout: dict[str, Any],
        writer: Any,
        role: str,
        store_id: str,
        camera_id: str,
        fps_skip: int = 2,
    ):
        self.writer = writer
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
            self.line = LineCrossingDetector(tuple(el["start"]), tuple(el["end"]), el.get("in_direction", "down"))
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
        visitor_id=None,
        track_id=None,
        zone_id=None,
        dwell_ms=0,
        confidence=0.85,
        metadata=None,
        is_staff=False,
        position_x: float | None = None,
        position_y: float | None = None,
    ):
        meta = dict(metadata or {})
        if position_x is not None and position_y is not None:
            meta["position_x"] = round(position_x, 1)
            meta["position_y"] = round(position_y, 1)
        self.writer.emit(
            Event(
                event_type=event_type,
                timestamp=utc_now_iso(),
                store_id=self.store_id,
                camera_id=self.camera_id,
                visitor_id=visitor_id,
                zone_id=zone_id,
                dwell_ms=dwell_ms or 0,
                confidence=confidence,
                track_id=track_id,
                metadata=meta,
                is_staff=is_staff,
            )
        )

    @staticmethod
    def _annotate(frame, detections):
        out = frame.copy()
        if detections is None or len(detections) == 0:
            return out
        for i in range(len(detections)):
            x1, y1, x2, y2 = detections.xyxy[i].astype(int).tolist()
            tid = int(detections.tracker_id[i]) if detections.tracker_id is not None else -1
            conf = float(detections.confidence[i]) if detections.confidence is not None else 0.75
            color = (52, 211, 153) if conf >= 0.5 else (251, 191, 36)
            cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
            cv2.putText(out, f"#{tid} {conf:.2f}", (x1, max(y1 - 6, 14)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
        return out

    def _process_frame(self, frame, frame_idx, fps, timestamp_ms):
        import supervision as sv

        count = 0
        results = self._model(frame, classes=[0], verbose=False)[0]
        detections = sv.Detections.from_ultralytics(results)
        detections = self._tracker.update_with_detections(detections)

        queue_depth = 0
        if self.billing and len(detections) > 0 and detections.tracker_id is not None:
            queue_depth = sum(
                1
                for i in range(len(detections))
                if self.billing._in_poly(
                    (detections.xyxy[i][0] + detections.xyxy[i][2]) / 2,
                    (detections.xyxy[i][1] + detections.xyxy[i][3]) / 2,
                    self.billing.queue_polygon,
                )
            )

        if detections.tracker_id is not None:
            for i in range(len(detections)):
                tid = int(detections.tracker_id[i])
                if tid < 0:
                    continue
                x1, y1, x2, y2 = detections.xyxy[i].astype(int).tolist()
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
                                "REENTRY", visitor_id=vid, track_id=tid, confidence=conf,
                                is_staff=staff_flag, position_x=cx, position_y=cy,
                            )
                        else:
                            vid = self.visitors.assign(tid)
                            self._emit(
                                "ENTRY", visitor_id=vid, track_id=tid, confidence=conf,
                                is_staff=staff_flag, position_x=cx, position_y=cy,
                            )
                        count += 1
                    elif crossing == "EXIT":
                        vid = self.visitors.get(tid)
                        self.reentry.record_exit(tid, timestamp_ms, bbox, frame, vid)
                        self._emit(
                            "EXIT", visitor_id=vid, track_id=tid, confidence=conf,
                            is_staff=staff_flag, position_x=cx, position_y=cy,
                        )
                        count += 1

                if self.zones:
                    for zev in self.zones.update(tid, cx, cy, timestamp_ms):
                        self._emit(
                            zev["event_type"],
                            visitor_id=self.visitors.get(tid),
                            track_id=tid,
                            zone_id=zev.get("zone"),
                            dwell_ms=zev.get("dwell_ms") or 0,
                            confidence=conf,
                            is_staff=staff_flag,
                            position_x=cx,
                            position_y=cy,
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
                            position_x=cx,
                            position_y=cy,
                        )
                        count += 1

        persons = len(detections) if detections.tracker_id is not None else 0
        return count, self._annotate(frame, detections), persons

    def _run_loop(self, cap, *, max_frames=None, realtime=False, on_frame=None, stop_event=None):
        fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
        if fps <= 0:
            fps = 15.0
        frame_idx = processed = total = 0
        t0 = time.perf_counter()
        while True:
            if stop_event and stop_event.is_set():
                break
            ret, frame = cap.read()
            if not ret:
                break
            frame_idx += 1
            if frame_idx % self.fps_skip != 0:
                continue
            processed += 1
            if max_frames and processed > max_frames:
                break
            n, annotated, persons = self._process_frame(frame, frame_idx, fps, int((frame_idx / fps) * 1000))
            total += n
            if on_frame:
                elapsed = max(time.perf_counter() - t0, 0.001)
                on_frame(frame, {"frame_idx": frame_idx, "person_count": persons, "fps": processed / elapsed, "annotated": annotated})
            if realtime:
                target = (frame_idx / fps) / self.fps_skip
                sleep = target - (time.perf_counter() - t0)
                if sleep > 0:
                    time.sleep(sleep)
        cap.release()
        return total

    def process(
        self,
        source: Source,
        *,
        max_frames: int | None = None,
        realtime: bool = False,
        on_frame: FrameCallback | None = None,
        stop_event=None,
        is_url: bool = False,
    ) -> int:
        if not is_url and isinstance(source, Path) and not source.exists():
            logger.warning("Video not found: %s", source)
            return 0
        self._init_model()
        cap = cv2.VideoCapture(str(source))
        if not cap.isOpened():
            logger.error("Cannot open: %s", source)
            return 0
        return self._run_loop(cap, max_frames=max_frames, realtime=realtime, on_frame=on_frame, stop_event=stop_event)

    def process_webcam(self, device=0, **kwargs):
        import sys
        import os

        # Cloud environments (Render, Railway, etc.) have no camera hardware.
        # Fail fast with a clear message instead of a long OpenCV timeout.
        if os.environ.get("RENDER") or os.environ.get("RAILWAY_ENVIRONMENT"):
            raise RuntimeError(
                "Webcam not available: this server runs in a cloud environment "
                "with no physical camera. Upload a video file instead."
            )

        self._init_model()
        if sys.platform.startswith("win"):
            cap = cv2.VideoCapture(device, cv2.CAP_DSHOW)
        else:
            cap = cv2.VideoCapture(device)

        if not cap.isOpened():
            cap = cv2.VideoCapture(device)

        if not cap.isOpened():
            raise RuntimeError(
                f"Hardware camera busy or not found (Device ID: {device}). "
                "Try uploading a video file instead."
            )

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        return self._run_loop(cap, **kwargs)
