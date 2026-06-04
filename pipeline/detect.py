import sys, json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from emit import make_event
import httpx

try:
    import cv2
except ImportError:
    cv2 = None

API_BASE = "http://localhost:8000"

def post_event(event):
    try:
        httpx.post(f'{API_BASE}/events/ingest',
                   json={'events': [event]}, timeout=5.0)
    except:
        pass

def set_current_camera(camera_name):
    try:
        httpx.post(f'{API_BASE}/camera/set/{camera_name}', timeout=5.0)
    except:
        pass

# --- Config: edit these to match your video ---
STORE_ID    = "ST1008"
CAMERA_ID   = "CAM_ENTRY_01"
CLIP_START  = datetime(2026, 4, 10, 20, 10, 0)
ZONE_ID     = "ENTRANCE"          # change per camera
DWELL_THRESHOLD_SEC = 30          # emit ZONE_DWELL every 30s
STAFF_REID_WINDOW_SEC = 20
STAFF_REID_DISTANCE_PX = 140
BLACK_UNIFORM_RATIO = 0.28
PINK_UNIFORM_RATIO = 0.20
CONFIG_PATH = Path("data/challenge/store_config.json")

def load_store_config(path=CONFIG_PATH):
    if not path.exists():
        return {"stores": {}}
    with open(path, encoding="utf-8") as config_file:
        return json.load(config_file)

def store_settings(store_id):
    return load_store_config().get("stores", {}).get(store_id, {})

def configured_staff_uniforms(store_id):
    uniforms = store_settings(store_id).get("staff_uniforms")
    return set(uniforms or ["black"])

def zone_label(store_id, zone_id):
    return store_settings(store_id).get("zones", {}).get(zone_id, zone_id)

def camera_role(camera_id, zone_id):
    value = f"{camera_id} {zone_id}".upper()
    if "BILLING" in value:
        return "billing"
    if "ENTRY" in value:
        return "entry"
    return "zone"

def first_seen_event_type(role, visitor_id, exited_identities):
    if role == "entry":
        return "REENTRY" if visitor_id in exited_identities else "ENTRY"
    if role == "billing":
        return "BILLING_QUEUE_JOIN"
    return "ZONE_ENTER"

def vanished_event_type(role):
    if role == "entry":
        return "EXIT"
    if role == "billing":
        return "BILLING_QUEUE_ABANDON"
    return "ZONE_EXIT"

def bbox_center(bbox):
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) / 2, (y1 + y2) / 2)

