"""Seed database from events.jsonl on startup."""

import json
from pathlib import Path

from backend.database import SessionLocal, init_db
from backend.main import ingest_events

EVENTS_FILE = Path(__file__).resolve().parent.parent / "events.jsonl"


def main():
    init_db()
    if not EVENTS_FILE.exists():
        from pipeline.run import run_pipeline

        run_pipeline(
            Path("dataset"),
            "store1",
            EVENTS_FILE,
            synthetic=True,
        )
    db = SessionLocal()
    events = []
    with EVENTS_FILE.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))
    if events:
        ingest_events(events, db)
    db.close()


if __name__ == "__main__":
    main()
