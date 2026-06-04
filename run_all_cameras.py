import argparse
import importlib.util
from pathlib import Path
import subprocess
import sys

import httpx


cameras = [
    {"store_id": "ST1", "video": "data/challenge/Store 1/CAM 3 - entry.mp4", "output": "outputs/events_store1_entry.jsonl", "camera_id": "STORE1_ENTRY_01", "zone": "ENTRY", "stream": "Store 1 Entry"},
    {"store_id": "ST1", "video": "data/challenge/Store 1/CAM 1 - zone.mp4", "output": "outputs/events_store1_zone1.jsonl", "camera_id": "STORE1_ZONE_01", "zone": "ZONE_01", "stream": "Store 1 Zone 1"},
    {"store_id": "ST1", "video": "data/challenge/Store 1/CAM 2 - zone.mp4", "output": "outputs/events_store1_zone2.jsonl", "camera_id": "STORE1_ZONE_02", "zone": "ZONE_02", "stream": "Store 1 Zone 2"},
    {"store_id": "ST1", "video": "data/challenge/Store 1/CAM 5 - billing.mp4", "output": "outputs/events_store1_billing.jsonl", "camera_id": "STORE1_BILLING_01", "zone": "BILLING", "stream": "Store 1 Billing"},
    {"store_id": "ST2", "video": "data/challenge/Store 2/entry 1.mp4", "output": "outputs/events_store2_entry1.jsonl", "camera_id": "STORE2_ENTRY_01", "zone": "ENTRY", "stream": "Store 2 Entry 1"},
    {"store_id": "ST2", "video": "data/challenge/Store 2/entry 2.mp4", "output": "outputs/events_store2_entry2.jsonl", "camera_id": "STORE2_ENTRY_02", "zone": "ENTRY", "stream": "Store 2 Entry 2"},
    {"store_id": "ST2", "video": "data/challenge/Store 2/zone.mp4", "output": "outputs/events_store2_zone.jsonl", "camera_id": "STORE2_ZONE_01", "zone": "ZONE_01", "stream": "Store 2 Zone"},
    {"store_id": "ST2", "video": "data/challenge/Store 2/billing_area.mp4", "output": "outputs/events_store2_billing.jsonl", "camera_id": "STORE2_BILLING_01", "zone": "BILLING", "stream": "Store 2 Billing"},
]


def require_detection_deps() -> bool:
    missing = [
        package for package in ("ultralytics", "cv2")
        if importlib.util.find_spec(package) is None
    ]
    if missing:
        print("Missing detection dependencies:", ", ".join(missing))
        print("Install them with:")
        print("  python -m pip install ultralytics opencv-python")
        return False
    return True


def normalize_api_base(api: str) -> str:
    api_base = api.rstrip("/")
    if api_base.endswith("/events/ingest"):
        api_base = api_base.removesuffix("/events/ingest")
    return api_base


def main():
    parser = argparse.ArgumentParser(description="Run people detection for all camera clips.")
    parser.add_argument("--api", default="http://127.0.0.1:8001")
    args = parser.parse_args()
    api_base = normalize_api_base(args.api)
    Path("outputs").mkdir(exist_ok=True)

    if not require_detection_deps():
        sys.exit(1)

    missing_clips = [cam["video"] for cam in cameras if not Path(cam["video"]).exists()]
    if missing_clips:
        print("Missing camera clips:")
        for clip in missing_clips:
            print(f"  {clip}")
        print("Extract the new Store 1 and Store 2 ZIP files under data/challenge/.")
        sys.exit(1)

    failures = 0
    for cam in cameras:
        print(f"\nProcessing {cam['video']} -> {cam['zone']}...")
        try:
            httpx.post(f"{api_base}/camera/set/{cam['stream']}", timeout=5.0)
        except Exception as exc:
            print(f"Could not update current camera on API: {exc}")

        result = subprocess.run([
            sys.executable,
            "pipeline/detect.py",
            cam["video"],
            cam["output"],
            cam["camera_id"],
            cam["zone"],
            api_base,
            cam["store_id"],
        ])
        if result.returncode != 0:
            failures += 1

    if failures:
        print(f"\nFinished with {failures} failed camera(s).")
        sys.exit(1)

    print("\nAll cameras processed!")


if __name__ == "__main__":
    main()
