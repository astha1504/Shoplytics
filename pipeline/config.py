from pathlib import Path
from typing import Any

from pipeline.dataset_loader import load_layout as load_layout

DWELL_THRESHOLD_MS = 30_000
STAFF_PRESENCE_MS = 15 * 60 * 1000
REENTRY_WINDOW_MS = 30 * 60 * 1000
REENTRY_SIMILARITY_THRESHOLD = 0.55
POS_CORRELATION_WINDOW_MS = 5 * 60 * 1000

# Re-export for backward compatibility
__all__ = [
    "DWELL_THRESHOLD_MS",
    "STAFF_PRESENCE_MS",
    "REENTRY_WINDOW_MS",
    "REENTRY_SIMILARITY_THRESHOLD",
    "POS_CORRELATION_WINDOW_MS",
    "load_layout",
]
