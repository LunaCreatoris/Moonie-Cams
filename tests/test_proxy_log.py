"""The ONVIF proxy's request log: one line per request, its levels, and no secret in it."""

import base64
import datetime
import re

import pytest

import onvif_proxy
from fake_tapo import ENVELOPE, HOST, FakeTapo
from test_proxy import COPY_IP, COPY_MAC, HA, MEDIA_NS, PROTECT, PTZ_NS, REQUEST, Clock, call, move, set_env, soap

NOW = datetime.datetime(2026, 11, 23, 10, 0, 0, 750000, tzinfo=datetime.UTC)
STAMP = "2026-11-23T10:00:00Z"
EVENTS_NS = "http://www.onvif.org/ver10/events/wsdl"
# The PTZ summary, when there is one, follows the operations in brackets and may hold spaces, never "]".
LINE_RE = re.compile(r"^\S+Z \S+ \S+ [^\s\[\]]+(?:\[[^\]\x00-\x1f\x7f]{1,200}\])? \S+ \S+ \d+ms$")
PROFILE = "[profile=profile_1]"


@pytest.fixture
def tapo():
    with FakeTapo() as fake:
        yield fake


def make_app(camera: str, clock, level: str = "all"):
    app = onvif_proxy.create_app(
        camera=camera,
        camera_host=HOST,
        camera_port=2020,
        copy_ip=COPY_IP,
        copy_mac=COPY_MAC,
        hostname="cams-c210",
        streams={"/stream1": "c210", "/stream2": "c210_sub"},
        ptz_hold=5,
        clock=clock,
        log_level=level,
        wall=lambda: NOW,
    )
    return app.test_client()


def lines(capsys) -> list[str]:
    out = capsys.readouterr().out
    return out.splitlines()


def test_a_relayed_read_logs_one_line(tapo, capsys):
    client = make_app(tapo.url, Clock())
    response = call(client, soap("GetProfiles", MEDIA_NS))
    assert lines(capsys) == [f"{STAMP} {PROTECT} /onvif/service GetProfiles relayed {response.status_code} 0ms"]


def test_the_duration_is_in_milliseconds(tapo, capsys):
    clock = Clock()

    class Slow:
        def __call__(self):
            clock.now += 0.0125
            return clock.now

    client = make_app(tapo.url, Slow())
    call(client, soap("GetProfiles", MEDIA_NS))
    (line,) = lines(capsys)
    assert line.endswith(" 12ms")


def test_an_empty_body_logs_a_dash(tapo, capsys):
    client = make_app(tapo.url, Clock())
    call(client, b"", path="/onvif/device_service")
    (line,) = lines(capsys)
    assert line.startswith(f"{STAMP} {PROTECT} /onvif/device_service - relayed ")


def test_a_refusal_logs_its_short_reason_and_no_status(tapo, capsys):
    client = make_app(tapo.url, Clock())
    call(client, soap("SystemReboot"))
    assert lines(capsys) == [f"{STAMP} {PROTECT} /onvif/service SystemReboot refused:camera-command - 0ms"]
    call(client, soap("SetVideoEncoderConfiguration", MEDIA_NS))
    assert lines(capsys) == [f"{STAMP} {PROTECT} /onvif/service SetVideoEncoderConfiguration refused:write - 0ms"]
    call(client, b"<not-xml")
    assert lines(capsys) == [f"{STAMP} {PROTECT} /onvif/service - refused:not-xml - 0ms"]
    call(client, soap("GetProfiles", MEDIA_NS), SOAPAction='"a b"')
    assert lines(capsys) == [f"{STAMP} {PROTECT} /onvif/service - refused:action - 0ms"]
    assert tapo.requests == []


def test_a_malformed_host_is_logged_as_refused(tapo, capsys):
    client = make_app(tapo.url, Clock())
    call(client, soap("GetProfiles", MEDIA_NS), Host="cam:99999")
    assert lines(capsys) == [f"{STAMP} {PROTECT} /onvif/service - refused:host - 0ms"]


