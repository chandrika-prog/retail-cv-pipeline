# PROMPT: Generate pytest tests for the Store Intelligence challenge API after
# adding Store 1/Store 2 support, sample event normalization, heatmap, recent
# events, staff exclusion, idempotency, and POS store mapping.
# CHANGES MADE: Replaced legacy ST1008-only tests with challenge-current tests,
# added a FastAPI dependency override so tests use an isolated SQLite DB, and
# asserted store-specific behavior for metrics, recent events, and POS event IDs.

import os
import sys
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, ROOT)

import main
from main import app
from models import Base, Event
from load_pos import make_event_id


TEST_DB_URL = "sqlite:///./test_store_intelligence.db"
engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[main.get_db] = override_get_db
client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def event(
    event_type="ZONE_DWELL",
    store_id="ST1",
    visitor_id="VIS_1",
    zone_id="ZONE_01",
    is_staff=False,
    event_id=None,
    queue_depth=None,
    dwell_ms=30000,
    timestamp="2026-04-10T20:10:00Z",
):
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": "CAM_TEST",
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": timestamp,
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": 0.91,
        "metadata": {
            "queue_depth": queue_depth,
            "sku_zone": zone_id,
            "session_seq": 1,
        },
    }


def pos_event(
    store_id="ST1",
    timestamp="2026-04-10T20:14:00Z",
    event_id="pos-1",
):
    return {
        "event_id": event_id,
        "store_id": store_id,
        "camera_id": "POS",
        "visitor_id": f"TXN_{event_id}",
        "event_type": "POS_TRANSACTION",
        "timestamp": timestamp,
        "zone_id": "BILLING",
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 1.0,
        "metadata": {"queue_depth": None, "sku_zone": None, "session_seq": 1},
    }


def ingest(events):
    return client.post("/events/ingest", json={"events": events})


def test_ingest_normalizes_challenge_sample_schema():
    raw = {
        "event_type": "zone_entered",
        "track_id": 101,
        "store_id": "ST1076",
        "camera_id": "cam2",
        "zone_id": "PURPLLE_MUM_1076_Z01",
        "zone_name": "Left Shelf",
        "event_time": "2026-03-08T18:10:45.280000",
        "is_revenue_zone": "Yes",
        "gender": "F",
        "age": 28,
    }

    response = ingest([raw])

    assert response.status_code == 200
    assert response.json()["inserted"] == 1
    recent = client.get("/stores/ST1076/events/recent").json()["events"]
    assert recent[0]["event_type"] == "ZONE_ENTER"
    assert recent[0]["visitor_id"] == "VIS_101"
    assert recent[0]["camera_id"] == "CAM2"


def test_store_code_normalizes_to_st_prefix():
    raw = {
        "event_type": "entry",
        "id_token": "ID_60001",
        "store_code": "store_1076",
        "camera_id": "cam1",
        "event_timestamp": "2026-03-08T18:10:05.120000",
        "is_staff": False,
    }

    ingest([raw])

    assert client.get("/stores/ST1076/events/recent").json()["events"][0]["store_id"] == "ST1076"


def test_duplicate_events_are_idempotent_by_event_id():
    ev = event(event_id="fixed-event-id")

    first = ingest([ev]).json()
    second = ingest([ev]).json()

    assert first["inserted"] == 1
    assert second["inserted"] == 0
    assert second["skipped"] == 1


def test_raw_schema_without_event_id_is_idempotent_by_deterministic_hash():
    raw = {
        "event_type": "queue_completed",
        "queue_event_id": "Q_1",
        "track_id": 42,
        "store_id": "ST1",
        "camera_id": "billing",
        "queue_join_ts": "2026-04-10T20:11:00",
        "queue_position_at_join": 2,
        "wait_seconds": 65,
    }

    first = ingest([raw]).json()
    second = ingest([raw]).json()

    assert first["inserted"] == 1
    assert second["skipped"] == 1


def test_malformed_event_returns_partial_success():
    good = event(event_id="valid")
    bad = {**event(event_id="bad"), "confidence": "not-a-number"}

    result = ingest([good, bad]).json()

    assert result["inserted"] == 1
    assert len(result["errors"]) == 1


def test_metrics_exclude_staff_and_count_qualified_visitors():
    events = [
        event(visitor_id="VIS_CUSTOMER", is_staff=False),
        event(visitor_id="STAFF_001", is_staff=True),
    ]
    ingest(events)

    metrics = client.get("/stores/ST1/metrics").json()

    assert metrics["unique_visitors"] == 1


