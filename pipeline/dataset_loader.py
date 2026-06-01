"""
Read-only hackathon dataset (do not modify files under dataset/).

Expected layout (Apex / Brigade bundle):
  dataset/
    CCTV Footage/CAM 1.mp4 … CAM 5.mp4
    Brigade_*_Store_layout*.xlsx
    Brigade_*pos*.csv  OR  pos_transactions.csv
"""

from __future__ import annotations

import csv
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = PROJECT_ROOT / "dataset"
CAM_RE = re.compile(r"cam\s*(\d+)", re.I)


def dataset_dir(path: Path | None = None) -> Path:
    return path or DEFAULT_DATASET


def load_camera_map() -> dict[str, Any]:
    path = PROJECT_ROOT / "config" / "camera_map.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def resolve_store_id(requested: str | None = None) -> str:
    cmap = load_camera_map()
    default = cmap.get("store_id", "ST1008")
    if not requested:
        return default
    aliases = set(cmap.get("store_aliases", []) + [default])
    if requested in aliases:
        return default
    return requested


def store_id_matches(event_store: str | None, requested: str) -> bool:
    if not event_store:
        return True
    canonical = resolve_store_id(requested)
    return event_store == requested or event_store == canonical or resolve_store_id(event_store) == canonical


def load_layout(data_dir: Path | None = None) -> dict[str, Any]:
    data_dir = dataset_dir(data_dir)
    cache = PROJECT_ROOT / "config" / "store_layout.json"

    xlsx = find_layout_xlsx(data_dir)
    if xlsx:
        from pipeline.layout_xlsx import cache_layout_json, parse_layout_xlsx

        cmap = load_camera_map()
        layout = parse_layout_xlsx(xlsx, store_id=cmap.get("store_id", "ST1008"))
        cache_layout_json(layout, cache)
        return layout

    if cache.is_file():
        with cache.open(encoding="utf-8") as f:
            return json.load(f)

    if (data_dir / "store_layout.json").is_file():
        with (data_dir / "store_layout.json").open(encoding="utf-8") as f:
            return json.load(f)

    raise FileNotFoundError(
        "No layout found. Add Brigade layout .xlsx under dataset/ or config/store_layout.json"
    )


def find_layout_xlsx(data_dir: Path) -> Path | None:
    from pipeline.layout_xlsx import find_layout_xlsx as _find

    return _find(data_dir)


def _parse_brigade_timestamp(order_date: str, order_time: str) -> datetime | None:
    try:
        d = datetime.strptime(order_date.strip(), "%d-%m-%Y")
        parts = order_time.strip().split(":")
        h, m, s = int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0
        return d.replace(hour=h, minute=m, second=s, tzinfo=timezone.utc)
    except (ValueError, IndexError):
        return None


def _parse_iso_timestamp(ts: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def load_pos_transactions(
    data_dir: Path | None = None,
) -> list[tuple[datetime, str, str, float | None]]:
    """Returns (timestamp UTC, transaction_id, store_id, basket_value_inr)."""
    data_dir = dataset_dir(data_dir)
    rows: list[tuple[datetime, str, str, float | None]] = []
    seen: set[str] = set()

    for csv_path in sorted(data_dir.glob("*.csv")):
        with csv_path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fields = {x.lower(): x for x in (reader.fieldnames or [])}

            if "transaction_id" in fields and "timestamp" in fields:
                logger.info("Reading standard POS: %s", csv_path.name)
                for row in reader:
                    tid = row[fields["transaction_id"]].strip()
                    if not tid or tid in seen:
                        continue
                    ts = _parse_iso_timestamp(row[fields["timestamp"]])
                    if not ts:
                        continue
                    seen.add(tid)
                    store = row.get(fields.get("store_id", "store_id"), "STORE_BLR_002")
                    basket = row.get(fields.get("basket_value_inr", "basket_value_inr"))
                    val = float(basket) if basket else None
                    rows.append((ts, tid, str(store).strip(), val))
                continue

            if "order_id" not in fields:
                continue

            logger.info("Reading Brigade POS: %s", csv_path.name)
            for row in reader:
                oid = str(row.get("order_id", "")).strip()
                if not oid or oid in seen:
                    continue
                ts = _parse_brigade_timestamp(
                    row.get("order_date", ""),
                    row.get("order_time", ""),
                )
                if not ts:
                    continue
                seen.add(oid)
                store_id = str(row.get("store_id", "ST1008")).strip()
                basket = row.get("total_amount") or row.get("NMV")
                try:
                    val = float(basket) if basket else None
                except (TypeError, ValueError):
                    val = None
                rows.append((ts, oid, store_id, val))

    rows.sort(key=lambda r: r[0])
    logger.info("Loaded %d POS transactions", len(rows))
    return rows


def _normalize_cam_key(stem: str) -> str:
    stem = stem.strip()
    m = CAM_RE.match(stem)
    if m:
        return f"CAM {int(m.group(1))}"
    return stem


def discover_hackathon_cameras(data_dir: Path | None = None) -> list[dict[str, Any]]:
    """
    Discover CAM 1..5 under dataset/CCTV Footage/ (or anywhere under dataset/).
    Returns list of {path, role, camera_id, cam_key}.
    """
    data_dir = dataset_dir(data_dir)
    cmap = load_camera_map()
    cam_defs = cmap.get("cameras", {})
    discovered: list[dict[str, Any]] = []

    for mp4 in sorted(data_dir.rglob("*.mp4")):
        key = _normalize_cam_key(mp4.stem)
        if key not in cam_defs:
            continue
        meta = cam_defs[key]
        discovered.append(
            {
                "path": mp4,
                "cam_key": key,
                "role": meta["role"],
                "camera_id": meta["camera_id"],
            }
        )

    if not discovered:
        for mp4 in sorted(data_dir.rglob("*.mp4")):
            logger.warning("Unmapped video (add to config/camera_map.json): %s", mp4)

    return discovered


def discover_camera_videos(data_dir: Path | None = None, store: str | None = None) -> dict[str, Path]:
    """Legacy: first video per role (entry/floor/billing)."""
    by_role: dict[str, Path] = {}
    for cam in discover_hackathon_cameras(data_dir):
        role = cam["role"]
        if role not in by_role:
            by_role[role] = cam["path"]
    return by_role


def list_dataset_contents(data_dir: Path | None = None) -> dict[str, Any]:
    data_dir = dataset_dir(data_dir)
    cams = discover_hackathon_cameras(data_dir)
    return {
        "csv_files": [p.name for p in data_dir.glob("*.csv")],
        "xlsx_files": [p.name for p in data_dir.glob("*.xlsx")],
        "pos_transactions": len(load_pos_transactions(data_dir)),
        "cameras": [
            {"file": c["path"].name, "role": c["role"], "camera_id": c["camera_id"]}
            for c in cams
        ],
        "layout_xlsx": find_layout_xlsx(data_dir).name if find_layout_xlsx(data_dir) else None,
    }
