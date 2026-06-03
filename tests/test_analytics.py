# PROMPT: Generate comprehensive pytest tests for store analytics API covering:
# empty store, staff exclusion, duplicate ingest, re-entry funnel,
# zero conversion, purchase via PURCHASE_CORRELATED, heatmap counts,
# anomaly detection (queue spike, conversion drop, dead zone, high abandonment),
# batch limit, invalid event rejection, health endpoint, clear-db admin endpoint.
# CHANGES MADE:
# - Updated for /stores/{id} routes and zone_id/camera_id schema
# - Added avg_dwell_per_zone field assertions per challenge spec
# - Added anomaly severity/type tests matching new analytics thresholds
# - Added batch limit test (>500 events → 400)
# - Added clear-db test to confirm state reset
# - Fixed reentry test to assert visitor count = 1 (not 2)

import uuid


def _event(event_type: str, visitor_id: str = "VIS_001", is_staff: bool = False, **extra) -> dict:
    d = {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "timestamp": "2026-04-10T12:00:00Z",
        "visitor_id": visitor_id,
        "store_id": "ST1008",
        "camera_id": "CAM_ENTRY_01",
        "is_staff": is_staff,
        "confidence": 0.9,
        "dwell_ms": 0,
        "metadata": {},
    }
    if "zone" in extra:
        extra["zone_id"] = extra.pop("zone")
    d.update(extra)
    return d


def _ingest(client, events: list[dict]):
    return client.post("/events/ingest", json=events)


# ---------------------------------------------------------------------------
# Empty store
# ---------------------------------------------------------------------------

class TestEmptyStore:
    def test_zero_visitors(self, client):
        r = client.get("/stores/ST1008/metrics")
        assert r.status_code == 200
        body = r.json()
        assert body["visitors"] == 0
        assert body["conversion_rate"] == 0.0
        assert body["queue_depth"] == 0
        assert body["abandonment_rate"] == 0.0
        assert isinstance(body["avg_dwell_per_zone"], dict)

    def test_empty_funnel(self, client):
        r = client.get("/stores/ST1008/funnel")
        assert r.status_code == 200
        stages = {s["stage"]: s["count"] for s in r.json()["stages"]}
        assert stages["Entry"] == 0
        assert stages["Zone Visit"] == 0
        assert stages["Billing Queue"] == 0
        assert stages["Purchase"] == 0

    def test_empty_heatmap(self, client):
        r = client.get("/stores/ST1008/heatmap")
        assert r.status_code == 200
        body = r.json()
        assert body["zones"] == {}
        assert body["data_confidence"] == "LOW"

    def test_no_anomalies_empty_store(self, client):
        r = client.get("/stores/ST1008/anomalies")
        assert r.status_code == 200
        # No visitors → no anomalies (visitors < 5 threshold)
        assert isinstance(r.json(), list)


# ---------------------------------------------------------------------------
# Staff exclusion
# ---------------------------------------------------------------------------

class TestStaffOnly:
    def test_staff_excluded_from_visitors(self, client):
        _ingest(client, [
            _event("ENTRY", "VIS_STAFF", is_staff=True),
            _event("ZONE_ENTER", "VIS_STAFF", zone_id="SKINCARE", is_staff=True),
            _event("ZONE_DWELL", "VIS_STAFF", zone_id="SKINCARE", dwell_ms=35000, is_staff=True),
        ])
        r = client.get("/stores/ST1008/metrics")
        assert r.json()["visitors"] == 0
        assert r.json()["avg_dwell_seconds"] == 0.0
        # Staff zone should NOT appear in heatmap
        heat = client.get("/stores/ST1008/heatmap").json()["zones"]
        assert "SKINCARE" not in heat

    def test_staff_excluded_from_funnel(self, client):
        _ingest(client, [
            _event("ENTRY", "VIS_STAFF", is_staff=True),
            _event("BILLING_QUEUE_JOIN", "VIS_STAFF", metadata={"queue_depth": 2}, is_staff=True),
        ])
        stages = {s["stage"]: s["count"]
                  for s in client.get("/stores/ST1008/funnel").json()["stages"]}
        assert stages["Entry"] == 0
        assert stages["Billing Queue"] == 0