def test_store_metrics_are_isolated_between_store_1_and_store_2():
    ingest([
        event(store_id="ST1", visitor_id="VIS_ST1"),
        event(store_id="ST2", visitor_id="VIS_ST2_A"),
        event(store_id="ST2", visitor_id="VIS_ST2_B"),
    ])

    assert client.get("/stores/ST1/metrics").json()["unique_visitors"] == 1
    assert client.get("/stores/ST2/metrics").json()["unique_visitors"] == 2


def test_funnel_uses_distinct_sessions_not_raw_duplicate_events():
    ingest([
        event(event_type="ZONE_DWELL", visitor_id="VIS_REPEAT", event_id="dwell-1"),
        event(event_type="ZONE_DWELL", visitor_id="VIS_REPEAT", event_id="dwell-2"),
        event(event_type="BILLING_QUEUE_JOIN", visitor_id="VIS_REPEAT", event_id="bill-1", queue_depth=1),
    ])

    funnel = client.get("/stores/ST1/funnel").json()["funnel"]

    assert funnel[0]["visitors"] == 1
    assert funnel[1]["visitors"] == 1
    assert funnel[2]["visitors"] == 1


def test_funnel_drop_off_never_goes_negative_when_cameras_have_partial_tracks():
    ingest([
        event(event_type="ZONE_DWELL", visitor_id="VIS_ZONE", event_id="zone-only"),
        event(event_type="BILLING_QUEUE_JOIN", visitor_id="VIS_BILL_1", event_id="bill-1", queue_depth=1),
        event(event_type="BILLING_QUEUE_JOIN", visitor_id="VIS_BILL_2", event_id="bill-2", queue_depth=2),
    ])

    funnel = client.get("/stores/ST1/funnel").json()["funnel"]

    assert funnel[2]["visitors"] == 2
    assert funnel[2]["drop_off_pct"] == 0.0


def test_heatmap_returns_normalized_zone_intensity_and_confidence():
    ingest([
        event(zone_id="ZONE_A", event_id="a1"),
        event(zone_id="ZONE_A", visitor_id="VIS_2", event_id="a2"),
        event(zone_id="ZONE_B", visitor_id="VIS_3", event_id="b1"),
    ])

    heatmap = client.get("/stores/ST1/heatmap").json()
    zones = {zone["zone_id"]: zone for zone in heatmap["zones"]}

    assert heatmap["data_confidence"] == "LOW"
    assert zones["ZONE_A"]["intensity"] == 100.0
    assert zones["ZONE_B"]["intensity"] == 50.0


def test_heatmap_includes_layout_zone_metadata():
    ingest([event(store_id="ST1", zone_id="ZONE_01", event_id="layout-zone")])

    heatmap = client.get("/stores/ST1/heatmap").json()
    zone = heatmap["zones"][0]

    assert zone["zone_id"] == "ZONE_01"
    assert zone["zone_name"] == "Main Floor Zone 1"
    assert zone["zone_type"] == "SHELF"
    assert zone["camera_ids"] == ["STORE1_ZONE_01"]
    assert zone["is_revenue_zone"] is True


def test_recent_events_are_store_specific():
    ingest([
        event(store_id="ST1", visitor_id="VIS_ST1", event_id="st1"),
        event(store_id="ST2", visitor_id="VIS_ST2", event_id="st2"),
    ])

    st1_events = client.get("/stores/ST1/events/recent").json()["events"]
    st2_events = client.get("/stores/ST2/events/recent").json()["events"]

    assert [ev["store_id"] for ev in st1_events] == ["ST1"]
    assert [ev["store_id"] for ev in st2_events] == ["ST2"]


def test_empty_store_returns_zero_metrics_and_empty_heatmap():
    metrics = client.get("/stores/EMPTY/metrics").json()
    heatmap = client.get("/stores/EMPTY/heatmap").json()
    recent = client.get("/stores/EMPTY/events/recent").json()

    assert metrics["unique_visitors"] == 0
    assert metrics["conversion_rate"] == 0.0
    assert heatmap["zones"] == []
    assert recent["events"] == []


def test_pos_event_ids_are_unique_per_target_store_and_idempotent_per_store():
    row = {"order_id": "1", "product_id": "399945"}

    st1_first = make_event_id(row, 1, "ST1")
    st1_second = make_event_id(row, 1, "ST1")
    st2 = make_event_id(row, 1, "ST2")

    assert st1_first == st1_second
    assert st1_first != st2


