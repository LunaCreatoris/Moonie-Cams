import json

import health

SECRET = "rtsp://cams:hunter2@192.0.2.2/s1"


def sample(recv: int, mjpeg_send: int, rtsp_send: int, drops: int = 0) -> dict:
    return {
        "cam": {
            # A leak upstream must not reach the report: unknown keys are dropped.
            "producers": [{"state": "running", "format_name": "rtsp", "bytes_recv": recv, "url": SECRET}],
            "consumers": [
                {"format_name": "mjpeg", "bytes_send": mjpeg_send, "drops": drops, "remote_addr": "203.0.113.9"},
                {"format_name": "rtsp", "bytes_send": rtsp_send},
                {"format_name": "rtsp", "bytes_send": rtsp_send},
                # go2rtc's own ffmpeg reading the stream back is not a client.
                {"format_name": "rtsp", "bytes_send": 10**9, "internal": True},
            ],
        },
        "cam_mjpeg": {"producers": [{"state": "idle"}], "consumers": []},
    }


def test_summary_per_output():
    report = health.summarize(sample(0, 0, 0), sample(250_000, 125_000, 50_000, drops=4), 2)
    assert report["cam"] == {
        "source": ["running"],
        "in_kbps": 1000,
        "readers": 1,
        "outputs": {
            "mjpeg": {"clients": 1, "kbps": 500, "drops": 4},
            "rtsp": {"clients": 2, "kbps": 400, "drops": 0},
        },
    }
    assert report["cam_mjpeg"] == {"source": ["idle"], "in_kbps": 0, "readers": 0, "outputs": {}}


def test_summary_never_carries_urls_addresses_or_secrets():
    text = json.dumps(health.summarize(sample(0, 0, 0), sample(1, 1, 1), 1))
    for leak in ("hunter2", "192.0.2.2", "203.0.113.9", "rtsp://", "url", "remote_addr"):
        assert leak not in text


def test_a_client_leaving_never_gives_a_negative_rate():
    report = health.summarize(sample(100, 900, 900), sample(50, 10, 10), 1)
    assert report["cam"]["in_kbps"] == 0
    assert report["cam"]["outputs"]["mjpeg"]["kbps"] == 0


def test_a_new_stream_counts_from_zero():
    report = health.summarize({}, sample(1000, 0, 0), 1)
    assert report["cam"]["in_kbps"] == 8


def test_main_reads_twice_and_prints(monkeypatch, capsys):
    samples = iter([sample(0, 0, 0), sample(1000, 0, 0)])
    monkeypatch.setattr(health, "read", lambda url: next(samples))
    monkeypatch.setattr(health.time, "sleep", lambda s: None)
    assert health.main(["1"]) == 0
    assert json.loads(capsys.readouterr().out)["cam"]["in_kbps"] == 8
