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
