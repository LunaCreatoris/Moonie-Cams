import json
import re
import shutil
from pathlib import Path

import pytest
import yaml

import app
import onvif_proxy

ROOT = Path(__file__).resolve().parent.parent


def write_cameras(tmp_path: Path, cameras: list[dict]) -> Path:
    path = tmp_path / "cameras.yaml"
    path.write_text(yaml.safe_dump({"cameras": cameras}), encoding="utf-8")
    return path


def render_one(tmp_path: Path, **cam) -> tuple[dict, dict]:
    """Render a single camera; return its go2rtc config and the compose file."""
    entry = {
        "name": "cam",
        "input": "rtsp",
        "source": "rtsp://192.0.2.2/s1",
        "outputs": ["onvif"],
        "ip": "198.51.100.20",
    }
    entry.update(cam)
    entry = {k: v for k, v in entry.items() if v is not None}
    out = tmp_path / "out"
    app.render(write_cameras(tmp_path, [entry]), out)
    config = yaml.safe_load((out / f"{entry['name']}.yaml").read_text(encoding="utf-8"))
    compose = yaml.safe_load((out / "compose.yml").read_text(encoding="utf-8"))
    return config, compose


def test_example_renders_one_service_per_camera(tmp_path):
    files = app.render(ROOT / "cameras.example.yaml", tmp_path, allow_placeholders=True)
    compose = yaml.safe_load((tmp_path / "compose.yml").read_text(encoding="utf-8"))
    assert sorted(compose["services"]) == [
        "cam-c100",
        "cam-c100-mirror",
        "cam-c210_a",
        "cam-c210_a-mirror",
        "cam-c210_a-onvif",
        "cam-esp32",
        "cam-esp32-mirror",
        "cam-octoprint",
        "cam-octoprint-mirror",
        "cams-webhook",
    ]
    assert compose["networks"]["lan"]["driver"] == "macvlan"
    assert len(files) == 5


def test_example_needs_ips_without_the_placeholder_flag(tmp_path):
    with pytest.raises(app.ConfigError, match="camera c100: ip 'CHANGE_ME'"):
        app.render(ROOT / "cameras.example.yaml", tmp_path)


@pytest.mark.parametrize(
    ("input_kind", "output_kind", "expected"),
    [
        ("rtsp", "onvif", False),
        ("rtsp", "hls", False),
        ("rtsp", "mjpeg", True),
        ("mjpeg", "onvif", True),
        ("mjpeg", "rtsp", True),
        ("mjpeg", "webrtc", True),
        ("hls", "rtsp", False),
    ],
)
def test_ffmpeg_table(input_kind, output_kind, expected):
    assert app.needs_ffmpeg(input_kind, output_kind) is expected


@pytest.mark.parametrize("kind", ["rtsp", "mjpeg", "onvif", "rtmp", "hls"])
def test_output_same_as_input_is_refused(tmp_path, kind):
    cam = {"name": "garage", "input": kind, "source": "x://y", "outputs": [kind], "ip": "198.51.100.20"}
    if kind == "rtmp":
        cam["rtmp_target"] = "rtmp://example/live"
    with pytest.raises(app.ConfigError, match=f"camera garage: output '{kind}' is the same format as the input"):
        app.render(write_cameras(tmp_path, [cam]), tmp_path / "out")


def test_tapo_to_onvif_passes_through(tmp_path):
    config, _ = render_one(tmp_path, source_sub="rtsp://192.0.2.2/s2")
    assert config["streams"] == {
        "cam": ["rtsp://192.0.2.2/s1#backchannel=0"],
        "cam_sub": ["rtsp://192.0.2.2/s2#backchannel=0"],
    }
    assert config["rtsp"] == {"listen": ":554", "username": "cams", "password": "${CAM_OUTPUT_PASSWORD}"}
    assert config["api"]["listen"] == ":80"


@pytest.mark.parametrize("kind", ["rtsp", "onvif"])
def test_camera_sources_never_open_the_backchannel(tmp_path, kind):
    # go2rtc opens an RTSP/ONVIF camera's backchannel by default: a WebRTC client could
    # then play audio through the camera's speaker.
    source = f"{kind}://192.0.2.2/s1"
    config, _ = render_one(tmp_path, input=kind, source=source, source_sub=f"{kind}://192.0.2.2/s2", outputs=["webrtc"])
    assert config["streams"]["cam"] == [f"{source}#backchannel=0"]
    assert config["streams"]["cam_sub"] == [f"{kind}://192.0.2.2/s2#backchannel=0"]


def test_backchannel_true_keeps_the_camera_speaker(tmp_path):
    config, _ = render_one(tmp_path, backchannel=True, source_sub="rtsp://192.0.2.2/s2")
    assert config["streams"] == {"cam": ["rtsp://192.0.2.2/s1"], "cam_sub": ["rtsp://192.0.2.2/s2"]}


def test_backchannel_is_left_alone_on_other_inputs(tmp_path):
    config, _ = render_one(tmp_path, input="mjpeg", source="http://192.0.2.3:81/stream", outputs=["webrtc"])
    assert config["streams"]["cam"][0] == "http://192.0.2.3:81/stream"


def test_backchannel_false_is_the_default(tmp_path):
    config, _ = render_one(tmp_path, backchannel=False)
    assert config["streams"]["cam"] == ["rtsp://192.0.2.2/s1#backchannel=0"]


@pytest.mark.parametrize("value", ["yes", "true", 1, 0])
def test_bad_backchannel_is_refused(tmp_path, value):
    with pytest.raises(app.ConfigError, match="camera cam: backchannel must be true or false"):
        render_one(tmp_path, backchannel=value)


def test_mjpeg_to_onvif_transcodes_to_h264(tmp_path):
    config, _ = render_one(tmp_path, input="mjpeg", source="http://192.0.2.3:81/stream")
    assert config["streams"]["cam"] == ["http://192.0.2.3:81/stream", "ffmpeg:cam#video=h264"]
    assert config["rtsp"]["default_query"] == "video=h264&audio"


@pytest.mark.parametrize("input_kind", ["mjpeg", "rtsp"])
def test_h264_encoder_is_one_unifi_protect_decodes(tmp_path, input_kind):
    config, _ = render_one(tmp_path, input=input_kind, source="http://192.0.2.3:81/stream")
    h264 = config["ffmpeg"]["h264"]
    # Main profile, one slice per frame, limited-range yuv420p: Protect decodes only the
    # first slice and mishandles yuvj (full range), which go2rtc's default template gives.
    for arg in ("-profile:v main", "sliced-threads=0:slices=1", "-pix_fmt:v yuv420p", "-color_range:v tv"):
        assert arg in h264


def test_h264_to_mjpeg_transcodes_and_keeps_rtsp_internal(tmp_path):
    config, _ = render_one(tmp_path, outputs=["mjpeg"], ip=None, ports={"http": 8081})
    assert config["streams"]["cam"] == ["rtsp://192.0.2.2/s1#backchannel=0", "ffmpeg:cam#video=mjpeg"]
    # ffmpeg reads the stream back through go2rtc's RTSP server, which stays on loopback
    assert config["rtsp"] == {"listen": "127.0.0.1:554"}


def test_webrtc_listens(tmp_path):
    config, _ = render_one(tmp_path, outputs=["webrtc"])
    assert config["webrtc"] == {"listen": ":8555"}
    assert config["api"]["allow_paths"] == ["/api/webrtc", "/api/ws"]


def test_no_rtsp_server_without_need(tmp_path):
    _, compose = render_one(tmp_path, outputs=["hls"], ip=None, ports={"http": 8082})
    config = yaml.safe_load((tmp_path / "out" / "cam.yaml").read_text(encoding="utf-8"))
    assert config["rtsp"] == {"listen": ""}
    assert compose["services"]["cam-cam"]["ports"] == ["8082:80"]


def test_api_exposes_only_what_the_outputs_need(tmp_path):
    config, _ = render_one(tmp_path)
    assert config["api"]["allow_paths"] == ["/api/frame.jpeg", "/onvif/"]
    config, _ = render_one(tmp_path, outputs=["rtsp"], input="mjpeg", ip=None, ports={"rtsp": 8554})
    assert config["api"] == {"listen": "", "allow_paths": []}


