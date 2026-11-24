"""Copy check, run by `just copy-check` inside the checker container (go2rtc's image: Python, ffmpeg, curl).

Plays two RTSP clients, one MJPEG and one HLS client against one copy of a test source, then
freezes the MJPEG client, kills it and starts a new one. At each step it asserts that the
other outputs keep flowing and that the source still sees exactly one connection.
"""

import json
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import health  # noqa: E402

CAM = "cam-copycheck"
PASSWORD = (Path(__file__).resolve().parents[2] / ".copy-check" / "secrets" / "COPYCHECK_OUTPUT_PASSWORD").read_text()
HEALTH = f"http://{CAM}/api/health"
SOURCE = "http://source:1984/api/streams?src=src"
RTSP = f"rtsp://cams:{PASSWORD}@{CAM}:554/copycheck"
MJPEG = f"http://{CAM}/api/stream.mjpeg?src=copycheck_mjpeg"
HLS = f"http://{CAM}/api/stream.m3u8?src=copycheck"
SAMPLE = 4.0


def get(url: str):
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.load(response)


def wait_for(url: str) -> None:
    deadline = time.monotonic() + 60
    while True:
        try:
            get(url)
            return
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)


def start(*cmd: str) -> subprocess.Popen:
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def rtsp_client() -> subprocess.Popen:
    return start("ffmpeg", "-loglevel", "error", "-rtsp_transport", "tcp", "-i", RTSP, "-f", "null", "-")


def mjpeg_client() -> subprocess.Popen:
    return start("curl", "-sN", "-o", "/dev/null", MJPEG)


def source_connections() -> int:
    return len(get(SOURCE).get("consumers") or [])


def step(title: str) -> dict:
    before = get(HEALTH)
    time.sleep(SAMPLE)
    report = health.summarize(before, get(HEALTH), SAMPLE)
    print(f"--- {title}: source connections={source_connections()}")
    print(json.dumps(report, indent=2), flush=True)
    return report


def outputs(report: dict) -> dict:
    merged: dict = {}
    for stream in report.values():
        for fmt, out in stream["outputs"].items():
            merged.setdefault(fmt, []).append(out)
    return {fmt: {k: sum(o[k] for o in outs) for k in ("clients", "kbps", "drops")} for fmt, outs in merged.items()}


def expect(ok: bool, what: str) -> None:
    print(("PASS " if ok else "FAIL ") + what, flush=True)
    if not ok:
        raise SystemExit(1)


def flowing(report: dict, fmt: str, clients: int) -> bool:
    out = outputs(report).get(fmt, {"clients": 0, "kbps": 0})
    return out["clients"] == clients and out["kbps"] > 0


def main() -> int:
    wait_for(HEALTH)
    wait_for(SOURCE)
    clients = [rtsp_client(), rtsp_client(), start("ffmpeg", "-loglevel", "error", "-i", HLS, "-f", "null", "-")]
    mjpeg = mjpeg_client()
    time.sleep(8)

    report = step("all clients")
    fmts = outputs(report)
    expect(flowing(report, "rtsp", 2), "two RTSP clients share the copy")
    expect(any(f not in ("rtsp", "mjpeg") and o["kbps"] > 0 for f, o in fmts.items()), "HLS flows")
    expect(flowing(report, "mjpeg", 1), "MJPEG flows, re-encoded by its own ffmpeg")
    expect(source_connections() == 1, "the source sees one connection")

    mjpeg.send_signal(signal.SIGSTOP)
    time.sleep(6)
    report = step("MJPEG client frozen")
    expect(flowing(report, "rtsp", 2), "RTSP keeps flowing while MJPEG is stuck")
    print(f"MJPEG drops while frozen: {outputs(report).get('mjpeg', {}).get('drops', 0)}")

    mjpeg.kill()
    mjpeg.wait()
    time.sleep(3)
    report = step("MJPEG client killed")
    expect(flowing(report, "rtsp", 2), "RTSP keeps flowing after MJPEG died")
    expect(outputs(report).get("mjpeg", {"clients": 0})["clients"] == 0, "the dead MJPEG client is gone")
    expect(source_connections() == 1, "the source connection was never cut")

    mjpeg = mjpeg_client()
    time.sleep(5)
    report = step("new MJPEG client")
    expect(flowing(report, "mjpeg", 1), "MJPEG comes back alone")
    expect(flowing(report, "rtsp", 2), "RTSP never stopped")

    for proc in [*clients, mjpeg]:
        proc.kill()
    print("copy check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