def test_pos_transaction_with_billing_visitor_inside_five_minutes_counts_purchase():
    ingest([
        event(
            event_type="BILLING_QUEUE_JOIN",
            visitor_id="VIS_BUYER",
            event_id="bill-buyer",
            timestamp="2026-04-10T20:10:00Z",
            queue_depth=1,
            dwell_ms=0,
        ),
        pos_event(timestamp="2026-04-10T20:14:30Z"),
    ])

    metrics = client.get("/stores/ST1/metrics").json()
    funnel = client.get("/stores/ST1/funnel").json()["funnel"]

    assert metrics["purchases"] == 1
    assert metrics["conversion_rate"] == 1.0
    assert funnel[-1]["stage"] == "Purchase"
    assert funnel[-1]["visitors"] == 1


def test_pos_transaction_outside_five_minutes_does_not_convert():
    ingest([
        event(
            event_type="BILLING_QUEUE_JOIN",
            visitor_id="VIS_TOO_EARLY",
            event_id="bill-early",
            timestamp="2026-04-10T20:00:00Z",
            queue_depth=1,
            dwell_ms=0,
        ),
        pos_event(timestamp="2026-04-10T20:10:01Z"),
    ])

    metrics = client.get("/stores/ST1/metrics").json()

    assert metrics["purchases"] == 0
    assert metrics["conversion_rate"] == 0.0


def test_staff_billing_activity_does_not_convert_pos_transaction():
    ingest([
        event(
            event_type="BILLING_QUEUE_JOIN",
            visitor_id="STAFF_001",
            event_id="staff-bill",
            timestamp="2026-04-10T20:10:00Z",
            queue_depth=1,
            dwell_ms=0,
            is_staff=True,
        ),
        pos_event(timestamp="2026-04-10T20:12:00Z"),
    ])

    metrics = client.get("/stores/ST1/metrics").json()

    assert metrics["unique_visitors"] == 0
    assert metrics["purchases"] == 0


def test_pos_loader_event_shape_is_transaction_not_billing_join():
    row = {"order_id": "1", "product_id": "399945"}
    event_id = make_event_id(row, 1, "ST1")

    assert event_id.startswith("POS_")
    assert make_event_id(row, 1, "ST1") != make_event_id(row, 1, "ST2")


def test_metrics_include_queue_depth_and_abandonment_rate():
    ingest([
        event(
            event_type="BILLING_QUEUE_JOIN",
            visitor_id="VIS_A",
            event_id="join-a",
            queue_depth=2,
            dwell_ms=0,
        ),
        event(
            event_type="BILLING_QUEUE_JOIN",
            visitor_id="VIS_B",
            event_id="join-b",
            queue_depth=4,
            dwell_ms=0,
        ),
        event(
            event_type="BILLING_QUEUE_ABANDON",
            visitor_id="VIS_B",
            event_id="abandon-b",
            queue_depth=4,
            dwell_ms=0,
        ),
    ])

    metrics = client.get("/stores/ST1/metrics").json()

    assert metrics["queue_depth"] == 4
    assert metrics["abandonment_rate"] == 0.5


def test_anomalies_detect_queue_spike():
    ingest([
        event(
            event_type="BILLING_QUEUE_JOIN",
            visitor_id="VIS_QUEUE",
            event_id="queue-spike",
            queue_depth=5,
            dwell_ms=0,
        )
    ])

    anomalies = client.get("/stores/ST1/anomalies").json()["anomalies"]
    types = {item["type"] for item in anomalies}

    assert "BILLING_QUEUE_SPIKE" in types


def test_health_lists_store_and_stale_feed_status():
    ingest([event(store_id="ST1", visitor_id="VIS_HEALTH", event_id="health-event")])

    health = client.get("/health").json()
    stores = {item["store_id"]: item for item in health["stores"]}

    assert health["status"] == "healthy"
    assert "ST1" in stores
    assert stores["ST1"]["status"] == "STALE_FEED"


def test_event_type_mapping_is_case_insensitive():
    raw = {
        "event_type": "Queue_Abandoned",
        "queue_event_id": "Q_ABANDON",
        "track_id": 55,
        "store_id": "ST2",
        "camera_id": "billing_area",
        "queue_join_ts": "2026-04-10T20:11:00",
        "queue_exit_ts": "2026-04-10T20:12:00",
        "queue_position_at_join": 3,
        "wait_seconds": 60,
    }

    ingest([raw])
    recent = client.get("/stores/ST2/events/recent").json()["events"]

    assert recent[0]["event_type"] == "BILLING_QUEUE_ABANDON"
    assert recent[0]["dwell_ms"] == 60000
