"""Live-stream events to the API — demonstrates real-time dashboard updating.

Usage:
  1. Start the API: uvicorn backend.main:app --port 8000
  2. Open the dashboard at http://localhost:5173 in Live Mode
  3. Run this script: python scripts/stream_events.py
  4. Watch the metrics update in real-time on the dashboard

Options:
  --file        Path to events.jsonl (default: events.jsonl)
  --api         API base URL (default: http://localhost:8000)
  --batch-size  Events per POST request (default: 5)
  --interval    Seconds between batches (default: 2.0)
  --clear       Clear the database before streaming (default: True)
  --no-clear    Skip the database clear step
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

try:
    import httpx
except ImportError:
    print("httpx not installed. Run: pip install httpx")
    sys.exit(1)


def stream_events(
    events_file: Path,
    api_url: str,
    batch_size: int,
    interval: float,
    clear_first: bool,
) -> None:
    if not events_file.exists():
        print(f"ERROR: Events file not found: {events_file}")
        print("Generate events first: python -m pipeline.run --synthetic --output events.jsonl")
        sys.exit(1)

    with events_file.open(encoding="utf-8") as f:
        all_events = [json.loads(line) for line in f if line.strip()]

    if not all_events:
        print("ERROR: events.jsonl is empty.")
        sys.exit(1)

    # Sort chronologically so the dashboard shows realistic progression
    all_events.sort(key=lambda e: e.get("timestamp", ""))
    print(f"Loaded {len(all_events)} events from {events_file}")

    client = httpx.Client(timeout=15.0)

    # Step 1: Clear DB so we can watch metrics build up from zero
    if clear_first:
        print("Clearing database for fresh live demo...")
        try:
            resp = client.post(f"{api_url}/admin/clear-db")
            resp.raise_for_status()
            print(f"  ✓ Cleared: {resp.json()}")
        except httpx.HTTPError as e:
            print(f"  ✗ Failed to clear DB: {e}")
            print("  Continuing anyway...")

    # Step 2: Stream events in batches
    print(f"\nStreaming {len(all_events)} events in batches of {batch_size} "
          f"every {interval}s to {api_url}/events/ingest")
    print("Open the dashboard at http://localhost:5173 and watch metrics update!\n")

    total_accepted = 0
    total_rejected = 0
    start_time = time.time()

    for batch_idx, start in enumerate(range(0, len(all_events), batch_size)):
        batch = all_events[start: start + batch_size]
        batch_num = batch_idx + 1
        total_batches = (len(all_events) + batch_size - 1) // batch_size

        try:
            resp = client.post(f"{api_url}/events/ingest", json=batch)

            if resp.status_code == 200:
                data = resp.json()
                total_accepted += data.get("accepted", 0)
                total_rejected += data.get("rejected", 0)
                elapsed = time.time() - start_time
                print(
                    f"  Batch {batch_num:3d}/{total_batches} | "
                    f"accepted={data['accepted']:3d} | "
                    f"dup={data['duplicates']:3d} | "
                    f"total_ingested={total_accepted:4d} | "
                    f"elapsed={elapsed:.0f}s",
                    end="\r",
                    flush=True,
                )
            else:
                print(f"\n  ERROR [{resp.status_code}]: {resp.text[:200]}")

        except httpx.ConnectError:
            print(f"\nERROR: Cannot connect to {api_url}. Is the API running?")
            print("Start it with: uvicorn backend.main:app --port 8000")
            sys.exit(1)
        except httpx.HTTPError as e:
            print(f"\n  HTTP error: {e}")

        if batch_idx < total_batches - 1:
            time.sleep(interval)

    elapsed = time.time() - start_time
    print(f"\n\n✓ Stream complete in {elapsed:.1f}s")
    print(f"  Total accepted: {total_accepted}")
    print(f"  Total rejected: {total_rejected}")
    print(f"\nCheck live metrics at: {api_url}/stores/STORE_BLR_002/metrics")


def main() -> None:
    parser = argparse.ArgumentParser(description="Stream CCTV events to the Store Intelligence API")
    parser.add_argument("--file", type=Path, default=Path("events.jsonl"))
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--clear", dest="clear", action="store_true", default=True)
    parser.add_argument("--no-clear", dest="clear", action="store_false")
    args = parser.parse_args()

    stream_events(
        events_file=args.file,
        api_url=args.api,
        batch_size=args.batch_size,
        interval=args.interval,
        clear_first=args.clear,
    )


if __name__ == "__main__":
    main()
