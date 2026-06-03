"""Rich synthetic events covering all challenge event types and edge cases.

Generates ~200 events for a simulated 2-hour store session:
- All 8 event types from the spec
- Staff exclusion (is_staff=True)
- Group entry (3 people entering simultaneously)
- Re-entry (same visitor_id after EXIT)
- Queue spike (queue_depth >= 5)
- Billing queue abandon
- Empty-store period
- Diverse zone dwell data
- Chronologically ordered timestamps
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from pipeline.events import Event, EventWriter

BASE_TIME = datetime(2026, 3, 3, 9, 0, 0, tzinfo=timezone.utc)

ZONES = ["SKINCARE", "MAKEUP", "HAIRCARE", "FRAGRANCE", "ACCESSORIES"]
DEFAULT_STORE = "STORE_BLR_002"

# Zone centroids for spatial analytics (matches config/store_layout.json)
ZONE_POSITIONS = {
    "SKINCARE": (300, 275),
    "MAKEUP": (710, 275),
    "HAIRCARE": (500, 535),
    "FRAGRANCE": (500, 535),
    "ACCESSORIES": (850, 535),
}
ENTRY_POS = (240, 540)
BILLING_POS = (875, 500)


def _pos(zone: str | None, vid: str, offset: int = 0) -> dict:
    """Position metadata for spatial floor map."""
    import hashlib
    if zone and zone in ZONE_POSITIONS:
        bx, by = ZONE_POSITIONS[zone]
    elif zone == "BILLING":
        bx, by = BILLING_POS
    else:
        bx, by = ENTRY_POS
    h = hashlib.md5(f"{vid}{offset}".encode()).hexdigest()
    dx = (int(h[:4], 16) / 65535 - 0.5) * 30
    dy = (int(h[4:8], 16) / 65535 - 0.5) * 30
    return {"position_x": round(bx + dx, 1), "position_y": round(by + dy, 1)}


def _ts(offset_seconds: int) -> str:
    t = BASE_TIME + timedelta(seconds=offset_seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _emit_visitor_session(
    writer: EventWriter,
    vid: str,
    store_id: str,
    entry_offset: int,
    zones: list[str],
    to_billing: bool,
    abandons: bool,
    purchases: bool,
    queue_depth: int = 2,
    is_staff: bool = False,
) -> int:
    """Emit a complete visitor session. Returns number of events emitted."""
    cam_entry = "CAM_ENTRY_01"
    cam_floor = "CAM_FLOOR_01"
    cam_bill = "CAM_BILLING_01"
    count = 0
    t = entry_offset
    seq = 1
    conf = 0.85 if not is_staff else 0.92

    def _ev(**kwargs):
        nonlocal count
        ev = Event(store_id=store_id, camera_id=cam_entry, **kwargs)
        if writer.emit(ev):
            count += 1

    # ENTRY
    ev = Event(
        event_type="ENTRY", timestamp=_ts(t), visitor_id=vid,
        store_id=store_id, camera_id=cam_entry,
        confidence=conf, is_staff=is_staff,
        metadata={"session_seq": seq, **_pos(None, vid, 0)},
    )
    if writer.emit(ev):
        count += 1
    seq += 1
    t += 20

    # ZONE visits
    for zi, zone in enumerate(zones):
        ev_enter = Event(
            event_type="ZONE_ENTER", timestamp=_ts(t), visitor_id=vid,
            store_id=store_id, camera_id=cam_floor, zone_id=zone,
            confidence=conf - 0.03, is_staff=is_staff,
            metadata={"session_seq": seq, **_pos(zone, vid, zi)},
        )
        if writer.emit(ev_enter):
            count += 1
        seq += 1
        t += 40

        # ZONE_DWELL after 30s
        ev_dwell = Event(
            event_type="ZONE_DWELL", timestamp=_ts(t), visitor_id=vid,
            store_id=store_id, camera_id=cam_floor, zone_id=zone,
            dwell_ms=35000, confidence=conf - 0.05, is_staff=is_staff,
            metadata={"session_seq": seq, "sku_zone": zone, **_pos(zone, vid, zi + 10)},
        )
        if writer.emit(ev_dwell):
            count += 1
        seq += 1
        t += 35

        ev_exit = Event(
            event_type="ZONE_EXIT", timestamp=_ts(t), visitor_id=vid,
            store_id=store_id, camera_id=cam_floor, zone_id=zone,
            confidence=conf - 0.02, is_staff=is_staff,
            metadata={"session_seq": seq, **_pos(zone, vid, zi + 20)},
        )
        if writer.emit(ev_exit):
            count += 1
        seq += 1
        t += 15

    # BILLING
    if to_billing:
        ev_q = Event(
            event_type="BILLING_QUEUE_JOIN", timestamp=_ts(t), visitor_id=vid,
            store_id=store_id, camera_id=cam_bill, confidence=conf,
            is_staff=is_staff,
            metadata={"queue_depth": queue_depth, "session_seq": seq, **_pos("BILLING", vid, 99)},
        )
        if writer.emit(ev_q):
            count += 1
        seq += 1
        t += 90

        if abandons:
            ev_ab = Event(
                event_type="BILLING_QUEUE_ABANDON", timestamp=_ts(t), visitor_id=vid,
                store_id=store_id, camera_id=cam_bill, confidence=conf - 0.1,
                is_staff=is_staff, metadata={"session_seq": seq},
            )
            if writer.emit(ev_ab):
                count += 1
            seq += 1
            t += 20

    # EXIT
    ev_exit = Event(
        event_type="EXIT", timestamp=_ts(t), visitor_id=vid,
        store_id=store_id, camera_id=cam_entry, confidence=conf,
        is_staff=is_staff, metadata={"session_seq": seq, **_pos(None, vid, 100)},
    )
    if writer.emit(ev_exit):
        count += 1

    return count


def generate_sample_events(output: Path, store_id: str = DEFAULT_STORE) -> int:
    writer = EventWriter(output)
    count = 0
    cam_entry = "CAM_ENTRY_01"
    cam_floor = "CAM_FLOOR_01"
    cam_bill = "CAM_BILLING_01"

    # -------------------------------------------------------
    # STAFF — 2 staff members present throughout the day
    # They are flagged is_staff=True and EXCLUDED from metrics
    # -------------------------------------------------------
    for staff_id, start_t in [("VIS_STAFF001", 60), ("VIS_STAFF002", 90)]:
        count += _emit_visitor_session(
            writer, staff_id, store_id,
            entry_offset=start_t,
            zones=["SKINCARE", "MAKEUP", "HAIRCARE"],
            to_billing=False, abandons=False, purchases=False,
            is_staff=True,
        )
        # Staff also dwell for a very long time (>15 min → triggers staff detection)
        for zone in ["SKINCARE", "MAKEUP"]:
            ev = Event(
                event_type="ZONE_DWELL", timestamp=_ts(start_t + 1800),
                visitor_id=staff_id, store_id=store_id, camera_id=cam_floor,
                zone_id=zone, dwell_ms=1800000, confidence=0.95, is_staff=True,
                metadata={"session_seq": 99, "sku_zone": zone},
            )
            if writer.emit(ev):
                count += 1

    # -------------------------------------------------------
    # REGULAR VISITORS — spread across 2 hours
    # (visitor_id, entry_offset_s, zones, to_billing, abandons, purchases, queue_depth)
    # -------------------------------------------------------
    sessions = [
        ("VIS_c8a2f1", 120,  ["SKINCARE", "MAKEUP"],        True,  False, True,  2),
        ("VIS_a1b2c3", 300,  ["HAIRCARE"],                  True,  True,  False, 3),
        ("VIS_d4e5f6", 480,  ["FRAGRANCE", "SKINCARE"],     True,  False, True,  4),
        ("VIS_e7f8g9", 660,  ["MAKEUP"],                    False, False, False, 0),
        ("VIS_h1i2j3", 840,  ["SKINCARE"],                  True,  False, True,  2),
        ("VIS_k4l5m6", 1020, ["HAIRCARE", "FRAGRANCE"],     False, False, False, 0),
        ("VIS_n7o8p9", 1200, ["MAKEUP", "SKINCARE"],        True,  True,  False, 5),  # queue spike
        ("VIS_q1r2s3", 1380, [],                            True,  False, True,  1),
        ("VIS_t4u5v6", 1560, ["SKINCARE"],                  True,  False, True,  3),
        ("VIS_w7x8y9", 1740, ["MAKEUP"],                    False, False, False, 0),
        ("VIS_z1a2b3", 1920, ["FRAGRANCE"],                 True,  False, True,  6),  # queue spike
        ("VIS_c3d4e5", 2100, ["SKINCARE", "HAIRCARE"],      True,  False, True,  2),
        ("VIS_f6g7h8", 2280, ["MAKEUP"],                    False, False, False, 0),
        ("VIS_i9j1k2", 2460, ["SKINCARE"],                  True,  True,  False, 4),
        ("VIS_l3m4n5", 2640, [],                            False, False, False, 0),
        ("VIS_o6p7q8", 2820, ["ACCESSORIES"],               True,  False, True,  2),
        ("VIS_r9s1t2", 3000, ["FRAGRANCE", "MAKEUP"],       True,  False, True,  3),
        ("VIS_u3v4w5", 3180, ["SKINCARE"],                  True,  True,  False, 5),  # abandon
        ("VIS_x6y7z8", 3360, ["HAIRCARE", "ACCESSORIES"],   True,  False, True,  2),
        ("VIS_a9b1c2", 3540, ["MAKEUP", "FRAGRANCE"],       True,  False, True,  3),
    ]

    for vid, t_off, zones, billing, abandon, purchase, qdepth in sessions:
        count += _emit_visitor_session(
            writer, vid, store_id,
            entry_offset=t_off,
            zones=zones,
            to_billing=billing,
            abandons=abandon,
            purchases=purchase,
            queue_depth=qdepth,
            is_staff=False,
        )

    # -------------------------------------------------------
    # RE-ENTRY: VIS_d4e5f6 steps out and returns (same person)
    # Our Re-ID system catches this and emits REENTRY not ENTRY
    # -------------------------------------------------------
    reentry_vid = "VIS_d4e5f6"
    t_re = 4500
    ev = Event(
        event_type="REENTRY", timestamp=_ts(t_re), visitor_id=reentry_vid,
        store_id=store_id, camera_id=cam_entry, confidence=0.78,
        metadata={"session_seq": 1, "reentry": True},
    )
    if writer.emit(ev):
        count += 1
    ev = Event(
        event_type="ZONE_ENTER", timestamp=_ts(t_re + 60), visitor_id=reentry_vid,
        store_id=store_id, camera_id=cam_floor, zone_id="ACCESSORIES",
        confidence=0.82, metadata={"session_seq": 2},
    )
    if writer.emit(ev):
        count += 1
    ev = Event(
        event_type="ZONE_DWELL", timestamp=_ts(t_re + 100), visitor_id=reentry_vid,
        store_id=store_id, camera_id=cam_floor, zone_id="ACCESSORIES",
        dwell_ms=40000, confidence=0.80, metadata={"session_seq": 3, "sku_zone": "ACCESSORIES"},
    )
    if writer.emit(ev):
        count += 1
    ev = Event(
        event_type="EXIT", timestamp=_ts(t_re + 300), visitor_id=reentry_vid,
        store_id=store_id, camera_id=cam_entry, confidence=0.88,
        metadata={"session_seq": 4},
    )
    if writer.emit(ev):
        count += 1

    # -------------------------------------------------------
    # GROUP ENTRY: 3 people enter simultaneously — pipeline must
    # count 3 individuals, not 1 group
    # -------------------------------------------------------
    t_grp = 5400
    for grp_vid in ["VIS_GRP_01", "VIS_GRP_02", "VIS_GRP_03"]:
        count += _emit_visitor_session(
            writer, grp_vid, store_id,
            entry_offset=t_grp,
            zones=["MAKEUP"],
            to_billing=True,
            abandons=False,
            purchases=True,
            queue_depth=3,
            is_staff=False,
        )

    # -------------------------------------------------------
    # BILLING QUEUE SPIKE: queue_depth=7 → triggers CRITICAL anomaly
    # -------------------------------------------------------
    spike_entry_t = 6000
    for i, vid in enumerate(["VIS_SPIKE_A", "VIS_SPIKE_B", "VIS_SPIKE_C"]):
        ev = Event(
            event_type="ENTRY", timestamp=_ts(spike_entry_t + i * 10),
            visitor_id=vid, store_id=store_id, camera_id=cam_entry,
            confidence=0.88, metadata={"session_seq": 1},
        )
        if writer.emit(ev):
            count += 1
        ev = Event(
            event_type="BILLING_QUEUE_JOIN", timestamp=_ts(spike_entry_t + 120 + i * 10),
            visitor_id=vid, store_id=store_id, camera_id=cam_bill, confidence=0.87,
            metadata={"queue_depth": 7 + i, "session_seq": 2},
        )
        if writer.emit(ev):
            count += 1
        ev = Event(
            event_type="EXIT", timestamp=_ts(spike_entry_t + 300 + i * 10),
            visitor_id=vid, store_id=store_id, camera_id=cam_entry,
            confidence=0.89, metadata={"session_seq": 3},
        )
        if writer.emit(ev):
            count += 1

    # -------------------------------------------------------
    # EMPTY STORE PERIOD: no events between t=6300 and t=6900 (10 min)
    # API must return 0 visitors, not crash, during this window
    # -------------------------------------------------------

    # Late afternoon traffic (after empty period)
    late_sessions = [
        ("VIS_LATE_01", 6960, ["SKINCARE", "FRAGRANCE"], True, False, True, 2),
        ("VIS_LATE_02", 7040, ["MAKEUP", "HAIRCARE"],    True, False, True, 3),
        ("VIS_LATE_03", 7120, ["ACCESSORIES"],            False, False, False, 0),
    ]
    for vid, t_off, zones, billing, abandon, purchase, qdepth in late_sessions:
        count += _emit_visitor_session(
            writer, vid, store_id,
            entry_offset=t_off,
            zones=zones,
            to_billing=billing,
            abandons=abandon,
            purchases=purchase,
            queue_depth=qdepth,
            is_staff=False,
        )

    return count
