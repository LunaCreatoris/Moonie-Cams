# Copyright (C) 2026 LunaCreatoris
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Render cameras.yaml into one go2rtc config per camera plus one compose file.

Usage:
    python app.py render CAMERAS_YAML OUT_DIR [--allow-placeholders] [--env=ENV_FILE] [--host-dir=DIR]

Each camera gets its own go2rtc container. go2rtc does all the streaming work; this
script only writes its config, refuses an output that repeats the input format (unless
the camera sets mode: copy), and adds an ffmpeg transcode source when an output needs a
codec the input lacks. ENV_FILE (the .env docker compose reads) gives the webhook settings.
DIR is where OUT_DIR sits on the Docker host, as an absolute path: the compose file then names
its config files and secrets by that path, so it also works pasted into a web editor (such as
Portainer's) that has none of the rendered files at hand. The secrets are in DIR's parent's secrets/.
"""

import hashlib
import ipaddress
import json
import math
import re
import shutil
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import yaml

# go2rtc built from source by go2rtc/Dockerfile (upstream 1.9.15 plus every go2rtc/*.patch, on
# the pinned upstream image). Bump the -pN suffix whenever the Dockerfile or a patch changes.
GO2RTC_IMAGE = "moonie-cams-go2rtc:1.9.15-p6"
GO2RTC_DIR = Path(__file__).resolve().parent / "go2rtc"
# The ONVIF proxy of mode proxy, built by proxy/Dockerfile. Bump the tag whenever proxy/ changes.
PROXY_IMAGE = "moonie-cams-onvif-proxy:8"
PROXY_DIR = Path(__file__).resolve().parent / "proxy"
# The state sentinel next to every copy, built by sentinel/Dockerfile. Bump the tag whenever sentinel/ changes.
SENTINEL_IMAGE = "moonie-cams-sentinel:3"
SENTINEL_DIR = Path(__file__).resolve().parent / "sentinel"
# The webhook service of a compose project, built by webhook/Dockerfile. Bump the tag whenever webhook/ changes.
WEBHOOK_IMAGE = "moonie-cams-webhook:2"
WEBHOOK_DIR = Path(__file__).resolve().parent / "webhook"
# Its service name. It has no container_name: compose names it <project>-cams-webhook-1, so two
# projects on one host never collide.
WEBHOOK_SERVICE = "cams-webhook"
# A camera named webhook would get the container cams-webhook, easy to mistake for that service
RESERVED_NAMES = {"webhook"}
WEBHOOK_TOKEN_KEY = "CAMS_WEBHOOK_TOKEN"
# The sentinels write one state file per camera to this volume; the webhook service reads it.
STATE_VOLUME = "state"
STATE_DIR = "/state"
# The compose project, overridden by COMPOSE_PROJECT_NAME in .env (one project per stack on a host)
DEFAULT_PROJECT = "moonie-cams"
# Docker compose's own rule for a project name
PROJECT_RE = re.compile(r"[a-z0-9][a-z0-9_-]*", re.ASCII)
# What --host-dir may hold: an absolute POSIX path, nothing a compose short entry or a shell trips on
HOST_DIR_RE = re.compile(r"/[A-Za-z0-9/._-]+", re.ASCII)
# Docker's own rule for a network name
NETWORK_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*", re.ASCII)

PLACEHOLDER = "CHANGE_ME"

# Video codec carried by each input kind (what go2rtc reads from that source).
INPUT_CODEC = {
    "rtsp": "h264",
    "onvif": "h264",
    "rtmp": "h264",
    "hls": "h264",
    "mjpeg": "mjpeg",
}

# Video codec each output kind needs.
OUTPUT_CODEC = {
    "onvif": "h264",
    "rtsp": "h264",
    "rtmp": "h264",
    "hls": "h264",
    "webrtc": "h264",
    "mjpeg": "mjpeg",
}

# Camera modes besides the default one (which converts and refuses an output in the input's format).
#   copy:  every output, the input's own format included, is a separate copy of the one source
#          connection; each output that re-encodes gets its own go2rtc stream and ffmpeg.
#   proxy: the copy relays the real camera's own ONVIF (see check_proxy and proxy_service).
# A camera has one mode at most: copy and proxy do not combine (see check_mode).
MODES = {"copy", "proxy"}

# Outputs served by the one RTSP server (ONVIF hands out RTSP URLs), so they share a stream.
RTSP_SERVED = {"onvif", "rtsp"}

# Outputs that need the container to own an address on the LAN.
NEEDS_IP = {"onvif", "webrtc"}

# go2rtc API paths each output needs; everything else (config editing, stream
# listing with source URLs, web UI) stays unregistered.
OUTPUT_PATHS = {
    "onvif": ["/onvif/", "/api/frame.jpeg"],
    "rtsp": [],
    "rtmp": [],
    "hls": [
        "/api/stream.m3u8",
        "/api/hls/playlist.m3u8",
        "/api/hls/segment.ts",
        "/api/hls/init.mp4",
        "/api/hls/segment.m4s",
    ],
    "webrtc": ["/api/webrtc", "/api/ws"],
    "mjpeg": ["/api/stream.mjpeg", "/api/frame.jpeg"],
}

# Read-only stream state with no URL or secret, added by go2rtc/health-endpoint.patch.
HEALTH_PATH = "/api/health"

HTTP_PORT = 80
RTSP_PORT = 554
WEBRTC_PORT = 8555

# mode: proxy. The ONVIF proxy (proxy/onvif_proxy.py) answers on port 80 and on 2020, a
# Tapo's ONVIF port; go2rtc's API moves to its own default port, and PTZ stays with the
# first client for PROXY_HOLD seconds after its last command.
PROXY_HTTP_PORTS = (80, 2020)
PROXY_API_PORT = 1984
PROXY_HOLD = 5
MAX_PROXY_HOLD = 600
# proxy_log: the proxy's PROXY_LOG (onvif_proxy.LOG_LEVELS); left out, the proxy logs at writes.
PROXY_LOG_LEVELS = ("all", "writes", "off")
# onvif_url: the camera's own address only, http://<IPv4>[:port]. proxy/onvif_proxy.py checks it
# again at startup (camera_address); tests/test_onvif_url.py holds both to one table.
ONVIF_URL_RE = re.compile(r"http://([0-9]{1,3}(?:\.[0-9]{1,3}){3})(?::([1-9][0-9]{0,4}))?/?", re.ASCII)
# The proxy runs with no privilege but binding port 80, on a read-only root; waitress may spill a
# large request or answer to a temporary file, hence a small /tmp.
PROXY_HARDENING = {
    "read_only": True,
    "cap_drop": ["ALL"],
    "cap_add": ["NET_BIND_SERVICE"],
    "security_opt": ["no-new-privileges:true"],
    "tmpfs": ["/tmp:size=16m"],
}

# The sentinel (sentinel/sentinel.py) closes a copy while its source is down: nft in the copy's own
# network namespace, so NET_ADMIN and nothing else, on a read-only root.
SENTINEL_HARDENING = {
    "read_only": True,
    "cap_drop": ["ALL"],
    "cap_add": ["NET_ADMIN"],
    "security_opt": ["no-new-privileges:true"],
}
# The webhook service reads files and posts over HTTP: no capability, a read-only root, and its
# image runs it as uid 65534.
WEBHOOK_HARDENING = {
    "read_only": True,
    "cap_drop": ["ALL"],
    "security_opt": ["no-new-privileges:true"],
}
# Port the sentinel probes when the source URL gives none, by scheme (go2rtc's source kinds).
DEFAULT_PORTS = {
    "rtsp": 554,
    "rtsps": 322,
    "rtspx": 322,
    "onvif": 80,
    "rtmp": 1935,
    "rtmps": 443,
    "http": 80,
    "https": 443,
}
HOST_RE = re.compile(r"[a-z0-9_](?:[a-z0-9_.-]{0,251}[a-z0-9_])?", re.ASCII)

# Frame rate and bitrate cap (kbit/s) of a camera go2rtc transcodes to H.264, unless it sets its own.
DEFAULT_FPS = 10
DEFAULT_BITRATE = 600
MAX_FPS = 60
MAX_BITRATE = 100_000
MAX_SIDE = 8192
SIZE_RE = re.compile(r"^([1-9][0-9]{0,4})x([1-9][0-9]{0,4})$")


def ffmpeg_h264(fps: int, bitrate: int) -> str:
    """go2rtc's H.264 encoder template for one camera.

    go2rtc's default gives High profile, full-range yuvj420p and several slices per frame
    (zerolatency's sliced threads). UniFi Protect decodes only the first slice and mishandles
    yuvj, so force Main, one slice and limited-range yuv420p. A fixed frame rate, a keyframe
    every two seconds and a capped bitrate keep the stream as small as the profile says.
    """
    return (
        f"-c:v libx264 -r {fps} -g {2 * fps} -bf 0 -profile:v main -level:v 4.1 -preset:v superfast"
        " -tune:v zerolatency -x264-params sliced-threads=0:slices=1"
        f" -crf 28 -maxrate {bitrate}k -bufsize {2 * bitrate}k -pix_fmt:v yuv420p -color_range:v tv"
    )


# The identity a copy announces over ONVIF (see go2rtc/onvif-vendor-identity.patch); its name
# is the container's, so Protect and docker show the same one.
ONVIF_MANUFACTURER = "Moonie-Cams"
ONVIF_MODEL = "Cams"
MAX_LOCATION = 64

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
SECRET_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")
# go2rtc/check-secrets.sh exports each secret KEY percent-encoded as KEY_URL, for a URL's account.
URL_SUFFIX = "_URL"
# A secret reference in the rendered config, ${KEY} or ${KEY_URL}; the group is the file KEY.
SECRET_REF_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*?)(?:" + URL_SUFFIX + r")?\}")


class ConfigError(Exception):
    """A camera entry that cannot be rendered."""


def needs_ffmpeg(input_kind: str, output_kind: str) -> bool:
    return INPUT_CODEC[input_kind] != OUTPUT_CODEC[output_kind]


def output_password_key(name: str) -> str:
    return f"{name.upper()}_OUTPUT_PASSWORD"


def stable_mac(name: str) -> str:
    """A locally administered MAC derived from the camera name, so Protect sees the same device."""
    digest = hashlib.sha256(name.encode()).digest()
    return ":".join(f"{b:02x}" for b in (0x02, *digest[:5]))


def url_account_secrets(url: str) -> str:
    """The URL with each ${KEY} of its account (user:password@) written ${KEY_URL}.

    The secret files hold the raw password; check-secrets.sh exports it percent-encoded as
    KEY_URL, which go2rtc's net/url decodes, so a password holding @ : / ^ reaches the camera.
    A ${KEY} elsewhere (path, query) stays raw.
    """
    scheme, sep, rest = url.partition("://")
    if not sep:
        return url
    authority = re.split(r"[/?#]", rest, maxsplit=1)[0]
    if "@" not in authority:
        return url
    account = authority.rpartition("@")[0]
    encoded = SECRET_RE.sub(lambda m: "${" + m.group(1) + URL_SUFFIX + "}", account)
    return f"{scheme}://{encoded}{rest[len(account) :]}"


def container_name(name: str) -> str:
    """The copy's container name, which it also announces as its ONVIF name."""
    return f"cams-{name}"


def is_int_in(value, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def parse_size(size: str) -> tuple[int, int]:
    match = SIZE_RE.match(size)
    return int(match.group(1)), int(match.group(2))


def transcodes_to_h264(cam: dict) -> bool:
    return any(OUTPUT_CODEC[o] == "h264" and needs_ffmpeg(cam["input"], o) for o in cam["outputs"])


def onvif_env(cam: dict) -> dict:
    """The ONVIF_* values go2rtc advertises when the stream is not running (see onvif-profile.patch).

    A transcoded camera always gets its encoder's frame rate and bitrate; a passed-through one
    only the keys it sets, since go2rtc cannot know the source's bitrate.
    """
    env = {}
    if cam.get("size") is not None:
        width, height = parse_size(cam["size"])
        env["ONVIF_WIDTH"], env["ONVIF_HEIGHT"] = str(width), str(height)
    transcoded = transcodes_to_h264(cam)
    for key, env_key, default in (("fps", "ONVIF_FPS", DEFAULT_FPS), ("bitrate", "ONVIF_BITRATE", DEFAULT_BITRATE)):
        if cam.get(key) is not None or transcoded:
            env[env_key] = str(cam.get(key, default))
    if "onvif" in cam["outputs"] and cam.get("mode") != "proxy":
        # A proxied copy relays the camera's own identity; go2rtc's ONVIF server is off there.
        env["ONVIF_NAME"] = container_name(cam["name"])
        env["ONVIF_MANUFACTURER"] = ONVIF_MANUFACTURER
        env["ONVIF_MODEL"] = ONVIF_MODEL
        # Set and empty drops go2rtc's location scope (upstream's is "github"). docker compose
        # interpolates the environment, so a literal $ is written $$.
        env["ONVIF_LOCATION"] = cam.get("location", "").replace("$", "$$")
    return env


def check_camera(cam: dict, allow_placeholders: bool) -> None:
    name = cam.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ConfigError(f"camera name {name!r}: use lowercase letters, digits and _ (max 31)")
    where = f"camera {name}"
    if name in RESERVED_NAMES:
        raise ConfigError(f"{where}: the name {name} is reserved (container {WEBHOOK_SERVICE}); pick another")
    if cam.get("input") not in INPUT_CODEC:
        raise ConfigError(f"{where}: input must be one of {sorted(INPUT_CODEC)}")
    if not isinstance(cam.get("source"), str) or not cam["source"]:
        raise ConfigError(f"{where}: source URL is missing")
    for field in ("source", "source_sub", "rtmp_target"):
        value = cam.get(field)
        reserved = [k for k in SECRET_RE.findall(value) if k.endswith(URL_SUFFIX)] if isinstance(value, str) else []
        if reserved:
            raise ConfigError(f"{where}: secret key {reserved[0]}: the {URL_SUFFIX} suffix is reserved; rename it")
    mode = check_mode(cam, where)
    outputs = cam.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        raise ConfigError(f"{where}: outputs must be a non-empty list")
    for out in outputs:
        if out not in OUTPUT_CODEC:
            raise ConfigError(f"{where}: output {out!r} must be one of {sorted(OUTPUT_CODEC)}")
        if out == cam["input"] and mode != "copy":
            raise ConfigError(f"{where}: output {out!r} is the same format as the input; drop it or set mode: copy")
    if len(set(outputs)) != len(outputs):
        raise ConfigError(f"{where}: outputs repeat a format")
    if "rtmp" in outputs and not cam.get("rtmp_target"):
        raise ConfigError(f"{where}: output rtmp needs rtmp_target")
    if not isinstance(cam.get("backchannel", False), bool):
        raise ConfigError(f"{where}: backchannel must be true or false")

    size = cam.get("size")
    if size is not None:
        sides = parse_size(size) if isinstance(size, str) and SIZE_RE.match(size) else (0,)
        if not all(1 <= side <= MAX_SIDE for side in sides):
            raise ConfigError(f"{where}: size {size!r} must be WIDTHxHEIGHT, e.g. 1280x720 (max {MAX_SIDE})")
    if cam.get("fps") is not None and not is_int_in(cam["fps"], 1, MAX_FPS):
        raise ConfigError(f"{where}: fps {cam['fps']!r} must be a whole number from 1 to {MAX_FPS}")
    if cam.get("bitrate") is not None and not is_int_in(cam["bitrate"], 1, MAX_BITRATE):
        bitrate = cam["bitrate"]
        raise ConfigError(f"{where}: bitrate {bitrate!r} must be a whole number of kbit/s from 1 to {MAX_BITRATE}")

    location = cam.get("location")
    if location is not None:
        text = isinstance(location, str) and location.strip() != "" and location.isprintable()
        if not (text and len(location) <= MAX_LOCATION):
            raise ConfigError(f"{where}: location {location!r} must be printable text, 1 to {MAX_LOCATION} characters")
        if "onvif" not in outputs:
            raise ConfigError(f"{where}: location needs the onvif output (it is announced over ONVIF)")

    ip = cam.get("ip")
    if NEEDS_IP & set(outputs) and ip is None:
        raise ConfigError(f"{where}: outputs {sorted(NEEDS_IP & set(outputs))} need an ip")
    if ip is not None and not (allow_placeholders and ip == PLACEHOLDER):
        try:
            ipaddress.IPv4Address(ip)
        except ValueError:
            raise ConfigError(f"{where}: ip {ip!r} is not an IPv4 address") from None
    if ip is None:
        ports = cam.get("ports")
        if not isinstance(ports, dict) or not all(isinstance(v, int) for v in ports.values()):
            raise ConfigError(f"{where}: without an ip, give host ports, e.g. ports: {{http: 8081, rtsp: 8555}}")
    if mode == "proxy":
        check_proxy(cam, where)
    elif "proxy_log" in cam:
        raise ConfigError(f"{where}: proxy_log needs mode proxy")
    mirror = cam.get("mirror_state", True)
    if not isinstance(mirror, bool):
        raise ConfigError(f"{where}: mirror_state must be true or false")
    if mirror:
        try:
            mirror_target(cam["source"])
        except ValueError:
            # The source is never echoed: it may carry a password
            raise ConfigError(
                f"{where}: mirror_state needs a source URL with a host the copy can probe"
                f" (scheme one of {sorted(DEFAULT_PORTS)}), or set mirror_state: false"
            ) from None


def check_mode(cam: dict, where: str) -> str | None:
    """The camera's mode, or None for the default one."""
    if "mode" not in cam:
        return None
    mode = cam["mode"]
    parts = mode if isinstance(mode, list) else re.split(r"[+, ]+", mode) if isinstance(mode, str) else []
    if {"copy", "proxy"} <= {p for p in parts if isinstance(p, str)}:
        # The proxy hands out the camera's own ONVIF profiles and RTSP paths, which know
        # nothing of copy mode's per-output streams; one mode keeps each one testable.
        raise ConfigError(f"{where}: modes copy and proxy do not combine; pick one")
    if not isinstance(mode, str) or mode not in MODES:
        raise ConfigError(f"{where}: mode {mode!r} must be one of {sorted(MODES)}, or left out")
    return mode


def check_proxy(cam: dict, where: str) -> None:
    if cam["input"] != "rtsp":
        raise ConfigError(f"{where}: mode proxy needs input rtsp (the camera's own streams)")
    if "onvif" not in cam["outputs"]:
        raise ConfigError(f"{where}: mode proxy needs the onvif output")
    if not cam.get("onvif_url"):
        raise ConfigError(f"{where}: mode proxy needs onvif_url, the camera's ONVIF address (http://<ip>:2020)")
    check_onvif_url(cam["onvif_url"], where)
    source = urlsplit(cam["source"])
    if not source.username or not source.password:
        raise ConfigError(f"{where}: source must carry the camera account (rtsp://<user>:${{KEY}}@<ip>/...)")
    if "output_user" in cam:
        raise ConfigError(f"{where}: mode proxy uses the camera account; drop output_user")
    # go2rtc's ONVIF server is off in this mode: clients see the real camera's own profiles.
    profile_keys = sorted(key for key in ("bitrate", "fps", "size") if key in cam)
    if profile_keys:
        raise ConfigError(f"{where}: mode proxy advertises the camera's own profiles; drop {', '.join(profile_keys)}")
    if "location" in cam:
        raise ConfigError(f"{where}: mode proxy relays the camera's own identity; drop location")
    hold = cam.get("ptz_hold", PROXY_HOLD)
    if isinstance(hold, bool) or not isinstance(hold, int | float) or not math.isfinite(hold):
        hold = 0
    if not 0 < hold <= MAX_PROXY_HOLD:
        raise ConfigError(f"{where}: ptz_hold must be a number of seconds above 0, at most {MAX_PROXY_HOLD}")
    level = cam.get("proxy_log", "writes")
    if not isinstance(level, str) or level not in PROXY_LOG_LEVELS:
        raise ConfigError(f"{where}: proxy_log must be one of {', '.join(PROXY_LOG_LEVELS)}")


def check_onvif_url(url, where: str) -> None:
    """onvif_url is http://<IPv4>[:port], port 1-65535, with no account, path, query or fragment.

    The URL is never echoed: one with an account would print its password.
    """
    match = ONVIF_URL_RE.fullmatch(url) if isinstance(url, str) else None
    ok = False
    if match:
        try:
            host = ipaddress.IPv4Address(match.group(1))
        except ValueError:
            host = None
        broadcast = ipaddress.IPv4Address("255.255.255.255")
        ok = host is not None and not (host.is_unspecified or host.is_multicast or host == broadcast)
        ok = ok and int(match.group(2) or 80) <= 65535
    if not ok:
        raise ConfigError(
            f"{where}: onvif_url must be http://<IPv4>[:port] (e.g. http://198.51.100.50:2020), port 1-65535,"
            " with no account, path, query or fragment"
        )


def mirror_target(source: str) -> tuple[str, int]:
    """The (host, port) the sentinel probes; never the account. ValueError when there is none.

    An '@' after the host means a raw password with a '/' or '?' in it, which would make the
    account look like the host: refused rather than guessed.
    """
    url = urlsplit(source)
    if url.scheme not in DEFAULT_PORTS or "@" in url.path + url.query + url.fragment:
        raise ValueError(source)
    host = url.hostname or ""
    # A scoped IPv6 literal (fe80::1%25eth0): the zone names an interface of the host, not of the
    # copy, and nft cannot match it
    if "%" in host:
        raise ValueError(source)
    try:
        host = str(ipaddress.ip_address(host))
    except ValueError:
        if not HOST_RE.fullmatch(host) or ".." in host:
            raise ValueError(source) from None
    port = DEFAULT_PORTS[url.scheme] if url.port is None else url.port
    if not 0 < port <= 65535:
        raise ValueError(source)
    return host, port


def rtsp_account(cam: dict) -> tuple[str, str]:
    """The account RTSP clients give the copy. A proxied camera keeps the camera's own,
    because Protect and Home Assistant give one account for ONVIF and RTSP alike."""
    if cam.get("mode") == "proxy":
        source = urlsplit(cam["source"])
        return source.username, source.password
    return cam.get("output_user", "cams"), "${" + output_password_key(cam["name"]) + "}"


def go2rtc_config(cam: dict) -> dict:
    """The go2rtc config for one camera. Secrets stay as ${KEY}, go2rtc reads them from files."""
    name, kind, outputs = cam["name"], cam["input"], cam["outputs"]
    copy = cam.get("mode") == "copy"
    target_codecs = {OUTPUT_CODEC[o] for o in outputs if needs_ffmpeg(kind, o)}
    # Copy mode: the RTSP-served outputs keep the main stream; every other output that
    # re-encodes reads its own stream, so each runs its own ffmpeg and a crash stays its own.
    own_ffmpeg = sorted(o for o in outputs if copy and o not in RTSP_SERVED and needs_ffmpeg(kind, o))
    shared_codecs = {OUTPUT_CODEC[o] for o in outputs if o not in own_ffmpeg and needs_ffmpeg(kind, o)}

    def stream(source: str, stream_name: str) -> list[str]:
        # Same stream name, extra ffmpeg sources: go2rtc picks the source whose codec the
        # consumer asks for, and starts ffmpeg only while such a consumer is connected.
        return [source] + [f"ffmpeg:{stream_name}#video={codec}" for codec in sorted(shared_codecs)]

    def camera(source: str) -> str:
        # go2rtc opens an RTSP/ONVIF camera's backchannel by default, so a WebRTC client could
        # play audio through the camera's speaker; only backchannel: true keeps it.
        if kind in ("rtsp", "onvif") and cam.get("backchannel") is not True:
            return f"{source}#backchannel=0"
        return source

    streams = {name: stream(camera(url_account_secrets(cam["source"])), name)}
    if cam.get("source_sub"):
        streams[f"{name}_sub"] = stream(camera(url_account_secrets(cam["source_sub"])), f"{name}_sub")
    for out in own_ffmpeg:
        # ffmpeg reads the source through go2rtc (one connection to the camera, shared).
        streams[f"{name}_{out}"] = [f"ffmpeg:{name}#video={OUTPUT_CODEC[out]}"]

    paths = {p for o in outputs for p in OUTPUT_PATHS[o]}
    http_port = HTTP_PORT
    if cam.get("mode") == "proxy":
        # The proxy serves ONVIF on port 80; go2rtc keeps the snapshot and the other outputs.
        paths.discard("/onvif/")
        http_port = PROXY_API_PORT
    public = bool(paths)
    if copy:
        paths.add(HEALTH_PATH)
    paths = sorted(paths)
    # Health alone stays on loopback, for the container's own healthcheck.
    listen = f":{http_port}" if public else f"127.0.0.1:{http_port}" if paths else ""
    config: dict = {
        "streams": streams,
        "api": {"listen": listen, "allow_paths": paths},
        "rtsp": {"listen": ""},
        "webrtc": {"listen": ""},
        "log": {"format": "text"},
        # Only used when go2rtc transcodes to H.264; harmless otherwise.
        "ffmpeg": {"h264": ffmpeg_h264(cam.get("fps", DEFAULT_FPS), cam.get("bitrate", DEFAULT_BITRATE))},
    }
    if target_codecs:
        # The ffmpeg sources read the stream back through go2rtc's own RTSP server.
        config["rtsp"] = {"listen": f"127.0.0.1:{RTSP_PORT}"}
    if {"onvif", "rtsp"} & set(outputs):
        username, password = rtsp_account(cam)
        rtsp = {"listen": f":{RTSP_PORT}", "username": username, "password": password}
        if "h264" in shared_codecs:
            # RTSP clients otherwise accept the raw MJPEG source; ask for the H.264 one.
            rtsp["default_query"] = "video=h264&audio"
        config["rtsp"] = rtsp
    if "webrtc" in outputs:
        config["webrtc"] = {"listen": f":{WEBRTC_PORT}"}
    if "rtmp" in outputs:
        config["publish"] = {
            f"{name}_rtmp" if "rtmp" in own_ffmpeg else name: [url_account_secrets(cam["rtmp_target"])]
        }
    return config


def compose_service(cam: dict, config_text: str, files: str = ".") -> tuple[dict, list[str]]:
    """The go2rtc service of a camera; files is where its config file is, as compose names it."""
    name = cam["name"]
    keys = sorted(set(SECRET_REF_RE.findall(config_text)))
    service: dict = {
        "build": "./go2rtc",
        "image": GO2RTC_IMAGE,
        "container_name": container_name(name),
        "restart": "unless-stopped",
        "environment": {"TZ": "${TZ:-UTC}", "CREDENTIALS_DIRECTORY": "/run/secrets", **onvif_env(cam)},
        "volumes": [f"{files}/{name}.yaml:/config/go2rtc.yaml:ro"],
        "secrets": [{"source": key.lower(), "target": key} for key in keys],
    }
    if cam.get("mode") == "copy":
        # Health is on whatever port go2rtc_config rendered, on loopback at least.
        api_port = json.loads(config_text)["api"]["listen"].rsplit(":", 1)[1]
        service["healthcheck"] = {
            "test": ["CMD", "curl", "-fsS", "-o", "/dev/null", f"http://127.0.0.1:{api_port}{HEALTH_PATH}"],
            "interval": "30s",
            "timeout": "5s",
            "retries": 3,
        }
    if cam.get("ip") is not None:
        service["networks"] = {"lan": {"ipv4_address": cam["ip"], "mac_address": cam.get("mac") or stable_mac(name)}}
    else:
        ports = cam["ports"]
        inner = {"http": HTTP_PORT, "rtsp": RTSP_PORT}
        service["ports"] = [f"{ports[k]}:{inner[k]}" for k in sorted(ports) if k in inner]
    return service, keys


def proxy_service(cam: dict) -> dict:
    """The ONVIF proxy of a camera, inside the go2rtc container's network namespace (one IP)."""
    name = cam["name"]
    streams = {urlsplit(cam["source"]).path: name}
    if cam.get("source_sub"):
        streams[urlsplit(cam["source_sub"]).path] = f"{name}_sub"
    return {
        "build": "./proxy",
        "image": PROXY_IMAGE,
        "container_name": f"cams-{name}-onvif",
        "restart": "unless-stopped",
        "network_mode": f"service:cam-{name}",
        "depends_on": [f"cam-{name}"],
        **PROXY_HARDENING,
        "environment": {
            "ONVIF_CAMERA": cam["onvif_url"],
            "ONVIF_STREAMS": json.dumps(streams),
            "COPY_IP": cam["ip"],
            "COPY_MAC": cam.get("mac") or stable_mac(name),
            "COPY_HOSTNAME": f"cams-{name}".replace("_", "-"),
            "PTZ_HOLD": str(cam.get("ptz_hold", PROXY_HOLD)),
            "RTSP_PORT": str(RTSP_PORT),
            "SNAPSHOT_PORT": str(PROXY_API_PORT),
            "HTTP_PORTS": " ".join(str(p) for p in PROXY_HTTP_PORTS),
            # Only when set: the proxy's own default (writes) applies otherwise.
            **({"PROXY_LOG": cam["proxy_log"]} if "proxy_log" in cam else {}),
        },
    }


def mirror_service(cam: dict, config: dict) -> dict | None:
    """The state sentinel of a copy, in its network namespace, or None when go2rtc listens on
    nothing (an RTMP push only): no client to hide from, and no port to watch go2rtc on."""
    listens = [config["rtsp"]["listen"], config["api"]["listen"]]
    watch = next((listen.rsplit(":", 1)[1] for listen in listens if listen), None)
    if watch is None:
        return None
    name = cam["name"]
    host, port = mirror_target(cam["source"])
    return {
        "build": "./sentinel",
        "image": SENTINEL_IMAGE,
        "container_name": f"cams-{name}-mirror",
        "restart": "unless-stopped",
        "network_mode": f"service:cam-{name}",
        "depends_on": [f"cam-{name}"],
        **SENTINEL_HARDENING,
        "environment": {
            "MIRROR_HOST": host,
            "MIRROR_PORT": str(port),
            "WATCH_PORT": watch,
            "CAMERA": name,
            "STATE_DIR": STATE_DIR,
        },
        # The only writable place: the state file the webhook service reads
        "volumes": [f"{STATE_VOLUME}:{STATE_DIR}"],
    }


def env_value(raw: str, where: str) -> str:
    """One .env value as docker compose reads it (checked against docker compose config).

    Quoted ('...' or "..."): the text up to the matching quote (a backslash escapes a double quote
    inside "..."), the rest of the line ignored. Unquoted: trimmed, then cut at the first space
    followed by #, then trimmed again.
    """
    value = raw.strip()
    if value[:1] in ("'", '"'):
        quote, i, text = value[0], 1, []
        while i < len(value):
            char = value[i]
            if char == "\\" and quote == '"' and i + 1 < len(value) and value[i + 1] in '"\\':
                text.append(value[i + 1])
                i += 2
                continue
            if char == quote:
                return "".join(text)
            text.append(char)
            i += 1
        raise ConfigError(f"{where}: unterminated quoted value")
    return value.split(" #", 1)[0].strip()


def read_env(path: Path) -> dict:
    """KEY=VALUE lines of a .env file, read as docker compose reads them (comments and blank lines
    skipped, a leading export dropped, quotes and inline comments as env_value says); empty when the
    file is missing."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return {}
    env = {}
    for number, line in enumerate(lines, start=1):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        head, _, rest = key.partition("export")
        if not head and rest[:1].isspace():
            key = rest.strip()
        env[key] = env_value(value, f"{path.name}: line {number}")
    return env


def check_webhook(env: dict) -> bool:
    """True when the webhook service posts somewhere (CAMS_WEBHOOK_URL set). The URL is never echoed:
    its path is the webhook's secret."""
    url = env.get("CAMS_WEBHOOK_URL", "").strip()
    if not url:
        return False
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError(".env: CAMS_WEBHOOK_URL must be an http:// or https:// URL")
    network = env.get("CAMS_WEBHOOK_NETWORK", "").strip()
    if not network:
        raise ConfigError(
            ".env: CAMS_WEBHOOK_NETWORK is required when CAMS_WEBHOOK_URL is set: the Docker network the"
            " endpoint is reachable on (e.g. the network n8n is on)"
        )
    if not NETWORK_RE.fullmatch(network):
        raise ConfigError(f".env: CAMS_WEBHOOK_NETWORK {network!r} is not a Docker network name")
    return True


def project_name(env: dict) -> str:
    """The compose project: COMPOSE_PROJECT_NAME from .env when set, else moonie-cams."""
    project = env.get("COMPOSE_PROJECT_NAME", "").strip() or DEFAULT_PROJECT
    if not PROJECT_RE.fullmatch(project):
        raise ConfigError(
            f".env: COMPOSE_PROJECT_NAME {project!r} is not a compose project name"
            " (lowercase letters, digits, - and _, starting with a letter or a digit)"
        )
    return project


def webhook_service(project: str, posts: bool, token: bool) -> dict:
    """The one webhook service of the project. Without a URL it joins no network at all and idles."""
    service: dict = {
        "build": "./webhook",
        "image": WEBHOOK_IMAGE,
        "restart": "unless-stopped",
        **WEBHOOK_HARDENING,
        # Taken from .env by docker compose, so the URL never lands in the rendered folder
        "environment": {
            "CAMS_WEBHOOK_URL": "${CAMS_WEBHOOK_URL:-}",
            "CAMS_WEBHOOK_ON_START": "${CAMS_WEBHOOK_ON_START:-false}",
            "WEBHOOK_HOST": project,
            "STATE_DIR": STATE_DIR,
        },
        "volumes": [f"{STATE_VOLUME}:{STATE_DIR}:ro"],
    }
    if posts:
        service["networks"] = ["webhook"]
    else:
        service["network_mode"] = "none"
    if token:
        service["secrets"] = [{"source": WEBHOOK_TOKEN_KEY.lower(), "target": WEBHOOK_TOKEN_KEY}]
    return service


def check_host_dir(host_dir: str) -> PurePosixPath:
    """The rendered folder's absolute path on the Docker host, which runs Linux (macvlan)."""
    path = PurePosixPath(host_dir)
    if not path.is_absolute() or path == PurePosixPath("/") or ".." in path.parts:
        raise ConfigError(f"--host-dir {host_dir!r} must be an absolute path on the Docker host, below /")
    # A colon would split a short volume entry, a space or a quote is easy to get wrong in a shell
    if not HOST_DIR_RE.fullmatch(str(path)):
        raise ConfigError(f"--host-dir {host_dir!r}: use letters, digits, / . _ and - only")
    return path


def render(
    cameras_file: Path,
    out_dir: Path,
    allow_placeholders: bool = False,
    env: dict | None = None,
    host_dir: str | None = None,
) -> list[Path]:
    host = check_host_dir(host_dir) if host_dir is not None else None
    posts = check_webhook(env or {})
    project = project_name(env or {})
    data = yaml.safe_load(cameras_file.read_text(encoding="utf-8")) or {}
    cameras = data.get("cameras")
    if not isinstance(cameras, list) or not cameras:
        raise ConfigError("cameras.yaml: 'cameras' must be a non-empty list")
    seen: set[str] = set()
    for cam in cameras:
        if not isinstance(cam, dict):
            raise ConfigError("cameras.yaml: every camera must be a mapping")
        check_camera(cam, allow_placeholders)
        if cam["name"] in seen:
            raise ConfigError(f"camera {cam['name']}: name used twice")
        seen.add(cam["name"])

    secrets_dir = Path("..") / "secrets"
    # How compose names the rendered files and the secrets: relative to the compose file, or by
    # their absolute path on the Docker host
    files = host.as_posix() if host else "."
    secret_files = (host.parent / "secrets") if host else PurePosixPath(secrets_dir.as_posix())
    compose: dict = {"name": project, "services": {}, "secrets": {}}
    written = []
    out_dir.mkdir(parents=True, exist_ok=True)
    # The compose file builds go2rtc from this copy, so the rendered folder stands alone.
    # Copied over in place: never delete a folder to recreate it.
    shutil.copytree(GO2RTC_DIR, out_dir / "go2rtc", dirs_exist_ok=True)
    # Same for the ONVIF proxy's and the sentinel's build folders, without what a local test run
    # leaves behind.
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    if any(cam.get("mode") == "proxy" for cam in cameras):
        shutil.copytree(PROXY_DIR, out_dir / "proxy", dirs_exist_ok=True, ignore=ignore)
    for cam in cameras:
        config = go2rtc_config(cam)
        # JSON is valid YAML, and it never reinterprets values.
        text = json.dumps(config, indent=2) + "\n"
        path = out_dir / f"{cam['name']}.yaml"
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)
        service, keys = compose_service(cam, text, files)
        compose["services"][f"cam-{cam['name']}"] = service
        if cam.get("mode") == "proxy":
            compose["services"][f"cam-{cam['name']}-onvif"] = proxy_service(cam)
        if cam.get("mirror_state", True) and (mirror := mirror_service(cam, config)):
            compose["services"][f"cam-{cam['name']}-mirror"] = mirror
            shutil.copytree(SENTINEL_DIR, out_dir / "sentinel", dirs_exist_ok=True, ignore=ignore)
        for key in keys:
            compose["secrets"][key.lower()] = {"file": (secret_files / key).as_posix()}
    if any(name.endswith("-mirror") for name in compose["services"]):
        # The token is optional: its secret exists only when its file does
        token = (out_dir / secrets_dir / WEBHOOK_TOKEN_KEY).is_file()
        compose["services"][WEBHOOK_SERVICE] = webhook_service(compose["name"], posts, token)
        compose["volumes"] = {STATE_VOLUME: {}}
        if token:
            compose["secrets"][WEBHOOK_TOKEN_KEY.lower()] = {"file": (secret_files / WEBHOOK_TOKEN_KEY).as_posix()}
        shutil.copytree(WEBHOOK_DIR, out_dir / "webhook", dirs_exist_ok=True, ignore=ignore)
    if any(cam.get("ip") is not None for cam in cameras):
        compose["networks"] = {
            "lan": {
                "driver": "macvlan",
                "driver_opts": {"parent": "${CAMS_PARENT:?set CAMS_PARENT in .env}"},
                "ipam": {
                    "config": [
                        {
                            "subnet": "${CAMS_SUBNET:?set CAMS_SUBNET in .env}",
                            "gateway": "${CAMS_GATEWAY:?set CAMS_GATEWAY in .env}",
                        }
                    ]
                },
            }
        }
    if posts and WEBHOOK_SERVICE in compose["services"]:
        compose.setdefault("networks", {})["webhook"] = {
            "external": True,
            "name": "${CAMS_WEBHOOK_NETWORK:?set CAMS_WEBHOOK_NETWORK in .env}",
        }
    if not compose["secrets"]:
        del compose["secrets"]
    path = out_dir / "compose.yml"
    text = "# Generated by app.py from cameras.yaml; edit that file instead.\n"
    text += yaml.safe_dump(compose, sort_keys=False)
    path.write_text(text, encoding="utf-8", newline="\n")
    written.append(path)
    return written


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 3 or args[0] != "render":
        print(__doc__.strip(), file=sys.stderr)
        return 2
    try:
        env_files = [a.split("=", 1)[1] for a in argv if a.startswith("--env=")]
        env = read_env(Path(env_files[-1])) if env_files else {}
        host_dirs = [a.split("=", 1)[1] for a in argv if a.startswith("--host-dir=")]
        files = render(
            Path(args[1]), Path(args[2]), "--allow-placeholders" in argv, env, host_dirs[-1] if host_dirs else None
        )
    except ConfigError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    for path in files:
        print(path.as_posix())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
