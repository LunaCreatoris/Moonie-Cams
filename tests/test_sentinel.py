"""The state sentinel: debounce, probe, nft ruleset, netns watch and a clean stop."""

import json
import os
import re
import signal
import socket
import subprocess

import pytest

import sentinel


def feed(results):
    """Every transition a sequence of probe results gives, as (index, state)."""
    state = sentinel.Debounce()
    return [(i, change) for i, ok in enumerate(results) if (change := state.feed(ok)) is not None]


def test_first_probe_sets_the_state_at_once():
    assert feed([True]) == [(0, True)]
    assert feed([False]) == [(0, False)]


def test_three_failures_in_a_row_go_offline():
    assert feed([True, False, False]) == [(0, True)]
    assert feed([True, False, False, False]) == [(0, True), (3, False)]


def test_two_successes_in_a_row_come_back():
    assert feed([False, True]) == [(0, False)]
    assert feed([False, True, True]) == [(0, False), (2, True)]


def test_a_flapping_source_never_moves_the_state():
    assert feed([True, False, False, True, False, False, True]) == [(0, True)]
    assert feed([False, True, False, True, False]) == [(0, False)]


def test_a_success_resets_the_failure_count():
    assert feed([True, False, False, True, False, False, False]) == [(0, True), (6, False)]


def test_state_holds_after_a_transition():
    assert feed([True, False, False, False, False, True, True, True]) == [(0, True), (3, False), (6, True)]


class FakeSocket:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_probe_connects_and_closes_at_once(monkeypatch):
    calls = []
    monkeypatch.setattr(sentinel.socket, "getaddrinfo", lambda *a, **k: [(None, None, None, "", ("192.0.2.2", 554))])

    def connect(address, timeout):
        calls.append((address, timeout))
        return FakeSocket()

    monkeypatch.setattr(sentinel.socket, "create_connection", connect)
    assert sentinel.probe("192.0.2.2", 554) == (True, {"192.0.2.2"})
    assert calls == [(("192.0.2.2", 554), sentinel.TIMEOUT)]
    assert sentinel.TIMEOUT == 2 and sentinel.INTERVAL == 5


def test_probe_keeps_the_address_of_an_unreachable_source(monkeypatch):
    monkeypatch.setattr(sentinel.socket, "getaddrinfo", lambda *a, **k: [(None, None, None, "", ("192.0.2.2", 554))])

    def refuse(address, timeout):
        raise ConnectionRefusedError

    monkeypatch.setattr(sentinel.socket, "create_connection", refuse)
    assert sentinel.probe("192.0.2.2", 554) == (False, {"192.0.2.2"})


def test_probe_fails_when_the_name_does_not_resolve(monkeypatch):
    def gaierror(*args, **kwargs):
        raise socket.gaierror

    monkeypatch.setattr(sentinel.socket, "getaddrinfo", gaierror)
    assert sentinel.probe("camera", 554) == (False, set())


def test_probe_against_a_real_listener():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        assert sentinel.probe("127.0.0.1", port) == (True, {"127.0.0.1"})
    assert sentinel.probe("127.0.0.1", port)[0] is False


def test_offline_ruleset_drops_everything_but_loopback_and_the_camera():
    script = sentinel.ruleset(False, {"192.0.2.2", "fd00::2"})
    assert "hook input" in script and "policy drop;" in script
    assert 'iif "lo" accept' in script
    assert "ip saddr 192.0.2.2 accept" in script
    assert "ip6 saddr fd00::2 accept" in script
    # Drop, never reject: an unplugged camera sends nothing back
    assert "reject" not in script
    # No state-based exception: established client connections are dropped too
    assert "ct state" not in script


def test_offline_ruleset_lets_in_replies_to_what_the_copy_opened():
    # DNS answers forwarded from the copy's namespace, the probe, the proxy and RTMP publish:
    # only the reply direction of a flow the copy itself started
    assert "    ct direction reply accept\n" in sentinel.ruleset(False, {"192.0.2.2"})


