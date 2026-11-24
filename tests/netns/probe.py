"""Run by `just proxy-check`: passes once the proxy answers again in go2rtc's new namespace.

Waits for the fake go2rtc's second boot (the one after its crash), then for the proxy's port 80
at the go2rtc container's address: only a proxy that joined the new namespace listens there.
"""

import socket
import sys
import time
import urllib.request

DEADLINE = time.monotonic() + 120


def boots() -> int:
    try:
        with urllib.request.urlopen("http://cam:554/", timeout=2) as answer:
            return int(answer.read())
    except OSError:
        return 0


def proxy_listens() -> bool:
    try:
        with socket.create_connection(("cam", 80), timeout=2):
            return True
    except OSError:
        return False


def wait(what: str, done) -> None:
    while not done():
        if time.monotonic() > DEADLINE:
            sys.exit(f"FAIL: {what} within 120 s")
        time.sleep(1)
    print(f"ok: {what}", flush=True)


wait("go2rtc crashed and restarted", lambda: boots() >= 2)
started = time.monotonic()
wait("the proxy answers in go2rtc's new namespace", proxy_listens)
print(f"proxy back {time.monotonic() - started:.0f} s after go2rtc's restart", flush=True)
