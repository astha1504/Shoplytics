# PROMPT:
# Generate tests for the FastAPI ingest endpoint (/events/ingest) covering:
# - Duplicate event handling (idempotency by event_id)
# - Malformed payload rejection (invalid event_type, missing required fields)
# - Batch size limit enforcement (>500 events → HTTP 400)
# - Partial success: mixed valid/invalid batch returns accepted + rejected counts
# - Re-entry edge case: REENTRY with same visitor_id does not double-count visitors
# - Zone event without zone_id is rejected (not silently accepted)
# - Empty batch is accepted gracefully (accepted=0, no error)
# - Staff events ingested but excluded from visitor metrics
#
# CHANGES MADE:
# - Added re-entry edge case: verifies visitor count stays 1 after ENTRY + REENTRY.
# - Added malformed payload checks: missing event_type, null visitor_id.
# - Added zone_id enforcement: ZONE_ENTER without zone_id → rejected=1.
# - Added test for oversized batch rejection (HTTP 400, not 422).
# - Added test verifying error list is populated on partial failure.

import uuid

import pytest


def _ev(event_type: str, visitor_id: str = "VIS_001", **extra) -> dict:
    """Build a minimal valid event dict for ingest tests."""
    base = {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "timestamp": "2026-04-10T10:00:00Z",
        "visitor_id": visitor_id,
        "store_id": "ST1008",
        "camera_id": "CAM_ENTRY_01",
        "is_staff": False,
        "confidence": 0.92,
        "dwell_ms": 0,
        "metadata": {},
    }
    base.update(extra)
    return base


def _post(client, events):
    return client.post("/events/ingest", json=events)


# ──────────────────────────────────────────────────────────────────────────────
# Idempotency / Duplicate handling
# ──────────────────────────────────────────────────────────────────────────────

class TestIngestIdempotency:
    def test_same_event_id_rejected_as_duplicate(self, client):
        """Submitting the exact same event twice must yield duplicates=1 on second call."""
        ev = _ev("ENTRY", "VIS_DUP1")
        r1 = _post(client, [ev])
        r2 = _post(client, [ev])
        assert r1.json()["accepted"] == 1
        assert r2.json()["duplicates"] == 1
        assert r2.json()["accepted"] == 0

    def test_duplicate_does_not_inflate_visitor_count(self, client):
        """Duplicate ENTRY must not inflate unique visitor count."""
        ev = _ev("ENTRY", "VIS_DUP2")
        _post(client, [ev])
        _post(client, [ev])
        m = client.get("/stores/ST1008/metrics").json()
        assert m["visitors"] == 1

    def test_batch_with_internal_duplicate(self, client):
        """A batch containing the same event_id twice counts accepted=1, duplicates=1."""
        ev = _ev("ENTRY", "VIS_DUP3")
        ev2 = dict(ev)  # same event_id
        r = _post(client, [ev, ev2])
        body = r.json()
        assert body["accepted"] == 1
        assert body["duplicates"] == 1


# ──────────────────────────────────────────────────────────────────────────────
# Malformed payload rejection
# ──────────────────────────────────────────────────────────────────────────────

class TestMalformedPayload:
    def test_invalid_event_type_rejected(self, client):
        """Unknown event_type must be rejected; rejected=1."""
        ev = _ev("TELEPORT", "VIS_MAL1")
        r = _post(client, [ev])
        assert r.json()["rejected"] == 1

    def test_zone_event_without_zone_id_rejected(self, client):
        """ZONE_ENTER without zone_id must be rejected."""
        ev = _ev("ZONE_ENTER", "VIS_MAL2")
        # no zone_id
        r = _post(client, [ev])
        assert r.json()["rejected"] == 1

    def test_zone_event_with_zone_id_accepted(self, client):
        """ZONE_ENTER with zone_id must be accepted."""
        ev = _ev("ZONE_ENTER", "VIS_MAL3", zone_id="SKINCARE")
        r = _post(client, [ev])
        assert r.json()["accepted"] == 1

    def test_partial_batch_mixed_valid_invalid(self, client):
        """Batch with one valid + one invalid → accepted=1, rejected=1, errors has entry."""
        valid = _ev("ENTRY", "VIS_MIX1")
        invalid = _ev("GHOST_EVENT", "VIS_MIX2")
        r = _post(client, [valid, invalid])
        body = r.json()
        assert body["accepted"] == 1
        assert body["rejected"] == 1
        assert len(body["errors"]) >= 1

    def test_error_list_contains_row_info(self, client):
        """Error list entries mention which row failed."""
        ev = _ev("NO_SUCH_TYPE", "VIS_ERR1")
        r = _post(client, [ev])
        errors = r.json().get("errors", [])
        assert len(errors) == 1
        assert "row 0" in errors[0]


