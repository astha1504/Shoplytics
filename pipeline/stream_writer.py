"""Event writers — file append and/or API batch ingest."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

import httpx

from pipeline.events import Event

logger = logging.getLogger(__name__)


class CallbackEventWriter:
    def __init__(
        self,
        on_batch: Callable[[list[dict]], None],
        batch_size: int = 5,
        file_path: Path | None = None,
    ):
        self.on_batch = on_batch
        self.batch_size = batch_size
        self.file_path = file_path
        self._buffer: list[dict] = []
        self._seen: set[str] = set()
        self.total_emitted = 0

    def emit(self, event: Event) -> bool:
        if not event.validate() or event.event_id in self._seen:
            return False
        self._seen.add(event.event_id)
        payload = event.to_dict()
        self._buffer.append(payload)
        self.total_emitted += 1
        if self.file_path:
            with self.file_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload) + "\n")
        if len(self._buffer) >= self.batch_size:
            self.flush()
        return True

    def flush(self) -> int:
        if not self._buffer:
            return 0
        batch = self._buffer
        self._buffer = []
        self.on_batch(batch)
        return len(batch)


class ApiEventWriter(CallbackEventWriter):
    def __init__(self, api_url: str = "http://localhost:8000", batch_size: int = 5, file_path: Path | None = None):
        self.api_url = api_url.rstrip("/")
        super().__init__(on_batch=self._post, batch_size=batch_size, file_path=file_path)

    def _post(self, batch: list[dict]) -> None:
        try:
            r = httpx.post(f"{self.api_url}/events/ingest", json=batch, timeout=15.0)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("Ingest failed: %s", exc)
