# PROMPT: Unit tests for event schema validation, staff rules, synthetic generation, dedupe.
# CHANGES MADE: Challenge schema requires store_id, camera_id, zone_id.

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
