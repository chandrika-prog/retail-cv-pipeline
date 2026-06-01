import subprocess
import sys
import httpx

cameras = [
    {"video": "data/clips/CAM 1.mp4", "output": "events_cam1.jsonl", "camera_id": "CAM_FLOOR_01",   "zone": "FOH",          "stream": "CAM 1"},
    {"video": "data/clips/CAM 2.mp4", "output": "events_cam2.jsonl", "camera_id": "CAM_PRODUCT_01", "zone": "PRODUCT_ZONE", "stream": "CAM 2"},
    {"video": "data/clips/CAM 3.mp4", "output": "events_cam3.jsonl", "camera_id": "CAM_BILLING_01", "zone": "BILLING",      "stream": "CAM 3"},
    {"video": "data/clips/CAM 4.mp4", "output": "events_cam4.jsonl", "camera_id": "CAM_ENTRY_01",   "zone": "ENTRY",        "stream": "CAM 4"},
    {"video": "data/clips/CAM 5.mp4", "output": "events_cam5.jsonl", "camera_id": "CAM_MAKEUP_01",  "zone": "MAKEUP_UNIT",  "stream": "CAM 5"},
]

for cam in cameras:
    print(f"\nProcessing {cam['video']} → {cam['zone']}...")
    # Tell API which video file to stream
    try:
        httpx.post(f"http://localhost:8000/camera/set/{cam['stream']}", timeout=5.0)
    except:
        pass
    subprocess.run([
        sys.executable, "pipeline/detect.py",
        cam["video"], cam["output"],
        cam["camera_id"], cam["zone"]
    ])

print("\nAll cameras processed!")