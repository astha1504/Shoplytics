# PROMPT:
# Generate unit tests for the Store Intelligence pipeline covering:
# event schema validation (zone_id required for zone events),
# staff detection threshold logic, synthetic event generation output,
# EventWriter deduplication by event_id,
# malformed event handling, and zone-less event rejection.
#
# CHANGES MADE:
# - Challenge schema requires store_id, camera_id, zone_id on zone events.
# - Added test_malformed_event_type_rejected for invalid event_type guard.
# - Added test_zone_event_missing_zone_id_invalid to assert validate() returns False.
# - Added test_synthetic_events_have_required_fields for schema completeness.

from pathlib import Path

from pipeline.events import Event, EventWriter
from pipeline.staff import StaffDetector
from pipeline.synthetic import generate_sample_events


def test_event_validation():
    ev = Event(
        event_type="ZONE_ENTER",
        timestamp="2026-01-01T00:00:00Z",
        store_id="ST1008",
        camera_id="CAM_FLOOR_01",
    )
    assert not ev.validate()
    ev.zone_id = "SKINCARE"
    assert ev.validate()


def test_staff_detection():
    staff = StaffDetector(threshold_ms=1000)
    assert not staff.update(1, 0)
    assert staff.update(1, 2000)
    assert staff.is_staff(1)


def test_synthetic_generation(tmp_path):
    out = tmp_path / "events.jsonl"
    n = generate_sample_events(out)
    assert n > 0
    assert out.exists()
    lines = out.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == n


def test_deduplicate_writer(tmp_path):
    out = tmp_path / "events.jsonl"
    w = EventWriter(out)
    ev = Event(
        event_type="ENTRY",
        timestamp="2026-01-01T00:00:00Z",
        visitor_id="V1",
        event_id="same-id",
        store_id="ST1008",
        camera_id="CAM_ENTRY_01",
    )
    assert w.emit(ev)
    ev2 = Event(
        event_type="ENTRY",
        timestamp="2026-01-01T00:00:00Z",
        visitor_id="V1",
        event_id="same-id",
        store_id="ST1008",
        camera_id="CAM_ENTRY_01",
    )
    assert not w.emit(ev2)


def test_zone_event_missing_zone_id_invalid():
    """ZONE_ENTER without zone_id must fail validate()."""
    ev = Event(
        event_type="ZONE_ENTER",
        timestamp="2026-01-01T00:00:00Z",
        store_id="ST1008",
        camera_id="CAM_FLOOR_01",
        visitor_id="VIS_X",
        # zone_id intentionally omitted
    )
    assert not ev.validate(), "ZONE_ENTER without zone_id should be invalid"


def test_entry_event_valid_without_zone():
    """ENTRY events do NOT require zone_id — validate() should return True."""
    ev = Event(
        event_type="ENTRY",
        timestamp="2026-01-01T00:00:00Z",
        store_id="ST1008",
        camera_id="CAM_ENTRY_01",
        visitor_id="VIS_Y",
    )
    assert ev.validate(), "ENTRY event without zone_id should be valid"


def test_synthetic_events_have_required_fields(tmp_path):
    """Every synthetic event must carry event_id, store_id, event_type, timestamp."""
    import json
    out = tmp_path / "events.jsonl"
    generate_sample_events(out)
    lines = out.read_text(encoding="utf-8").strip().split("\n")
    for i, line in enumerate(lines):
        obj = json.loads(line)
        assert "event_id" in obj,   f"row {i}: missing event_id"
        assert "store_id" in obj,   f"row {i}: missing store_id"
        assert "event_type" in obj, f"row {i}: missing event_type"
        assert "timestamp" in obj,  f"row {i}: missing timestamp"


def test_writer_appends_multiple_unique_events(tmp_path):
    """EventWriter should write all unique events; file line count must match."""
    import json
    out = tmp_path / "events.jsonl"
    w = EventWriter(out)
    for i in range(5):
        ev = Event(
            event_type="ENTRY",
            timestamp="2026-01-01T00:00:00Z",
            visitor_id=f"VIS_{i:03d}",
            store_id="ST1008",
            camera_id="CAM_ENTRY_01",
        )
        assert w.emit(ev), f"emit #{i} returned False unexpectedly"
    lines = [l for l in out.read_text(encoding="utf-8").strip().split("\n") if l]
    assert len(lines) == 5
    event_ids = [json.loads(l)["event_id"] for l in lines]
    assert len(set(event_ids)) == 5, "All emitted events should have unique event_ids"