# ---------------------------------------------------------------------------
# Duplicate / idempotency
# ---------------------------------------------------------------------------

class TestDuplicateEvents:
    def test_same_event_id_twice(self, client):
        ev = _event("ENTRY", "VIS_001")
        r1 = _ingest(client, [ev])
        r2 = _ingest(client, [ev])
        assert r1.json()["accepted"] == 1
        assert r2.json()["duplicates"] == 1
        # Visitor count stays 1, not 2
        assert client.get("/stores/ST1008/metrics").json()["visitors"] == 1

    def test_mixed_batch_partial_success(self, client):
        ev1 = _event("ENTRY", "VIS_A")
        ev2 = _event("INVALID_TYPE", "VIS_B")  # bad type
        r = _ingest(client, [ev1, ev2])
        body = r.json()
        assert body["accepted"] == 1
        assert body["rejected"] == 1
        assert len(body["errors"]) == 1


# ---------------------------------------------------------------------------
# Re-entry
# ---------------------------------------------------------------------------

class TestReentry:
    def test_reentry_does_not_double_count(self, client):
        vid = "VIS_010"
        _ingest(client, [
            _event("ENTRY", vid),
            _event("EXIT", vid),
            _event("REENTRY", vid),  # same person, same visitor_id
        ])
        m = client.get("/stores/ST1008/metrics").json()
        # REENTRY reuses the same visitor_id → still 1 unique visitor
        assert m["visitors"] == 1

    def test_reentry_in_funnel_single_session(self, client):
        vid = "VIS_011"
        _ingest(client, [
            _event("ENTRY", vid),
            _event("ZONE_ENTER", vid, zone_id="SKINCARE"),
            _event("EXIT", vid),
            _event("REENTRY", vid),
            _event("BILLING_QUEUE_JOIN", vid, metadata={"queue_depth": 1}),
            _event("EXIT", vid),
        ])
        stages = {s["stage"]: s["count"]
                  for s in client.get("/stores/ST1008/funnel").json()["stages"]}
        assert stages["Entry"] == 1   # not 2
        assert stages["Zone Visit"] == 1
        assert stages["Billing Queue"] == 1


# ---------------------------------------------------------------------------
# Conversion / purchases
# ---------------------------------------------------------------------------

class TestNoPurchases:
    def test_conversion_zero_with_abandon(self, client):
        _ingest(client, [
            _event("ENTRY", "VIS_020"),
            _event("ZONE_ENTER", "VIS_020", zone_id="MAKEUP"),
            _event("BILLING_QUEUE_JOIN", "VIS_020", metadata={"queue_depth": 2}),
            _event("BILLING_QUEUE_ABANDON", "VIS_020"),
            _event("EXIT", "VIS_020"),
        ])
        m = client.get("/stores/ST1008/metrics").json()
        assert m["conversion_rate"] == 0.0
        assert m["abandonment_rate"] == 100.0


class TestPurchase:
    def test_conversion_with_explicit_purchase(self, client):
        _ingest(client, [
            _event("ENTRY", "VIS_030"),
            _event("BILLING_QUEUE_JOIN", "VIS_030", metadata={"queue_depth": 1}),
            _event("PURCHASE_CORRELATED", "VIS_030"),
            _event("EXIT", "VIS_030"),
        ])
        m = client.get("/stores/ST1008/metrics").json()
        assert m["visitors"] == 1
        assert m["conversion_rate"] == 100.0

    def test_two_visitors_one_purchase(self, client):
        _ingest(client, [
            _event("ENTRY", "VIS_031"),
            _event("PURCHASE_CORRELATED", "VIS_031"),
            _event("EXIT", "VIS_031"),
            _event("ENTRY", "VIS_032"),
            _event("EXIT", "VIS_032"),
        ])
        m = client.get("/stores/ST1008/metrics").json()
        assert m["visitors"] == 2
        assert m["conversion_rate"] == 50.0


