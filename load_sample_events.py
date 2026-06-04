import argparse
import json

import httpx


def main():
    parser = argparse.ArgumentParser(description="Load challenge sample_events.jsonl into the API.")
    parser.add_argument("--events", default="data/challenge/sample_events.jsonl")
    parser.add_argument("--api", default="http://127.0.0.1:8001/events/ingest")
    args = parser.parse_args()

    events = []
    with open(args.events, encoding="utf-8") as event_file:
        for line in event_file:
            line = line.strip()
            if line:
                events.append(json.loads(line))

    response = httpx.post(args.api, json={"events": events}, timeout=30.0)
    print(response.json())


if __name__ == "__main__":
    main()
