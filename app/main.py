from fastapi import FastAPI, Depends, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from contextlib import asynccontextmanager
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from typing import List, Optional, Any
from datetime import datetime, timedelta, timezone
from models import Event, SessionLocal, init_db
from dashboard import manager
import asyncio, uvicorn, os, time, uuid, logging, hashlib, json
from pathlib import Path
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)
from fastapi.middleware.cors import CORSMiddleware

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    print("DB ready")
    yield

app = FastAPI(title="Store Intelligence API", lifespan=lifespan)

def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

def utc_now_iso() -> str:
    return utc_now().isoformat() + "Z"

@app.middleware("http")
async def log_requests(request, call_next):
    # Skip logging for video streaming endpoint
    if request.url.path.startswith("/video"):
        return await call_next(request)
    trace_id = str(uuid.uuid4())[:8]
    store_id = request.path_params.get("store_id", "-")
    start = time.time()
    response = await call_next(request)
    latency = round((time.time() - start) * 1000)
    logger.info(
        f"trace_id={trace_id} "
        f"store_id={store_id} "
        f"method={request.method} "
        f"endpoint={request.url.path} "
        f"status={response.status_code} "
        f"latency_ms={latency}"
    )
    return response
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class EventMetadata(BaseModel):
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: int = 1

class StoreEvent(BaseModel):
    event_id: str
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: str
    timestamp: str
    zone_id: Optional[str] = None
    dwell_ms: int = 0
    is_staff: bool = False
    confidence: float = 1.0
    metadata: EventMetadata = EventMetadata()

class IngestPayload(BaseModel):
    events: List[dict[str, Any]]

EVENT_TYPE_MAP = {
    "entry": "ENTRY",
    "exit": "EXIT",
    "zone_entered": "ZONE_ENTER",
    "zone_exited": "ZONE_EXIT",
    "zone_dwell": "ZONE_DWELL",
    "queue_completed": "BILLING_QUEUE_JOIN",
    "queue_abandoned": "BILLING_QUEUE_ABANDON",
    "billing_queue_join": "BILLING_QUEUE_JOIN",
    "billing_queue_abandon": "BILLING_QUEUE_ABANDON",
    "pos_transaction": "POS_TRANSACTION",
    "reentry": "REENTRY",
}

def deterministic_event_id(raw: dict[str, Any]) -> str:
    payload = json.dumps(raw, sort_keys=True, default=str)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()
    return str(uuid.UUID(digest[:32]))

def event_timestamp(raw: dict[str, Any]) -> str:
    ts = raw.get("timestamp") or raw.get("event_timestamp") or raw.get("event_time")
    if not ts:
        ts = raw.get("queue_join_ts") or raw.get("queue_exit_ts") or raw.get("queue_served_ts")
    if not ts:
        ts = utc_now().isoformat()
    return ts if ts.endswith("Z") else f"{ts}Z"

def normalize_store_id(value: Any) -> str:
    store_id = str(value or "UNKNOWN_STORE").upper()
    if store_id.startswith("STORE_") and store_id.replace("STORE_", "").isdigit():
        return f"ST{store_id.replace('STORE_', '')}"
    return store_id

LAYOUT_PATH = Path(__file__).resolve().parent.parent / "data" / "challenge" / "store_layout.json"

def load_store_layout() -> dict[str, Any]:
    if not LAYOUT_PATH.exists():
        return {"stores": {}}
    with open(LAYOUT_PATH, encoding="utf-8") as layout_file:
        return json.load(layout_file)

def zone_metadata(store_id: str, zone_id: str | None) -> dict[str, Any]:
    if not zone_id:
        return {}
    store = load_store_layout().get("stores", {}).get(store_id, {})
    for zone in store.get("zones", []):
        if zone.get("zone_id") == zone_id:
            return zone
    return {
        "zone_id": zone_id,
        "zone_name": zone_id,
        "zone_type": "UNKNOWN",
        "camera_ids": [],
        "is_revenue_zone": False,
    }

