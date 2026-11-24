# Copyright (C) 2026 LunaCreatoris
# SPDX-License-Identifier: AGPL-3.0-or-later
"""State sentinel: a camera copy goes dark while its source camera is down.

Runs in the copy's network namespace (compose network_mode: service:cam-<name>) with NET_ADMIN only.
Every INTERVAL seconds it opens a TCP connection to the source's host:port and closes it at once:
no session is ever opened, since a Tapo allows one connection per stream. No ping: a camera whose
streaming service hangs still answers ICMP.

DOWN_AFTER failed probes in a row close the copy: an nft table drops every inbound packet but
loopback, the source's own address, the reply direction of flows the copy itself opened (the DNS
answers Docker's resolver forwards from this namespace, the probe, the proxy to the camera, an
RTMP publish) and IPv6 neighbour discovery, which conntrack does not track. A client's flow, new
or established before the copy closed, is original direction from the client's side and stays
dropped. Drop, never reject, so the copy looks unplugged. UP_AFTER successes in a row remove the
table. At start, the first probe sets the state at once.

Docker joins the copy's namespace only when this container starts. If go2rtc restarts, it gets a
new namespace with no rules; this process notices that go2rtc is gone from 127.0.0.1:WATCH_PORT and
exits, and the restart policy starts it again in the new namespace, where it probes afresh.

SIGTERM removes the table before exiting. A hard kill leaves the table in the copy's namespace: the
copy stays closed until the sentinel starts again (it re-probes and applies) or go2rtc restarts.

Each state, the first one and every change, is also written to STATE_DIR/<CAMERA>.json for the
webhook service (webhook/webhook.py): {"camera", "state": "online"|"offline", "since" (UTC ISO),
"source": "<host>:<port>"}, written whole under a hidden name, then renamed over the old one, so a
reader never sees half a file. No STATE_DIR, no file. A failed write is logged and never stops the
sentinel. The file holds no secret.

Environment: MIRROR_HOST, MIRROR_PORT (the source, never its account), WATCH_PORT, and optionally
CAMERA and STATE_DIR.
"""

import json
import os
import signal
import socket
import subprocess
import sys
import time

INTERVAL = 5
TIMEOUT = 2
DOWN_AFTER = 3
UP_AFTER = 2
WATCH_FAILURES = 3
TABLE = "moonie_mirror"


class Debounce:
    """Online/offline from probe results; feed returns the new state on a change, else None."""

    def __init__(self):
        self.online = None
        self.run = 0

    def feed(self, ok: bool):
        if self.online is None:
            self.online = ok
            return ok
        if ok == self.online:
            self.run = 0
            return None
        self.run += 1
        if self.run < (UP_AFTER if ok else DOWN_AFTER):
            return None
        self.online, self.run = ok, 0
        return ok


def probe(host: str, port: int) -> tuple[bool, set[str]]:
    """(reachable, the addresses host resolves to). Connect, then close: nothing is sent."""
    try:
        addrs = {info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    except OSError:
        return False, set()
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT):
            return True, addrs
    except OSError:
        return False, addrs


def ruleset(online: bool, addrs: set[str]) -> str:
    """An nft script that replaces the table atomically; online leaves no table at all."""
    script = f"table inet {TABLE}\ndelete table inet {TABLE}\n"
    if online:
        return script
    # A link-local address comes with its zone (fe80::1%eth0), which nft cannot parse
    plain = sorted({a.split("%", 1)[0] for a in addrs})
    allow = "".join(f"    {'ip6' if ':' in a else 'ip'} saddr {a} accept\n" for a in plain)
    return (
        f"{script}table inet {TABLE} {{\n  chain input {{\n"
        "    type filter hook input priority filter; policy drop;\n"
        '    iif "lo" accept\n'
        "    ct direction reply accept\n"
        "    icmpv6 type { nd-neighbor-solicit, nd-neighbor-advert, nd-router-advert } accept\n"
        f"{allow}  }}\n}}\n"
    )


def apply(online: bool, addrs: set[str], run=subprocess.run) -> None:
    if run(["nft", "-f", "-"], input=ruleset(online, addrs), text=True).returncode != 0:
        raise RuntimeError("nft failed")


def reachable(port: int) -> bool:
    """True when go2rtc listens on localhost, that is, when this process shares its namespace."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=TIMEOUT):
            return True
    except OSError:
        return False


def log(line: str) -> None:
    print(line, flush=True)


def now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def source_label(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def write_state(folder, camera: str, online: bool, source: str, now=now_utc) -> None:
    """Write folder/<camera>.json atomically: in full under a hidden name, then renamed over it."""
    data = {"camera": camera, "state": "online" if online else "offline", "since": now(), "source": source}
    tmp = os.path.join(folder, f".{camera}.json.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as file:
        json.dump(data, file)
        file.write("\n")
    os.replace(tmp, os.path.join(folder, f"{camera}.json"))


def recorder(folder: str, camera: str, source: str, log=log):
    """What the loop calls with each new state: writes the state file, or nothing without a folder."""

    def record(online: bool) -> None:
        if not folder:
            return
        try:
            write_state(folder, camera, online, source)
        except OSError as error:
            log(f"cannot write the state file of {camera}: {type(error).__name__}")

    return record


def loop(
    host,
    port,
    watch,
    probe=probe,
    reachable=reachable,
    apply=apply,
    sleep=time.sleep,
    log=log,
    exit=sys.exit,
    record=lambda online: None,
):
    state, applied, missed = Debounce(), set(), 0
    while True:
        ok, addrs = probe(host, port)
        addrs = addrs or applied
        change = state.feed(ok)
        if change is not None or (state.online is False and addrs != applied):
            apply(state.online, addrs)
            applied = addrs
            if change is not None:
                log(f"source {host}:{port} {'reachable: copy open' if change else 'unreachable: copy closed'}")
                record(change)
        missed = 0 if reachable(watch) else missed + 1
        if missed >= WATCH_FAILURES:
            log(f"go2rtc unreachable on 127.0.0.1:{watch}; exiting to rejoin its network")
            exit(1)
        sleep(INTERVAL)


def main() -> int:
    host, port, watch = os.environ["MIRROR_HOST"], int(os.environ["MIRROR_PORT"]), int(os.environ["WATCH_PORT"])

    def stop(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    record = recorder(os.environ.get("STATE_DIR", ""), os.environ.get("CAMERA", host), source_label(host, port))
    try:
        loop(host, port, watch, record=record)
    except SystemExit as done:
        return done.code
    finally:
        # Never leave the copy closed behind a stopped sentinel
        apply(True, set())
    return 0


if __name__ == "__main__":
    sys.exit(main())