def test_secrets_come_from_files_never_inline(tmp_path):
    _, compose = render_one(tmp_path, source="rtsp://cams:${CAM_SOURCE_PASSWORD}@192.0.2.2/s1")
    service = compose["services"]["cam-cam"]
    assert service["environment"]["CREDENTIALS_DIRECTORY"] == "/run/secrets"
    assert service["secrets"] == [
        {"source": "cam_output_password", "target": "CAM_OUTPUT_PASSWORD"},
        {"source": "cam_source_password", "target": "CAM_SOURCE_PASSWORD"},
    ]
    assert compose["secrets"]["cam_source_password"] == {"file": "../secrets/CAM_SOURCE_PASSWORD"}


def test_secrets_in_a_url_account_use_the_url_encoded_value(tmp_path):
    # go2rtc/check-secrets.sh exports KEY_URL, the password percent-encoded, which net/url decodes:
    # the camera gets the real password even when it holds @, :, / or ^.
    config, compose = render_one(
        tmp_path,
        source="rtsp://${CAM_USER}:${CAM_SOURCE_PASSWORD}@192.0.2.2:554/s1",
        source_sub="rtsp://cams:${CAM_SOURCE_PASSWORD}@192.0.2.2:554/s2",
    )
    assert config["streams"]["cam"] == [
        "rtsp://${CAM_USER_URL}:${CAM_SOURCE_PASSWORD_URL}@192.0.2.2:554/s1#backchannel=0"
    ]
    assert config["streams"]["cam_sub"] == ["rtsp://cams:${CAM_SOURCE_PASSWORD_URL}@192.0.2.2:554/s2#backchannel=0"]
    # The copy's own RTSP account stays raw: clients type the real password
    assert config["rtsp"]["password"] == "${CAM_OUTPUT_PASSWORD}"
    # The secret files are the raw ones; KEY_URL is never a file
    targets = [s["target"] for s in compose["services"]["cam-cam"]["secrets"]]
    assert targets == ["CAM_OUTPUT_PASSWORD", "CAM_SOURCE_PASSWORD", "CAM_USER"]
    assert sorted(compose["secrets"]) == ["cam_output_password", "cam_source_password", "cam_user"]


def test_a_secret_outside_the_url_account_stays_raw(tmp_path):
    config, _ = render_one(
        tmp_path,
        input="mjpeg",
        source="http://192.0.2.3/stream?token=${CAM_TOKEN}",
        outputs=["onvif", "rtmp"],
        rtmp_target="rtmp://${CAM_RTMP_USER}@192.0.2.9/live/${CAM_STREAM_KEY}",
    )
    assert config["streams"]["cam"][0] == "http://192.0.2.3/stream?token=${CAM_TOKEN}"
    assert config["publish"] == {"cam": ["rtmp://${CAM_RTMP_USER_URL}@192.0.2.9/live/${CAM_STREAM_KEY}"]}


@pytest.mark.parametrize("field", ["source", "source_sub", "rtmp_target"])
def test_a_secret_key_ending_in_url_is_refused(tmp_path, field):
    cam = {field: "rtsp://cams:${CAM_PASSWORD_URL}@192.0.2.2/s1", "outputs": ["onvif", "rtmp"]}
    if field != "rtmp_target":
        cam["rtmp_target"] = "rtmp://192.0.2.9/live"
    with pytest.raises(app.ConfigError, match=r"camera cam: secret key CAM_PASSWORD_URL: the _URL suffix is reserved"):
        render_one(tmp_path, **cam)


def test_go2rtc_config_is_json(tmp_path):
    render_one(tmp_path)
    json.loads((tmp_path / "out" / "cam.yaml").read_text(encoding="utf-8"))


def test_copy_gets_its_ip_and_a_stable_mac(tmp_path):
    _, compose = render_one(tmp_path)
    lan = compose["services"]["cam-cam"]["networks"]["lan"]
    assert lan == {"ipv4_address": "198.51.100.20", "mac_address": app.stable_mac("cam")}
    assert app.stable_mac("cam") == app.stable_mac("cam") != app.stable_mac("cam2")
    assert app.stable_mac("cam").startswith("02:")


def test_rtmp_push(tmp_path):
    config, _ = render_one(tmp_path, input="mjpeg", outputs=["rtmp"], ip=None, ports={}, rtmp_target="rtmp://x/${KEY}")
    assert config["publish"] == {"cam": ["rtmp://x/${KEY}"]}
    assert config["streams"]["cam"][-1] == "ffmpeg:cam#video=h264"


@pytest.mark.parametrize(
    ("cam", "message"),
    [
        ({"name": "Bad Name"}, "use lowercase"),
        ({"input": "dvd"}, "input must be one of"),
        ({"source": ""}, "source URL is missing"),
        ({"outputs": []}, "outputs must be a non-empty list"),
        ({"outputs": ["fax"]}, "output 'fax' must be one of"),
        ({"outputs": ["onvif", "onvif"]}, "outputs repeat"),
        ({"outputs": ["rtmp"], "ip": None}, "rtmp needs rtmp_target"),
        ({"ip": None}, r"outputs \['onvif'\] need an ip"),
        ({"ip": "192.0.2.300"}, "is not an IPv4 address"),
        ({"outputs": ["hls"], "ip": None}, "give host ports"),
    ],
)
def test_bad_entries_are_refused(tmp_path, cam, message):
    with pytest.raises(app.ConfigError, match=message):
        render_one(tmp_path, **cam)


def test_duplicate_names_are_refused(tmp_path):
    cam = {"name": "a", "input": "rtsp", "source": "rtsp://x", "outputs": ["hls"], "ports": {"http": 1}}
    with pytest.raises(app.ConfigError, match="camera a: name used twice"):
        app.render(write_cameras(tmp_path, [cam, cam]), tmp_path / "out")


def test_main_reports_errors(tmp_path, capsys):
    path = write_cameras(tmp_path, [{"name": "a", "input": "rtsp", "source": "x", "outputs": ["rtsp"]}])
    assert app.main(["render", str(path), str(tmp_path / "out")]) == 1
    assert "camera a: output 'rtsp' is the same format" in capsys.readouterr().err
    assert app.main([]) == 2


def env_of(compose: dict, name: str = "cam") -> dict:
    return compose["services"][f"cam-{name}"]["environment"]


# The identity go2rtc announces over ONVIF (see go2rtc/onvif-vendor-identity.patch)
IDENTITY_KEYS = {"ONVIF_NAME", "ONVIF_MANUFACTURER", "ONVIF_MODEL", "ONVIF_LOCATION"}


def onvif_env(compose: dict) -> dict:
    """The advertised video profile: the ONVIF_* values besides the identity."""
    return {k: v for k, v in env_of(compose).items() if k.startswith("ONVIF_") and k not in IDENTITY_KEYS}


def identity_env(compose: dict, name: str = "cam") -> dict:
    return {k: v for k, v in env_of(compose, name).items() if k in IDENTITY_KEYS}


def test_copy_announces_its_container_name_over_onvif(tmp_path):
    _, compose = render_one(tmp_path)
    assert identity_env(compose) == {
        "ONVIF_NAME": "cams-cam",
        "ONVIF_MANUFACTURER": "Moonie-Cams",
        "ONVIF_MODEL": "Cams",
        # Set and empty: go2rtc drops its location scope instead of announcing upstream's "github"
        "ONVIF_LOCATION": "",
    }


def test_onvif_name_is_always_the_rendered_container_name(tmp_path):
    app.render(ROOT / "cameras.example.yaml", tmp_path, allow_placeholders=True)
    compose = yaml.safe_load((tmp_path / "compose.yml").read_text(encoding="utf-8"))
    named = 0
    for service in compose["services"].values():
        env = service.get("environment", {})
        if "ONVIF_NAME" in env:
            assert env["ONVIF_NAME"] == service["container_name"]
            named += 1
    # Every default-mode copy with the onvif output (the proxied one relays the camera's own)
    assert named == 3


def test_container_name_has_one_source(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "container_name", lambda name: f"other-{name}")
    _, compose = render_one(tmp_path)
    service = compose["services"]["cam-cam"]
    assert service["container_name"] == service["environment"]["ONVIF_NAME"] == "other-cam"


