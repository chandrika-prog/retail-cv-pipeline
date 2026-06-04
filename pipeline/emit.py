import hashlib
import uuid
from datetime import datetime, timedelta

def deterministic_event_id(*parts):
    key = "|".join(str(part) for part in parts)
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return str(uuid.UUID(digest[:32]))

def make_event(
    store_id,
    camera_id,
    visitor_id,
    event_type,
    timestamp,
    zone_id=None,
    dwell_ms=0,
    is_staff=False,
    confidence=1.0,
    queue_depth=None,
    sku_zone=None,
    session_seq=1
):
    timestamp_str = timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "event_id": deterministic_event_id(
            store_id, camera_id, visitor_id, event_type,
            timestamp_str, zone_id, session_seq
        ),
        "store_id": store_id,
        "camera_id": camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": timestamp_str,
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": round(confidence, 3),
        "metadata": {
            "queue_depth": queue_depth,
            "sku_zone": sku_zone,
            "session_seq": session_seq
        }
    }
