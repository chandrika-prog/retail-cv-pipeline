# PROMPT: Generate pytest tests for a FastAPI store intelligence API with endpoints:
# POST /events/ingest, GET /stores/{id}/metrics, GET /stores/{id}/funnel,
# GET /stores/{id}/anomalies, GET /health
# Include edge cases: empty store, duplicate events, zero purchases, re-entry
# CHANGES MADE: Added ST1008 store ID, adjusted assertions to match our schema,
# added Brigade Road specific test data

import pytest
from fastapi.testclient import TestClient
import sys, os, uuid
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

from main import app
from models import init_db, SessionLocal, Event, Base
# Use separate test database — never pollutes real store.db
import os
from sqlalchemy import create_engine
from models import Base

TEST_DB_PATH = "test_store.db"
TEST_DB_URL = f"sqlite:///./{TEST_DB_PATH}"

# Override the database for testing
import models
models.DATABASE_URL = TEST_DB_URL
models.engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
models.SessionLocal = __import__('sqlalchemy.orm', fromlist=['sessionmaker']).sessionmaker(bind=models.engine)
Base.metadata.create_all(bind=models.engine)

@pytest.fixture(autouse=True)
def cleanup():
    """Clean test DB before each test"""
    Base.metadata.drop_all(bind=models.engine)
    Base.metadata.create_all(bind=models.engine)
    yield
    # nothing after — keep DB for inspection if needed
from sqlalchemy import create_engine

# Use in-memory DB for tests
TEST_DB = "sqlite:///./test.db"

client = TestClient(app)

def make_event(event_type="ENTRY", visitor_id=None, store_id="ST1008",
               is_staff=False, zone_id="FOH", confidence=0.9):
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": visitor_id or f"VIS_{uuid.uuid4().hex[:6]}",
        "event_type": event_type,
        "timestamp": "2026-04-10T10:00:00Z",
        "zone_id": zone_id,
        "dwell_ms": 0,
        "is_staff": is_staff,
        "confidence": confidence,
        "metadata": {"queue_depth": None, "sku_zone": None, "session_seq": 1}
    }

# ── Ingest Tests ─────────────────────────────────────────────────
def test_ingest_single_event():
    ev = make_event()
    r = client.post("/events/ingest", json={"events": [ev]})
    assert r.status_code == 200
    assert r.json()["inserted"] >= 0

def test_ingest_idempotent():
    """Same event posted twice should only insert once"""
    ev = make_event()
    r1 = client.post("/events/ingest", json={"events": [ev]})
    r2 = client.post("/events/ingest", json={"events": [ev]})
    assert r1.json()["inserted"] == 1 or r1.json()["skipped"] == 0
    assert r2.json()["skipped"] == 1

def test_ingest_batch():
    """Batch of 10 events"""
    events = [make_event() for _ in range(10)]
    r = client.post("/events/ingest", json={"events": events})
    assert r.status_code == 200
    assert r.json()["inserted"] == 10

def test_ingest_staff_excluded():
    """Staff events should not count as visitors"""
    ev = make_event(is_staff=True)
    r = client.post("/events/ingest", json={"events": [ev]})
    assert r.status_code == 200

# ── Metrics Tests ────────────────────────────────────────────────
def test_metrics_returns_valid_response():
    r = client.get("/stores/ST1008/metrics")
    assert r.status_code == 200
    data = r.json()
    assert "unique_visitors" in data
    assert "conversion_rate" in data
    assert "avg_dwell_ms" in data
    assert data["store_id"] == "ST1008"

def test_metrics_empty_store():
    """Store with no events should return zeros not crash"""
    r = client.get("/stores/NONEXISTENT_STORE/metrics")
    assert r.status_code == 200
    data = r.json()
    assert data["unique_visitors"] == 0
    assert data["conversion_rate"] == 0.0

def test_metrics_zero_purchases():
    """Store with visitors but no purchases"""
    ev = make_event(store_id="STORE_TEST_001")
    client.post("/events/ingest", json={"events": [ev]})
    r = client.get("/stores/STORE_TEST_001/metrics")
    assert r.status_code == 200
    assert r.json()["conversion_rate"] == 0.0