def test_ptz_claim_hold_and_busy(tapo, capsys):
    clock = Clock()
    client = make_app(tapo.url, clock)
    move(client, PROTECT)
    assert lines(capsys) == [f"{STAMP} {PROTECT} /onvif/service ContinuousMove{PROFILE} ptz-claim 200 0ms"]
    clock.now += 1
    move(client, PROTECT, "Stop")
    assert lines(capsys) == [f"{STAMP} {PROTECT} /onvif/service Stop{PROFILE} ptz-hold 200 0ms"]
    clock.now += 2
    move(client, HA)
    assert lines(capsys) == [
        f"{STAMP} {HA} /onvif/service ContinuousMove{PROFILE} ptz-busy:holder={PROTECT},left=3.0s - 0ms"
    ]
    clock.now += 3.1
    move(client, HA)
    assert lines(capsys) == [f"{STAMP} {HA} /onvif/service ContinuousMove{PROFILE} ptz-claim 200 0ms"]


def test_a_ptz_command_the_camera_refuses_is_only_relayed(tapo, capsys):
    tapo.challenge = True
    client = make_app(tapo.url, Clock())
    move(client, PROTECT)
    assert lines(capsys) == [f"{STAMP} {PROTECT} /onvif/service ContinuousMove{PROFILE} relayed 401 0ms"]


def test_camera_down_logs_unreachable(capsys):
    client = make_app("http://127.0.0.1:9", Clock())
    call(client, soap("GetCapabilities"))
    assert lines(capsys) == [f"{STAMP} {PROTECT} /onvif/service GetCapabilities error:camera-unreachable - 0ms"]


def test_every_body_operation_is_listed(tapo, capsys):
    client = make_app(tapo.url, Clock())
    body = REQUEST.format(f'<p:GetStatus xmlns:p="{PTZ_NS}"/><p:GetNodes xmlns:p="{PTZ_NS}"/>').encode()
    call(client, body)
    (line,) = lines(capsys)
    assert " GetStatus,GetNodes relayed " in line


def test_a_hostile_path_cannot_break_the_line(tapo, capsys):
    client = make_app(tapo.url, Clock())
    call(client, soap("GetProfiles", MEDIA_NS), path="/onvif/a%0Dfake%20line%09x%C3%A9")
    (line,) = lines(capsys)
    assert LINE_RE.match(line)
    assert "/onvif/a%0Dfake%20line%09x%C3%A9" in line


def test_every_line_has_the_same_shape(tapo, capsys):
    clock = Clock()
    client = make_app(tapo.url, clock)
    call(client, soap("GetProfiles", MEDIA_NS))
    move(client, PROTECT)
    move(client, HA)
    call(client, soap("SystemReboot"))
    found = lines(capsys)
    assert len(found) == 4
    assert all(LINE_RE.match(line) for line in found)


# Secrets a client sends: a WS-Security UsernameToken, and a Basic or Digest Authorization.
USER, PASSWORD, NONCE, DIGEST = "cams-user-7f3a", "pw-Secret-91c2", "bm9uY2Utc2VjcmV0", "ZGlnZXN0LXNlY3JldA=="
SECRET_REQUEST = (
    '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Header>'
    '<wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">'
    f"<wsse:UsernameToken><wsse:Username>{USER}</wsse:Username>"
    f"<wsse:Password>{DIGEST}</wsse:Password><wsse:Nonce>{NONCE}</wsse:Nonce>"
    "<wsu:Created xmlns:wsu='http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd'>"
    "2031-07-14T03:21:09Z</wsu:Created></wsse:UsernameToken></wsse:Security></s:Header>"
    "<s:Body>{}</s:Body></s:Envelope>"
)
BASIC = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
DIGEST_AUTH = (
    f'Digest username="{USER}", realm="tapo", nonce="n0nce-d1g3st", uri="/onvif/service",'
    ' response="9f8e7d6c5b4a39281706f5e4d3c2b1a0", cnonce="c-n0nce-55", nc=00000001, qop=auth'
)
SECRETS = [
    USER,
    PASSWORD,
    NONCE,
    DIGEST,
    "2031-07-14",
    BASIC.split()[1],
    "n0nce-d1g3st",
    "9f8e7d6c5b4a39281706f5e4d3c2b1a0",
    "c-n0nce-55",
    "Basic",
    "Digest",
    "Security",
    "UsernameToken",
]


