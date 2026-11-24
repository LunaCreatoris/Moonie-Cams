"""The webhook service: reads the sentinels' state files and posts each change."""

import http.server
import json
import os
import threading
import urllib.error

import pytest

import webhook

TOKEN = "t0k3n-s3cr3t"


def state(camera="cam", value="offline", since="2026-11-23T10:00:00Z"):
    return {"camera": camera, "state": value, "since": since, "source": "192.0.2.2:554"}


def write(folder, data):
    (folder / f"{data['camera']}.json").write_text(json.dumps(data), encoding="utf-8")


BEFORE = "2026-11-23T09:59:59Z"
AFTER = "2026-11-23T10:00:05Z"


# Reading the state folder


def test_read_states_reads_every_camera(tmp_path):
    write(tmp_path, state("a"))
    write(tmp_path, state("b", "online"))
    assert webhook.read_states(tmp_path) == {"a": state("a"), "b": state("b", "online")}


def test_read_states_skips_temporary_broken_and_foreign_files(tmp_path):
    write(tmp_path, state("a"))
    (tmp_path / ".a.json.tmp").write_text(json.dumps(state("a", "online")), encoding="utf-8")
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    (tmp_path / "list.json").write_text("[]", encoding="utf-8")
    (tmp_path / "odd.json").write_text(json.dumps(state("odd", "sleeping")), encoding="utf-8")
    # A file must hold its own camera
    (tmp_path / "other.json").write_text(json.dumps(state("a", "online")), encoding="utf-8")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    assert webhook.read_states(tmp_path, log=lambda line: None) == {"a": state("a")}


def test_read_states_of_a_missing_folder_is_empty(tmp_path):
    assert webhook.read_states(tmp_path / "missing") == {}


def read_with_logs(folder):
    logs = []
    return webhook.read_states(folder, log=logs.append), logs


def test_a_deeply_nested_file_is_skipped_and_logged_by_name_only(tmp_path):
    # json.loads raises RecursionError on it, which is no ValueError
    (tmp_path / "deep.json").write_text("[" * 4000, encoding="utf-8")
    write(tmp_path, state("a"))
    states, logs = read_with_logs(tmp_path)
    assert states == {"a": state("a")}
    assert len(logs) == 1 and "deep.json" in logs[0] and "[[" not in logs[0]


def test_an_oversized_file_is_skipped(tmp_path):
    (tmp_path / "big.json").write_text(json.dumps({**state("big"), "pad": "x" * 5000}), encoding="utf-8")
    states, logs = read_with_logs(tmp_path)
    assert states == {}
    assert len(logs) == 1 and "big.json" in logs[0] and "xxx" not in logs[0]


