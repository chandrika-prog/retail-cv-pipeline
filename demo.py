import argparse
import subprocess
import sys
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parent
DB_PATHS = [
    ROOT / "app" / "store.db",
    ROOT / ".runtime" / "store.db",
]
REQUIRED_FILES = [
    ROOT / "data" / "challenge" / "Store 1" / "CAM 3 - entry.mp4",
    ROOT / "data" / "challenge" / "Store 1" / "CAM 1 - zone.mp4",
    ROOT / "data" / "challenge" / "Store 1" / "CAM 2 - zone.mp4",
    ROOT / "data" / "challenge" / "Store 1" / "CAM 5 - billing.mp4",
    ROOT / "data" / "challenge" / "Store 2" / "entry 1.mp4",
    ROOT / "data" / "challenge" / "Store 2" / "entry 2.mp4",
    ROOT / "data" / "challenge" / "Store 2" / "zone.mp4",
    ROOT / "data" / "challenge" / "Store 2" / "billing_area.mp4",
    ROOT / "data" / "challenge" / "pos_transactions.csv",
]


def api_events_url(api_base: str) -> str:
    base = api_base.rstrip("/")
    if base.endswith("/events/ingest"):
        return base
    return f"{base}/events/ingest"


def reset_demo(_args):
    for path in DB_PATHS:
        if path.exists():
            path.unlink()
            print(f"deleted {path}")
        else:
            print(f"not found {path}")
    (ROOT / ".runtime").mkdir(exist_ok=True)
    (ROOT / "outputs").mkdir(exist_ok=True)
    print("demo DB reset complete")


def check_demo(_args):
    missing = [path for path in REQUIRED_FILES if not path.exists()]
    if missing:
        print("missing required files:")
        for path in missing:
            print(f"  {path}")
        return 1
    print("dataset check passed")
    print("dashboard stores: ST1, ST2")
    return 0


def smoke_demo(args):
    base = args.api.rstrip("/")
    endpoints = [
        f"{base}/health",
        f"{base}/stores/ST1/metrics",
        f"{base}/stores/ST2/metrics",
        f"{base}/stores/ST1/funnel",
        f"{base}/stores/ST2/funnel",
        f"{base}/stores/ST1/heatmap",
        f"{base}/stores/ST2/heatmap",
    ]
    for endpoint in endpoints:
        response = httpx.get(endpoint, timeout=10.0)
        response.raise_for_status()
        print(f"ok {endpoint}")
    return 0


def load_pos(args):
    ingest_url = api_events_url(args.api)
    for store_id in ("ST1", "ST2"):
        print(f"loading POS for {store_id}")
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "load_pos.py"),
                "--store-id",
                store_id,
                "--api",
                ingest_url,
            ],
            check=True,
            cwd=ROOT,
        )
    print("POS loaded for ST1 and ST2")
    return 0


def print_commands(args):
    api = args.api.rstrip("/")
    print("1. Reset DB")
    print("   python demo.py reset")
    print()
    print("2. Start API")
    print("   cd app")
    print("   python -m uvicorn main:app --host 127.0.0.1 --port 8001")
    print()
    print("3. Run detection")
    print(f"   python run_all_cameras.py --api {api}")
    print()
    print("4. Load POS for Store 1 and Store 2")
    print(f"   python demo.py load-pos --api {api}")
    print()
    print("5. Open dashboard")
    print(f"   {api}/dashboard")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Store Intelligence demo helper.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    reset_parser = subparsers.add_parser("reset", help="Delete known SQLite DB files.")
    reset_parser.set_defaults(func=reset_demo)

    check_parser = subparsers.add_parser("check", help="Verify required challenge files exist.")
    check_parser.set_defaults(func=check_demo)

    smoke_parser = subparsers.add_parser("smoke", help="Call key API endpoints.")
    smoke_parser.add_argument("--api", default="http://127.0.0.1:8001")
    smoke_parser.set_defaults(func=smoke_demo)

    load_pos_parser = subparsers.add_parser("load-pos", help="Load POS transactions for ST1 and ST2.")
    load_pos_parser.add_argument("--api", default="http://127.0.0.1:8001")
    load_pos_parser.set_defaults(func=load_pos)

    commands_parser = subparsers.add_parser("commands", help="Print full local demo command sequence.")
    commands_parser.add_argument("--api", default="http://127.0.0.1:8001")
    commands_parser.set_defaults(func=print_commands)

    args = parser.parse_args()
    raise SystemExit(args.func(args) or 0)


if __name__ == "__main__":
    main()
