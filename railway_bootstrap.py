import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "app"))

from load_pos import iso_timestamp, make_event_id
from main import normalize_event
from models import Event, SessionLocal, init_db


EVENT_LOG_PATH = Path("deliverables/final_event_log.jsonl")
POS_PATH = Path("data/challenge/pos_transactions.csv")


def insert_event(db, raw: dict) -> bool:
    event = normalize_event(raw)
    if db.query(Event.event_id).filter(Event.event_id == event.event_id).first():
        return False
    db.add(
        Event(
            event_id=event.event_id,
            store_id=event.store_id,
            camera_id=event.camera_id,
            visitor_id=event.visitor_id,
            event_type=event.event_type,
            timestamp=event.timestamp,
            zone_id=event.zone_id,
            dwell_ms=event.dwell_ms,
            is_staff=event.is_staff,
            confidence=event.confidence,
            queue_depth=event.metadata.queue_depth,
            session_seq=event.metadata.session_seq,
        )
    )
    return True


def seed_event_log(db) -> int:
    inserted = 0
    with EVENT_LOG_PATH.open(encoding="utf-8") as event_file:
        for line in event_file:
            if line.strip():
                inserted += insert_event(db, json.loads(line))
    return inserted


def seed_pos(db) -> int:
    inserted = 0
    with POS_PATH.open(newline="", encoding="utf-8") as csv_file:
        rows = list(csv.DictReader(csv_file))

    for store_id in ("ST1", "ST2"):
        for row_number, row in enumerate(rows, start=1):
            order_id = row.get("order_id") or row.get("transaction_id") or row_number
            raw = {
                "event_id": make_event_id(row, row_number, store_id),
                "store_id": store_id,
                "camera_id": "POS",
                "visitor_id": f"TXN_{store_id}_{order_id}",
                "event_type": "POS_TRANSACTION",
                "timestamp": iso_timestamp(row),
                "zone_id": "BILLING",
                "dwell_ms": 0,
                "is_staff": False,
                "confidence": 1.0,
                "metadata": {
                    "queue_depth": None,
                    "sku_zone": row.get("brand_name", ""),
                    "session_seq": 1,
                },
            }
            inserted += insert_event(db, raw)
    return inserted


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        event_count = seed_event_log(db)
        pos_count = seed_pos(db)
        db.commit()
        print(f"Railway seed complete: {event_count} events, {pos_count} POS rows inserted")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
