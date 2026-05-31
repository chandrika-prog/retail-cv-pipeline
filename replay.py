import json, httpx, time

events = [json.loads(l) for l in open('events_cam1.jsonl')]
print(f'Replaying {len(events)} events...')

for i, ev in enumerate(events):
    httpx.post('http://localhost:8000/events/ingest', json={'events': [ev]})
    print(f'Sent event {i+1}/{len(events)}: {ev["event_type"]} {ev["visitor_id"]}')
    time.sleep(0.5)

print('Done!')