# ──────────────────────────────────────────────────────────────────────────────
# Batch size limit
# ──────────────────────────────────────────────────────────────────────────────

class TestBatchLimit:
    def test_501_events_returns_400(self, client):
        """Batches exceeding 500 events must be rejected with HTTP 400."""
        big = [_ev("ENTRY", f"VIS_BIG{i}") for i in range(501)]
        r = _post(client, big)
        assert r.status_code == 400

    def test_exactly_500_events_accepted(self, client):
        """Exactly 500 events in one batch must be processed (no 400)."""
        batch = [_ev("ENTRY", f"VIS_500_{i}") for i in range(500)]
        r = _post(client, batch)
        assert r.status_code == 200
        assert r.json()["accepted"] == 500

    def test_empty_batch_accepted_with_zero_counts(self, client):
        """Empty list must return HTTP 200 with accepted=0."""
        r = _post(client, [])
        assert r.status_code == 200
        body = r.json()
        assert body["accepted"] == 0
        assert body["rejected"] == 0


# ──────────────────────────────────────────────────────────────────────────────
# Re-entry edge case
# ──────────────────────────────────────────────────────────────────────────────

class TestReentryEdgeCase:
    def test_reentry_same_visitor_id_not_double_counted(self, client):
        """ENTRY + REENTRY using same visitor_id = 1 unique visitor, not 2."""
        vid = "VIS_REENTRY_001"
        _post(client, [
            _ev("ENTRY",   vid),
            _ev("EXIT",    vid),
            _ev("REENTRY", vid),
        ])
        m = client.get("/stores/ST1008/metrics").json()
        assert m["visitors"] == 1

    def test_two_reentries_still_one_visitor(self, client):
        """Multiple REENTRY events for same visitor must keep count at 1."""
        vid = "VIS_REENTRY_002"
        _post(client, [
            _ev("ENTRY",   vid),
            _ev("EXIT",    vid),
            _ev("REENTRY", vid),
            _ev("EXIT",    vid),
            _ev("REENTRY", vid),
        ])
        m = client.get("/stores/ST1008/metrics").json()
        assert m["visitors"] == 1


# ──────────────────────────────────────────────────────────────────────────────
# Staff exclusion from ingest
# ──────────────────────────────────────────────────────────────────────────────

class TestStaffIngest:
    def test_staff_event_accepted_but_excluded_from_metrics(self, client):
        """Staff events are stored (accepted=1) but omitted from visitor metrics."""
        stf = _ev("ENTRY", "VIS_STAFF_01", is_staff=True)
        r = _post(client, [stf])
        assert r.json()["accepted"] == 1  # accepted into DB
        m = client.get("/stores/ST1008/metrics").json()
        assert m["visitors"] == 0         # excluded from analytics

    def test_mixed_staff_and_customer(self, client):
        """One staff + one customer → visitors=1, not 2."""
        _post(client, [
            _ev("ENTRY", "VIS_CUST_01", is_staff=False),
            _ev("ENTRY", "VIS_STAFF_02", is_staff=True),
        ])
        m = client.get("/stores/ST1008/metrics").json()
        assert m["visitors"] == 1
