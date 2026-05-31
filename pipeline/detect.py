from ultralytics import YOLO
import cv2, sys, json
from datetime import datetime, timedelta
from emit import make_event
import httpx

def post_event(event):
    try:
        httpx.post('http://localhost:8000/events/ingest', 
                   json={'events': [event]}, timeout=5.0)
    except:
        pass  # don't crash detection if API is down

model = YOLO("yolov8n.pt")

# --- Config: edit these to match your video ---
STORE_ID    = "ST1008"
CAMERA_ID   = "CAM_ENTRY_01"
CLIP_START  = datetime(2026, 3, 3, 10, 0, 0)
ZONE_ID     = "ENTRANCE"          # change per camera
DWELL_THRESHOLD_SEC = 30          # emit ZONE_DWELL every 30s

def process_video(video_path, output_path="events.jsonl"):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"ERROR: Could not open {video_path}"); return

    fps      = cap.get(cv2.CAP_PROP_FPS)
    total    = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"Processing: {video_path}  |  FPS:{fps:.1f}  |  Frames:{total}")

    SAMPLE_EVERY = int(fps)   # one sample per second

    # Per-visitor state
    visitor_first_seen   = {}   # track_id -> timestamp first seen
    visitor_last_seen    = {}   # track_id -> timestamp last seen
    visitor_seq          = {}   # track_id -> event sequence counter
    visitor_dwell_emitted= {}   # track_id -> last dwell emit timestamp
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

                for track_id, conf in zip(track_ids, confidences):
                    vid = f"VIS_{track_id:06x}"
                    seen_now.add(track_id)

                    # --- ENTRY event ---
                    if track_id not in active_ids:
                        visitor_first_seen[track_id]    = now
                        visitor_last_seen[track_id]     = now
                        visitor_seq[track_id]           = 1
                        visitor_dwell_emitted[track_id] = now
                        active_ids.add(track_id)

                        e = make_event(STORE_ID, CAMERA_ID, vid, "ENTRY",
                                       now, zone_id=ZONE_ID,
                                       confidence=conf, session_seq=1)
                        events.append(e)
                        post_event(e)
                        print(f"  ENTRY   {vid} @ {now.strftime('%H:%M:%S')}  conf:{conf:.2f}")

                    else:
                        visitor_last_seen[track_id] = now
                        visitor_seq[track_id]       += 1

                        # --- ZONE_DWELL every 30s ---
                        seconds_in_zone = (now - visitor_dwell_emitted[track_id]).total_seconds()
                        if seconds_in_zone >= DWELL_THRESHOLD_SEC:
                            dwell_ms = int(seconds_in_zone * 1000)
                            e = make_event(STORE_ID, CAMERA_ID, vid, "ZONE_DWELL",
                                           now, zone_id=ZONE_ID, dwell_ms=dwell_ms,
                                           confidence=conf,
                                           session_seq=visitor_seq[track_id])
                            events.append(e)
                            post_event(e)
                            visitor_dwell_emitted[track_id] = now
                            print(f"  DWELL   {vid} @ {now.strftime('%H:%M:%S')}  {dwell_ms}ms")

            # --- EXIT for anyone who vanished ---
            vanished = active_ids - seen_now
            for track_id in vanished:
                vid      = f"VIS_{track_id:06x}"
                exit_ts  = visitor_last_seen.get(track_id, now)
                total_ms = int((exit_ts - visitor_first_seen[track_id]).total_seconds() * 1000)
                visitor_seq[track_id] += 1

                e = make_event(STORE_ID, CAMERA_ID, vid, "EXIT",
                               exit_ts, zone_id=ZONE_ID, dwell_ms=total_ms,
                               confidence=0.99,
                               session_seq=visitor_seq[track_id])
                events.append(e)
                post_event(e)
                print(f"  EXIT    {vid} @ {exit_ts.strftime('%H:%M:%S')}  total:{total_ms}ms")
                active_ids.discard(track_id)

        frame_num += 1

    cap.release()

    # Save to .jsonl (one JSON object per line)
    with open(output_path, "w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")

    print(f"\nDone! {len(events)} events saved to {output_path}")
    print(f"Unique visitors: {len(visitor_first_seen)}")

if __name__ == "__main__":
    video_path  = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else "events.jsonl"
    if len(sys.argv) > 3:
        CAMERA_ID = sys.argv[3]
    if len(sys.argv) > 4:
        ZONE_ID = sys.argv[4]
    process_video(video_path, output_file)