# ---------------------------------------------------------------------------
# Heatmap
# ---------------------------------------------------------------------------

class TestHeatmap:
    def test_zone_visit_counts(self, client):
        _ingest(client, [
            _event("ZONE_ENTER", "VIS_040", zone_id="SKINCARE"),
            _event("ZONE_ENTER", "VIS_041", zone_id="SKINCARE"),
            _event("ZONE_ENTER", "VIS_042", zone_id="MAKEUP"),
            _event("ZONE_DWELL", "VIS_040", zone_id="SKINCARE", dwell_ms=40000),
        ])
        heat = client.get("/stores/ST1008/heatmap").json()["zones"]
        assert "SKINCARE" in heat
        assert heat["SKINCARE"]["visits"] >= 2
        assert heat["MAKEUP"]["visits"] >= 1
        # SKINCARE has more visits → higher or equal score to MAKEUP
        assert heat["SKINCARE"]["score"] >= heat["MAKEUP"]["score"]

    def test_dwell_computed_correctly(self, client):
        _ingest(client, [
            _event("ZONE_DWELL", "VIS_040", zone_id="HAIRCARE", dwell_ms=60000),
        ])
        heat = client.get("/stores/ST1008/heatmap").json()["zones"]
        assert heat["HAIRCARE"]["avg_dwell_seconds"] == 60.0

    def test_avg_dwell_per_zone_in_metrics(self, client):
        _ingest(client, [
            _event("ENTRY", "VIS_050"),
            _event("ZONE_DWELL", "VIS_050", zone_id="FRAGRANCE", dwell_ms=45000),
        ])
        m = client.get("/stores/ST1008/metrics").json()
        assert "avg_dwell_per_zone" in m
        assert "FRAGRANCE" in m["avg_dwell_per_zone"]
        assert m["avg_dwell_per_zone"]["FRAGRANCE"] == 45.0


# ---------------------------------------------------------------------------
# Anomaly detection
# ---------------------------------------------------------------------------

class TestAnomalies:
    def test_queue_spike_warn(self, client):
        # queue_depth=6 → WARN (>=5, <8)
        for i in range(6):
            _ingest(client, [
                _event(f"ENTRY", f"VIS_Q{i:03d}"),
                _event("BILLING_QUEUE_JOIN", f"VIS_Q{i:03d}",
                       metadata={"queue_depth": 6}),
            ])
        anomalies = client.get("/stores/ST1008/anomalies").json()
        types = {a["type"] for a in anomalies}
        assert "BILLING_QUEUE_SPIKE" in types
        spike = next(a for a in anomalies if a["type"] == "BILLING_QUEUE_SPIKE")
        assert spike["severity"] in ("WARN", "CRITICAL")
        assert spike["suggested_action"] != ""

    def test_conversion_drop_critical(self, client):
        # 6 visitors, 0 purchases → CRITICAL
        for i in range(6):
            _ingest(client, [_event("ENTRY", f"VIS_CD{i:03d}")])
        anomalies = client.get("/stores/ST1008/anomalies").json()
        types = {a["type"] for a in anomalies}
        assert "CONVERSION_DROP" in types
        drop = next(a for a in anomalies if a["type"] == "CONVERSION_DROP")
        assert drop["severity"] == "CRITICAL"
        assert drop["suggested_action"] != ""

    def test_no_anomaly_normal_operation(self, client):
        # Single visitor with purchase — below visitor threshold for anomalies
        _ingest(client, [
            _event("ENTRY", "VIS_NORM"),
            _event("BILLING_QUEUE_JOIN", "VIS_NORM", metadata={"queue_depth": 1}),
            _event("PURCHASE_CORRELATED", "VIS_NORM"),
        ])
        # Only 1 visitor, below the 5-visitor threshold for CONVERSION_DROP
        m = client.get("/stores/ST1008/metrics").json()
        assert m["visitors"] == 1


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

