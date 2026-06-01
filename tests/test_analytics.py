# PROMPT: Generate pytest tests for store analytics API: empty store, staff exclusion,
# duplicate ingest, re-entry funnel, zero conversion, purchase correlation.
# CHANGES MADE: Updated for /stores/{id} routes, zone_id/camera_id schema, funnel stages.

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


class TestEmptyStore:
    def test_zero_visitors(self, client):
        r = client.get("/stores/ST1008/metrics")
        assert r.status_code == 200
        assert r.json()["visitors"] == 0
        assert r.json()["conversion_rate"] == 0

    def test_empty_funnel(self, client):
        r = client.get("/stores/ST1008/funnel")
        stages = {s["stage"]: s["count"] for s in r.json()["stages"]}
        assert stages["Entry"] == 0


class TestStaffOnly:
    def test_staff_excluded_from_visitors(self, client):
        events = [
            _event("ENTRY", "VIS_STAFF", is_staff=True),
            _event("ZONE_ENTER", "VIS_STAFF", zone_id="SKINCARE", is_staff=True),
        ]
        _ingest(client, events)
        r = client.get("/stores/ST1008/metrics")
        assert r.json()["visitors"] == 0


class TestDuplicateEvents:
    def test_same_event_twice(self, client):
        ev = _event("ENTRY", "VIS_001")
        r1 = _ingest(client, [ev])
        r2 = _ingest(client, [ev])
        assert r1.json()["accepted"] == 1
        assert r2.json()["duplicates"] == 1
        assert client.get("/stores/ST1008/metrics").json()["visitors"] == 1


class TestReentry:
    def test_entry_exit_reentry(self, client):
        vid = "VIS_010"
        events = [
            _event("ENTRY", vid),
            _event("EXIT", vid),
            _event("REENTRY", vid),
        ]
        _ingest(client, events)
        stages = {s["stage"]: s["count"] for s in client.get("/stores/ST1008/funnel").json()["stages"]}
        assert stages["Entry"] == 1
        assert client.get("/stores/ST1008/metrics").json()["visitors"] == 1


class TestNoPurchases:
    def test_conversion_zero(self, client):
        events = [
            _event("ENTRY", "VIS_020"),
            _event("ZONE_ENTER", "VIS_020", zone_id="MAKEUP"),
            _event("BILLING_QUEUE_JOIN", "VIS_020", metadata={"queue_depth": 2}),
            _event("BILLING_QUEUE_ABANDON", "VIS_020"),
            _event("EXIT", "VIS_020"),
        ]
        _ingest(client, events)
        m = client.get("/stores/ST1008/metrics").json()
        assert m["conversion_rate"] == 0


class TestPurchase:
    def test_conversion_with_purchase(self, client):
        events = [
            _event("ENTRY", "VIS_030"),
            _event("BILLING_QUEUE_JOIN", "VIS_030", metadata={"queue_depth": 1}),
            _event("PURCHASE_CORRELATED", "VIS_030"),
            _event("EXIT", "VIS_030"),
        ]
        _ingest(client, events)
        m = client.get("/stores/ST1008/metrics").json()
        assert m["visitors"] == 1
        assert m["conversion_rate"] == 100.0


class TestHeatmap:
    def test_zone_counts(self, client):
        events = [
            _event("ZONE_ENTER", "VIS_040", zone_id="SKINCARE"),
            _event("ZONE_ENTER", "VIS_041", zone_id="SKINCARE"),
            _event("ZONE_ENTER", "VIS_042", zone_id="MAKEUP"),
        ]
        _ingest(client, events)
        heat = client.get("/stores/ST1008/heatmap").json()["zones"]
        assert heat["SKINCARE"]["visits"] == 2
        assert heat["MAKEUP"]["visits"] == 1


class TestInvalidEvents:
    def test_reject_bad_type(self, client):
        bad = _event("INVALID_TYPE")
        r = _ingest(client, [bad])
        assert r.json()["rejected"] == 1


class TestHealth:
    def test_health(self, client):
        body = client.get("/health").json()
        assert body["status"] in ("healthy", "degraded")