@pytest.mark.parametrize("auth", [BASIC, DIGEST_AUTH])
@pytest.mark.parametrize(
    ("op", "ns"),
    [("GetProfiles", MEDIA_NS), ("ContinuousMove", PTZ_NS), ("SystemReboot", "http://www.onvif.org/ver10/device/wsdl")],
)
def test_no_secret_is_ever_logged(tapo, capsys, auth, op, ns):
    tapo.answers[""] = (200, f"<html>{USER} {PASSWORD} answer-body-marker</html>", "text/html")
    for camera in (tapo.url, "http://127.0.0.1:9"):
        client = make_app(camera, Clock())
        body = SECRET_REQUEST.format(f'<p:{op} xmlns:p="{ns}"/>').encode()
        call(client, body, Authorization=auth)
        call(client, body, path="/onvif/service?user=" + USER, Authorization=auth)
        # The digest challenge the camera may answer with is not logged either.
        tapo.challenge = True
        call(client, body, Authorization=auth)
        tapo.challenge = False
    out = capsys.readouterr().out
    assert out.count("\n") == 6
    for secret in [*SECRETS, "answer-body-marker", "realm"]:
        assert secret not in out


def at_level(tapo, capsys, level: str) -> list[str]:
    # The fake answers 404 to calls it does not know: answer them as a camera that knows them.
    tapo.answers[""] = (200, "<ok/>", "application/soap+xml")
    clock = Clock()
    client = make_app(tapo.url, clock, level)
    call(client, soap("GetProfiles", MEDIA_NS))  # a read
    call(client, soap("GetSystemDateAndTime"))  # a read
    call(client, soap("PullMessages", EVENTS_NS), path="/event-0_2020")  # a long poll
    call(client, soap("CreatePullPointSubscription", EVENTS_NS))  # an event call
    call(client, soap("GetStatus", PTZ_NS))  # a PTZ read
    move(client, PROTECT)  # PTZ claim
    move(client, HA)  # PTZ busy
    call(client, soap("SystemReboot"))  # a refusal
    tapo.answers["GetDeviceInformation"] = (503, "down", "text/plain")
    call(client, soap("GetDeviceInformation"))  # a read the camera fails
    return [line.split()[3] + " " + line.split()[4] for line in lines(capsys)]


def test_level_all_logs_every_request(tapo, capsys):
    assert at_level(tapo, capsys, "all") == [
        "GetProfiles relayed",
        "GetSystemDateAndTime relayed",
        "PullMessages relayed",
        "CreatePullPointSubscription relayed",
        "GetStatus relayed",
        f"ContinuousMove{PROFILE} ptz-claim",
        f"ContinuousMove{PROFILE} ptz-busy:holder={PROTECT},left=5.0s",
        "SystemReboot refused:camera-command",
        "GetDeviceInformation relayed",
    ]


def test_level_writes_skips_reads_and_long_polls(tapo, capsys):
    assert at_level(tapo, capsys, "writes") == [
        "CreatePullPointSubscription relayed",
        "GetStatus relayed",
        f"ContinuousMove{PROFILE} ptz-claim",
        f"ContinuousMove{PROFILE} ptz-busy:holder={PROTECT},left=5.0s",
        "SystemReboot refused:camera-command",
        "GetDeviceInformation relayed",
    ]


def test_level_writes_logs_a_read_when_the_camera_is_down(capsys):
    client = make_app("http://127.0.0.1:9", Clock(), "writes")
    call(client, soap("GetProfiles", MEDIA_NS))
    assert [line.split()[4] for line in lines(capsys)] == ["error:camera-unreachable"]


def test_level_off_logs_nothing(tapo, capsys):
    assert at_level(tapo, capsys, "off") == []


def test_the_default_level_is_writes(monkeypatch):
    set_env(monkeypatch)
    monkeypatch.delenv("PROXY_LOG", raising=False)
    assert onvif_proxy.config_from_env()["log_level"] == "writes"


@pytest.mark.parametrize("level", ["all", "writes", "off"])
def test_a_known_level_is_accepted(monkeypatch, level):
    set_env(monkeypatch, PROXY_LOG=level)
    assert onvif_proxy.config_from_env()["log_level"] == level


@pytest.mark.parametrize("level", ["", "ALL", "debug", "write", " off"])
def test_a_bad_level_stops_the_proxy(monkeypatch, level):
    set_env(monkeypatch, PROXY_LOG=level)
    with pytest.raises(ValueError, match="PROXY_LOG must be one of all, writes, off"):
        onvif_proxy.config_from_env()


def test_create_app_refuses_a_bad_level():
    with pytest.raises(ValueError, match="PROXY_LOG"):
        make_app("http://127.0.0.1:9", Clock(), "loud")


