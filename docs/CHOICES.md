# Architectural Choices

## 1. Detection Model: YOLOv8n + ByteTrack

Options considered: YOLOv8n, YOLOv8m, RT-DETR, MediaPipe.

I chose YOLOv8n because the challenge needs a runnable end-to-end system on ordinary CPU hardware. YOLOv8m or RT-DETR would likely improve occlusion accuracy, but they are slower and make the demo less reliable without GPU access.

ByteTrack is used through Ultralytics because it provides stable per-frame tracking with minimal setup.

Tradeoff: some short tracks fragment. To avoid turning fragments into fake customers, the API reports qualified visitors rather than raw track count.

## 2. Deterministic Event IDs

The problem statement requires idempotent ingest. Random UUIDs satisfy uniqueness but not rerun idempotency.

I changed generated detection events to deterministic IDs based on:

```text
store_id, camera_id, visitor_id, event_type, timestamp, zone_id, session_seq
```

This means rerunning the same clip produces the same event IDs and duplicate ingest safely skips existing rows.

## 3. Staff Detection

The footage includes staff movement, and staff must not affect customer metrics.

I use visual uniform heuristics:

- dark/black torso area for general staff uniforms
- pink torso area for Store 2 staff shirts

This is not a hardcoded staff count. It is a configurable visual rule based on observed store uniforms. The implementation also reuses `STAFF_###` identities when a staff member briefly disappears and returns near the same location.

Tradeoff: a customer wearing similar colors could be misclassified. A production version should replace this with a trained uniform/person-role classifier.

## 4. Store 1 / Store 2 Dashboard Scope

The dashboard only exposes Store 1 and Store 2 because those are the current video datasets. Legacy/sample stores such as `ST1008` or `ST1076` can exist in the database for testing, but they are hidden from the main demo selector to avoid evaluator confusion.

## 5. POS Mapping

The provided POS CSV contains `ST1008`, while the new video folders are Store 1 and Store 2. Since no direct mapping file is provided, `load_pos.py` requires an explicit target store:

```powershell
python load_pos.py --store-id ST1
python load_pos.py --store-id ST2
```

For the two-store demo, the same POS rows can be loaded into both stores. Event IDs include the target store, so Store 1 and Store 2 transaction events do not collide.

Tradeoff: this is a demo mapping assumption. In production, POS store IDs would come from a store master table.

## 6. POS Conversion Logic

Earlier iterations treated POS rows as billing queue joins. That was too simplistic.

The current implementation ingests POS rows as `POS_TRANSACTION` events and computes conversion by correlation:

```text
non-staff visitor with BILLING_QUEUE_JOIN in the 5 minutes before a POS_TRANSACTION
```

This follows the challenge statement's time-window approach and avoids inventing customer identity in POS data.

## 7. Storage: SQLite

SQLite is used because it makes local and Docker setup simple. It is sufficient for the challenge clips and automated tests.

Production path:

- PostgreSQL for durable multi-store event storage
- TimescaleDB or partitioning for time-series query scale
- Redis or pub/sub for live event fanout

## 8. Tests

The tests cover the evaluator-facing risks:

- sample event schema normalization
- idempotent ingest
- malformed partial success
- staff exclusion
- Store 1 / Store 2 isolation
- heatmap normalization
- recent feed store filtering
- POS 5-minute conversion correlation
- queue/anomaly/health behavior

OpenCV-dependent uniform tests are skipped when `cv2` is unavailable locally, but run in the full requirements/Docker environment.

## 9. AI-Assisted Decisions

AI helped generate test ideas, identify dashboard/WebSocket store mixing, and compare direct POS-as-purchase ingestion against the required 5-minute correlation approach.

I overrode or refined AI suggestions where they risked hardcoding:

- no fixed "2 visitors / 5 staff" counts
- no dashboard-only metric patches
- no treating raw track IDs as final visitors
- no global live feed broadcasting across stores

The implementation is intentionally transparent: terminal output separates raw tracks, uniform staff identities, qualified visitors, and excluded tracks.
