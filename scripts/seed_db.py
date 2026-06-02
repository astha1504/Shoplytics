"""Seed the database from events.jsonl on first startup.

If events.jsonl does not exist, synthetic events are generated first.
"""

import json
import sys
from pathlib import Path

# Ensure root is on sys.path when run as a standalone script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.database import SessionLocal, init_db
from backend.models import StoredEvent  # noqa: F401 — ensures table exists


def main() -> None:
    init_db()

    events_file = Path(__file__).resolve().parent.parent / "events.jsonl"

    # Generate synthetic events if no file present
    if not events_file.exists():
        from pipeline.run import run_pipeline
        run_pipeline(Path("dataset"), "ST1008", events_file, synthetic=True)

    with events_file.open(encoding="utf-8") as f:
        events = [json.loads(line) for line in f if line.strip()]

    if not events:
        print("events.jsonl is empty — nothing to seed.")
        return

    db = SessionLocal()
    try:
        # Import internal ingest (avoids Depends resolution)
        from backend.main import _ingest_batch
        result = _ingest_batch(events, db)
        print(
            f"Seeded: accepted={result.accepted} "
            f"duplicates={result.duplicates} "
            f"rejected={result.rejected}"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
