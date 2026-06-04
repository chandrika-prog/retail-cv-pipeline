import argparse
import csv
import hashlib
from datetime import datetime, time

DEFAULT_ORDER_DATE = "2026-04-10"


def parse_time(value: str) -> time:
    return datetime.strptime(value, "%H:%M:%S").time()


def read_transactions(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as csv_file:
        return list(csv.DictReader(csv_file))


def filter_video_window(rows: list[dict], start: str, end: str) -> list[dict]:
    if not start or not end:
        return rows
    start_time = parse_time(start)
    end_time = parse_time(end)
    window_rows = []
    for row in rows:
        try:
            order_time = parse_time(row["order_time"])
        except (KeyError, ValueError):
            continue
        if start_time <= order_time <= end_time:
            window_rows.append(row)
    return window_rows


def make_event_id(row: dict, row_number: int, store_id: str) -> str:
    product_key = row.get("sku") or row.get("product_id") or ""
    key = f"{store_id}:{row.get('order_id', '')}:{product_key}:{row_number}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return f"POS_{digest}"


def money(value: str) -> float:
    try:
        return float(value or 0)
    except ValueError:
        return 0.0


def iso_timestamp(row: dict) -> str:
    if row.get("timestamp"):
        return row["timestamp"] if row["timestamp"].endswith("Z") else f"{row['timestamp']}Z"
    order_date = row.get("order_date", "")
    order_time = row.get("order_time", "00:00:00")
    try:
        parsed_date = datetime.strptime(order_date, "%d-%m-%Y").date().isoformat()
    except ValueError:
        parsed_date = DEFAULT_ORDER_DATE
    return f"{parsed_date}T{order_time}Z"


def main():
    parser = argparse.ArgumentParser(description="Load POS transactions as billing events.")
    parser.add_argument("--csv", default="data/challenge/pos_transactions.csv")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--api", default="http://127.0.0.1:8001/events/ingest")
    parser.add_argument(
        "--store-id",
        default=None,
        help="Override POS store_id when the transaction CSV uses a legacy/anonymized store code.",
    )
    args = parser.parse_args()

    rows = read_transactions(args.csv)
    window_rows = filter_video_window(rows, args.start, args.end)
    order_times = [row["order_time"] for row in rows if row.get("order_time")]

    print(f"Loaded {len(rows)} POS transaction rows")
    print(f"Full POS time range: {min(order_times)} - {max(order_times)}")
    print(f"POS window: {args.start} - {args.end}" if args.start and args.end else "POS window: all rows")
    print(f"Rows in video window: {len(window_rows)}")

    if not window_rows:
        print("No POS transactions fall inside the requested POS window; nothing to ingest.")
        return

    source_stores = sorted({row.get("store_id", "") for row in window_rows})
    target_store = args.store_id or ", ".join(source_stores)
    print(f"Source store: {source_stores}")
    print(f"Target store: {target_store}")
    print(f"Window revenue: {sum(money(row.get('total_amount', '0')) for row in window_rows):.2f}")

    events = []
    for row_number, row in enumerate(window_rows, start=1):
        dt_str = iso_timestamp(row)
        store_id = args.store_id or row.get("store_id") or "UNKNOWN_STORE"
        order_id = row.get("order_id") or row.get("transaction_id") or row_number
        events.append({
            "event_id": make_event_id(row, row_number, store_id),
            "store_id": store_id,
            "camera_id": "POS",
            "visitor_id": f"TXN_{store_id}_{order_id}",
            "event_type": "POS_TRANSACTION",
            "timestamp": dt_str,
            "zone_id": "BILLING",
            "dwell_ms": 0,
            "is_staff": False,
            "confidence": 1.0,
            "metadata": {
                "queue_depth": None,
                "sku_zone": str(row.get("brand_name") or row.get("dep_name", "")),
                "session_seq": 1
            }
        })

    batch_size = 10
    last_response = None
    import httpx

    for i in range(0, len(events), batch_size):
        batch = events[i:i + batch_size]
        last_response = httpx.post(args.api, json={"events": batch}, timeout=30.0)
        print(f"Batch {i // batch_size + 1}: {last_response.json()}")

    print("POS transactions loaded!")
    print(f"Ingested: {last_response.json()}")


if __name__ == "__main__":
    main()