def test_location_is_announced_when_set(tmp_path):
    _, compose = render_one(tmp_path, location="Hall, 1st floor")
    assert identity_env(compose)["ONVIF_LOCATION"] == "Hall, 1st floor"
    # docker compose interpolates $ in the environment: a literal one is written $$
    _, compose = render_one(tmp_path, location="Room $1")
    assert identity_env(compose)["ONVIF_LOCATION"] == "Room $$1"
    render_one(tmp_path, location="x" * 64)


def test_copy_mode_announces_its_identity_too(tmp_path):
    _, compose = render_one(tmp_path, mode="copy", outputs=["onvif", "rtsp"], location="Garage")
    assert identity_env(compose)["ONVIF_NAME"] == "cams-cam"
    assert identity_env(compose)["ONVIF_LOCATION"] == "Garage"


def test_no_identity_without_go2rtcs_onvif_server(tmp_path):
    _, compose = render_one(tmp_path, outputs=["hls"], ip=None, ports={"http": 8082})
    assert identity_env(compose) == {}
    _, compose = render_one(tmp_path, **PROXY_CAM)
    assert identity_env(compose) == {}


@pytest.mark.parametrize(
    ("cam", "message"),
    [
        ({"location": ""}, "location '' must be printable text, 1 to 64 characters"),
        ({"location": "   "}, "location '   ' must be printable text, 1 to 64 characters"),
        ({"location": "x" * 65}, "location 'x{65}' must be printable text, 1 to 64 characters"),
        ({"location": "Hall\nTwo"}, r"location 'Hall\\nTwo' must be printable text"),
        ({"location": "Tab\there"}, r"location 'Tab\\there' must be printable text"),
        ({"location": 12}, "location 12 must be printable text"),
        (
            {"location": "Hall", "outputs": ["hls"], "ip": None, "ports": {"http": 8082}},
            "location needs the onvif output",
        ),
    ],
)
def test_bad_location_is_refused(tmp_path, cam, message):
    with pytest.raises(app.ConfigError, match=f"camera cam: {message}"):
        render_one(tmp_path, **cam)


def test_proxy_mode_refuses_a_location(tmp_path):
    with pytest.raises(app.ConfigError, match="camera cam: mode proxy relays the camera's own identity; drop location"):
        render_one(tmp_path, **PROXY_CAM, location="Hall")


def test_compose_builds_the_patched_go2rtc(tmp_path):
    _, compose = render_one(tmp_path)
    service = compose["services"]["cam-cam"]
    assert service["build"] == "./go2rtc"
    assert service["image"] == app.GO2RTC_IMAGE
    assert app.GO2RTC_IMAGE.startswith("moonie-cams-go2rtc:1.9.15-p")


def test_render_copies_the_go2rtc_build_folder(tmp_path):
    render_one(tmp_path)
    for name in ("Dockerfile", "onvif-profile.patch", "NOTICE"):
        copied = tmp_path / "out" / "go2rtc" / name
        assert copied.read_bytes() == (ROOT / "go2rtc" / name).read_bytes()
    # Rendering twice into the same folder refills it in place
    render_one(tmp_path)


def test_go2rtc_is_built_from_the_pinned_upstream(tmp_path):
    dockerfile = (ROOT / "go2rtc" / "Dockerfile").read_text(encoding="utf-8")
    froms = re.findall(r"^FROM (?:--platform=\S+ )?(\S+)", dockerfile, re.MULTILINE)
    assert froms[0].startswith("golang:1.26") and "@sha256:" in froms[0]
    assert froms[-1].startswith("alexxit/go2rtc:1.9.15@sha256:")
    # Every patch in the folder, sorted, each one able to fail the build
    assert "COPY *.patch /patches/" in dockerfile and "LC_ALL=C sort" in dockerfile and "set -e" in dockerfile
    assert "git apply" in dockerfile and "CGO_ENABLED=0" in dockerfile and "-trimpath" in dockerfile


def test_transcoded_camera_defaults_to_10_fps_and_600_kbits(tmp_path):
    config, compose = render_one(tmp_path, input="mjpeg", source="http://192.0.2.3:81/stream")
    assert onvif_env(compose) == {"ONVIF_FPS": "10", "ONVIF_BITRATE": "600"}
    h264 = config["ffmpeg"]["h264"]
    assert "-r 10 -g 20 " in h264
    assert "-crf 28 -maxrate 600k -bufsize 1200k " in h264


def test_transcoded_camera_takes_its_size_fps_and_bitrate(tmp_path):
    config, compose = render_one(
        tmp_path, input="mjpeg", source="http://192.0.2.3:81/stream", size="640x480", fps=15, bitrate=800
    )
    assert onvif_env(compose) == {
        "ONVIF_WIDTH": "640",
        "ONVIF_HEIGHT": "480",
        "ONVIF_FPS": "15",
        "ONVIF_BITRATE": "800",
    }
    h264 = config["ffmpeg"]["h264"]
    assert h264.startswith("-c:v libx264 -r 15 -g 30 -bf 0 ")
    assert "-maxrate 800k -bufsize 1600k " in h264


def test_passthrough_camera_sets_only_the_keys_given(tmp_path):
    _, compose = render_one(tmp_path)
    assert onvif_env(compose) == {}
    _, compose = render_one(tmp_path, bitrate=2000)
    assert onvif_env(compose) == {"ONVIF_BITRATE": "2000"}
    _, compose = render_one(tmp_path, size="1920x1080", fps=15)
    assert onvif_env(compose) == {"ONVIF_WIDTH": "1920", "ONVIF_HEIGHT": "1080", "ONVIF_FPS": "15"}


PROXY_CAM = {
    "mode": "proxy",
    "source": "rtsp://cams:${CAM_SOURCE_PASSWORD}@192.0.2.10:554/stream1",
    "source_sub": "rtsp://cams:${CAM_SOURCE_PASSWORD}@192.0.2.10:554/stream2",
    "onvif_url": "http://192.0.2.10:2020",
}


def test_proxy_mode_hands_onvif_to_the_proxy(tmp_path):
    config, _ = render_one(tmp_path, **PROXY_CAM)
    # go2rtc's own ONVIF server is off; its API moves off port 80, which the proxy takes.
    assert config["api"] == {"listen": ":1984", "allow_paths": ["/api/frame.jpeg"]}
    # One RTSP session per Tapo stream, passed through, without the camera's speaker.
    assert config["streams"] == {
        "cam": ["rtsp://cams:${CAM_SOURCE_PASSWORD_URL}@192.0.2.10:554/stream1#backchannel=0"],
        "cam_sub": ["rtsp://cams:${CAM_SOURCE_PASSWORD_URL}@192.0.2.10:554/stream2#backchannel=0"],
    }


def test_proxy_mode_restreams_with_the_camera_account(tmp_path):
    # Protect and HA give one account to the copy, the Tapo's, for ONVIF and for RTSP.
    config, compose = render_one(tmp_path, **PROXY_CAM)
    assert config["rtsp"] == {"listen": ":554", "username": "cams", "password": "${CAM_SOURCE_PASSWORD}"}
    assert compose["services"]["cam-cam"]["secrets"] == [
        {"source": "cam_source_password", "target": "CAM_SOURCE_PASSWORD"}
    ]


def test_proxy_service_shares_the_copy_network(tmp_path):
    _, compose = render_one(tmp_path, ptz_hold=8, **PROXY_CAM)
    proxy = compose["services"]["cam-cam-onvif"]
    assert proxy["build"] == "./proxy"
    assert proxy["image"] == app.PROXY_IMAGE
    assert proxy["network_mode"] == "service:cam-cam"
    assert proxy["depends_on"] == ["cam-cam"]
    assert "networks" not in proxy and "ports" not in proxy
    assert proxy["environment"] == {
        "ONVIF_CAMERA": "http://192.0.2.10:2020",
        "ONVIF_STREAMS": json.dumps({"/stream1": "cam", "/stream2": "cam_sub"}),
        "COPY_IP": "198.51.100.20",
        "COPY_MAC": app.stable_mac("cam"),
        "COPY_HOSTNAME": "cams-cam",
        "PTZ_HOLD": "8",
        "RTSP_PORT": "554",
        "SNAPSHOT_PORT": "1984",
        "HTTP_PORTS": "80 2020",
    }