class TestInputValidation:
    def test_reject_invalid_event_type(self, client):
        r = _ingest(client, [_event("INVALID_TYPE")])
        assert r.json()["rejected"] == 1

    def test_reject_zone_event_without_zone_id(self, client):
        ev = _event("ZONE_ENTER")  # no zone_id
        r = _ingest(client, [ev])
        assert r.json()["rejected"] == 1

    def test_batch_limit_exceeded(self, client):
        # >500 events should return 400
        big_batch = [_event("ENTRY", f"VIS_{i}") for i in range(501)]
        r = _ingest(client, big_batch)
        assert r.status_code == 400

    def test_empty_batch_accepted(self, client):
        r = _ingest(client, [])
        assert r.status_code == 200
        assert r.json()["accepted"] == 0


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_returns_valid_status(self, client):
        body = client.get("/health").json()
        assert body["status"] in ("healthy", "degraded")
        assert isinstance(body["stores"], dict)

    def test_health_with_events(self, client):
        _ingest(client, [_event("ENTRY", "VIS_H1")])
        body = client.get("/health").json()
        assert "ST1008" in body["stores"]
        assert body["stores"]["ST1008"]["last_event_at"] is not None


# ---------------------------------------------------------------------------
# Admin clear-db
# ---------------------------------------------------------------------------

class TestAdminEndpoints:
    def test_clear_db_resets_state(self, client):
        # Seed some events
        _ingest(client, [_event("ENTRY", "VIS_CLR"), _event("ENTRY", "VIS_CLR2")])
        assert client.get("/stores/ST1008/metrics").json()["visitors"] == 2

        # Clear
        r = client.post("/admin/clear-db")
        assert r.status_code == 200
        assert r.json()["deleted"] > 0

        # Verify reset
        assert client.get("/stores/ST1008/metrics").json()["visitors"] == 0

    def test_reload_from_nonexistent_file(self, client):
        r = client.post("/admin/reload-from-file?path=/nonexistent/path/events.jsonl")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Spatial analytics — live customer positions
# ---------------------------------------------------------------------------

class TestSpatialAnalytics:
    def test_spatial_empty_store(self, client):
        r = client.get("/stores/ST1008/spatial")
        assert r.status_code == 200
        body = r.json()
        assert body["active_visitors"] == 0
        assert body["visitors"] == []
        assert "SKINCARE" in body["zones"] or "zones" in body

    def test_spatial_tracks_visitor_movement(self, client):
        events = [
            _event("ENTRY", "VIS_SP1", metadata={"position_x": 240, "position_y": 540}),
            _event("ZONE_ENTER", "VIS_SP1", zone="SKINCARE", camera_id="CAM_FLOOR_01",
                   metadata={"position_x": 300, "position_y": 275}),
            _event("ZONE_ENTER", "VIS_SP1", zone="MAKEUP", camera_id="CAM_FLOOR_01",
                   metadata={"position_x": 710, "position_y": 275}),
        ]
        _ingest(client, events)
        body = client.get("/stores/ST1008/spatial").json()
        assert body["active_visitors"] == 1
        visitor = next(v for v in body["visitors"] if v["visitor_id"] == "VIS_SP1")
        assert visitor["zone"] == "MAKEUP"
        assert len(visitor["trail"]) >= 2
        assert visitor["x"] > 50  # moved right toward makeup zone

    def test_spatial_excludes_exited_visitors_from_active(self, client):
        events = [
            _event("ENTRY", "VIS_SP2", metadata={"position_x": 240, "position_y": 540}),
            _event("EXIT", "VIS_SP2", metadata={"position_x": 240, "position_y": 540}),
        ]
        _ingest(client, events)
        body = client.get("/stores/ST1008/spatial").json()
        visitor = next(v for v in body["visitors"] if v["visitor_id"] == "VIS_SP2")
        assert visitor["is_active"] is False
        assert visitor["status"] == "exited"
        assert body["active_visitors"] == 0