def normalize_event(raw: dict[str, Any]) -> StoreEvent:
    raw_type = str(raw.get("event_type", "")).strip()
    event_type = EVENT_TYPE_MAP.get(raw_type.lower(), raw_type.upper())
    store_id = normalize_store_id(raw.get("store_id") or raw.get("store_code") or "UNKNOWN_STORE")
    visitor_id = raw.get("visitor_id") or raw.get("id_token")
    if not visitor_id:
        track_id = raw.get("track_id") or raw.get("queue_event_id") or deterministic_event_id(raw)[:8]
        visitor_id = f"VIS_{track_id}"

    queue_depth = raw.get("queue_depth")
    if queue_depth is None:
        queue_depth = raw.get("queue_position_at_join")

    dwell_ms = raw.get("dwell_ms")
    if dwell_ms is None and raw.get("wait_seconds") is not None:
        dwell_ms = int(float(raw.get("wait_seconds") or 0) * 1000)
    if dwell_ms is None:
        dwell_ms = 0

    metadata = raw.get("metadata") or {}
    metadata = {
        "queue_depth": metadata.get("queue_depth", queue_depth),
        "sku_zone": metadata.get("sku_zone") or raw.get("zone_name") or raw.get("zone_type"),
        "session_seq": metadata.get("session_seq", 1),
    }

    return StoreEvent(
        event_id=str(raw.get("event_id") or deterministic_event_id(raw)),
        store_id=store_id,
        camera_id=str(raw.get("camera_id") or "UNKNOWN_CAMERA").upper(),
        visitor_id=str(visitor_id),
        event_type=event_type,
        timestamp=event_timestamp(raw),
        zone_id=raw.get("zone_id"),
        dwell_ms=int(dwell_ms or 0),
        is_staff=bool(raw.get("is_staff", False)),
        confidence=float(raw.get("confidence", 1.0)),
        metadata=EventMetadata(**metadata),
    )

def parse_event_dt(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp.replace("Z", ""))

def correlated_purchase_visitors(store_id: str, db: Session, window_minutes: int = 5) -> set[str]:
    transactions = db.query(Event).filter(
        Event.store_id == store_id,
        Event.event_type == "POS_TRANSACTION",
    ).all()
    converted = set()
    for txn in transactions:
        txn_dt = parse_event_dt(txn.timestamp)
        window_start = (txn_dt - timedelta(minutes=window_minutes)).isoformat()
        window_end = txn_dt.isoformat()
        rows = db.query(Event.visitor_id).filter(
            Event.store_id == store_id,
            Event.event_type == "BILLING_QUEUE_JOIN",
            Event.is_staff == False,
            Event.timestamp >= window_start,
            Event.timestamp <= window_end,
        ).distinct().all()
        converted.update(visitor_id for (visitor_id,) in rows)
    return converted