def test_proxy_service_runs_without_privileges(tmp_path):
    _, compose = render_one(tmp_path, **PROXY_CAM)
    proxy = compose["services"]["cam-cam-onvif"]
    # Only what binding port 80 needs; nothing on disk is writable but a small /tmp for waitress.
    assert proxy["cap_drop"] == ["ALL"]
    assert proxy["cap_add"] == ["NET_BIND_SERVICE"]
    assert proxy["read_only"] is True
    assert proxy["security_opt"] == ["no-new-privileges:true"]
    assert proxy["tmpfs"] == ["/tmp:size=16m"]


def test_the_proxy_check_runs_the_proxy_as_the_rendered_compose_does(tmp_path):
    _, compose = render_one(tmp_path, **PROXY_CAM)
    rendered = compose["services"]["cam-cam-onvif"]
    check = yaml.safe_load((ROOT / "tests" / "netns" / "compose.yml").read_text(encoding="utf-8"))
    checked = check["services"]["cam-onvif"]
    for key in ("cap_drop", "cap_add", "read_only", "security_opt", "tmpfs"):
        assert checked[key] == rendered[key]


@pytest.mark.parametrize("level", ["all", "writes", "off"])
def test_proxy_log_is_rendered_only_when_set(tmp_path, level):
    _, compose = render_one(tmp_path, proxy_log=level, **PROXY_CAM)
    assert compose["services"]["cam-cam-onvif"]["environment"]["PROXY_LOG"] == level


@pytest.mark.parametrize("level", ["", "ALL", "debug", True, 1])
def test_a_bad_proxy_log_is_refused(tmp_path, level):
    with pytest.raises(app.ConfigError, match="camera cam: proxy_log must be one of all, writes, off"):
        render_one(tmp_path, proxy_log=level, **PROXY_CAM)


def test_proxy_log_levels_are_the_proxy_ones():
    assert app.PROXY_LOG_LEVELS == onvif_proxy.LOG_LEVELS


def test_proxy_log_needs_mode_proxy(tmp_path):
    with pytest.raises(app.ConfigError, match="camera cam: proxy_log needs mode proxy"):
        render_one(tmp_path, proxy_log="all")


@pytest.mark.parametrize("hold", [0.5, 600])
def test_ptz_hold_up_to_ten_minutes_is_accepted(tmp_path, hold):
    _, compose = render_one(tmp_path, ptz_hold=hold, **PROXY_CAM)
    assert compose["services"]["cam-cam-onvif"]["environment"]["PTZ_HOLD"] == str(hold)


def test_copy_check_cameras_render_one_copy_with_every_output(tmp_path):
    cameras = ROOT / "tests" / "copy_check" / "cameras.check.yaml"
    app.render(cameras, tmp_path)
    config = json.loads((tmp_path / "copycheck.yaml").read_text(encoding="utf-8"))
    compose = yaml.safe_load((tmp_path / "compose.yml").read_text(encoding="utf-8"))
    assert list(compose["services"]) == ["cam-copycheck"]
    assert config["streams"] == {
        "copycheck": ["rtsp://source:8554/src#backchannel=0"],
        "copycheck_mjpeg": ["ffmpeg:copycheck#video=mjpeg"],
    }
    assert config["api"]["listen"] == ":80"
    assert config["rtsp"]["username"] == "cams"
    # The checker reaches the copy over the compose network: no host port is published.
    assert compose["services"]["cam-copycheck"]["ports"] == []
    justfile = (ROOT / "justfile").read_text(encoding="utf-8")
    assert "python app.py render tests/copy_check/cameras.check.yaml .copy-check/out" in justfile


def test_proxied_copy_runs_the_patched_go2rtc(tmp_path):
    config, compose = render_one(tmp_path, **PROXY_CAM)
    service = compose["services"]["cam-cam"]
    assert (service["build"], service["image"]) == ("./go2rtc", app.GO2RTC_IMAGE)
    # Nothing to advertise: go2rtc's ONVIF server is off, the proxy relays the camera's profiles.
    assert onvif_env(compose) == {}
    assert "/onvif/" not in config["api"]["allow_paths"]


def test_render_copies_the_proxy_build_folder(tmp_path, monkeypatch):
    # A source folder as a local test run leaves it, with bytecode next to the code
    source = tmp_path / "proxy"
    shutil.copytree(ROOT / "proxy", source, ignore=shutil.ignore_patterns("__pycache__"))
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "onvif_proxy.cpython-313.pyc").write_bytes(b"")
    monkeypatch.setattr(app, "PROXY_DIR", source)
    render_one(tmp_path, **PROXY_CAM)
    out = tmp_path / "out" / "proxy"
    for name in ("Dockerfile", ".dockerignore", "onvif_proxy.py", "requirements.txt"):
        assert (out / name).read_bytes() == (ROOT / "proxy" / name).read_bytes()
    assert not (out / "__pycache__").exists()
    # Rendering twice into the same folder refills it in place
    render_one(tmp_path, **PROXY_CAM)


def test_proxy_folder_is_copied_only_when_a_camera_needs_it(tmp_path):
    render_one(tmp_path)
    assert not (tmp_path / "out" / "proxy").exists()


def test_proxy_gets_the_configured_mac(tmp_path):
    _, compose = render_one(tmp_path, mac="02:00:00:00:00:09", name="c210_a", **PROXY_CAM)
    env = compose["services"]["cam-c210_a-onvif"]["environment"]
    assert env["COPY_MAC"] == "02:00:00:00:00:09"
    assert env["COPY_HOSTNAME"] == "cams-c210-a"
    assert env["PTZ_HOLD"] == "5"


@pytest.mark.parametrize(
    ("cam", "message"),
    [
        ({"size": "640"}, "size '640' must be WIDTHxHEIGHT"),
        ({"size": "0x480"}, "size '0x480' must be WIDTHxHEIGHT"),
        ({"size": "640x480x2"}, "size '640x480x2' must be WIDTHxHEIGHT"),
        ({"size": 640}, "size 640 must be WIDTHxHEIGHT"),
        ({"size": "99999x480"}, r"size '99999x480' must be WIDTHxHEIGHT, e.g. 1280x720 \(max 8192\)"),
        ({"fps": 0}, "fps 0 must be a whole number from 1 to 60"),
        ({"fps": 61}, "fps 61 must be"),
        ({"fps": "10"}, "fps '10' must be"),
        ({"fps": True}, "fps True must be"),
        ({"fps": 7.5}, "fps 7.5 must be"),
        ({"bitrate": 0}, r"bitrate 0 must be a whole number of kbit/s from 1 to 100000"),
        ({"bitrate": "600k"}, "bitrate '600k' must be"),
        ({"bitrate": 100001}, "bitrate 100001 must be"),
    ],
)
def test_bad_profile_keys_are_refused(tmp_path, cam, message):
    with pytest.raises(app.ConfigError, match=f"camera cam: {message}"):
        render_one(tmp_path, **cam)


def test_justfile_builds_the_image_compose_uses():
    justfile = (ROOT / "justfile").read_text(encoding="utf-8")
    assert f'go2rtc_image := "{app.GO2RTC_IMAGE}"' in justfile