# The PTZ summary: a short, allow-listed view of what a PTZ command asks for.
TT = "http://www.onvif.org/ver10/schema"
DEVICE_NS = "http://www.onvif.org/ver10/device/wsdl"
OPS_RE = re.compile(r"^\S+ \S+ \S+ ([^\s\[]+)(?:\[([^\]]*)\])? ")


def ops_of(line: str) -> str:
    """The operations field of a line, without its summary."""
    return OPS_RE.match(line).group(1)


def summary_of(line: str) -> str | None:
    """The PTZ summary of a line (the text in its brackets), or None."""
    return OPS_RE.match(line).group(2)


def ptz(op: str, inner: str = "", token: str | None = "profile_1") -> bytes:
    """A PTZ command as zeep sends it, with tt: vectors and an optional ProfileToken."""
    profile = "" if token is None else f"<p:ProfileToken>{token}</p:ProfileToken>"
    return REQUEST.format(f'<p:{op} xmlns:p="{PTZ_NS}" xmlns:tt="{TT}">{profile}{inner}</p:{op}>').encode()


def vector(name: str, x="0.5", y="-0.25", zoom="0") -> str:
    """A PTZVector or PTZSpeed element: PanTilt x/y and Zoom x attributes, each optional."""
    pan = "" if x is None else f'<tt:PanTilt x="{x}" y="{y}"/>'
    zoom_el = "" if zoom is None else f'<tt:Zoom x="{zoom}"/>'
    return f"<p:{name}>{pan}{zoom_el}</p:{name}>"


SUMMARIES = [
    ("ContinuousMove", vector("Velocity"), "pan=0.5 tilt=-0.25 zoom=0 profile=profile_1"),
    (
        "ContinuousMove",
        vector("Velocity", "1", "0", "-1") + "<p:Timeout>PT1.5S</p:Timeout>",
        "pan=1 tilt=0 zoom=-1 timeout=PT1.5S profile=profile_1",
    ),
    ("ContinuousMove", vector("Velocity", zoom=None), "pan=0.5 tilt=-0.25 profile=profile_1"),
    ("ContinuousMove", vector("Velocity", x=None), "zoom=0 profile=profile_1"),
    ("RelativeMove", vector("Translation", "0.1", "0.2", "0.3"), "pan=0.1 tilt=0.2 zoom=0.3 profile=profile_1"),
    (
        "RelativeMove",
        vector("Translation", "0.1", "0.2", "0.3") + vector("Speed", "1", "1", "0.5"),
        "pan=0.1 tilt=0.2 zoom=0.3 speed.pan=1 speed.tilt=1 speed.zoom=0.5 profile=profile_1",
    ),
    ("AbsoluteMove", vector("Position", "-1", "1", "0.75"), "pan=-1 tilt=1 zoom=0.75 profile=profile_1"),
    (
        "AbsoluteMove",
        vector("Position", "0", "0", "0") + vector("Speed", "0.5", "0.5", None),
        "pan=0 tilt=0 zoom=0 speed.pan=0.5 speed.tilt=0.5 profile=profile_1",
    ),
    ("Stop", "<p:PanTilt>true</p:PanTilt><p:Zoom>false</p:Zoom>", "pantilt=true zoom=false profile=profile_1"),
    ("Stop", "<p:PanTilt> 1 </p:PanTilt>", "pantilt=true profile=profile_1"),
    ("Stop", "<p:Zoom>maybe</p:Zoom>", "zoom=? profile=profile_1"),
    ("Stop", "", "profile=profile_1"),
    ("GotoPreset", "<p:PresetToken>3</p:PresetToken>", "preset=3 profile=profile_1"),
    (
        "SetPreset",
        "<p:PresetName>Front door</p:PresetName><p:PresetToken>4</p:PresetToken>",
        "preset=4 name=Front%20door profile=profile_1",
    ),
    ("SetPreset", "<p:PresetName>Gate</p:PresetName>", "name=Gate profile=profile_1"),
    ("RemovePreset", "<p:PresetToken>4</p:PresetToken>", "preset=4 profile=profile_1"),
    ("GotoHomePosition", "", "profile=profile_1"),
    (
        "GotoHomePosition",
        vector("Speed", "0.5", "0.5", "1"),
        "speed.pan=0.5 speed.tilt=0.5 speed.zoom=1 profile=profile_1",
    ),
    ("SetHomePosition", "", "profile=profile_1"),
]


