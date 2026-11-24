# Copyright (C) 2026 LunaCreatoris
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Webhook service: posts each camera state change to one HTTP endpoint, as JSON.

Every sentinel writes its copy's state to STATE_DIR/<camera>.json (sentinel/sentinel.py). The
sentinels sit in the copies' network namespaces, on camera VLANs that may not reach the endpoint,
so this one service per compose project does the posting: it reads the folder every POLL seconds
and posts each state that differs from the last one it saw for that camera.

The state volume is shared and writable by every sentinel, so any of them can write any file: each
file is treated as hostile. It is opened without following a symlink and without blocking (a
FIFO), skipped unless it is a regular file of at most MAX_FILE bytes, and its camera must be its
own file name. The body holds only the whitelisted FIELDS, each a string of at most MAX_FIELD
characters, plus "host", the compose project. A bad file is logged once by name, never by content.

A camera seen for the first time is only remembered, never posted, unless CAMS_WEBHOOK_ON_START is
true: only the changes after that are posted. At most MAX_CAMERAS cameras are tracked.

Each camera has a one-slot mailbox and its own worker, so a slow or failing endpoint never delays
another camera, and never piles up states: a newer state replaces the pending one, and a state
equal to the one last sent is dropped. A post is retried after each BACKOFF delay, then logged and
dropped; a 4xx answer other than 408 and 429 is dropped at once. Changes while the endpoint is down
are never replayed beyond that latest state. Redirects are never followed (they would carry the
token elsewhere). Neither the token nor the URL's path (an n8n webhook's secret) is ever logged.

Environment: CAMS_WEBHOOK_URL (empty: off, and the service idles), CAMS_WEBHOOK_ON_START,
WEBHOOK_HOST and STATE_DIR. Optional bearer token in TOKEN_FILE.
"""

import json
import os
import signal
import stat
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

POLL = 2
TIMEOUT = 5
# Seconds before each retry; four attempts in all
BACKOFF = (2, 5, 10)
# 4xx answers worth retrying: a request timeout and a throttle; any other 4xx is final
RETRY_4XX = {408, 429}
STATES = {"online", "offline"}
TOKEN_FILE = "/run/secrets/CAMS_WEBHOOK_TOKEN"
# A state file is about 120 bytes; anything bigger is not one
MAX_FILE = 4096
FIELDS = ("camera", "state", "since", "source")
MAX_FIELD = 64
MAX_CAMERAS = 256
# Linux flags; the service only runs in its Linux image, the fallbacks keep the module importable
OPEN_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)


def log(line: str) -> None:
    print(line, flush=True)


def clean(data, camera: str) -> dict:
    """The whitelisted fields of one state file; ValueError when it is not a valid state of camera."""
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    out = {}
    for field in FIELDS:
        value = data.get(field)
        if not isinstance(value, str) or len(value) > MAX_FIELD:
            raise ValueError(f"{field} is not a string of at most {MAX_FIELD} characters")
        out[field] = value
    if out["state"] not in STATES:
        raise ValueError("state is neither online nor offline")
    if out["camera"] != camera:
        raise ValueError("camera is not the file's name")
    return out


def read_state(path: str, camera: str) -> dict:
    """One state file, read without trusting it; raises on anything wrong."""
    fd = os.open(path, OPEN_FLAGS)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("not a regular file")
        if info.st_size > MAX_FILE:
            raise ValueError(f"over {MAX_FILE} bytes")
        raw = b""
        while len(raw) <= MAX_FILE:
            chunk = os.read(fd, MAX_FILE + 1 - len(raw))
            if not chunk:
                break
            raw += chunk
    finally:
        os.close(fd)
    if len(raw) > MAX_FILE:
        raise ValueError(f"over {MAX_FILE} bytes")
    return clean(json.loads(raw), camera)


def reason(error: Exception) -> str:
    """Why a file was skipped, without anything it holds."""
    if type(error) is ValueError:
        return str(error)
    return type(error).__name__


def read_states(folder, log=log, warned=None) -> dict[str, dict]:
    """Every valid state file, by camera. Hidden (temporary) files are ignored; any other bad file is
    skipped and logged, once while it stays bad when warned (a set of names) is given."""
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return {}
    if warned is None:
        warned = set()
    else:
        warned &= set(names)
    states = {}
    for name in names:
        if name.startswith(".") or not name.endswith(".json"):
            continue
        camera = name[:-5]
        try:
            states[camera] = read_state(os.path.join(folder, name), camera)
        except Exception as error:
            if name not in warned:
                warned.add(name)
                log(f"skipped state file {ascii(name)}: {reason(error)}")
            continue
        warned.discard(name)
    return states


class Watcher:
    """The last state seen per camera, and which states are news."""

    def __init__(self, on_start: bool, limit: int = MAX_CAMERAS, log=log):
        self.on_start, self.limit, self.log = on_start, limit, log
        self.last: dict[str, str] = {}
        self.full = False

    def changes(self, states: dict) -> list[dict]:
        """The states to post; last is updated with every state seen.

        A camera seen for the first time is only remembered, unless on_start is set. Past limit
        cameras, a new one is ignored (logged once).
        """
        out = []
        for camera, data in states.items():
            if camera not in self.last:
                if len(self.last) >= self.limit:
                    if not self.full:
                        self.full = True
                        self.log(f"tracking {self.limit} cameras at most: ignoring the others")
                    continue
                self.last[camera] = data["state"]
                if self.on_start:
                    out.append(data)
                continue
            if data["state"] != self.last[camera]:
                self.last[camera] = data["state"]
                out.append(data)
        return out


def payload(data: dict, host: str) -> bytes:
    body = {field: data[field] for field in FIELDS}
    body["host"] = host
    return json.dumps(body, allow_nan=False).encode()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def post(url: str, body: bytes, token: str | None) -> int:
    """POST body as JSON; the HTTP status. Raises on a network error or a status of 300 or more."""
    headers = {"Content-Type": "application/json", "User-Agent": "moonie-cams-webhook"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with OPENER.open(request, timeout=TIMEOUT) as answer:
        return answer.status


def describe(error: Exception) -> str:
    """Why a post failed, without the URL or anything the endpoint sent back."""
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code}"
    if isinstance(error, urllib.error.URLError) and isinstance(error.reason, BaseException):
        return f"URLError({type(error.reason).__name__})"
    return type(error).__name__


def deliver(url, data, host, token, post=post, sleep=time.sleep, log=log) -> bool:
    """Post one state, retrying after each BACKOFF delay; True once the endpoint took it."""
    body, what = payload(data, host), f"{data['camera']} {data['state']}"
    attempts = len(BACKOFF) + 1
    for attempt, delay in enumerate((0, *BACKOFF), start=1):
        if delay:
            sleep(delay)
        try:
            status = post(url, body, token)
        except Exception as error:
            if isinstance(error, urllib.error.HTTPError) and 400 <= error.code < 500 and error.code not in RETRY_4XX:
                log(f"dropped {what}: HTTP {error.code}, not retried")
                return False
            log(f"send {what} failed (attempt {attempt}/{attempts}): {describe(error)}")
            continue
        log(f"sent {what} (HTTP {status})")
        return True
    log(f"gave up on {what}")
    return False


class Mailbox:
    """One camera's single pending state, and the state last sent."""

    def __init__(self):
        self.ready = threading.Condition()
        self.pending: dict | None = None
        self.sent: str | None = None