@pytest.mark.parametrize(
    ("cam", "message"),
    [
        ({"mode": "mirror"}, r"mode 'mirror' must be one of \['copy', 'proxy'\]"),
        ({"input": "mjpeg"}, "mode proxy needs input rtsp"),
        ({"outputs": ["hls"]}, "mode proxy needs the onvif output"),
        ({"onvif_url": None}, "mode proxy needs onvif_url"),
        ({"onvif_url": "192.0.2.10:2020"}, r"onvif_url must be http://<IPv4>\[:port\]"),
        ({"onvif_url": "http://192.0.2.10:2020/onvif"}, r"onvif_url must be http://<IPv4>\[:port\]"),
        ({"onvif_url": 2020}, r"onvif_url must be http://<IPv4>\[:port\]"),
        ({"source": "rtsp://192.0.2.10:554/stream1"}, "source must carry the camera account"),
        ({"output_user": "other"}, "mode proxy uses the camera account; drop output_user"),
        ({"ptz_hold": 0}, "ptz_hold must be a number of seconds above 0, at most 600"),
        ({"ptz_hold": -1}, "ptz_hold must be a number of seconds above 0, at most 600"),
        ({"ptz_hold": 600.5}, "ptz_hold must be a number of seconds above 0, at most 600"),
        ({"ptz_hold": float("inf")}, "ptz_hold must be a number of seconds above 0, at most 600"),
        ({"ptz_hold": float("nan")}, "ptz_hold must be a number of seconds above 0, at most 600"),
        ({"ptz_hold": True}, "ptz_hold must be a number of seconds above 0, at most 600"),
        ({"ptz_hold": "5"}, "ptz_hold must be a number of seconds above 0, at most 600"),
        ({"fps": 10}, "mode proxy advertises the camera's own profiles; drop fps"),
        ({"size": "640x480", "bitrate": 600}, "mode proxy advertises the camera's own profiles; drop bitrate, size"),
    ],
)
def test_bad_proxy_entries_are_refused(tmp_path, cam, message):
    entry = {**PROXY_CAM, **cam}
    if entry.get("mode") != "proxy":
        entry["onvif_url"] = None
    with pytest.raises(app.ConfigError, match=f"camera cam: {message}"):
        render_one(tmp_path, **entry)


def test_example_proxies_the_pan_tilt_tapo(tmp_path):
    app.render(ROOT / "cameras.example.yaml", tmp_path, allow_placeholders=True)
    compose = yaml.safe_load((tmp_path / "compose.yml").read_text(encoding="utf-8"))
    assert compose["services"]["cam-c210_a-onvif"]["network_mode"] == "service:cam-c210_a"


# Copy mode: one source connection, many isolated outputs.


@pytest.mark.parametrize("kind", ["rtsp", "mjpeg", "onvif", "rtmp", "hls"])
def test_copy_mode_allows_the_input_format_and_passes_it_through(tmp_path, kind):
    extra = {"rtmp_target": "rtmp://example/live"} if kind == "rtmp" else {}
    config, _ = render_one(
        tmp_path, mode="copy", input=kind, source="x://y", outputs=[kind], mirror_state=False, **extra
    )
    source = "x://y#backchannel=0" if kind in ("rtsp", "onvif") else "x://y"
    assert config["streams"] == {"cam": [source]}


def test_copy_mode_gives_each_reencoding_output_its_own_ffmpeg(tmp_path):
    config, _ = render_one(tmp_path, mode="copy", outputs=["rtsp", "mjpeg"], ip=None, ports={"http": 1, "rtsp": 2})
    # RTSP passes through on the main stream; MJPEG re-encodes on a stream of its own.
    assert config["streams"] == {"cam": ["rtsp://192.0.2.2/s1#backchannel=0"], "cam_mjpeg": ["ffmpeg:cam#video=mjpeg"]}


def test_copy_mode_never_shares_an_ffmpeg_between_outputs(tmp_path):
    config, _ = render_one(
        tmp_path,
        mode="copy",
        input="mjpeg",
        source="http://192.0.2.3:81/stream",
        outputs=["onvif", "rtsp", "hls", "webrtc", "rtmp"],
        rtmp_target="rtmp://x/live",
    )
    streams = config["streams"]
    # ONVIF and RTSP are one RTSP server: they share the main stream and its one encoder.
    assert streams["cam"] == ["http://192.0.2.3:81/stream", "ffmpeg:cam#video=h264"]
    for out in ("hls", "webrtc", "rtmp"):
        assert streams[f"cam_{out}"] == ["ffmpeg:cam#video=h264"]
    ffmpegs = [src for sources in streams.values() for src in sources if src.startswith("ffmpeg:")]
    assert len(ffmpegs) == 4
    assert config["publish"] == {"cam_rtmp": ["rtmp://x/live"]}
    assert config["rtsp"]["default_query"] == "video=h264&audio"


def test_copy_mode_reencoding_outputs_read_the_source_through_go2rtc(tmp_path):
    config, _ = render_one(tmp_path, mode="copy", outputs=["mjpeg"], ip=None, ports={"http": 1})
    # The ffmpeg sources read the main stream back over loopback RTSP: one camera connection.
    assert config["rtsp"] == {"listen": "127.0.0.1:554"}
    assert all(src.startswith("ffmpeg:cam#") for src in config["streams"]["cam_mjpeg"])


def test_copy_mode_passthrough_rtmp_publishes_the_main_stream(tmp_path):
    config, _ = render_one(tmp_path, mode="copy", outputs=["rtmp"], ip=None, ports={}, rtmp_target="rtmp://x/live")
    assert config["publish"] == {"cam": ["rtmp://x/live"]}
    assert config["streams"] == {"cam": ["rtsp://192.0.2.2/s1#backchannel=0"]}


def test_default_mode_keeps_one_shared_ffmpeg_per_codec(tmp_path):
    config, _ = render_one(tmp_path, input="mjpeg", source="http://s", outputs=["onvif", "hls"])
    assert config["streams"] == {"cam": ["http://s", "ffmpeg:cam#video=h264"]}
    assert "/api/health" not in config["api"]["allow_paths"]
    _, compose = render_one(tmp_path, input="mjpeg", source="http://s", outputs=["onvif", "hls"])
    assert "healthcheck" not in compose["services"]["cam-cam"]


def test_copy_mode_serves_health_and_the_healthcheck_uses_it(tmp_path):
    config, compose = render_one(tmp_path, mode="copy", outputs=["onvif", "mjpeg"])
    assert "/api/health" in config["api"]["allow_paths"]
    assert config["api"]["listen"] == ":80"
    test = compose["services"]["cam-cam"]["healthcheck"]["test"]
    assert test == ["CMD", "curl", "-fsS", "-o", "/dev/null", "http://127.0.0.1:80/api/health"]


def test_copy_mode_health_stays_on_loopback_without_http_outputs(tmp_path):
    config, _ = render_one(tmp_path, mode="copy", outputs=["rtsp"], ip=None, ports={"rtsp": 8554})
    assert config["api"] == {"listen": "127.0.0.1:80", "allow_paths": ["/api/health"]}


def test_copy_mode_never_allows_the_stream_list(tmp_path):
    config, _ = render_one(tmp_path, mode="copy", outputs=["onvif", "rtsp", "hls", "webrtc", "mjpeg"])
    for path in ("/api/streams", "/api/streams.dot", "/api/config", "/api"):
        assert path not in config["api"]["allow_paths"]


def test_copy_mode_secrets_stay_out_of_the_healthcheck(tmp_path):
    _, compose = render_one(tmp_path, mode="copy", source="rtsp://cams:${CAM_SOURCE_PASSWORD}@192.0.2.2/s1")
    healthcheck = json.dumps(compose["services"]["cam-cam"]["healthcheck"])
    assert "PASSWORD" not in healthcheck and "192.0.2.2" not in healthcheck


@pytest.mark.parametrize("mode", ["COPY", "convert", "", 1, True, ["copy"], {"copy": 1}])
def test_unknown_mode_is_refused(tmp_path, mode):
    with pytest.raises(app.ConfigError, match=r"camera cam: mode .* must be one of \['copy', 'proxy'\]"):
        render_one(tmp_path, mode=mode)


@pytest.mark.parametrize("mode", [["copy", "proxy"], ["proxy", "copy"], "copy+proxy", "proxy,copy", "copy proxy"])
def test_copy_and_proxy_do_not_combine(tmp_path, mode):
    with pytest.raises(app.ConfigError, match="camera cam: modes copy and proxy do not combine; pick one"):
        render_one(tmp_path, **{**PROXY_CAM, "mode": mode})


def test_copy_mode_reencoding_streams_use_the_camera_encoder(tmp_path):
    # Every per-output ffmpeg asks for #video=h264, which go2rtc builds from this one template.
    config, compose = render_one(
        tmp_path,
        mode="copy",
        input="mjpeg",
        source="http://s",
        outputs=["onvif", "hls", "webrtc"],
        fps=15,
        bitrate=900,
    )
    assert config["streams"]["cam_hls"] == ["ffmpeg:cam#video=h264"]
    assert config["streams"]["cam_webrtc"] == ["ffmpeg:cam#video=h264"]
    assert config["ffmpeg"] == {"h264": app.ffmpeg_h264(15, 900)}
    assert "-r 15 " in config["ffmpeg"]["h264"] and "-maxrate 900k " in config["ffmpeg"]["h264"]
    assert onvif_env(compose) == {"ONVIF_FPS": "15", "ONVIF_BITRATE": "900"}


