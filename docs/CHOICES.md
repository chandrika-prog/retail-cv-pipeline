\# Architectural Choices



\## 1. Detection Model: YOLOv8n



\*\*Options considered\*\*: YOLOv8n, YOLOv8m, RT-DETR, MediaPipe



\*\*What AI suggested\*\*: Claude suggested YOLOv8m for better accuracy on partially

occluded people (a known challenge in the footage).



\*\*What I chose and why\*\*: YOLOv8n — because this runs on CPU without a GPU.

YOLOv8m was 5x slower on my machine (no CUDA). For a hackathon on CPU hardware,

speed × accuracy tradeoff favors nano. In production with a GPU, I would upgrade

to YOLOv8m or RT-DETR.



\## 2. Event Schema Design



\*\*Options considered\*\*: Flat schema vs nested metadata, ms vs seconds for dwell



\*\*What AI suggested\*\*: Keep metadata nested to allow schema evolution without

breaking the top-level contract.



\*\*What I chose and why\*\*: Agreed with AI. The nested `metadata` object means

new fields (like `sku\_zone`) can be added without changing the core schema.

Used milliseconds for `dwell\_ms` for precision — sub-second events matter

for queue abandonment detection.



\## 3. API Storage: SQLite vs PostgreSQL



\*\*Options considered\*\*: SQLite, PostgreSQL, Redis + PostgreSQL



\*\*What AI suggested\*\*: PostgreSQL with indexes on `store\_id` and `timestamp`

for production-scale queries.



\*\*What I chose and why\*\*: SQLite for the hackathon — zero setup, file-based,

works inside Docker with a volume mount. The dataset is 5 stores × \~64 events

each, well within SQLite's limits. Noted in DESIGN.md that PostgreSQL is the

production path.

