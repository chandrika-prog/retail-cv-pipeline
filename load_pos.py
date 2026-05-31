import pandas as pd
import httpx

df = pd.read_csv('data/pos_transactions.csv.csv')
print(f"Loaded {len(df)} POS transactions")
print(f"Store: {df['store_id'].unique()}")
print(f"Time range: {df['order_time'].min()} - {df['order_time'].max()}")
print(f"Total revenue: {df['total_amount'].sum():.2f}")

# Convert each transaction into a BILLING_QUEUE_JOIN event
import uuid
from datetime import datetime

events = []
for _, row in df.iterrows():
    dt_str = f"2026-04-10T{row['order_time']}Z"
    events.append({
        "event_id": str(uuid.uuid4()),
        "store_id": "ST1008",
        "camera_id": "CAM_BILLING_01",
        "visitor_id": f"VIS_POS_{row['order_id']}",
        "event_type": "BILLING_QUEUE_JOIN",
        "timestamp": dt_str,
        "zone_id": "BILLING",
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 1.0,
        "metadata": {
            "queue_depth": 1,
            "sku_zone": str(row.get('dep_name', '')),
            "session_seq": 1
        }
    })
# Send in batches of 10
batch_size = 10
for i in range(0, len(events), batch_size):
    batch = events[i:i+batch_size]
    r = httpx.post('http://localhost:8000/events/ingest', json={'events': batch}, timeout=30.0)
    print(f"Batch {i//batch_size + 1}: {r.json()}")

print("All POS transactions loaded!")
print(f"Ingested: {r.json()}")