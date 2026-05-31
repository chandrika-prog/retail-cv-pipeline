import json, httpx

all_events = []
for i in range(1, 6):
    try:
        events = [json.loads(l) for l in open(f'events_cam{i}.jsonl')]
        all_events.extend(events)
        print(f'CAM {i}: {len(events)} events')
    except:
        print(f'CAM {i}: skipped')

batch_size = 50
for i in range(0, len(all_events), batch_size):
    batch = all_events[i:i+batch_size]
    r = httpx.post('http://localhost:8000/events/ingest', json={'events': batch}, timeout=60.0)
    print(f'Batch {i//batch_size + 1}: {r.json()}')

print('Done!')