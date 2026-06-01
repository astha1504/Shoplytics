"""Sample events matching challenge schema when CCTV not processed yet."""

from pathlib import Path

from pipeline.events import Event, EventWriter, utc_now_iso


def generate_sample_events(output: Path, store_id: str = "ST1008") -> int:
    writer = EventWriter(output)
    base = utc_now_iso()
    cam_entry, cam_floor, cam_bill = "CAM_ENTRY_01", "CAM_FLOOR_01", "CAM_BILLING_01"

    scenarios = [
        ("VIS_c8a2f1", "ENTRY", cam_entry, None, 0, {}),
        ("VIS_c8a2f1", "ZONE_ENTER", cam_floor, "SKINCARE", 0, {}),
        ("VIS_c8a2f1", "ZONE_DWELL", cam_floor, "SKINCARE", 32000, {}),
        ("VIS_c8a2f1", "ZONE_EXIT", cam_floor, "SKINCARE", 0, {}),
        ("VIS_c8a2f1", "BILLING_QUEUE_JOIN", cam_bill, None, 0, {"queue_depth": 3}),
        ("VIS_c8a2f1", "EXIT", cam_entry, None, 0, {}),
        ("VIS_a1b2c3", "ENTRY", cam_entry, None, 0, {}),
        ("VIS_a1b2c3", "BILLING_QUEUE_JOIN", cam_bill, None, 0, {"queue_depth": 2}),
        ("VIS_a1b2c3", "BILLING_QUEUE_ABANDON", cam_bill, None, 0, {}),
        ("VIS_a1b2c3", "EXIT", cam_entry, None, 0, {}),
        ("VIS_d4e5f6", "ENTRY", cam_entry, None, 0, {}),
        ("VIS_d4e5f6", "EXIT", cam_entry, None, 0, {}),
        ("VIS_d4e5f6", "REENTRY", cam_entry, None, 0, {}),
        ("VIS_STAFF01", "ENTRY", cam_entry, None, 0, {"is_staff": True}),
        ("VIS_STAFF01", "ZONE_ENTER", cam_floor, "MAKEUP", 0, {"is_staff": True}),
    ]

    count = 0
    seq = 0
    for visitor_id, event_type, camera_id, zone_id, dwell_ms, extra in scenarios:
        seq += 1
        is_staff = extra.pop("is_staff", False)
        meta = dict(extra)
        meta["session_seq"] = seq
        ev = Event(
            event_type=event_type,
            timestamp=base,
            visitor_id=visitor_id,
            store_id=store_id,
            camera_id=camera_id,
            zone_id=zone_id,
            dwell_ms=dwell_ms,
            is_staff=is_staff,
            metadata=meta,
            confidence=0.91,
        )
        if writer.emit(ev):
            count += 1
    return count
