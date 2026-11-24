"""Run by `just webhook-check`: waits for what the sentinel wrote, or for what the endpoint got.

expect.py file online|offline [SECONDS]   the state file says so
expect.py quiet [SECONDS]                 the endpoint gets nothing for SECONDS
expect.py online|offline [SECONDS]        the endpoint's last POST says so, in full
"""

import json
import sys
import time
import urllib.request

RECEIVER = "http://receiver:8080/"
TOKEN = "webhook-check-token"


def received() -> list:
    with urllib.request.urlopen(RECEIVER, timeout=2) as answer:
        return json.load(answer)


def state_file() -> str | None:
    try:
        with open("/state/cam.json", encoding="utf-8") as file:
            return json.load(file)["state"]
    except (OSError, ValueError, KeyError):
        return None


def wait(check, what: str, limit: float) -> None:
    started = time.monotonic()
    while not check():
        if time.monotonic() - started > limit:
            sys.exit(f"FAIL: {what} within {limit:.0f} s")
        time.sleep(1)
    print(f"ok: {what} after {time.monotonic() - started:.0f} s", flush=True)


def posted(state: str) -> None:
    posts = received()
    last = posts[-1]
    body = last["body"]
    expected = {"camera": "cam", "state": state, "source": "192.0.2.10:554", "host": "moonie-cams-webhook-check"}
    if {k: body.get(k) for k in expected} != expected or set(body) != {*expected, "since"}:
        sys.exit(f"FAIL: unexpected payload {body}")
    if last["auth"] != f"Bearer {TOKEN}" or last["type"] != "application/json" or last["path"] != "/webhook/check":
        sys.exit("FAIL: wrong path, content type or Authorization header")
    if [p["body"]["state"] for p in posts].count(state) != 1:
        sys.exit(f"FAIL: {state} posted more than once: {[p['body']['state'] for p in posts]}")
    print(f"payload: {json.dumps(body)}", flush=True)


def main() -> None:
    args = sys.argv[1:]
    if args[0] == "file":
        wait(lambda: state_file() == args[1], f"the state file says {args[1]}", float(args[2]) if len(args) > 2 else 30)
    elif args[0] == "quiet":
        limit = float(args[1]) if len(args) > 1 else 6
        time.sleep(limit)
        if received():
            sys.exit(f"FAIL: the endpoint got an old state at start: {received()}")
        print(f"ok: nothing posted at start for {limit:.0f} s", flush=True)
    else:
        limit = float(args[1]) if len(args) > 1 else 30

        def got() -> bool:
            posts = received()
            return bool(posts) and posts[-1]["body"].get("state") == args[0]

        wait(got, f"the endpoint got {args[0]}", limit)
        posted(args[0])


main()
