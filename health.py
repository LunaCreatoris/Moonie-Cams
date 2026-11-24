# Copyright (C) 2026 LunaCreatoris
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Health per output of one camera copy: source state, clients, bitrate and drops.

Runs inside the camera's go2rtc container (its image ships Python), with `just health <name>`:
    python3 health.py [SECONDS]

It reads go2rtc's read-only /api/health (go2rtc/health-endpoint.patch) twice, SECONDS apart,
and turns the byte counters into kbit/s. The endpoint never carries a URL, an address or a
secret, and the report keeps only stream names, states and counters on top of that.
"""

import json
import sys
import time
import urllib.request

HEALTH_URL = "http://127.0.0.1:80/api/health"


def kbps(before: int, after: int, seconds: float) -> int:
    # A client that left takes its bytes with it; never report a negative rate.
    return max(0, round((after - before) * 8 / 1000 / seconds))


def outputs_of(stream: dict) -> dict:
    """Clients, bytes sent and drops per output format, go2rtc's own loopback readers left out."""
    outputs: dict = {}
    for cons in stream.get("consumers", []):
        if cons.get("internal"):
            continue
        out = outputs.setdefault(cons.get("format_name") or "unknown", {"clients": 0, "bytes": 0, "drops": 0})
        out["clients"] += 1
        out["bytes"] += cons.get("bytes_send", 0)
        out["drops"] += cons.get("drops", 0)
    return outputs


def summarize(before: dict, after: dict, seconds: float) -> dict:
    report = {}
    for name in sorted(after):
        old, new = before.get(name, {}), after[name]
        old_out, new_out = outputs_of(old), outputs_of(new)
        received = (sum(p.get("bytes_recv", 0) for p in s.get("producers", [])) for s in (old, new))
        report[name] = {
            "source": [p.get("state", "unknown") for p in new.get("producers", [])],
            "in_kbps": kbps(*received, seconds),
            "readers": sum(1 for c in new.get("consumers", []) if c.get("internal")),
            "outputs": {
                fmt: {
                    "clients": out["clients"],
                    "kbps": kbps(old_out.get(fmt, {}).get("bytes", 0), out["bytes"], seconds),
                    "drops": out["drops"],
                }
                for fmt, out in sorted(new_out.items())
            },
        }
    return report


def read(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.load(response)


def main(argv: list[str]) -> int:
    seconds = float(argv[0]) if argv else 2.0
    before = read(HEALTH_URL)
    time.sleep(seconds)
    print(json.dumps(summarize(before, read(HEALTH_URL), seconds), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