@pytest.mark.parametrize(("op", "inner", "summary"), SUMMARIES)
def test_each_ptz_command_logs_its_summary(tapo, capsys, op, inner, summary):
    client = make_app(tapo.url, Clock())
    # The fake answers only ContinuousMove and Stop: answer the others as a camera that knows them.
    tapo.answers[""] = (200, ENVELOPE.format(f"<tptz:{op}Response/>"), "application/soap+xml")
    response = call(client, ptz(op, inner))
    (line,) = lines(capsys)
    assert line == f"{STAMP} {PROTECT} /onvif/service {op}[{summary}] ptz-claim {response.status_code} 0ms"
    assert LINE_RE.match(line)


@pytest.mark.parametrize(
    ("value", "written"),
    [
        ("0.12345", "0.123"),
        ("0.9996", "1"),
        ("-0.0001", "0"),
        ("  2.5E-1 ", "0.25"),
        ("+.5", "0.5"),
        ("abc", "?"),
        ("", "?"),
        ("nan", "?"),
        ("INF", "?"),
        ("1e999", "?"),
        ("1_0", "?"),
        ("0x10", "?"),
        ("1,5", "?"),
        ("\u0661", "?"),
    ],
)
def test_numbers_are_rounded_floats_or_a_question_mark(tapo, capsys, value, written):
    client = make_app(tapo.url, Clock())
    call(client, ptz("ContinuousMove", vector("Velocity", value, "0", None), token=None))
    (line,) = lines(capsys)
    assert summary_of(line) == f"pan={written} tilt=0"


def test_a_field_given_twice_is_a_question_mark(tapo, capsys):
    client = make_app(tapo.url, Clock())
    inner = "<p:PresetToken>1</p:PresetToken><p:PresetToken>2</p:PresetToken>"
    call(client, ptz("GotoPreset", inner, token="a</p:ProfileToken><p:ProfileToken>b"))
    (line,) = lines(capsys)
    assert summary_of(line) == "preset=? profile=?"


def test_a_field_holding_an_element_is_a_question_mark(tapo, capsys):
    client = make_app(tapo.url, Clock())
    call(client, ptz("GotoPreset", "<p:PresetToken>1<x>2</x></p:PresetToken>", token="a<b>c</b>"))
    (line,) = lines(capsys)
    assert summary_of(line) == "preset=? profile=?"


def test_a_command_with_no_field_logs_no_brackets(tapo, capsys):
    client = make_app(tapo.url, Clock())
    call(client, ptz("SetHomePosition", token=None))
    (line,) = lines(capsys)
    assert ops_of(line) == "SetHomePosition"
    assert summary_of(line) is None
    assert line == f"{STAMP} {PROTECT} /onvif/service SetHomePosition relayed 404 0ms"


def test_a_token_is_percent_encoded_and_cut_at_40(tapo, capsys):
    client = make_app(tapo.url, Clock())
    call(client, ptz("GotoPreset", "<p:PresetToken>" + "\u00e9" * 30 + "</p:PresetToken>", token="p" * 41))
    (line,) = lines(capsys)
    preset, profile = summary_of(line).split(" ")
    # The cut keeps whole characters: never half a percent escape nor half a UTF-8 sequence.
    assert preset == "preset=" + "%C3%A9" * 6 + "..."
    assert profile == "profile=" + "p" * 37 + "..."


def test_a_token_of_40_characters_is_kept_whole(tapo, capsys):
    client = make_app(tapo.url, Clock())
    call(client, ptz("GotoPreset", "<p:PresetToken>" + "t" * 40 + "</p:PresetToken>", token="p" * 40))
    (line,) = lines(capsys)
    assert summary_of(line) == f"preset={'t' * 40} profile={'p' * 40}"


HOSTILE = {
    "newline": "x\n2026-11-23T10:00:00Z 198.51.100.9 /onvif/service SystemReboot relayed 200 1ms",
    "carriage return": "x\r\nforged",
    "spaces": "a b ptz-claim 200 0ms",
    "bracket": "x] relayed 200 0ms [y",
    "xml-looking": "&lt;wsse:Password&gt;hunter2&lt;/wsse:Password&gt;",
    "huge": "A" * 10240,
    "unicode breaks": "a\tb\u0085c\u2028d\u2029e",
}