def test_offline_ruleset_keeps_ipv6_neighbour_discovery():
    # Conntrack does not track NDP: without these the copy loses its IPv6 neighbours
    script = sentinel.ruleset(False, {"192.0.2.2"})
    assert "icmpv6 type { nd-neighbor-solicit, nd-neighbor-advert, nd-router-advert } accept" in script
    assert "echo-request" not in script


def test_offline_ruleset_drops_a_scope_id():
    # getaddrinfo gives a link-local address with its zone, which nft cannot parse
    script = sentinel.ruleset(False, {"fe80::1%eth0", "192.0.2.2"})
    assert "ip6 saddr fe80::1 accept" in script
    assert "%" not in script


def test_rulesets_replace_the_table_atomically():
    offline = sentinel.ruleset(False, {"192.0.2.2"})
    online = sentinel.ruleset(True, {"192.0.2.2"})
    head = f"table inet {sentinel.TABLE}\ndelete table inet {sentinel.TABLE}\n"
    # Declaring the table first makes the delete safe when it is not there
    assert offline.startswith(head) and online == head
    assert "192.0.2.2" not in online


def test_apply_feeds_nft_from_stdin():
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs["input"]))
        return subprocess.CompletedProcess(cmd, 0)

    sentinel.apply(False, {"192.0.2.2"}, run=run)
    assert calls == [(["nft", "-f", "-"], sentinel.ruleset(False, {"192.0.2.2"}))]


def test_apply_raises_when_nft_fails():
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1)

    with pytest.raises(RuntimeError, match="nft failed"):
        sentinel.apply(True, set(), run=run)


class Stop(Exception):
    pass


def run_loop(probes, reachable=lambda port: True, after=None):
    """Run the loop on scripted probes; return what it applied and logged."""
    applied, logs, probes = [], [], list(probes)

    def probe(host, port):
        if not probes:
            raise Stop
        return probes.pop(0)

    def exit_(code):
        logs.append(f"exit {code}")
        raise Stop

    with pytest.raises(Stop):
        sentinel.loop(
            "192.0.2.2",
            554,
            1984,
            probe=probe,
            reachable=reachable,
            apply=lambda online, addrs: applied.append((online, set(addrs))),
            sleep=lambda seconds: None,
            log=logs.append,
            exit=exit_,
        )
    return applied, logs


def test_loop_applies_the_first_state_then_only_transitions():
    up, down = (True, {"192.0.2.2"}), (False, {"192.0.2.2"})
    applied, logs = run_loop([up, up, down, down, down, down, up, up])
    assert applied == [(True, {"192.0.2.2"}), (False, {"192.0.2.2"}), (True, {"192.0.2.2"})]
    assert len(logs) == 3


def test_loop_logs_carry_host_and_port_only():
    applied, logs = run_loop([(False, {"192.0.2.2"})])
    assert logs == ["source 192.0.2.2:554 unreachable: copy closed"]


def test_loop_follows_a_new_address_while_offline():
    applied, _ = run_loop([(False, {"192.0.2.2"}), (False, set()), (False, {"192.0.2.9"})])
    # A failed lookup keeps the last address; a new one is let through at once
    assert applied == [(False, {"192.0.2.2"}), (False, {"192.0.2.9"})]


def test_loop_exits_once_go2rtc_left_the_namespace():
    seen = iter([True, False, False, False])
    applied, logs = run_loop([(True, {"192.0.2.2"})] * 10, reachable=lambda port: next(seen))
    assert logs[-2:] == ["go2rtc unreachable on 127.0.0.1:1984; exiting to rejoin its network", "exit 1"]


def test_stop_opens_the_copy(monkeypatch):
    calls = []
    monkeypatch.setattr(sentinel, "apply", lambda online, addrs: calls.append(online))
    handlers = {}
    monkeypatch.setattr(sentinel.signal, "signal", lambda sig, handler: handlers.setdefault(sig, handler))

    def loop(*args, **kwargs):
        handlers[signal.SIGTERM](signal.SIGTERM, None)

    monkeypatch.setattr(sentinel, "loop", loop)
    monkeypatch.setenv("MIRROR_HOST", "192.0.2.2")
    monkeypatch.setenv("MIRROR_PORT", "554")
    monkeypatch.setenv("WATCH_PORT", "554")
    assert sentinel.main() == 0
    assert calls == [True]


