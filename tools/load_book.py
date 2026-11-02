"""Manual booking load probe. Never run automatically or against OpenRouter.

Run a local API with ScriptedLLM and at least 21 available appointments.
Example: python tools/load_book.py --base-url http://localhost:8000 --patient PT-LOAD
"""
import argparse
from collections import Counter
from urllib.parse import urlparse
from uuid import uuid4

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--patient", default="PT-LOAD")
    args = parser.parse_args()
    if "openrouter" in (urlparse(args.base_url).hostname or "").lower():
        parser.error("Use a running Sahyog API backed by ScriptedLLM, never OpenRouter")
    counts = Counter()
    pending = None
    with httpx.Client(base_url=args.base_url, timeout=60,
                      headers={"Authorization": f"Bearer demo-{args.patient}"}) as client:
        for _ in range(1000):
            if pending is None:
                triage = client.post("/w1/triage", headers={"Idempotency-Key": str(uuid4())},
                                     json={"text": "Lower back ache for three weeks", "age": 41})
                triage.raise_for_status()
                state = triage.json()
                pause = state.get("interrupt") or {}
                if pause.get("type") != "pick_slot":
                    raise RuntimeError("Probe needs scripted matching and available slots")
                pending = (f"/w1/{state['thread_id']}/resume", pause["options"][0]["slot_id"])
            response = client.post(pending[0], headers={"Idempotency-Key": str(uuid4())},
                                   json={"value": pending[1]})
            if response.status_code == 429:
                counts["429"] += 1
            elif response.is_success and response.json().get("booking_id"):
                counts["succeeded"] += 1
                pending = None
            else:
                counts["other"] += 1
                response.raise_for_status()
                raise RuntimeError("Booking did not complete; use sufficient fresh synthetic slots")
    print(f"attempts=1000 succeeded={counts['succeeded']} 429={counts['429']} other={counts['other']}")


if __name__ == "__main__":
    main()
