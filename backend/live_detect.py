"""Background YOLOv8 detection — webcam, file, or RTSP → events + preview."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import cv2

from backend import timeline
from pipeline.config import load_layout
from pipeline.processor import VideoProcessor
from pipeline.stream_writer import CallbackEventWriter

logger = logging.getLogger(__name__)
UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)


@dataclass
class DetectStatus:
    running: bool = False
    source: str = ""
    role: str = "floor"
    camera_id: str = "CAM_FLOOR_01"
    store_id: str = "ST1008"
    frames_processed: int = 0
    events_emitted: int = 0
    persons_tracked: int = 0
    fps: float = 0.0
    error: str | None = None
    last_event_types: list[str] = field(default_factory=list)


class DetectionManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.status = DetectStatus()
        self._latest_jpeg: bytes | None = None
        self._ingest_fn: Callable[[list[dict]], None] | None = None

    def set_ingest_handler(self, handler: Callable[[list[dict]], None]) -> None:
        self._ingest_fn = handler

    def get_frame_jpeg(self) -> bytes | None:
        with self._lock:
            return self._latest_jpeg

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        was = self.status.running
        self.status = DetectStatus() # FULL RESET
        with self._lock:
            self._latest_jpeg = None
        return {"stopped": was}

    def start(
        self,
        *,
        source_type: str,
        source_path: str | None = None,
        webcam_index: int = 0,
        role: str = "floor",
        camera_id: str = "CAM_FLOOR_01",
        store_id: str = "ST1008",
        realtime: bool = True,
        max_frames: int | None = None,
        fps_skip: int = 2,
    ) -> dict[str, Any]:
        # Force stop if somehow stuck
        if self.status.running:
            self.stop()
            time.sleep(0.5)

        self._stop.clear()
        self.status = DetectStatus(
            running=True,
            source=source_path or f"webcam:{webcam_index}",
            role=role,
            camera_id=camera_id,
            store_id=store_id,
        )
        with self._lock:
            self._latest_jpeg = None

        def _run() -> None:
            try:
                self._run_job(source_type, source_path, webcam_index, role, camera_id, store_id, realtime, max_frames, fps_skip)
            except Exception as exc:
                logger.exception("Detection error")
                self.status.error = str(exc)
            finally:
                self.status.running = False

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        return {"ok": True, "source": self.status.source}

    def _ingest(self, batch: list[dict]) -> None:
        if self._ingest_fn:
            self._ingest_fn(batch)
        timeline.mark_inference()
        types = [e.get("event_type", "?") for e in batch]
        self.status.last_event_types = (self.status.last_event_types + types)[-20:]
        self.status.events_emitted += len(batch)

    def _run_job(self, source_type, source_path, webcam_index, role, camera_id, store_id, realtime, max_frames, fps_skip):
        layout = load_layout(Path("dataset"))
        writer = CallbackEventWriter(on_batch=self._ingest, batch_size=5)
        proc = VideoProcessor(layout, writer, role, store_id, camera_id, fps_skip)

        def on_frame(_frame, meta: dict) -> None:
            self.status.frames_processed = meta.get("frame_idx", 0)
            self.status.persons_tracked = meta.get("person_count", 0)
            self.status.fps = meta.get("fps", 0.0)
            frame = meta.get("annotated")
            if frame is not None:
                ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 72])
                if ok:
                    with self._lock:
                        self._latest_jpeg = buf.tobytes()

        if source_type == "webcam":
            proc.process_webcam(webcam_index, max_frames=max_frames, realtime=realtime, on_frame=on_frame, stop_event=self._stop)
        elif source_type in ("file", "rtsp") and source_path:
            proc.process(Path(source_path) if source_type == "file" else source_path, max_frames=max_frames, realtime=realtime, on_frame=on_frame, stop_event=self._stop, is_url=source_type == "rtsp")
        else:
            raise ValueError(f"Bad source: {source_type}")
        writer.flush()


_manager: DetectionManager | None = None


def get_detection_manager() -> DetectionManager:
    global _manager
    if _manager is None:
        _manager = DetectionManager()
    return _manager