@pytest.mark.parametrize("name", list(HOSTILE))
def test_a_hostile_value_cannot_forge_a_line_or_blow_its_size(tapo, capsys, name):
    value = HOSTILE[name]
    client = make_app(tapo.url, Clock())
    inner = f"<p:PresetName>{value}</p:PresetName><p:PresetToken>{value}</p:PresetToken>"
    call(client, ptz("SetPreset", inner, token=value))
    move_inner = vector("Velocity", value, value, value) + f"<p:Timeout>{value}</p:Timeout>"
    call(client, ptz("ContinuousMove", move_inner, token=value))
    found = lines(capsys)
    assert [ops_of(line) for line in found] == ["SetPreset", "ContinuousMove"]
    for line in found:
        assert LINE_RE.match(line), line
        assert len(summary_of(line)) <= 200
        assert len(line) < 320
        assert "<" not in line and ">" not in line
    # The numbers are not numbers: each is a question mark, whatever it held.
    assert summary_of(found[1]).startswith("pan=? tilt=? zoom=? timeout=")


def test_the_whole_summary_is_cut_at_200(tapo, capsys):
    client = make_app(tapo.url, Clock())
    inner = f"<p:PresetName>{'n' * 100}</p:PresetName><p:PresetToken>{'t' * 100}</p:PresetToken>"
    big = "1" + "0" * 45
    speed = vector("Speed", big, big, big)
    call(client, ptz("SetPreset", inner, token="q" * 100))
    call(client, ptz("RelativeMove", vector("Translation", big, big, big) + speed, token="q" * 100))
    first, second = (summary_of(line) for line in lines(capsys))
    # Each value is cut at 40 first: three of them fit whole.
    assert first == f"preset={'t' * 37}... name={'n' * 37}... profile={'q' * 37}..."
    # Six long numbers and a long profile do not: the whole summary is cut at 200.
    assert len(second) == 200
    assert re.match(r"pan=[0-9]{37}\.\.\. tilt=[0-9]{37}\.\.\. ", second) and second.endswith("...")


# A Header that mimics the PTZ fields: none of it may reach the log.
TRAP, TRAP_N = "trap-9d1e", "8642"
TRAP_HEADER = (
    "<s:Header><wsse:Security"
    ' xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"'
    f' xmlns:p="{PTZ_NS}" xmlns:tt="{TT}">'
    f"<wsse:UsernameToken><wsse:Username>{USER}</wsse:Username><wsse:Password>{DIGEST}</wsse:Password>"
    f"<wsse:Nonce>{NONCE}</wsse:Nonce></wsse:UsernameToken>"
    f'<p:ProfileToken>{TRAP}</p:ProfileToken><p:Velocity><tt:PanTilt x="0.{TRAP_N}" y="0.{TRAP_N}"/>'
    f'<tt:Zoom x="0.{TRAP_N}"/></p:Velocity><x>{TRAP}</x><p:PresetToken>{TRAP}</p:PresetToken>'
    f"<p:PresetName>{TRAP}</p:PresetName><p:Timeout>{TRAP}</p:Timeout></wsse:Security>"
    f'<p:ContinuousMove xmlns:p="{PTZ_NS}"><p:ProfileToken>{TRAP}</p:ProfileToken></p:ContinuousMove>'
    "</s:Header>"
)


def trap_request(op: str, inner: str) -> bytes:
    return (
        f'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">{TRAP_HEADER}'
        f'<s:Body><p:{op} xmlns:p="{PTZ_NS}" xmlns:tt="{TT}">{inner}</p:{op}></s:Body></s:Envelope>'
    ).encode()


@pytest.mark.parametrize(
    ("op", "inner", "summary"),
    [
        ("ContinuousMove", "", None),
        ("ContinuousMove", '<p:Velocity><tt:PanTilt x="0.5" y="0"/></p:Velocity>', "pan=0.5 tilt=0"),
        ("SetPreset", "", None),
        ("Stop", "<p:ProfileToken>profile_1</p:ProfileToken>", "profile=profile_1"),
    ],
)
@pytest.mark.parametrize("auth", [BASIC, DIGEST_AUTH])
def test_the_header_and_authorization_never_reach_the_summary(tapo, capsys, op, inner, summary, auth):
    for camera in (tapo.url, "http://127.0.0.1:9"):
        client = make_app(camera, Clock())
        call(client, trap_request(op, inner), Authorization=auth)
        # The other client: told PTZ is busy, or that the camera is unreachable.
        call(client, trap_request(op, inner), source=HA, Authorization=auth)
    found = lines(capsys)
    assert len(found) == 4
    for line in found:
        assert ops_of(line) == op
        assert summary_of(line) == summary
    out = "\n".join(found)
    for secret in [*SECRETS, TRAP, TRAP_N, "Authorization", "realm"]:
        assert secret not in out


