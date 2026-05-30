\# Store Intelligence API



End-to-end retail analytics pipeline: CCTV footage → people detection → structured events → live REST API.



\## Setup (5 commands)



git clone <your-repo-url>

cd store-intelligence

python -m venv venv \&\& venv\\Scripts\\activate

pip install -r requirements.txt

docker compose up --build



\## Run Detection Pipeline



Process each camera clip and emit structured events:



&#x20;   python pipeline\\detect.py "data\\clips\\CAM 1.mp4" events\_cam1.jsonl

&#x20;   python pipeline\\detect.py "data\\clips\\CAM 2.mp4" events\_cam2.jsonl

&#x20;   python pipeline\\detect.py "data\\clips\\CAM 3.mp4" events\_cam3.jsonl

&#x20;   python pipeline\\detect.py "data\\clips\\CAM 4.mp4" events\_cam4.jsonl

&#x20;   python pipeline\\detect.py "data\\clips\\CAM 5.mp4" events\_cam5.jsonl



\## Ingest Events into API



&#x20;   python -c "

&#x20;   import json, httpx

&#x20;   all\_events = \[]

&#x20;   for i in range(1, 6):

&#x20;       try:

&#x20;           events = \[json.loads(l) for l in open(f'events\_cam{i}.jsonl')]

&#x20;           all\_events.extend(events)

&#x20;       except: pass

&#x20;   httpx.post('http://localhost:8000/events/ingest', json={'events': all\_events})

&#x20;   print('Done')

&#x20;   "



\## API Endpoints



| Endpoint | Description |

|---|---|

| POST /events/ingest | Ingest up to 500 events (idempotent) |

| GET /stores/{id}/metrics | Unique visitors, conversion rate, avg dwell |

| GET /stores/{id}/funnel | Entry → Zone → Billing → Purchase funnel |

| GET /stores/{id}/anomalies | Queue spikes, dead zones, conversion drops |

| GET /health | Service status + stale feed detection |



Interactive docs: http://localhost:8000/docs



\## Architecture