@app.post("/events/ingest")
async def ingest_events(payload: IngestPayload, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    inserted = 0
    skipped = 0
    errors = []
    new_events = []
    for raw in payload.events:
        try:
            ev = normalize_event(raw)
        except Exception as e:
            errors.append({"event_id": raw.get("event_id"), "error": str(e)})
            continue
        existing = db.query(Event).filter(Event.event_id == ev.event_id).first()
        if existing:
            skipped += 1
            continue
        try:
            row = Event(
                event_id=ev.event_id, store_id=ev.store_id,
                camera_id=ev.camera_id, visitor_id=ev.visitor_id,
                event_type=ev.event_type, timestamp=ev.timestamp,
                zone_id=ev.zone_id, dwell_ms=ev.dwell_ms,
                is_staff=ev.is_staff, confidence=ev.confidence,
                queue_depth=ev.metadata.queue_depth,
                session_seq=ev.metadata.session_seq,
            )
            db.add(row)
            inserted += 1
            new_events.append(ev.model_dump())
        except Exception as e:
            errors.append({"event_id": ev.event_id, "error": str(e)})
    db.commit()
    for ev_dict in new_events:
        background_tasks.add_task(
            manager.broadcast,
            ev_dict["store_id"],
            {"type": "event", "data": ev_dict},
        )
    return {"inserted": inserted, "skipped": skipped, "errors": errors}

@app.get("/stores/{store_id}/metrics")
def get_metrics(store_id: str, db: Session = Depends(get_db)):
    base = db.query(Event).filter(Event.store_id == store_id, Event.is_staff == False)
    qualified_visitors = db.query(Event.visitor_id).filter(
        Event.store_id == store_id,
        Event.is_staff == False,
        Event.event_type.in_(["ZONE_DWELL", "BILLING_QUEUE_JOIN"]),
    ).distinct()
    unique_visitors = qualified_visitors.count()
    purchases = len(correlated_purchase_visitors(store_id, db))
    conversion_rate = round(purchases / unique_visitors, 3) if unique_visitors > 0 else 0.0
    avg_dwell = db.query(func.avg(Event.dwell_ms)).filter(
        Event.store_id == store_id, Event.event_type == "ZONE_DWELL", Event.is_staff == False
    ).scalar() or 0
    avg_dwell_by_zone = db.query(Event.zone_id, func.avg(Event.dwell_ms)).filter(
        Event.store_id == store_id,
        Event.event_type == "ZONE_DWELL",
        Event.is_staff == False,
        Event.zone_id != None,
    ).group_by(Event.zone_id).all()
    latest_queue_depth = db.query(func.max(Event.queue_depth)).filter(
        Event.store_id == store_id,
        Event.event_type == "BILLING_QUEUE_JOIN",
        Event.queue_depth != None,
    ).scalar() or 0
    queue_joins = base.filter(Event.event_type == "BILLING_QUEUE_JOIN").count()
    queue_abandons = base.filter(Event.event_type == "BILLING_QUEUE_ABANDON").count()
    abandonment_rate = round(queue_abandons / queue_joins, 3) if queue_joins else 0.0
    return {"store_id": store_id, "unique_visitors": unique_visitors,
            "conversion_rate": conversion_rate, "avg_dwell_ms": round(avg_dwell),
            "avg_dwell_by_zone": {zone: round(value or 0) for zone, value in avg_dwell_by_zone},
            "queue_depth": latest_queue_depth,
            "abandonment_rate": abandonment_rate,
            "purchases": purchases,
            "as_of": utc_now_iso()}

@app.get("/stores/{store_id}/funnel")
def get_funnel(store_id: str, db: Session = Depends(get_db)):
    def count_unique(event_type):
        return db.query(Event.visitor_id).filter(
            Event.store_id == store_id, Event.event_type == event_type, Event.is_staff == False
        ).distinct().count()
    entries = db.query(Event.visitor_id).filter(
        Event.store_id == store_id,
        Event.is_staff == False,
        Event.event_type.in_(["ZONE_DWELL", "BILLING_QUEUE_JOIN"]),
    ).distinct().count()
    zone = count_unique("ZONE_DWELL")
    billing = count_unique("BILLING_QUEUE_JOIN")
    purchases = len(correlated_purchase_visitors(store_id, db))
    def drop(a, b):
        if a <= 0 or b >= a:
            return 0.0
        return round((1 - b/a)*100, 1)
    return {"store_id": store_id, "funnel": [
        {"stage": "Entry", "visitors": entries, "drop_off_pct": 0},
        {"stage": "Zone Visit", "visitors": zone, "drop_off_pct": drop(entries, zone)},
        {"stage": "Billing Queue", "visitors": billing, "drop_off_pct": drop(zone, billing)},
        {"stage": "Purchase", "visitors": purchases, "drop_off_pct": drop(billing, purchases)},
    ]}

@app.get("/stores/{store_id}/heatmap")
def get_heatmap(store_id: str, db: Session = Depends(get_db)):
    rows = db.query(
        Event.zone_id,
        func.count(Event.event_id),
        func.avg(Event.dwell_ms),
    ).filter(
        Event.store_id == store_id,
        Event.is_staff == False,
        Event.zone_id != None,
        Event.event_type.in_(["ZONE_ENTER", "ZONE_DWELL", "BILLING_QUEUE_JOIN"]),
    ).group_by(Event.zone_id).all()

    max_visits = max((visits for _, visits, _ in rows), default=0) or 1
    sessions = db.query(Event.visitor_id).filter(
        Event.store_id == store_id,
        Event.is_staff == False,
    ).distinct().count()

    zones = []
    for zone_id, visits, avg_dwell in rows:
        metadata = zone_metadata(store_id, zone_id)
        zones.append({
            "zone_id": zone_id,
            "zone_name": metadata.get("zone_name", zone_id),
            "zone_type": metadata.get("zone_type", "UNKNOWN"),
            "camera_ids": metadata.get("camera_ids", []),
            "is_revenue_zone": bool(metadata.get("is_revenue_zone", False)),
            "visits": visits,
            "avg_dwell_ms": round(avg_dwell or 0),
            "intensity": round((visits / max_visits) * 100, 1),
        })

    return {
        "store_id": store_id,
        "data_confidence": "LOW" if sessions < 20 else "OK",
        "zones": zones,
    }

@app.get("/stores/{store_id}/events/recent")
def get_recent_events(store_id: str, limit: int = 50, db: Session = Depends(get_db)):
    limit = max(1, min(limit, 100))
    rows = db.query(Event).filter(
        Event.store_id == store_id
    ).order_by(Event.timestamp.desc()).limit(limit).all()
    return {
        "store_id": store_id,
        "events": [
            {
                "event_id": row.event_id,
                "store_id": row.store_id,
                "camera_id": row.camera_id,
                "visitor_id": row.visitor_id,
                "event_type": row.event_type,
                "timestamp": row.timestamp,
                "zone_id": row.zone_id,
                "dwell_ms": row.dwell_ms,
                "is_staff": row.is_staff,
                "confidence": row.confidence,
                "metadata": {
                    "queue_depth": row.queue_depth,
                    "sku_zone": None,
                    "session_seq": row.session_seq,
                },
            }
            for row in rows
        ],
    }

@app.get("/stores/{store_id}/anomalies")
def get_anomalies(store_id: str, db: Session = Depends(get_db)):
    anomalies = []

    # 1. DEAD_ZONE — no events in 30 minutes
    latest = db.query(func.max(Event.timestamp)).filter(
        Event.store_id == store_id).scalar()
    if latest:
        latest_dt = datetime.fromisoformat(latest.replace("Z", ""))
        if (utc_now() - latest_dt).total_seconds() > 1800:
            anomalies.append({
                "type": "DEAD_ZONE",
                "severity": "WARN",
                "message": "No events received in 30+ minutes",
                "suggested_action": "Check camera feed and network connection"
            })

    # 2. BILLING_QUEUE_SPIKE — queue depth > 3
    queue_events = db.query(Event).filter(
        Event.store_id == store_id,
        Event.event_type == "BILLING_QUEUE_JOIN",
        Event.queue_depth != None
    ).all()
    if queue_events:
        max_depth = max(e.queue_depth for e in queue_events)
        if max_depth > 3:
            anomalies.append({
                "type": "BILLING_QUEUE_SPIKE",
                "severity": "CRITICAL",
                "message": f"Billing queue depth reached {max_depth} customers",
                "suggested_action": "Open additional billing counter immediately"
            })

    # 3. CONVERSION_DROP — conversion rate below 5%
    total_visitors = db.query(Event.visitor_id).filter(
        Event.store_id == store_id,
        Event.event_type.in_(["ZONE_DWELL", "BILLING_QUEUE_JOIN"]),
        Event.is_staff == False
    ).distinct().count()

    total_purchases = len(correlated_purchase_visitors(store_id, db))

    if total_visitors > 10:
        conversion = total_purchases / total_visitors
        if conversion < 0.05:
            anomalies.append({
                "type": "CONVERSION_DROP",
                "severity": "WARN",
                "message": f"Conversion rate is {round(conversion*100, 1)}% — below 5% threshold",
                "suggested_action": "Review product placement and staff engagement"
            })

    # 4. EMPTY_ZONE — no visits to a zone in 30 minutes
    zone_events = db.query(Event.zone_id, func.max(Event.timestamp)).filter(
        Event.store_id == store_id,
        Event.zone_id != None
    ).group_by(Event.zone_id).all()

    for zone_id, last_ts in zone_events:
        last_dt = datetime.fromisoformat(last_ts.replace("Z", ""))
        if (utc_now() - last_dt).total_seconds() > 1800:
            anomalies.append({
                "type": "EMPTY_ZONE",
                "severity": "INFO",
                "message": f"No visits to zone {zone_id} in 30+ minutes",
                "suggested_action": f"Check if {zone_id} zone needs attention or restocking"
            })

    return {"store_id": store_id, "anomalies": anomalies}

@app.get("/health")
def health(db: Session = Depends(get_db)):
    stores = db.query(Event.store_id, func.max(Event.timestamp)).group_by(Event.store_id).all()
    feeds = []
    for store_id, last_ts in stores:
        last_dt = datetime.fromisoformat(last_ts.replace("Z", ""))
        lag_min = (utc_now() - last_dt).total_seconds() / 60
        feeds.append({"store_id": store_id, "last_event": last_ts,
                      "lag_minutes": round(lag_min, 1),
                      "status": "STALE_FEED" if lag_min > 10 else "OK"})
    return {"status": "healthy", "stores": feeds}

@app.websocket("/ws/live/{store_id}")
async def websocket_endpoint(websocket: WebSocket, store_id: str):
    await manager.connect(store_id, websocket)
    try:
        while True:
            await asyncio.sleep(30)
    except WebSocketDisconnect:
        manager.disconnect(store_id, websocket)

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/dashboard", status_code=307)

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    html_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    return HTMLResponse(content=open(html_path, encoding="utf-8").read())
# Track currently processing camera
current_camera = {"name": None}

@app.post("/camera/set/{camera_name}")
def set_camera(camera_name: str):
    current_camera["name"] = camera_name
    return {"camera": camera_name}

@app.get("/camera/current")
def get_camera():
    return {"camera": current_camera["name"]}
# Track currently processing camera
# ── Current camera tracker ───────────────────────────────────────
current_camera = {"name": None}

@app.post("/camera/set/{camera_name}")
def set_camera(camera_name: str):
    current_camera["name"] = camera_name
    return {"camera": camera_name}

@app.get("/camera/current")
def get_camera():
    return {"camera": current_camera["name"]}

# ── Video streaming with detection overlay ───────────────────────
@app.get("/video/{camera_name:path}")
async def video_feed(camera_name: str):
    video_path = f"../data/clips/{camera_name}.mp4"
    print(f"Streaming: {video_path}")

    def generate_frames():
        import cv2
        from ultralytics import YOLO
        model = YOLO("../yolov8n.pt")
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return
        while True:
            ret, frame = cap.read()
            if not ret:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            results = model.track(frame, classes=[0], tracker="bytetrack.yaml",
                                  persist=True, verbose=False)
            boxes = results[0].boxes
            if boxes is not None and boxes.id is not None:
                track_ids = boxes.id.int().tolist()
                confidences = boxes.conf.tolist()
                bboxes = boxes.xyxy.tolist()
                for track_id, conf, bbox in zip(track_ids, confidences, bboxes):
                    x1, y1, x2, y2 = map(int, bbox)
                    vid = f"VIS_{track_id:06x}"
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    cv2.putText(frame, f"{vid} {conf:.2f}", (x1, y1-10),
                               cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
            # Resize for faster streaming
            frame = cv2.resize(frame, (640, 360))
            _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')

    return StreamingResponse(
        generate_frames(),
        media_type="multipart/x-mixed-replace;boundary=frame"
    )
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