def test_copy_mode_healthcheck_follows_the_rendered_api_port(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "HTTP_PORT", 8080)
    config, compose = render_one(tmp_path, mode="copy", outputs=["rtsp"], ip=None, ports={"rtsp": 8554})
    assert config["api"]["listen"] == "127.0.0.1:8080"
    test = compose["services"]["cam-cam"]["healthcheck"]["test"]
    assert test[-1] == "http://127.0.0.1:8080/api/health"


def test_proxy_mode_has_no_health_endpoint_or_healthcheck(tmp_path):
    config, compose = render_one(tmp_path, **PROXY_CAM)
    assert config["api"]["listen"] == ":1984"
    assert "/api/health" not in config["api"]["allow_paths"]
    assert "healthcheck" not in compose["services"]["cam-cam"]


def test_copy_mode_still_refuses_a_repeated_output(tmp_path):
    with pytest.raises(app.ConfigError, match="outputs repeat"):
        render_one(tmp_path, mode="copy", outputs=["rtsp", "rtsp"])


def test_same_format_error_names_copy_mode(tmp_path):
    with pytest.raises(app.ConfigError, match="set mode: copy"):
        render_one(tmp_path, outputs=["rtsp"])


@pytest.mark.parametrize("requirements", ["requirements.txt", "proxy/requirements.txt"])
def test_every_pinned_package_carries_its_hashes(requirements):
    # pip turns hash checking on for a whole install as soon as one line has a hash, and the tool
    # image installs the proxy's file too: both files carry them.
    text = (ROOT / requirements).read_text(encoding="utf-8").replace("\\\n", " ")
    pins = [line for line in text.splitlines() if "==" in line]
    assert pins
    for pin in pins:
        assert re.search(r"--hash=sha256:[0-9a-f]{64}", pin), pin


def test_the_proxy_image_installs_only_hashed_packages():
    dockerfile = (ROOT / "proxy" / "Dockerfile").read_text(encoding="utf-8")
    assert "pip install --no-cache-dir --no-deps --require-hashes -r /tmp/requirements.txt" in dockerfile


# State mirror: a sentinel next to every copy closes it while its source is down.


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("rtsp://192.0.2.2/s1", ("192.0.2.2", 554)),
        ("rtsp://cams:${CAM_SOURCE_PASSWORD}@192.0.2.2:8554/s1", ("192.0.2.2", 8554)),
        ("rtsps://cams:${KEY}@192.0.2.2/s1", ("192.0.2.2", 322)),
        ("rtspx://192.0.2.2/s1", ("192.0.2.2", 322)),
        ("onvif://admin:${KEY}@192.0.2.2", ("192.0.2.2", 80)),
        ("onvif://admin:${KEY}@192.0.2.2:2020", ("192.0.2.2", 2020)),
        ("rtmp://192.0.2.2/live/cam", ("192.0.2.2", 1935)),
        ("rtmps://192.0.2.2/live/cam", ("192.0.2.2", 443)),
        ("http://192.0.2.2:81/stream", ("192.0.2.2", 81)),
        ("http://user:${KEY}@octopi.local/webcam/?action=stream", ("octopi.local", 80)),
        ("https://192.0.2.2/live.m3u8", ("192.0.2.2", 443)),
        ("rtsp://[fd00::2]:554/s1", ("fd00::2", 554)),
        ("RTSP://Camera-1/s1", ("camera-1", 554)),
    ],
)
def test_mirror_target_from_every_source_kind(source, target):
    assert app.mirror_target(source) == target


@pytest.mark.parametrize(
    "source",
    [
        "ffmpeg:cam#video=h264",
        "exec:ffmpeg -i x",
        "x://y",
        "rtsp://",
        "rtsp://192.0.2.2:0/s1",
        "rtsp://192.0.2.2:99999/s1",
        "rtsp://192.0.2.2:port/s1",
        "rtsp://${HOST}/s1",
        # A raw password with a slash: urlsplit would read the user as the host
        "rtsp://cams:pa/ss@192.0.2.2/s1",
        "rtsp://cams:1234/x@192.0.2.2/s1",
        # A scoped IPv6 literal: nft cannot match a zone, and the copy has its own interfaces
        "rtsp://[fe80::1%25eth0]:554/s1",
        "rtsp://[fe80::1%eth0]/s1",
        "onvif://admin:${KEY}@[fd00::2%251]:2020",
    ],
)
def test_mirror_target_refuses_what_it_cannot_probe(source):
    with pytest.raises(ValueError):
        app.mirror_target(source)


MIRROR_SECRET = "s3cr3t-pa55"


@pytest.mark.parametrize(
    "source",
    [
        f"rtsp://admin:{MIRROR_SECRET}@192.0.2.2:554/s1",
        f"rtsp://admin:{MIRROR_SECRET}/x@192.0.2.2/s1",
        f"http://{MIRROR_SECRET}@192.0.2.2/stream",
    ],
)
def test_mirror_never_leaks_the_account(tmp_path, source):
    try:
        _, compose = render_one(tmp_path, source=source)
    except app.ConfigError as err:
        # The error names the camera, never the URL
        assert MIRROR_SECRET not in str(err) and "admin" not in str(err)
        return
    mirror = compose["services"]["cam-cam-mirror"]
    assert MIRROR_SECRET not in json.dumps(mirror) and "admin" not in json.dumps(mirror)
    assert mirror["environment"]["MIRROR_HOST"] == "192.0.2.2"


def test_every_copy_gets_a_sentinel_by_default(tmp_path):
    _, compose = render_one(tmp_path, source="rtsp://cams:${KEY}@192.0.2.2/s1")
    mirror = compose["services"]["cam-cam-mirror"]
    assert mirror["build"] == "./sentinel"
    # Bumped with every change to sentinel/
    assert mirror["image"] == app.SENTINEL_IMAGE
    assert mirror["container_name"] == "cams-cam-mirror"
    assert mirror["restart"] == "unless-stopped"
    assert mirror["network_mode"] == "service:cam-cam"
    assert mirror["depends_on"] == ["cam-cam"]
    assert "networks" not in mirror and "ports" not in mirror and "secrets" not in mirror
    # Host and port only: no account, no path
    assert mirror["environment"] == {
        "MIRROR_HOST": "192.0.2.2",
        "MIRROR_PORT": "554",
        "WATCH_PORT": "554",
        "CAMERA": "cam",
        "STATE_DIR": "/state",
    }


def test_sentinel_holds_only_net_admin(tmp_path):
    _, compose = render_one(tmp_path)
    mirror = compose["services"]["cam-cam-mirror"]
    assert mirror["cap_drop"] == ["ALL"]
    assert mirror["cap_add"] == ["NET_ADMIN"]
    assert mirror["read_only"] is True
    assert mirror["security_opt"] == ["no-new-privileges:true"]
    assert "privileged" not in mirror and "pid" not in mirror


@pytest.mark.parametrize(
    ("cam", "watch"),
    [
        ({}, "554"),
        (PROXY_CAM, "554"),
        ({"mode": "copy", "outputs": ["hls"], "ip": None, "ports": {"http": 8081}}, "80"),
        # go2rtc's internal RTSP server, on loopback only, for its own ffmpeg
        ({"input": "mjpeg", "source": "http://192.0.2.2:81/stream", "outputs": ["hls"]}, "554"),
    ],
)
def test_sentinel_runs_in_every_mode_and_watches_a_port_go2rtc_listens_on(tmp_path, cam, watch):
    config, compose = render_one(tmp_path, **cam)
    env = compose["services"]["cam-cam-mirror"]["environment"]
    assert env["WATCH_PORT"] == watch
    listens = [config["rtsp"]["listen"], config["api"]["listen"]]
    assert any(listen.rsplit(":", 1)[-1] == watch for listen in listens if listen)


def test_a_copy_that_listens_on_nothing_has_no_sentinel(tmp_path):
    # RTMP push only: nothing to close, and no port to watch go2rtc on
    _, compose = render_one(tmp_path, outputs=["rtmp"], rtmp_target="rtmp://x/live", ip=None, ports={})
    assert "cam-cam-mirror" not in compose["services"]


