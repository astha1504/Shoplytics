"""CLI: process hackathon CCTV (CAM 1–5) → events.jsonl"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from pipeline.dataset_loader import (
    discover_hackathon_cameras,
    list_dataset_contents,
    load_layout,
    resolve_store_id,
)
from pipeline.events import EventWriter
from pipeline.synthetic import generate_sample_events

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def run_pipeline(
    dataset_dir: Path,
    store: str,
    output: Path,
    synthetic: bool = False,
    max_frames_per_cam: int | None = None,
) -> int:
    if output.exists():
        output.unlink()

    layout = load_layout(dataset_dir)
    store_id = resolve_store_id(layout.get("store_id", store))

    if synthetic:
        n = generate_sample_events(output, store_id)
        logger.info("Wrote %d synthetic events to %s", n, output)
        return n

    info = list_dataset_contents(dataset_dir)
    logger.info("Dataset scan: %s", info)

    cameras = discover_hackathon_cameras(dataset_dir)
    if not cameras:
        logger.warning(
            "No CAM *.mp4 under dataset/CCTV Footage/ — run with videos present or --synthetic"
        )
        return generate_sample_events(output, store_id)

    writer = EventWriter(output)
    total = 0
    from pipeline.processor import VideoProcessor

    for cam in cameras:
        proc = VideoProcessor(
            layout,
            writer,
            role=cam["role"],
            store_id=store_id,
            camera_id=cam["camera_id"],
        )
        if max_frames_per_cam:
            proc.fps_skip = max(1, max_frames_per_cam // 100)
        n = proc.process(cam["path"])
        logger.info(
            "%s (%s): %d events from %s",
            cam["cam_key"],
            cam["camera_id"],
            n,
            cam["path"].name,
        )
        total += n

    logger.info("Total events: %d → %s", total, output)
    return total


def main():
    parser = argparse.ArgumentParser(description="Apex Retail CCTV → event stream")
    parser.add_argument("--dataset", type=Path, default=Path("dataset"))
    parser.add_argument("--store", type=str, default="ST1008")
    parser.add_argument("--output", type=Path, default=Path("events.jsonl"))
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument(
        "--max-frames-per-cam",
        type=int,
        default=None,
        help="Dev shortcut: process fewer frames per camera",
    )
    args = parser.parse_args()
    run_pipeline(
        args.dataset,
        args.store,
        args.output,
        args.synthetic,
        args.max_frames_per_cam,
    )


if __name__ == "__main__":
    main()