def test_a_refused_ptz_command_logs_its_summary(tapo, capsys):
    client = make_app(tapo.url, Clock())
    inner = vector("Velocity") + f'<d:SystemReboot xmlns:d="{DEVICE_NS}"/>'
    call(client, ptz("ContinuousMove", inner))
    assert lines(capsys) == [
        f"{STAMP} {PROTECT} /onvif/service ContinuousMove[pan=0.5 tilt=-0.25 zoom=0 profile=profile_1]"
        " refused:camera-command - 0ms"
    ]
    assert tapo.requests == []


def test_a_busy_ptz_command_logs_its_own_summary(tapo, capsys):
    client = make_app(tapo.url, Clock())
    call(client, ptz("ContinuousMove", vector("Velocity")))
    call(client, ptz("GotoPreset", "<p:PresetToken>7</p:PresetToken>", token="profile_2"), source=HA)
    assert lines(capsys)[1] == (
        f"{STAMP} {HA} /onvif/service GotoPreset[preset=7 profile=profile_2] ptz-busy:holder={PROTECT},left=5.0s - 0ms"
    )


def test_an_unreachable_camera_keeps_the_summary(capsys):
    client = make_app("http://127.0.0.1:9", Clock())
    call(client, ptz("Stop", "<p:Zoom>true</p:Zoom>"))
    assert lines(capsys) == [
        f"{STAMP} {PROTECT} /onvif/service Stop[zoom=true profile=profile_1] error:camera-unreachable - 0ms"
    ]


@pytest.mark.parametrize("level", ["all", "writes"])
def test_the_summary_is_written_at_every_level_that_writes_the_line(tapo, capsys, level):
    client = make_app(tapo.url, Clock(), level)
    call(client, ptz("Stop", "<p:PanTilt>true</p:PanTilt>"))
    assert lines(capsys) == [f"{STAMP} {PROTECT} /onvif/service Stop[pantilt=true profile=profile_1] ptz-claim 200 0ms"]


def test_a_command_outside_the_ptz_service_has_no_summary(tapo, capsys):
    client = make_app(tapo.url, Clock())
    call(client, soap("ContinuousMove", "urn:vendor", "<ns0:ProfileToken>profile_1</ns0:ProfileToken>"))
    call(client, soap("Stop", MEDIA_NS, "<ns0:ProfileToken>profile_1</ns0:ProfileToken>"))
    found = lines(capsys)
    assert [line.split()[3] for line in found] == ["ContinuousMove", "Stop"]
    assert [summary_of(line) for line in found] == [None, None]


def test_non_ptz_lines_are_unchanged(tapo, capsys):
    tapo.answers[""] = (200, "<ok/>", "application/soap+xml")
    client = make_app(tapo.url, Clock())
    call(client, soap("GetProfiles", MEDIA_NS, "<ns0:ProfileToken>profile_1</ns0:ProfileToken>"))
    call(client, soap("GetStatus", PTZ_NS, "<ns0:ProfileToken>profile_1</ns0:ProfileToken>"))
    call(client, soap("CreatePullPointSubscription", EVENTS_NS))
    call(client, soap("SystemReboot"))
    assert lines(capsys) == [
        f"{STAMP} {PROTECT} /onvif/service GetProfiles relayed 200 0ms",
        f"{STAMP} {PROTECT} /onvif/service GetStatus relayed 200 0ms",
        f"{STAMP} {PROTECT} /onvif/service CreatePullPointSubscription relayed 200 0ms",
        f"{STAMP} {PROTECT} /onvif/service SystemReboot refused:camera-command - 0ms",
    ]


def test_the_summary_follows_every_operation_of_the_body(tapo, capsys):
    client = make_app(tapo.url, Clock())
    body = REQUEST.format(
        f'<p:GetStatus xmlns:p="{PTZ_NS}"/><p:GotoPreset xmlns:p="{PTZ_NS}">'
        "<p:ProfileToken>profile_1</p:ProfileToken><p:PresetToken>2</p:PresetToken></p:GotoPreset>"
    ).encode()
    call(client, body)
    (line,) = lines(capsys)
    assert " GetStatus,GotoPreset[preset=2 profile=profile_1] " in line