def test_a_file_of_exactly_the_cap_is_read(tmp_path):
    body = json.dumps(state("a"))
    body += " " * (webhook.MAX_FILE - len(body))
    (tmp_path / "a.json").write_text(body, encoding="utf-8")
    assert webhook.read_states(tmp_path) == {"a": state("a")}


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks on this platform")
def test_a_symlink_is_never_followed(tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    write(target, state("a"))
    (tmp_path / "a.json").symlink_to(target / "a.json")
    states, logs = read_with_logs(tmp_path)
    assert states == {}
    assert len(logs) == 1 and "a.json" in logs[0]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are POSIX only")
def test_a_fifo_is_skipped_without_hanging(tmp_path):
    os.mkfifo(tmp_path / "a.json")
    result = []
    reader = threading.Thread(target=lambda: result.append(read_with_logs(tmp_path)), daemon=True)
    reader.start()
    reader.join(5)
    assert not reader.is_alive(), "reading a FIFO hung"
    states, logs = result[0]
    assert states == {}
    assert len(logs) == 1 and "a.json" in logs[0]


def test_a_folder_named_like_a_state_file_is_skipped(tmp_path):
    (tmp_path / "a.json").mkdir()
    states, logs = read_with_logs(tmp_path)
    assert states == {} and len(logs) == 1


def test_a_bad_file_is_logged_once_until_it_is_fixed(tmp_path):
    logs, warned = [], set()
    (tmp_path / "a.json").write_text("{", encoding="utf-8")
    for _ in range(3):
        webhook.read_states(tmp_path, log=logs.append, warned=warned)
    assert len(logs) == 1
    write(tmp_path, state("a"))
    assert webhook.read_states(tmp_path, log=logs.append, warned=warned) == {"a": state("a")}
    (tmp_path / "a.json").write_text("{", encoding="utf-8")
    webhook.read_states(tmp_path, log=logs.append, warned=warned)
    assert len(logs) == 2


# The payload whitelist


def test_clean_keeps_only_the_whitelisted_fields():
    data = {**state(), "host": "spoofed", "extra": {"a": 1}}
    assert webhook.clean(data, "cam") == state()


@pytest.mark.parametrize(
    "bad",
    [
        {"state": "sleeping"},
        {"state": None},
        {"since": 1700000000},
        {"source": None},
        {"since": "x" * 65},
        {"source": "x" * 65},
        {"camera": "other"},
        {"since": float("nan")},
    ],
)
def test_clean_refuses_a_bad_field(bad):
    with pytest.raises(ValueError):
        webhook.clean({**state(), **bad}, "cam")


@pytest.mark.parametrize("missing", ["camera", "state", "since", "source"])
def test_clean_refuses_a_missing_field(missing):
    data = state()
    del data[missing]
    with pytest.raises(ValueError):
        webhook.clean(data, "cam")


def test_clean_refuses_what_is_not_an_object():
    for data in ([], "cam", None, 1):
        with pytest.raises(ValueError):
            webhook.clean(data, "cam")


def test_a_field_of_64_characters_is_kept():
    data = state(since="x" * 64)
    assert webhook.clean(data, "cam") == data


def test_a_file_whose_camera_is_not_its_name_is_skipped_and_logged(tmp_path):
    # Any sentinel can write any file of the shared volume: a file speaks for its own camera only
    (tmp_path / "a.json").write_text(json.dumps(state("b", "online")), encoding="utf-8")
    states, logs = read_with_logs(tmp_path)
    assert states == {}
    assert len(logs) == 1 and "a.json" in logs[0]


# Change detection and start behaviour


def watcher(on_start=False, limit=webhook.MAX_CAMERAS):
    logs = []
    return webhook.Watcher(on_start, limit=limit, log=logs.append), logs


def test_a_change_is_sent_once():
    watch, _ = watcher()
    watch.last = {"cam": "online"}
    assert watch.changes({"cam": state()}) == [state()]
    assert watch.last == {"cam": "offline"}
    assert watch.changes({"cam": state()}) == []


def test_a_rewritten_file_with_the_same_state_is_not_sent():
    # A sentinel that restarts (go2rtc restarted) writes its state again with a new since
    watch, _ = watcher()
    watch.last = {"cam": "offline"}
    assert watch.changes({"cam": state(since="2026-11-23T11:00:00Z")}) == []


@pytest.mark.parametrize("since", [BEFORE, AFTER, "yesterday"])
def test_a_first_seen_camera_is_never_sent(since):
    # Whatever its since: only the changes after the first sight are posted
    watch, _ = watcher()
    assert watch.changes({"cam": state(since=since)}) == []
    assert watch.last == {"cam": "offline"}
    assert watch.changes({"cam": state(value="online", since=AFTER)}) == [state(value="online", since=AFTER)]


def test_on_start_sends_the_first_seen_states_too():
    watch, _ = watcher(on_start=True)
    assert watch.changes({"cam": state(since=BEFORE)}) == [state(since=BEFORE)]
    # A camera that appears later is first seen too
    assert watch.changes({"cam": state(since=BEFORE), "new": state("new")}) == [state("new")]


def test_the_cameras_tracked_are_capped_and_logged_once():
    watch, logs = watcher(on_start=True, limit=2)
    states = {name: state(name) for name in ("a", "b", "c", "d")}
    assert watch.changes(states) == [state("a"), state("b")]
    assert watch.changes(states) == []
    assert set(watch.last) == {"a", "b"}
    assert len(logs) == 1 and "2" in logs[0]


def test_the_cap_is_256_cameras():
    assert webhook.MAX_CAMERAS == 256


class Stop(BaseException):
    pass


def test_run_hands_each_change_to_its_mailbox():
    reads = [{"cam": state(since=BEFORE)}, {"cam": state(value="online", since=AFTER)}]
    sent = []

    def sleep(seconds):
        if not reads:
            raise Stop

    with pytest.raises(Stop):
        webhook.run("/state", watcher()[0], sent.append, read=lambda folder, **kw: reads.pop(0), sleep=sleep)
    assert sent == [state(value="online", since=AFTER)]


def test_one_bad_poll_pass_never_kills_the_loop():
    reads = [RuntimeError("boom"), {"cam": state()}, {"cam": state(value="online")}]
    sent, logs = [], []

    def read(folder, **kwargs):
        item = reads.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def sleep(seconds):
        if not reads:
            raise Stop

    with pytest.raises(Stop):
        webhook.run("/state", watcher()[0], sent.append, read=read, sleep=sleep, log=logs.append)
    assert sent == [state(value="online")]
    assert logs == ["poll failed: RuntimeError"]


def test_polls_every_two_seconds():
    assert webhook.POLL == 2


# Payload, token and retries


def test_payload_is_the_whitelisted_state_plus_the_host():
    assert json.loads(webhook.payload({**state(), "extra": 1}, "moonie-cams")) == {**state(), "host": "moonie-cams"}


def test_payload_refuses_nan():
    with pytest.raises(ValueError):
        webhook.payload(state(since=float("nan")), "moonie-cams")


class Receiver(http.server.BaseHTTPRequestHandler):
    seen: list = []
    status = 200

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Receiver.seen.append((self.path, dict(self.headers), json.loads(body)))
        self.send_response(Receiver.status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def receiver():
    Receiver.seen, Receiver.status = [], 200
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/webhook/abc"
    server.shutdown()
    server.server_close()


def test_post_sends_json_with_the_bearer_token(receiver):
    assert webhook.post(receiver, webhook.payload(state(), "moonie-cams"), TOKEN) == 200
    path, headers, body = Receiver.seen[0]
    assert path == "/webhook/abc"
    assert headers["Content-Type"] == "application/json"
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert body == {**state(), "host": "moonie-cams"}


def test_post_without_a_token_sends_no_authorization(receiver):
    webhook.post(receiver, webhook.payload(state(), "moonie-cams"), None)
    assert "Authorization" not in Receiver.seen[0][1]


def test_post_raises_on_an_error_status(receiver):
    Receiver.status = 500
    with pytest.raises(urllib.error.HTTPError):
        webhook.post(receiver, b"{}", None)


def test_post_never_follows_a_redirect(receiver):
    # A redirect would carry the token to another address
    Receiver.status = 302
    with pytest.raises(urllib.error.HTTPError):
        webhook.post(receiver, b"{}", TOKEN)
    assert len(Receiver.seen) == 1


def test_deliver_retries_with_backoff_then_gives_up():
    calls, sleeps, logs = [], [], []

    def post(url, body, token):
        calls.append(body)
        raise urllib.error.URLError(OSError(f"refused {TOKEN} {url}"))

    assert (
        webhook.deliver(
            "http://n8n:5678/webhook/x", state(), "h", TOKEN, post=post, sleep=sleeps.append, log=logs.append
        )
        is False
    )
    assert len(calls) == 1 + len(webhook.BACKOFF)
    assert sleeps == list(webhook.BACKOFF)
    assert sleeps == sorted(sleeps) and len(sleeps) >= 2
    assert logs[-1] == "gave up on cam offline"
    # Neither the token nor the URL (its path is the webhook's secret) reaches the logs
    assert not any(TOKEN in line or "webhook/x" in line for line in logs)


def test_deliver_stops_at_the_first_success():
    calls, logs = [], []

    def post(url, body, token):
        calls.append(body)
        if len(calls) == 1:
            raise urllib.error.HTTPError(url, 503, "busy", {}, None)
        return 200

    assert webhook.deliver("http://x/", state(), "h", TOKEN, post=post, sleep=lambda s: None, log=logs.append)
    assert len(calls) == 2
    assert logs == ["send cam offline failed (attempt 1/4): HTTP 503", "sent cam offline (HTTP 200)"]


@pytest.mark.parametrize("code", [400, 401, 403, 404, 410, 422])
def test_deliver_fails_fast_on_a_client_error(code):
    calls, sleeps, logs = [], [], []

    def post(url, body, token):
        calls.append(body)
        raise urllib.error.HTTPError(url, code, "no", {}, None)

    assert webhook.deliver("http://x/", state(), "h", TOKEN, post=post, sleep=sleeps.append, log=logs.append) is False
    assert len(calls) == 1 and sleeps == []
    assert logs == [f"dropped cam offline: HTTP {code}, not retried"]


@pytest.mark.parametrize("code", [408, 429, 500, 503])
def test_deliver_retries_a_timeout_a_throttle_and_a_server_error(code):
    calls = []

    def post(url, body, token):
        calls.append(body)
        raise urllib.error.HTTPError(url, code, "busy", {}, None)

    quiet = {"sleep": lambda s: None, "log": lambda s: None}
    assert webhook.deliver("http://x/", state(), "h", TOKEN, post=post, **quiet) is False
    assert len(calls) == 1 + len(webhook.BACKOFF)


def wait_for(check, limit=5.0):
    event = threading.Event()
    for _ in range(int(limit / 0.02)):
        if check():
            return True
        event.wait(0.02)
    return check()


def test_one_camera_never_waits_for_another():
    # Each camera has its own mailbox and worker: a webhook stuck on one camera's retries holds no other one
    release, sent = threading.Event(), []

    def send(data):
        if data["camera"] == "slow":
            release.wait(5)
        sent.append(data["camera"])

    boxes = webhook.Mailboxes(send)
    boxes.put(state("slow"))
    boxes.put(state("fast"))
    assert wait_for(lambda: sent)
    assert sent == ["fast"]
    release.set()


def test_flaps_while_the_endpoint_is_down_give_only_the_latest_state():
    # The first post is stuck (endpoint down); meanwhile the camera flaps four times
    release, started, sent = threading.Event(), threading.Event(), []

    def send(data):
        started.set()
        release.wait(5)
        sent.append(data["state"])
        return True

    boxes = webhook.Mailboxes(send)
    boxes.put(state(value="offline"))
    assert started.wait(5)
    for value in ("online", "offline", "online", "offline", "online"):
        boxes.put(state(value=value))
    release.set()
    assert wait_for(lambda: len(sent) >= 2)
    wait_for(lambda: False, 0.3)
    assert sent == ["offline", "online"]


def test_a_pending_state_equal_to_the_one_just_sent_is_dropped():
    release, started, sent = threading.Event(), threading.Event(), []

    def send(data):
        started.set()
        release.wait(5)
        sent.append(data["state"])
        return True

    boxes = webhook.Mailboxes(send)
    boxes.put(state(value="offline"))
    assert started.wait(5)
    # Back online, then down again before the endpoint answered: the endpoint ends where it was told
    boxes.put(state(value="online"))
    boxes.put(state(value="offline"))
    release.set()
    assert wait_for(lambda: sent)
    wait_for(lambda: False, 0.3)
    assert sent == ["offline"]


def test_the_newest_pending_state_replaces_the_older_one():
    release, started, sent = threading.Event(), threading.Event(), []

    def send(data):
        started.set()
        release.wait(5)
        sent.append(data["since"])
        return True

    boxes = webhook.Mailboxes(send)
    boxes.put(state(value="offline", since="t1"))
    assert started.wait(5)
    boxes.put(state(value="online", since="t2"))
    boxes.put(state(value="online", since="t3"))
    release.set()
    assert wait_for(lambda: len(sent) >= 2)
    assert sent == ["t1", "t3"]


def test_a_camera_keeps_its_order():
    sent, done = [], threading.Event()

    def send(data):
        sent.append(data["state"])
        if len(sent) == 3:
            done.set()
        return True

    boxes = webhook.Mailboxes(send)
    for count, value in enumerate(("offline", "online", "offline"), start=1):
        boxes.put(state(value=value))
        assert wait_for(lambda count=count: len(sent) >= count)
    assert done.wait(5)
    assert sent == ["offline", "online", "offline"]


# Settings


def settings(tmp_path, **env):
    logs = []
    env.setdefault("CAMS_WEBHOOK_URL", "http://n8n:5678/webhook/abc")
    result = webhook.settings(env, tmp_path / "CAMS_WEBHOOK_TOKEN", log=logs.append)
    return result, logs


def test_an_empty_url_turns_the_webhook_off(tmp_path):
    result, logs = settings(tmp_path, CAMS_WEBHOOK_URL="  ")
    assert result is None
    assert logs == ["webhook off: CAMS_WEBHOOK_URL is empty"]


@pytest.mark.parametrize("url", ["n8n:5678/webhook", "ftp://n8n/x", "file:///etc/passwd", "http://"])
def test_a_url_that_is_not_http_turns_the_webhook_off(tmp_path, url):
    result, logs = settings(tmp_path, CAMS_WEBHOOK_URL=url)
    assert result is None
    assert logs == ["webhook off: CAMS_WEBHOOK_URL must be an http:// or https:// URL"]


def test_settings_read_the_token_and_never_log_it(tmp_path):
    (tmp_path / "CAMS_WEBHOOK_TOKEN").write_text(TOKEN + "\n", encoding="utf-8")
    result, logs = settings(tmp_path, CAMS_WEBHOOK_ON_START="true")
    assert result == ("http://n8n:5678/webhook/abc", TOKEN, True)
    assert logs == ["posting state changes to http://n8n:5678 with a bearer token"]
    assert not any(TOKEN in line or "/webhook/abc" in line for line in logs)


def test_settings_without_a_token_file(tmp_path):
    result, logs = settings(tmp_path)
    assert result == ("http://n8n:5678/webhook/abc", None, False)
    assert logs == ["posting state changes to http://n8n:5678"]


@pytest.mark.parametrize("value", ["", "\n", "a\nb", "a b", "café"])
def test_a_bad_token_turns_the_webhook_off(tmp_path, value):
    (tmp_path / "CAMS_WEBHOOK_TOKEN").write_text(value, encoding="utf-8")
    result, logs = settings(tmp_path)
    assert result is None
    assert logs == ["webhook off: secrets/CAMS_WEBHOOK_TOKEN must hold one line of printable ASCII, no spaces"]


def test_an_unreadable_token_turns_the_webhook_off(tmp_path):
    (tmp_path / "CAMS_WEBHOOK_TOKEN").mkdir()
    result, logs = settings(tmp_path)
    assert result is None
    assert logs == ["webhook off: cannot read secrets/CAMS_WEBHOOK_TOKEN (it must be readable by uid 65534)"]


@pytest.mark.parametrize(
    ("value", "expected"), [("true", True), ("TRUE", True), ("false", False), ("", False), ("1", False)]
)
def test_on_start_is_off_unless_true(tmp_path, value, expected):
    result, _ = settings(tmp_path, CAMS_WEBHOOK_ON_START=value)
    assert result[2] is expected


def test_main_idles_when_off(monkeypatch):
    idled = []
    monkeypatch.setattr(webhook.signal, "signal", lambda *a: None)
    monkeypatch.setattr(webhook, "idle", lambda: idled.append(True))
    monkeypatch.setattr(webhook, "log", lambda line: None)
    assert webhook.main({"CAMS_WEBHOOK_URL": ""}) == 0
    assert idled == [True]
