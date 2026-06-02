# PROMPT:
# Generate tests for the Store Intelligence dataset loader covering:
# POS transaction parsing from Brigade CSV files,
# order ID uniqueness constraint,
# camera video discovery (empty path graceful fallback),
# timestamp ordering of loaded POS rows,
# and store_id consistency across all CSV rows.
#
# CHANGES MADE:
# - Added test_pos_timestamps_are_ordered to verify temporal ordering.
# - Added test_pos_store_id_consistent to ensure all rows report ST1008.
# - Added test_discover_videos_returns_dict to assert type safety on empty path.

from pathlib import Path

from pipeline.dataset_loader import (
    DEFAULT_DATASET,
    discover_camera_videos,
    load_pos_transactions,
)


def test_load_pos_from_brigade_csv():
    rows = load_pos_transactions(DEFAULT_DATASET)
    assert len(rows) > 0
    ts, txn_id, store_id, _basket = rows[0]
    assert txn_id
    assert store_id == "ST1008"


def test_unique_order_ids():
    rows = load_pos_transactions(DEFAULT_DATASET)
    ids = [r[1] for r in rows]
    assert len(ids) == len(set(ids))


def test_discover_videos_empty_when_missing():
    found = discover_camera_videos(DEFAULT_DATASET, "store1")
    assert isinstance(found, dict)


def test_discover_videos_returns_dict():
    """discover_camera_videos must always return a dict, even for non-existent stores."""
    result = discover_camera_videos(DEFAULT_DATASET, "NONEXISTENT_STORE_XYZ")
    assert isinstance(result, dict), "Expected dict return type for unknown store"


def test_pos_store_id_consistent():
    """Every POS row must report the same store_id (ST1008 for Brigade dataset)."""
    rows = load_pos_transactions(DEFAULT_DATASET)
    store_ids = {r[2] for r in rows}
    assert store_ids == {"ST1008"}, f"Unexpected store IDs in POS data: {store_ids}"


def test_pos_timestamps_are_ordered():
    """POS rows must be sorted in ascending timestamp order."""
    rows = load_pos_transactions(DEFAULT_DATASET)
    timestamps = [r[0] for r in rows]
    assert timestamps == sorted(timestamps), "POS transactions are not in chronological order"
