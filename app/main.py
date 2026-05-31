from fastapi import FastAPI, Depends, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from models import Event, SessionLocal, init_db
from dashboard import manager
import asyncio, uvicorn, os
from fastapi.middleware.cors import CORSMiddleware
app = FastAPI(title="Store Intelligence API")
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

@app.on_event("startup")
def startup():
    init_db()
    print("DB ready")

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
    events: List[StoreEvent]

@app.post("/events/ingest")
async def ingest_events(payload: IngestPayload, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    inserted = 0
    skipped = 0
    errors = []
    new_events = []
    for ev in payload.events:
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
            new_events.append(ev.dict())
        except Exception as e:
            errors.append({"event_id": ev.event_id, "error": str(e)})
    db.commit()
    for ev_dict in new_events:
        background_tasks.add_task(manager.broadcast, {"type": "event", "data": ev_dict})
    return {"inserted": inserted, "skipped": skipped, "errors": errors}

@app.get("/stores/{store_id}/metrics")
def get_metrics(store_id: str, db: Session = Depends(get_db)):
    base = db.query(Event).filter(Event.store_id == store_id, Event.is_staff == False)
    unique_visitors = base.filter(Event.event_type == "ENTRY").with_entities(Event.visitor_id).distinct().count()
    purchases = base.filter(Event.event_type == "BILLING_QUEUE_JOIN").count()
    conversion_rate = round(purchases / unique_visitors, 3) if unique_visitors > 0 else 0.0
    avg_dwell = db.query(func.avg(Event.dwell_ms)).filter(
        Event.store_id == store_id, Event.event_type == "ZONE_DWELL", Event.is_staff == False
    ).scalar() or 0
    return {"store_id": store_id, "unique_visitors": unique_visitors,
            "conversion_rate": conversion_rate, "avg_dwell_ms": round(avg_dwell),
            "as_of": datetime.utcnow().isoformat() + "Z"}

@app.get("/stores/{store_id}/funnel")
def get_funnel(store_id: str, db: Session = Depends(get_db)):
    def count_unique(event_type):
        return db.query(Event.visitor_id).filter(
            Event.store_id == store_id, Event.event_type == event_type, Event.is_staff == False
        ).distinct().count()
    entries = count_unique("ENTRY")
    zone = count_unique("ZONE_DWELL")
    billing = count_unique("BILLING_QUEUE_JOIN")
    def drop(a, b):
        return round((1 - b/a)*100, 1) if a > 0 else 0.0
    return {"store_id": store_id, "funnel": [
        {"stage": "Entry", "visitors": entries, "drop_off_pct": 0},
        {"stage": "Zone Visit", "visitors": zone, "drop_off_pct": drop(entries, zone)},
        {"stage": "Billing Queue", "visitors": billing, "drop_off_pct": drop(zone, billing)},
        {"stage": "Purchase", "visitors": billing, "drop_off_pct": 0},
    ]}

@app.get("/stores/{store_id}/anomalies")
def get_anomalies(store_id: str, db: Session = Depends(get_db)):
    anomalies = []
    latest = db.query(func.max(Event.timestamp)).filter(Event.store_id == store_id).scalar()
    if latest:
        latest_dt = datetime.fromisoformat(latest.replace("Z", ""))
        if (datetime.utcnow() - latest_dt).total_seconds() > 1800:
            anomalies.append({"type": "DEAD_ZONE", "severity": "WARN",
                "message": "No events in 30+ minutes", "suggested_action": "Check camera feed"})
    return {"store_id": store_id, "anomalies": anomalies}

@app.get("/health")
def health(db: Session = Depends(get_db)):
    stores = db.query(Event.store_id, func.max(Event.timestamp)).group_by(Event.store_id).all()
    feeds = []
    for store_id, last_ts in stores:
        last_dt = datetime.fromisoformat(last_ts.replace("Z", ""))
        lag_min = (datetime.utcnow() - last_dt).total_seconds() / 60
        feeds.append({"store_id": store_id, "last_event": last_ts,
                      "lag_minutes": round(lag_min, 1),
                      "status": "STALE_FEED" if lag_min > 10 else "OK"})
    return {"status": "healthy", "stores": feeds}

@app.websocket("/ws/live/{store_id}")
async def websocket_endpoint(websocket: WebSocket, store_id: str):
    await manager.connect(websocket)
    try:
        while True:
            await asyncio.sleep(30)
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    html_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    return HTMLResponse(content=open(html_path, encoding="utf-8").read())

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