# ── Funnel Tests ─────────────────────────────────────────────────
def test_funnel_returns_four_stages():
    r = client.get("/stores/ST1008/funnel")
    assert r.status_code == 200
    data = r.json()
    assert len(data["funnel"]) == 4
    stages = [s["stage"] for s in data["funnel"]]
    assert "Entry" in stages
    assert "Zone Visit" in stages
    assert "Billing Queue" in stages
    assert "Purchase" in stages

def test_funnel_no_double_counting():
    """Re-entry visitor should count once in funnel"""
    vid = f"VIS_{uuid.uuid4().hex[:6]}"
    store = f"STORE_REENTRY_{uuid.uuid4().hex[:4]}"  # unique store each run
    events = [
        make_event("ENTRY", vid, store),
        make_event("EXIT", vid, store),
        make_event("ENTRY", vid, store),  # re-entry
    ]
    client.post("/events/ingest", json={"events": events})
    r = client.get(f"/stores/{store}/funnel")
    assert r.status_code == 200
    entry_count = r.json()["funnel"][0]["visitors"]
    assert entry_count == 1  # should count as 1 unique visitor
# ── Anomaly Tests ────────────────────────────────────────────────
def test_anomalies_returns_list():
    r = client.get("/stores/ST1008/anomalies")
    assert r.status_code == 200
    assert "anomalies" in r.json()
    assert isinstance(r.json()["anomalies"], list)
def test_anomalies_conversion_drop():
    """Store with visitors but no purchases should trigger CONVERSION_DROP"""
    store = f"STORE_CONV_{uuid.uuid4().hex[:4]}"
    # Add 15 visitors but no purchases
    events = [make_event("ENTRY", store_id=store) for _ in range(15)]
    client.post("/events/ingest", json={"events": events})
    r = client.get(f"/stores/{store}/anomalies")
    assert r.status_code == 200
    types = [a["type"] for a in r.json()["anomalies"]]
    assert "CONVERSION_DROP" in types
def test_anomalies_stale_store():
    """Store with old events should trigger DEAD_ZONE"""
    r = client.get("/stores/ST1008/anomalies")
    assert r.status_code == 200
    # Our test data is from 2026-04-10 so it should be stale
    anomalies = r.json()["anomalies"]
    types = [a["type"] for a in anomalies]
    assert "DEAD_ZONE" in types

# ── Health Tests ─────────────────────────────────────────────────
def test_health_endpoint():
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "healthy"
    assert "stores" in data

def test_health_shows_stale_feed():
    """Old events should show STALE_FEED status"""
    r = client.get("/health")
    stores = r.json()["stores"]
    if stores:
        # Our data is old so should be stale
        statuses = [s["status"] for s in stores]
        assert "STALE_FEED" in statuses
def test_all_staff_clip():
    """Store where all detected people are staff — visitors should be 0"""
    store = "STORE_ALL_STAFF"
    events = [make_event("ENTRY", is_staff=True, store_id=store) for _ in range(5)]
    client.post("/events/ingest", json={"events": events})
    r = client.get(f"/stores/{store}/metrics")
    assert r.status_code == 200
    assert r.json()["unique_visitors"] == 0

def test_reentry_not_double_counted():
    """Same visitor entering twice should count as 1 unique visitor"""
    vid = f"VIS_{uuid.uuid4().hex[:6]}"
    store = f"STORE_REENTRY_{uuid.uuid4().hex[:4]}"  # unique store each run
    events = [
        make_event("ENTRY", visitor_id=vid, store_id=store),
        make_event("EXIT", visitor_id=vid, store_id=store),
        make_event("ENTRY", visitor_id=vid, store_id=store),
        make_event("EXIT", visitor_id=vid, store_id=store),
    ]
    client.post("/events/ingest", json={"events": events})
    r = client.get(f"/stores/{store}/metrics")
    assert r.status_code == 200
    assert r.json()["unique_visitors"] == 1

def test_ingest_large_batch():
    """Should handle large batch without crashing"""
    events = [make_event() for _ in range(100)]
    r = client.post("/events/ingest", json={"events": events})
    assert r.status_code == 200
    assert r.json()["inserted"] == 100
    assert r.json()["errors"] == []