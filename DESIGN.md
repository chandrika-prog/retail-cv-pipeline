# Store Intelligence System - Design

## Architecture Overview

The system turns raw retail CCTV clips into store analytics through four stages:

```text
CCTV clips -> Detection pipeline -> Event ingest API -> Metrics/dashboard
```

1. `pipeline/detect.py` runs YOLO + ByteTrack on Store 1 and Store 2 clips.
2. `pipeline/emit.py` converts tracks into schema-compliant events with deterministic event IDs.
3. `app/main.py` normalizes incoming events, stores them in SQLite, and computes metrics.
4. `app/dashboard.html` shows store-specific metrics and live/recent events for Store 1 and Store 2.

The Docker path uses `docker compose up --build`, mounts `data/`, `.runtime/`, and `outputs/`, and persists SQLite under `.runtime/store.db`.

## Event Model

The API accepts both the project event schema and the provided challenge `sample_events.jsonl` schema. Incoming sample event names are normalized:

- `entry` -> `ENTRY`
- `exit` -> `EXIT`
- `zone_entered` -> `ZONE_ENTER`
- `zone_exited` -> `ZONE_EXIT`
- `queue_completed` -> `BILLING_QUEUE_JOIN`
- `queue_abandoned` -> `BILLING_QUEUE_ABANDON`
- `pos_transaction` -> `POS_TRANSACTION`

Store codes such as `store_1076` normalize to `ST1076`.

## Detection Layer

The detector samples one frame per second. This is a deliberate tradeoff: dwell, queue, and entry events are measured at seconds/minutes scale, so per-frame processing is not needed for this take-home pipeline.

Camera role determines emitted event types:

- Entry cameras emit `ENTRY`, `EXIT`, and `REENTRY`.
- Zone cameras emit `ZONE_ENTER`, `ZONE_EXIT`, and repeated `ZONE_DWELL`.
- Billing cameras emit `BILLING_QUEUE_JOIN`, `BILLING_QUEUE_ABANDON`, and `ZONE_DWELL`.

Event IDs are deterministic hashes of store, camera, visitor, event type, timestamp, zone, and sequence. This makes rerunning detection idempotent: the same event is skipped by ingest instead of duplicated.

## Staff Exclusion

Staff are excluded from customer metrics using visual uniform heuristics, not fixed counts.

- Dark/black torso region counts as a staff uniform for all stores.
- Store 2 additionally checks for pink shirt uniforms, based on observed staff clothing in the Store 2 clips.

The detector assigns stable `STAFF_###` identities across short disappear/reappear gaps using time and bounding-box center distance. This reduces staff recounting when a staff member briefly leaves the frame and returns.

Store-specific detection settings are in `data/challenge/store_config.json`, including uniform color profiles and zone labels. This keeps Store 2 pink-shirt staff detection configurable instead of hidden in code.

Zone definitions for API heatmaps are in `data/challenge/store_layout.json`. The layout file lists each store's zones, names, camera coverage, open hours, zone type, and whether the zone is revenue-producing.

## Raw Tracks vs Qualified Visitors

YOLO/ByteTrack track IDs are not treated directly as customers. Raw tracks can include staff, fragmented IDs, and short pass-through detections.

The business metric uses qualified visitors:

```text
non-staff visitor with ZONE_DWELL or BILLING_QUEUE_JOIN activity
```

This prevents the dashboard from presenting raw tracker fragments as customer count.

## POS Correlation

The POS CSV uses legacy/anonymized store code `ST1008`, while the current video demo uses `ST1` and `ST2`. `load_pos.py` supports explicit mapping:

```powershell
python load_pos.py --store-id ST1
python load_pos.py --store-id ST2
```

POS rows are ingested as `POS_TRANSACTION` events. Conversion is computed at query time:

```text
visitor is converted if they were in BILLING_QUEUE_JOIN within 5 minutes before a POS_TRANSACTION
```

This matches the problem statement more closely than directly treating POS rows as customer events.

## API Metrics

The API exposes:

- `/stores/{id}/metrics`: qualified visitors, conversion rate, purchases, dwell, queue depth, abandonment rate.
- `/stores/{id}/funnel`: Entry -> Zone Visit -> Billing Queue -> Purchase.
- `/stores/{id}/heatmap`: zone intensity normalized 0-100 with layout metadata and data confidence.
- `/stores/{id}/anomalies`: stale/dead zones, queue spikes, conversion drops.
- `/stores/{id}/events/recent`: recent persisted feed for dashboard backfill.
- `/health`: store-level last event and stale feed status.

Live events use store-specific WebSocket channels, so Store 1 and Store 2 dashboards do not receive each other's events.

Each processed clip writes a sidecar `.summary.json` file next to the JSONL output. The summary reports raw tracks, uniform staff identities, qualified visitors, excluded tracks, event counts, camera role, zone label, and active staff uniform colors. This makes terminal/dashboard numbers auditable.

## AI-Assisted Decisions

AI assistance was used to generate initial implementation scaffolding, propose test coverage, and review edge cases. I changed the implementation after observing dataset-specific realities:

- Replaced full-day POS ingestion with configurable store mapping and POS transaction events.
- Changed raw `ENTRY` count metrics to qualified visitors.
- Added Store 2 pink-uniform staff detection after inspecting the Store 2 footage.
- Added store-specific WebSocket broadcasting after noticing both dashboards shared the same live feed.

## Known Limitations

- Staff detection is color-heuristic, not a trained uniform classifier.
- Re-identification is distance/time based and can fail with long occlusions.
- POS correlation is time-window based because POS data has no customer identity.
- Layout images are staged but not yet parsed into polygonal zone boundaries.
- SQLite is adequate for the challenge but PostgreSQL would be preferred for production.