def center_distance(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5

def torso_crop(frame, bbox):
    if cv2 is None:
        raise RuntimeError("OpenCV is required for video detection")
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = map(int, bbox)
    x1, x2 = max(0, x1), min(width, x2)
    y1, y2 = max(0, y1), min(height, y2)
    if x2 <= x1 or y2 <= y1:
        return None

    person_h = y2 - y1
    torso_y1 = y1 + int(person_h * 0.20)
    torso_y2 = y1 + int(person_h * 0.65)
    torso = frame[torso_y1:torso_y2, x1:x2]
    if torso.size == 0:
        return None
    return torso

def black_uniform_score(frame, bbox):
    torso = torso_crop(frame, bbox)
    if torso is None:
        return 0.0

    hsv = cv2.cvtColor(torso, cv2.COLOR_BGR2HSV)
    value = hsv[:, :, 2]
    saturation = hsv[:, :, 1]
    dark_uniform_pixels = (value < 65) & (saturation < 95)
    return float(dark_uniform_pixels.mean())

def is_black_uniform(frame, bbox):
    return black_uniform_score(frame, bbox) >= BLACK_UNIFORM_RATIO

def pink_uniform_score(frame, bbox):
    torso = torso_crop(frame, bbox)
    if torso is None:
        return 0.0

    hsv = cv2.cvtColor(torso, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0]
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    pink_pixels = (hue >= 145) & (hue <= 179) & (saturation > 45) & (value > 80)
    return float(pink_pixels.mean())

def is_pink_uniform(frame, bbox):
    return pink_uniform_score(frame, bbox) >= PINK_UNIFORM_RATIO

def is_staff_uniform(frame, bbox):
    uniforms = configured_staff_uniforms(STORE_ID)
    if "black" in uniforms and is_black_uniform(frame, bbox):
        return True
    if "pink" in uniforms and is_pink_uniform(frame, bbox):
        return True
    return False

def staff_identity(track_id, center, now, track_to_identity, staff_memory):
    if track_id in track_to_identity:
        identity = track_to_identity[track_id]
    else:
        identity = None
        best_distance = STAFF_REID_DISTANCE_PX
        for candidate, state in staff_memory.items():
            age = (now - state["last_seen"]).total_seconds()
            distance = center_distance(center, state["center"])
            if age <= STAFF_REID_WINDOW_SEC and distance <= best_distance:
                identity = candidate
                best_distance = distance
        if identity is None:
            identity = f"STAFF_{len(staff_memory) + 1:03d}"
        track_to_identity[track_id] = identity
    staff_memory[identity] = {"center": center, "last_seen": now}
    return identity

def detection_summary(events, raw_track_ids, staff_ids, role, output_path):
    qualified_visitor_ids = {
        ev["visitor_id"]
        for ev in events
        if not ev["is_staff"] and ev["event_type"] in {"ZONE_DWELL", "BILLING_QUEUE_JOIN"}
    }
    return {
        "store_id": STORE_ID,
        "camera_id": CAMERA_ID,
        "camera_role": role,
        "zone_id": ZONE_ID,
        "zone_label": zone_label(STORE_ID, ZONE_ID),
        "output_jsonl": str(output_path),
        "staff_uniforms": sorted(configured_staff_uniforms(STORE_ID)),
        "raw_tracks": len(raw_track_ids),
        "staff_identities": len(staff_ids),
        "qualified_visitors": len(qualified_visitor_ids),
        "excluded_short_pass_or_staff_like_tracks": max(len(raw_track_ids) - len(qualified_visitor_ids), 0),
        "event_counts": dict(Counter(ev["event_type"] for ev in events)),
    }

def write_detection_summary(summary, output_path):
    summary_path = Path(output_path).with_suffix(".summary.json")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with open(summary_path, "w", encoding="utf-8") as summary_file:
        json.dump(summary, summary_file, indent=2)
    return summary_path

def process_video(video_path, output_path="events.jsonl"):
    if cv2 is None:
        raise RuntimeError("OpenCV is required for video detection")
    from ultralytics import YOLO

    model = YOLO("yolov8n.pt")
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"ERROR: Could not open {video_path}"); return

    fps      = cap.get(cv2.CAP_PROP_FPS)
    total    = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"Processing: {video_path}  |  FPS:{fps:.1f}  |  Frames:{total}")
    set_current_camera(CAMERA_ID)
    role = camera_role(CAMERA_ID, ZONE_ID)
    print(f"Camera role: {role}")

    SAMPLE_EVERY = int(fps)   # one sample per second

    # Per-visitor state
    visitor_first_seen   = {}   # track_id -> timestamp first seen
    visitor_last_seen    = {}   # track_id -> timestamp last seen
    visitor_seq          = {}   # track_id -> event sequence counter
    visitor_dwell_emitted= {}   # track_id -> last dwell emit timestamp
    track_to_identity    = {}   # track_id -> visitor/staff identity
    track_is_staff       = {}   # track_id -> staff classification
    staff_memory         = {}   # staff identity -> last center/seen time
    exited_identities    = set()
    active_ids           = set()
    frame_num            = 0
    events               = []

    while True:
        ret, frame = cap.read()
        if not ret: break

        if frame_num % SAMPLE_EVERY == 0:
            results  = model.track(frame, classes=[0], tracker="bytetrack.yaml",
                                   persist=True, verbose=False)
            boxes    = results[0].boxes
            now      = CLIP_START + timedelta(seconds=frame_num / fps)
            seen_now = set()

            if boxes is not None and boxes.id is not None:
                track_ids   = boxes.id.int().tolist()
                confidences = boxes.conf.tolist()
                bboxes      = boxes.xyxy.tolist()

                for track_id, conf, bbox in zip(track_ids, confidences, bboxes):
                    center = bbox_center(bbox)
                    is_staff = is_staff_uniform(frame, bbox)
                    if is_staff:
                        vid = staff_identity(track_id, center, now, track_to_identity, staff_memory)
                    else:
                        vid = track_to_identity.setdefault(track_id, f"VIS_{track_id:06x}")
                    track_is_staff[track_id] = is_staff
                    seen_now.add(track_id)

                    # --- ENTRY event ---
                    if track_id not in active_ids:
                        visitor_first_seen[track_id]    = now
                        visitor_last_seen[track_id]     = now
                        visitor_seq[track_id]           = 1
                        visitor_dwell_emitted[track_id] = now
                        active_ids.add(track_id)
                        event_type = first_seen_event_type(role, vid, exited_identities)
                        queue_depth = None
                        if role == "billing" and not is_staff:
                            queue_depth = sum(
                                1 for active_track in active_ids
                                if not track_is_staff.get(active_track, False)
                            )

                        e = make_event(STORE_ID, CAMERA_ID, vid, event_type,
                                       now, zone_id=ZONE_ID,
                                       is_staff=is_staff,
                                       confidence=conf,
                                       queue_depth=queue_depth,
                                       sku_zone=ZONE_ID,
                                       session_seq=1)
                        events.append(e)
                        post_event(e)
                        person_role = "STAFF" if is_staff else "VISITOR"
                        print(f"  {event_type:<18} {vid} {person_role} @ {now.strftime('%H:%M:%S')}  conf:{conf:.2f}")

                    else:
                        visitor_last_seen[track_id] = now
                        visitor_seq[track_id]       += 1
                        if is_staff:
                            staff_memory[vid] = {"center": center, "last_seen": now}

                        # --- ZONE_DWELL every 30s ---
                        seconds_in_zone = (now - visitor_dwell_emitted[track_id]).total_seconds()
                        if role in {"zone", "billing"} and seconds_in_zone >= DWELL_THRESHOLD_SEC:
                            dwell_ms = int(seconds_in_zone * 1000)
                            e = make_event(STORE_ID, CAMERA_ID, vid, "ZONE_DWELL",
                                           now, zone_id=ZONE_ID, dwell_ms=dwell_ms,
                                           is_staff=track_is_staff.get(track_id, False),
                                           confidence=conf,
                                           session_seq=visitor_seq[track_id])
                            events.append(e)
                            post_event(e)
                            visitor_dwell_emitted[track_id] = now
                            print(f"  DWELL   {vid} @ {now.strftime('%H:%M:%S')}  {dwell_ms}ms")

            # --- EXIT for anyone who vanished ---
            vanished = active_ids - seen_now
            for track_id in vanished:
                vid      = track_to_identity.get(track_id, f"VIS_{track_id:06x}")
                exit_ts  = visitor_last_seen.get(track_id, now)
                total_ms = int((exit_ts - visitor_first_seen[track_id]).total_seconds() * 1000)
                visitor_seq[track_id] += 1
                event_type = vanished_event_type(role)

                e = make_event(STORE_ID, CAMERA_ID, vid, event_type,
                               exit_ts, zone_id=ZONE_ID, dwell_ms=total_ms,
                               is_staff=track_is_staff.get(track_id, False),
                               confidence=0.99,
                               sku_zone=ZONE_ID,
                               session_seq=visitor_seq[track_id])
                events.append(e)
                post_event(e)
                if role == "entry":
                    exited_identities.add(vid)
                print(f"  {event_type:<18} {vid} @ {exit_ts.strftime('%H:%M:%S')}  total:{total_ms}ms")
                active_ids.discard(track_id)

        frame_num += 1

    cap.release()
    qualified_visitor_ids = {
        ev["visitor_id"]
        for ev in events
        if not ev["is_staff"] and ev["event_type"] in {"ZONE_DWELL", "BILLING_QUEUE_JOIN"}
    }
    raw_track_ids = {f"VIS_{track_id:06x}" for track_id in visitor_first_seen}
    staff_ids = {ev["visitor_id"] for ev in events if ev["is_staff"]}
    excluded_track_count = max(len(raw_track_ids) - len(qualified_visitor_ids), 0)

    # Save to .jsonl (one JSON object per line)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")

    summary = detection_summary(events, raw_track_ids, staff_ids, role, output_path)
    summary_path = write_detection_summary(summary, output_path)

    print(f"\nDone! {len(events)} events saved to {output_path}")
    print(f"Summary: {summary_path}")
    print(f"Raw tracked people: {len(raw_track_ids)}")
    print(f"Uniform staff identities: {len(staff_ids)}")
    print(f"Qualified visitors: {len(qualified_visitor_ids)}")
    print(f"Excluded short-pass/staff-like tracks: {excluded_track_count}")

if __name__ == "__main__":
    video_path  = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else "events.jsonl"
    if len(sys.argv) > 3:
        CAMERA_ID = sys.argv[3]
    if len(sys.argv) > 4:
        ZONE_ID = sys.argv[4]
    if len(sys.argv) > 5:
        API_BASE = sys.argv[5].rstrip("/")
    if len(sys.argv) > 6:
        STORE_ID = sys.argv[6]
    process_video(video_path, output_file)
