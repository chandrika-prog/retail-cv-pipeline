\# Store Intelligence System — Design Document



\## Architecture Overview



Raw CCTV footage → Detection Layer → Event Stream → Intelligence API → Dashboard



The system is a four-stage pipeline:



1\. \*\*Detection Layer\*\* (`pipeline/detect.py`): YOLOv8n processes video frames at 1fps

&#x20;  using ByteTrack for persistent person tracking across frames.



2\. \*\*Event Stream\*\* (`pipeline/emit.py`): Tracker output is translated into structured

&#x20;  JSON events (ENTRY, EXIT, ZONE\_DWELL etc.) and saved to a `.jsonl` file.



3\. \*\*Intelligence API\*\* (`app/main.py`): FastAPI ingests events, stores them in SQLite,

&#x20;  and exposes queryable endpoints for metrics, funnel, anomalies, and health.



4\. \*\*Storage\*\*: SQLite for simplicity. Each event is deduplicated by `event\_id` 

&#x20;  ensuring idempotent ingestion.



\## Key Design Decisions



\- \*\*1fps sampling\*\*: Retail foot traffic doesn't change meaningfully frame-to-frame.

&#x20; Sampling every second reduces compute 30x with negligible accuracy loss.

\- \*\*ByteTrack\*\*: Built into Ultralytics, no extra dependencies. Handles occlusion

&#x20; well using IoU-based re-association.

\- \*\*SQLite\*\*: Sufficient for 5 stores × 20min clips. In production, PostgreSQL

&#x20; with TimescaleDB would handle the event volume.



\## AI-Assisted Decisions



1\. \*\*Event schema design\*\*: Used Claude to evaluate whether `dwell\_ms` should be

&#x20;  on ENTRY or ZONE\_DWELL events. Claude suggested ZONE\_DWELL only — agreed,

&#x20;  because entry is instantaneous.



2\. \*\*Idempotency approach\*\*: Asked Claude to compare UUID dedup vs. hash-based dedup.

&#x20;  Chose UUID as primary key — simpler and the schema already mandates unique event\_ids.



3\. \*\*Sampling rate\*\*: Claude suggested 2fps initially. Overrode to 1fps after observing

&#x20;  that our 30fps clips had very little change second-to-second in the retail setting.

