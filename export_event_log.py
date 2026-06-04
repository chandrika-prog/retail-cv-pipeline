import argparse
import glob
import hashlib
import json
from pathlib import Path


EVENT_TYPE_MAP = {
    "ENTRY": "entry",
    "EXIT": "exit",
    "REENTRY": "reentry",
    "ZONE_ENTER": "zone_entered",
    "ZONE_EXIT": "zone_exited",
    "ZONE_DWELL": "zone_dwell",
    "BILLING_QUEUE_JOIN": "queue_completed",
    "BILLING_QUEUE_ABANDON": "queue_abandoned",
}


ZONE_NAMES = {
    "ENTRY": "Entry",
    "ZONE_01": "Main Floor Zone 1",
    "ZONE_02": "Main Floor Zone 2",
    "BILLING": "Billing Counter",
}


def clean_ts(timestamp: str) -> str:
    return timestamp.replace("Z", "")


def as_track_id(visitor_id: str) -> int:
    digits = "".join(ch for ch in visitor_id if ch.isdigit())
    if digits:
        return int(digits)
    return int(hashlib.sha1(visitor_id.encode("utf-8")).hexdigest()[:8], 16) % 100000


def convert_event(event: dict) -> dict:
    event_type = EVENT_TYPE_MAP.get(event["event_type"], event["event_type"].lower())
    zone_id = event.get("zone_id")
    metadata = event.get("metadata") or {}
    base = {
        "event_type": event_type,
        "store_id": event["store_id"],
        "camera_id": event["camera_id"],
        "is_staff": bool(event.get("is_staff", False)),
        "confidence": event.get("confidence", 1.0),
    }

    if event_type in {"entry", "exit", "reentry"}:
        base.update({
            "id_token": event["visitor_id"],
            "store_code": event["store_id"],
            "event_timestamp": clean_ts(event["timestamp"]),
            "gender_pred": None,
            "age_pred": None,
            "age_bucket": None,
            "is_face_hidden": True,
            "group_id": None,
            "group_size": None,
        })
        return base

    if event_type.startswith("zone_"):
        base.update({
            "track_id": as_track_id(event["visitor_id"]),
            "zone_id": zone_id,
            "zone_name": ZONE_NAMES.get(zone_id, zone_id),
            "zone_type": "BILLING" if zone_id == "BILLING" else "SHELF",
            "is_revenue_zone": "Yes" if zone_id != "ENTRY" else "No",
            "event_time": clean_ts(event["timestamp"]),
            "zone_hotspot_x": None,
            "zone_hotspot_y": None,
            "gender": None,
            "age": None,
            "age_bucket": None,
            "dwell_ms": event.get("dwell_ms", 0),
        })
        return base

    if event_type.startswith("queue_"):
        base.update({
            "queue_event_id": event["event_id"],
            "track_id": as_track_id(event["visitor_id"]),
            "zone_id": zone_id or "BILLING",
            "queue_join_ts": clean_ts(event["timestamp"]),
            "queue_exit_ts": clean_ts(event["timestamp"]),
            "queue_position_at_join": metadata.get("queue_depth"),
            "wait_seconds": round((event.get("dwell_ms") or 0) / 1000, 3),
        })
        return base

    return {**base, **event}


def main() -> None:
    parser = argparse.ArgumentParser(description="Export detector outputs as challenge-style event log JSONL.")
    parser.add_argument("--input-glob", default="outputs/*.jsonl")
    parser.add_argument("--output", default="deliverables/final_event_log.jsonl")
    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for path in sorted(glob.glob(args.input_glob)):
        with open(path, encoding="utf-8") as source:
            for line in source:
                if line.strip():
                    rows.append(convert_event(json.loads(line)))

    with open(output_path, "w", encoding="utf-8", newline="\n") as target:
        for row in rows:
            target.write(json.dumps(row, separators=(",", ":")) + "\n")

    print(f"wrote {len(rows)} events to {output_path}")


if __name__ == "__main__":
    main()