class Mailboxes:
    """One mailbox and one thread per camera: in order for a camera, independent across cameras,
    and never more than one pending state each."""

    def __init__(self, send):
        self.send, self.boxes, self.lock = send, {}, threading.Lock()

    def put(self, data: dict) -> None:
        with self.lock:
            box = self.boxes.get(data["camera"])
            if box is None:
                box = self.boxes[data["camera"]] = Mailbox()
                threading.Thread(target=self.work, args=(box,), daemon=True).start()
        with box.ready:
            box.pending = data
            box.ready.notify()

    def work(self, box: Mailbox) -> None:
        while True:
            with box.ready:
                while box.pending is None:
                    box.ready.wait()
                data, box.pending = box.pending, None
                if data["state"] == box.sent:
                    continue
            if self.send(data):
                with box.ready:
                    box.sent = data["state"]


def run(folder, watcher: Watcher, put, read=read_states, sleep=time.sleep, log=log) -> None:
    warned: set = set()
    while True:
        try:
            for data in watcher.changes(read(folder, log=log, warned=warned)):
                put(data)
        except Exception as error:
            log(f"poll failed: {type(error).__name__}")
        sleep(POLL)


def settings(env, token_file, log=log):
    """(url, token or None, on_start), or None when the webhook is off (logged once)."""
    url = env.get("CAMS_WEBHOOK_URL", "").strip()
    if not url:
        log("webhook off: CAMS_WEBHOOK_URL is empty")
        return None
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        log("webhook off: CAMS_WEBHOOK_URL must be an http:// or https:// URL")
        return None
    token = None
    if os.path.lexists(token_file):
        try:
            with open(token_file, encoding="utf-8") as file:
                text = file.read()
        except (OSError, ValueError):
            log("webhook off: cannot read secrets/CAMS_WEBHOOK_TOKEN (it must be readable by uid 65534)")
            return None
        token = text.removesuffix("\n").removesuffix("\r")
        if not token or not all("!" <= c <= "~" for c in token):
            log("webhook off: secrets/CAMS_WEBHOOK_TOKEN must hold one line of printable ASCII, no spaces")
            return None
    on_start = env.get("CAMS_WEBHOOK_ON_START", "").strip().lower() == "true"
    where = f"{parts.scheme}://{parts.netloc.rpartition('@')[2]}"
    log(f"posting state changes to {where}" + (" with a bearer token" if token else ""))
    return url, token, on_start


def idle() -> None:
    while True:
        time.sleep(3600)


def main(env=os.environ) -> int:
    def stop(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    try:
        found = settings(env, env.get("TOKEN_FILE", TOKEN_FILE))
        if found is None:
            idle()
            return 0
        url, token, on_start = found
        host = env.get("WEBHOOK_HOST", "")
        boxes = Mailboxes(lambda data: deliver(url, data, host, token))
        run(env.get("STATE_DIR", "/state"), Watcher(on_start), boxes.put)
    except SystemExit as done:
        return done.code or 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