def test_mirror_state_false_opts_out(tmp_path):
    _, compose = render_one(tmp_path, mirror_state=False)
    assert "cam-cam-mirror" not in compose["services"]
    assert not (tmp_path / "out" / "sentinel").exists()


def test_mirror_state_false_skips_the_source_check(tmp_path):
    _, compose = render_one(tmp_path, input="mjpeg", source="exec:fake", outputs=["onvif"], mirror_state=False)
    assert list(compose["services"]) == ["cam-cam"]


@pytest.mark.parametrize("value", ["no", 0, 1, None, "false"])
def test_bad_mirror_state_is_refused(tmp_path, value):
    entry = {"name": "cam", "input": "rtsp", "source": "rtsp://192.0.2.2/s1", "outputs": ["onvif"]}
    entry.update(ip="198.51.100.20", mirror_state=value)
    with pytest.raises(app.ConfigError, match="camera cam: mirror_state must be true or false"):
        app.render(write_cameras(tmp_path, [entry]), tmp_path / "out")


def test_a_source_without_a_host_needs_the_opt_out(tmp_path):
    with pytest.raises(app.ConfigError, match="camera cam: mirror_state needs .* or set mirror_state: false"):
        render_one(tmp_path, source="ffmpeg:other#video=h264")


def test_a_scoped_ipv6_source_needs_the_opt_out(tmp_path):
    # Rendered, it would crash the sentinel on an nft syntax error and leave the copy open
    with pytest.raises(app.ConfigError, match="camera cam: mirror_state needs .* or set mirror_state: false"):
        render_one(tmp_path, source="rtsp://[fe80::1%25eth0]:554/s1")


def test_render_copies_the_sentinel_build_folder(tmp_path, monkeypatch):
    source = tmp_path / "sentinel"
    shutil.copytree(ROOT / "sentinel", source, ignore=shutil.ignore_patterns("__pycache__"))
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "sentinel.cpython-313.pyc").write_bytes(b"")
    monkeypatch.setattr(app, "SENTINEL_DIR", source)
    render_one(tmp_path)
    out = tmp_path / "out" / "sentinel"
    for name in ("Dockerfile", ".dockerignore", "sentinel.py"):
        assert (out / name).read_bytes() == (ROOT / "sentinel" / name).read_bytes()
    assert not (out / "__pycache__").exists()
    render_one(tmp_path)


def test_sentinel_image_is_pinned():
    text = (ROOT / "sentinel" / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^FROM [^\s]+@sha256:[0-9a-f]{64}$", text, re.M)
    assert re.search(r"apt-get install [^\n]*\bnftables=[0-9][^\s]*", text)


def test_the_mirror_check_runs_the_sentinel_as_the_rendered_compose_does(tmp_path):
    _, compose = render_one(tmp_path)
    rendered = compose["services"]["cam-cam-mirror"]
    check = yaml.safe_load((ROOT / "tests" / "mirror" / "compose.yml").read_text(encoding="utf-8"))
    checked = check["services"]["cam-mirror"]
    for key in ("cap_drop", "cap_add", "read_only", "security_opt", "restart"):
        assert checked[key] == rendered[key]
    assert checked["network_mode"] == "service:cam"


# Webhook support: the sentinels write their state to a shared volume, cams-webhook posts each change.

HOOK_ENV = {"CAMS_WEBHOOK_URL": "http://n8n:5678/webhook/abc", "CAMS_WEBHOOK_NETWORK": "proxy"}


def render_env(tmp_path: Path, env: dict, cameras: list[dict] | None = None) -> dict:
    entry = {
        "name": "cam",
        "input": "rtsp",
        "source": "rtsp://192.0.2.2/s1",
        "outputs": ["onvif"],
        "ip": "198.51.100.20",
    }
    out = tmp_path / "out"
    app.render(write_cameras(tmp_path, cameras or [entry]), out, env=env)
    return yaml.safe_load((out / "compose.yml").read_text(encoding="utf-8"))


def test_sentinel_writes_its_state_to_the_shared_volume(tmp_path):
    _, compose = render_one(tmp_path)
    mirror = compose["services"]["cam-cam-mirror"]
    assert mirror["volumes"] == ["state:/state"]
    assert mirror["environment"]["CAMERA"] == "cam"
    assert mirror["environment"]["STATE_DIR"] == "/state"
    assert compose["volumes"] == {"state": {}}
    # The root stays read-only: only the volume is writable
    assert mirror["read_only"] is True


def test_the_sentinel_image_is_bumped_for_the_state_file():
    assert app.SENTINEL_IMAGE == "moonie-cams-sentinel:3"


def test_one_webhook_service_per_project(tmp_path):
    cameras = [
        {"name": "a", "input": "rtsp", "source": "rtsp://192.0.2.2/s1", "outputs": ["onvif"], "ip": "198.51.100.20"},
        {"name": "b", "input": "rtsp", "source": "rtsp://192.0.2.3/s1", "outputs": ["onvif"], "ip": "198.51.100.21"},
    ]
    compose = render_env(tmp_path, HOOK_ENV, cameras)
    hooks = [name for name in compose["services"] if "webhook" in name]
    assert hooks == ["cams-webhook"]
    service = compose["services"]["cams-webhook"]
    assert service["build"] == "./webhook"
    assert service["image"] == app.WEBHOOK_IMAGE == "moonie-cams-webhook:2"
    # No container_name: compose names it <project>-cams-webhook-1, unique per project
    assert "container_name" not in service
    assert service["restart"] == "unless-stopped"
    assert service["volumes"] == ["state:/state:ro"]
    assert service["environment"] == {
        # Taken from .env by docker compose: the URL never lands in the rendered folder
        "CAMS_WEBHOOK_URL": "${CAMS_WEBHOOK_URL:-}",
        "CAMS_WEBHOOK_ON_START": "${CAMS_WEBHOOK_ON_START:-false}",
        "WEBHOOK_HOST": "moonie-cams",
        "STATE_DIR": "/state",
    }
    assert service["networks"] == ["webhook"]
    assert compose["networks"]["webhook"] == {
        "external": True,
        "name": "${CAMS_WEBHOOK_NETWORK:?set CAMS_WEBHOOK_NETWORK in .env}",
    }
    assert "abc" not in (tmp_path / "out" / "compose.yml").read_text(encoding="utf-8")


def test_webhook_is_hardened(tmp_path):
    service = render_env(tmp_path, HOOK_ENV)["services"]["cams-webhook"]
    assert service["cap_drop"] == ["ALL"]
    assert "cap_add" not in service
    assert service["read_only"] is True
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert "privileged" not in service and "ports" not in service


def test_without_a_url_the_webhook_has_no_network(tmp_path):
    for env in ({}, {"CAMS_WEBHOOK_URL": "", "CAMS_WEBHOOK_NETWORK": ""}, {"CAMS_WEBHOOK_NETWORK": "proxy"}):
        compose = render_env(tmp_path, env)
        service = compose["services"]["cams-webhook"]
        assert service["network_mode"] == "none"
        assert "networks" not in service
        assert "webhook" not in compose.get("networks", {})


def test_a_url_needs_the_network(tmp_path):
    with pytest.raises(app.ConfigError, match=r"\.env: CAMS_WEBHOOK_NETWORK is required when CAMS_WEBHOOK_URL is set"):
        render_env(tmp_path, {"CAMS_WEBHOOK_URL": "http://n8n:5678/webhook/abc"})


@pytest.mark.parametrize("url", ["n8n:5678/webhook/abc", "ftp://n8n/abc", "http://"])
def test_a_url_must_be_http(tmp_path, url):
    with pytest.raises(app.ConfigError, match=r"\.env: CAMS_WEBHOOK_URL must be an http:// or https:// URL") as err:
        render_env(tmp_path, {**HOOK_ENV, "CAMS_WEBHOOK_URL": url})
    # Never echoed: its path is the webhook's secret
    assert "abc" not in str(err.value)


@pytest.mark.parametrize("network", ["-proxy", "pro xy", "proxy$", "a/b"])
def test_a_network_name_must_be_a_docker_name(tmp_path, network):
    with pytest.raises(app.ConfigError, match=r"\.env: CAMS_WEBHOOK_NETWORK"):
        render_env(tmp_path, {**HOOK_ENV, "CAMS_WEBHOOK_NETWORK": network})


def test_no_sentinel_means_no_webhook_and_no_volume(tmp_path):
    _, compose = render_one(tmp_path, mirror_state=False)
    assert "cams-webhook" not in compose["services"]
    assert "volumes" not in compose
    assert not (tmp_path / "out" / "webhook").exists()


def test_the_token_secret_is_mounted_only_when_its_file_exists(tmp_path):
    compose = render_env(tmp_path, HOOK_ENV)
    assert "secrets" not in compose["services"]["cams-webhook"]
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "CAMS_WEBHOOK_TOKEN").write_text("t", encoding="utf-8")
    compose = render_env(tmp_path, HOOK_ENV)
    service = compose["services"]["cams-webhook"]
    assert service["secrets"] == [{"source": "cams_webhook_token", "target": "CAMS_WEBHOOK_TOKEN"}]
    assert compose["secrets"]["cams_webhook_token"] == {"file": "../secrets/CAMS_WEBHOOK_TOKEN"}