# State file: the sentinel writes its state to /state for the webhook service (webhook/webhook.py).


def test_write_state_writes_one_json_file_per_camera(tmp_path):
    sentinel.write_state(tmp_path, "c100", False, "192.0.2.2:554", now=lambda: "2026-11-23T10:00:00Z")
    path = tmp_path / "c100.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "camera": "c100",
        "state": "offline",
        "since": "2026-11-23T10:00:00Z",
        "source": "192.0.2.2:554",
    }
    sentinel.write_state(tmp_path, "c100", True, "192.0.2.2:554", now=lambda: "2026-11-23T10:01:00Z")
    assert json.loads(path.read_text(encoding="utf-8"))["state"] == "online"
    # Only the state file is left: the temporary one was renamed over it
    assert sorted(p.name for p in tmp_path.iterdir()) == ["c100.json"]


def test_write_state_is_atomic(tmp_path, monkeypatch):
    # The file is written in full under a hidden name, then renamed over the real one: a reader
    # never sees half a file
    sentinel.write_state(tmp_path, "cam", True, "192.0.2.2:554")
    renames = []
    real = sentinel.os.replace

    def replace(src, dst):
        renames.append((os.path.basename(src), os.path.basename(dst), json.loads(open(src, encoding="utf-8").read())))
        assert json.loads((tmp_path / "cam.json").read_text(encoding="utf-8"))["state"] == "online"
        real(src, dst)

    monkeypatch.setattr(sentinel.os, "replace", replace)
    sentinel.write_state(tmp_path, "cam", False, "192.0.2.2:554")
    assert renames[0][:2] == (".cam.json.tmp", "cam.json")
    assert renames[0][2]["state"] == "offline"


def test_write_state_since_is_utc_iso():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", sentinel.now_utc())


@pytest.mark.parametrize(
    ("host", "port", "source"),
    [("192.0.2.2", 554, "192.0.2.2:554"), ("camera.lan", 80, "camera.lan:80"), ("fd00::2", 554, "[fd00::2]:554")],
)
def test_source_label(host, port, source):
    assert sentinel.source_label(host, port) == source


def test_loop_records_the_first_state_and_each_transition():
    up, down = (True, {"192.0.2.2"}), (False, {"192.0.2.2"})
    recorded = []
    with pytest.raises(Stop):
        probes = [up, down, down, down, up, up]

        def probe(host, port):
            if not probes:
                raise Stop
            return probes.pop(0)

        sentinel.loop(
            "192.0.2.2",
            554,
            554,
            probe=probe,
            reachable=lambda port: True,
            apply=lambda online, addrs: None,
            sleep=lambda seconds: None,
            log=lambda line: None,
            record=recorded.append,
        )
    assert recorded == [True, False, True]


def test_main_records_to_the_state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sentinel, "apply", lambda online, addrs: None)

    def loop(host, port, watch, record):
        record(False)
        raise SystemExit(0)

    monkeypatch.setattr(sentinel, "loop", loop)
    monkeypatch.setattr(sentinel.signal, "signal", lambda *a: None)
    monkeypatch.setenv("MIRROR_HOST", "192.0.2.2")
    monkeypatch.setenv("MIRROR_PORT", "554")
    monkeypatch.setenv("WATCH_PORT", "554")
    monkeypatch.setenv("CAMERA", "c100")
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    assert sentinel.main() == 0
    state = json.loads((tmp_path / "c100.json").read_text(encoding="utf-8"))
    assert state["state"] == "offline" and state["source"] == "192.0.2.2:554"


def test_a_failed_state_write_never_stops_the_sentinel(tmp_path, monkeypatch):
    logs = []
    record = sentinel.recorder(str(tmp_path / "missing"), "c100", "192.0.2.2:554", log=logs.append)
    record(True)
    assert len(logs) == 1 and logs[0].startswith("cannot write the state file")


def test_no_state_dir_records_nothing():
    record = sentinel.recorder("", "c100", "192.0.2.2:554", log=lambda line: pytest.fail(line))
    record(True)