def test_render_copies_the_webhook_build_folder(tmp_path):
    render_env(tmp_path, HOOK_ENV)
    out = tmp_path / "out" / "webhook"
    for name in ("Dockerfile", ".dockerignore", "webhook.py"):
        assert (out / name).read_bytes() == (ROOT / "webhook" / name).read_bytes()
    assert not (out / "__pycache__").exists()


def test_the_webhook_image_reuses_the_sentinel_base():
    def base(folder):
        text = (ROOT / folder / "Dockerfile").read_text(encoding="utf-8")
        return re.search(r"^FROM (\S+)$", text, re.M).group(1)

    assert base("webhook") == base("sentinel")
    text = (ROOT / "webhook" / "Dockerfile").read_text(encoding="utf-8")
    assert "USER 65534:65534" in text


def effective_container_names(compose: dict) -> set[str]:
    """The container names docker compose gives: container_name, else <project>-<service>-1."""
    return {service.get("container_name", f"{compose['name']}-{key}-1") for key, service in compose["services"].items()}


def test_two_projects_on_one_host_share_no_container_name(tmp_path):
    def stack(project: str, camera: str, ip: str) -> dict:
        cam = {"name": camera, "input": "rtsp", "source": "rtsp://192.0.2.2/s1", "outputs": ["onvif"], "ip": ip}
        folder = tmp_path / project
        folder.mkdir()
        return render_env(folder, {**HOOK_ENV, "COMPOSE_PROJECT_NAME": project}, [cam])

    vlan10, vlan20 = stack("cams-vlan10", "garage", "198.51.100.20"), stack("cams-vlan20", "porch", "203.0.113.20")
    assert vlan10["name"] == "cams-vlan10" and vlan20["name"] == "cams-vlan20"
    assert "cams-vlan10-cams-webhook-1" in effective_container_names(vlan10)
    assert not effective_container_names(vlan10) & effective_container_names(vlan20)
    # Volumes and the macvlan network are project-scoped (no fixed name); only the endpoint's
    # network is shared, on purpose
    for compose in (vlan10, vlan20):
        assert all("name" not in volume for volume in compose["volumes"].values())
        assert "name" not in compose["networks"]["lan"]
        assert compose["services"]["cams-webhook"]["environment"]["WEBHOOK_HOST"] == compose["name"]


def test_the_project_name_defaults_to_moonie_cams(tmp_path):
    assert render_env(tmp_path, HOOK_ENV)["name"] == "moonie-cams"
    assert render_env(tmp_path, {**HOOK_ENV, "COMPOSE_PROJECT_NAME": ""})["name"] == "moonie-cams"


@pytest.mark.parametrize("project", ["Cams", "-cams", "cams vlan", "cams.vlan", "cams/vlan"])
def test_the_project_name_must_be_a_compose_name(tmp_path, project):
    with pytest.raises(app.ConfigError, match=r"\.env: COMPOSE_PROJECT_NAME"):
        render_env(tmp_path, {**HOOK_ENV, "COMPOSE_PROJECT_NAME": project})


def test_a_camera_cannot_take_the_webhook_container_name(tmp_path):
    with pytest.raises(app.ConfigError, match="camera webhook: the name webhook is reserved"):
        render_one(tmp_path, name="webhook")


def test_read_env(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# comment\n\nCAMS_WEBHOOK_URL=http://n8n:5678/webhook/a#b\nCAMS_WEBHOOK_NETWORK = 'proxy'\nTZ=\"Etc/UTC\"\n",
        encoding="utf-8",
    )
    assert app.read_env(path) == {
        "CAMS_WEBHOOK_URL": "http://n8n:5678/webhook/a#b",
        "CAMS_WEBHOOK_NETWORK": "proxy",
        "TZ": "Etc/UTC",
    }
    assert app.read_env(tmp_path / "missing") == {}


@pytest.mark.parametrize(
    ("line", "value"),
    [
        # As docker compose reads a .env: a leading export is dropped
        ("export KEY=value", "value"),
        ("export\tKEY=value", "value"),
        ("exported=value", None),
        # Unquoted: cut at a space followed by #, then trimmed; a # inside a word stays.
        # Checked against docker compose config: a tab before # does not cut, and the value is
        # trimmed before the cut, so a value that starts with # is kept whole
        ("KEY=value # a comment", "value"),
        ("KEY=a #b #c", "a"),
        ("KEY=a\t #c", "a"),
        ("KEY=value\t# a comment", "value\t# a comment"),
        ("KEY=http://n8n/webhook/a#b", "http://n8n/webhook/a#b"),
        ("KEY=a b  ", "a b"),
        ("KEY= #only a comment", "#only a comment"),
        # Quoted: up to the matching quote, the rest ignored
        ("KEY='value' # comment", "value"),
        ('KEY="value" trailing', "value"),
        ("KEY='a # b'", "a # b"),
        ('KEY="it\'s"', "it's"),
        ("KEY='say \"hi\"'", 'say "hi"'),
        ("KEY=''", ""),
        ("KEY='x'y", "x"),
        ("KEY=  'q' ", "q"),
        ('KEY="a\\"b"', 'a"b'),
    ],
)
def test_read_env_matches_docker_compose(tmp_path, line, value):
    path = tmp_path / ".env"
    path.write_text(line + "\n", encoding="utf-8")
    env = app.read_env(path)
    if value is None:
        assert env == {"exported": "value"}
    else:
        assert env == {"KEY": value}


def test_read_env_refuses_an_unterminated_quote(tmp_path):
    path = tmp_path / ".env"
    path.write_text("KEY='value\n", encoding="utf-8")
    with pytest.raises(app.ConfigError):
        app.read_env(path)


def test_main_reads_the_env_file(tmp_path):
    (tmp_path / ".env").write_text("CAMS_WEBHOOK_URL=http://n8n/x\n", encoding="utf-8")
    cameras = write_cameras(
        tmp_path,
        [
            {
                "name": "cam",
                "input": "rtsp",
                "source": "rtsp://192.0.2.2/s1",
                "outputs": ["onvif"],
                "ip": "198.51.100.20",
            }
        ],
    )
    code = app.main(["render", str(cameras), str(tmp_path / "out"), f"--env={tmp_path / '.env'}"])
    assert code == 1


def test_env_example_lists_the_webhook_keys():
    env = app.read_env(ROOT / ".env.example")
    assert env["CAMS_WEBHOOK_URL"] == "" and env["CAMS_WEBHOOK_NETWORK"] == ""
    assert env["CAMS_WEBHOOK_ON_START"] == "false"


def test_the_webhook_check_runs_the_services_as_the_rendered_compose_does(tmp_path):
    compose = render_env(tmp_path, HOOK_ENV)
    check = yaml.safe_load((ROOT / "tests" / "webhook" / "compose.yml").read_text(encoding="utf-8"))
    for rendered, checked in (("cams-webhook", "cams-webhook"), ("cam-cam-mirror", "cam-mirror")):
        for key in ("cap_drop", "read_only", "security_opt", "restart", "volumes"):
            assert check["services"][checked][key] == compose["services"][rendered][key], (checked, key)
    assert check["services"]["cam-mirror"]["environment"]["STATE_DIR"] == "/state"
