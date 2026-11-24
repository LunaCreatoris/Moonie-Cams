"""go2rtc/check-secrets.sh: the go2rtc image exports each secret URL-encoded as <KEY>_URL, and
refuses a secret that would break go2rtc's config."""

import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote, unquote

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "go2rtc" / "check-secrets.sh"
# dash (Debian's sh) and busybox ash, the go2rtc image's runtime shell (the tools image ships
# the same busybox release as the go2rtc image's Alpine)
SHELLS = [["sh"], ["busybox", "sh"]]
MESSAGE = (
    "secret {name} holds a character go2rtc's config cannot hold"
    ' (" \\, a control character such as a tab or an inner newline,'
    " or a Unicode space such as a no-break space); change it\n"
)


@pytest.fixture(params=SHELLS, ids=lambda shell: " ".join(shell), autouse=True)
def shell(request, monkeypatch):
    """Every test runs once per shell; a missing busybox fails rather than skips."""
    assert shutil.which(request.param[0]), f"{request.param[0]} is not installed"
    monkeypatch.setitem(globals(), "SHELL", request.param)
    return request.param


SHELL = SHELLS[0]


def run(tmp_path: Path, secrets: dict[str, bytes] | None, args: list[str]):
    env = {k: v for k, v in os.environ.items() if k != "CREDENTIALS_DIRECTORY" and not k.endswith("_URL")}
    if secrets is not None:
        creds = tmp_path / "creds"
        creds.mkdir(exist_ok=True)
        for name, value in secrets.items():
            (creds / name).write_bytes(value)
        env["CREDENTIALS_DIRECTORY"] = str(creds)
    return subprocess.run([*SHELL, str(SCRIPT), *args], env=env, capture_output=True, check=False)


def show(name: str) -> list[str]:
    """A command that prints one variable of the environment the script execs into."""
    return ["sh", "-c", f'printf "%s" "${{{name}-unset}}"']


def url_value(tmp_path: Path, value: bytes) -> str:
    result = run(tmp_path, {"CAM_SOURCE_PASSWORD": value}, show("CAM_SOURCE_PASSWORD_URL"))
    assert result.returncode == 0, result.stderr
    assert result.stderr == b""
    return result.stdout.decode("ascii")


@pytest.mark.parametrize("char", list("@:/?#[]%^ !$&'()*+,;=<>{}|`"))
def test_each_special_character_is_percent_encoded(tmp_path, char):
    assert url_value(tmp_path, f"a{char}b".encode()) == f"a%{ord(char):02X}b"


def test_unreserved_characters_stay_as_they_are(tmp_path):
    value = "AZaz09-._~"
    assert url_value(tmp_path, value.encode()) == value


def test_utf8_is_encoded_byte_by_byte(tmp_path):
    value = "Zone Ω Ü"
    assert url_value(tmp_path, value.encode()) == quote(value, safe="")


def test_a_real_camera_password_round_trips(tmp_path):
    value = "p@ss^wo:rd/1 %#?[]"
    encoded = url_value(tmp_path, value.encode())
    assert encoded == quote(value, safe="")
    assert unquote(encoded) == value


@pytest.mark.parametrize("value", [b"p@ss^word\n", b"p@ss^word\r\n", b"  p@ss^word  \n\n"])
def test_surrounding_whitespace_is_trimmed_as_go2rtc_does(tmp_path, value):
    # go2rtc reads a credential file with strings.TrimSpace
    assert url_value(tmp_path, value) == "p%40ss%5Eword"


def test_the_secret_file_keeps_the_raw_value(tmp_path):
    run(tmp_path, {"CAM_SOURCE_PASSWORD": b"p@ss"}, ["true"])
    assert (tmp_path / "creds" / "CAM_SOURCE_PASSWORD").read_bytes() == b"p@ss"


def test_every_file_gets_its_url_variable(tmp_path):
    secrets = {"A_PASSWORD": b"a@1", "B_PASSWORD": b"b/2"}
    args = ["sh", "-c", 'printf "%s %s" "$A_PASSWORD_URL" "$B_PASSWORD_URL"']
    assert run(tmp_path, secrets, args).stdout == b"a%401 b%2F2"


def test_an_empty_secret_gives_an_empty_url_variable(tmp_path):
    assert url_value(tmp_path, b"") == ""


def test_a_file_name_that_is_no_variable_name_is_skipped(tmp_path):
    result = run(tmp_path, {"not-a-name": b"x@y", "GOOD": b"x@y"}, show("GOOD_URL"))
    assert result.returncode == 0 and result.stdout == b"x%40y"


def test_good_secrets_exec_the_command_with_its_args(tmp_path):
    result = run(tmp_path, {"CAM_SOURCE_PASSWORD": b"x"}, ["printf", "%s\\n", "go2rtc", "-config", "/c.yaml"])
    assert result.returncode == 0
    assert result.stdout.decode().splitlines() == ["go2rtc", "-config", "/c.yaml"]
    # Nothing is logged, a value least of all
    assert result.stderr == b""


def test_no_credentials_directory_just_execs(tmp_path):
    result = run(tmp_path, None, ["printf", "go2rtc"])
    assert result.returncode == 0 and result.stdout == b"go2rtc"


def test_exit_status_of_the_command_is_kept(tmp_path):
    assert run(tmp_path, {}, ["sh", "-c", "exit 7"]).returncode == 7


@pytest.mark.parametrize("char", ['"', "\\", "\t", "\x01", "\x1b", "\x7f", "\r", "\n", "\x00"])
def test_a_character_that_breaks_the_config_is_refused(tmp_path, char):
    value = f"Sekr3t{char}Value".encode()
    result = run(tmp_path, {"FRONT_SOURCE_PASSWORD": value}, ["printf", "started"])
    assert result.returncode != 0
    assert result.stdout == b""
    assert result.stderr.decode() == MESSAGE.format(name="FRONT_SOURCE_PASSWORD")
    # The value never reaches the log
    assert b"Sekr3t" not in result.stderr


# Every Unicode space Go's strings.TrimSpace trims, plus the C1 controls YAML cannot hold: the
# script trims ASCII only, so such a secret would reach go2rtc encoded but trimmed raw
UNICODE_REFUSED = [
    "\u0085",
    "\u00a0",
    "\u1680",
    *(chr(c) for c in range(0x2000, 0x200B)),
    "\u2028",
    "\u2029",
    "\u202f",
    "\u205f",
    "\u3000",
    "\u0080",
    "\u009f",
]


@pytest.mark.parametrize("where", ["lead", "inner", "trail"])
@pytest.mark.parametrize("char", UNICODE_REFUSED, ids=lambda c: f"U+{ord(c):04X}")
def test_a_unicode_space_or_c1_control_is_refused(tmp_path, char, where):
    value = {"lead": f"{char}Sekr3t", "inner": f"Sekr3t{char}Value", "trail": f"Sekr3t{char}\n"}[where]
    result = run(tmp_path, {"FRONT_SOURCE_PASSWORD": value.encode()}, ["printf", "started"])
    assert result.returncode != 0
    assert result.stdout == b""
    assert result.stderr.decode() == MESSAGE.format(name="FRONT_SOURCE_PASSWORD")
    assert b"Sekr3t" not in result.stderr


@pytest.mark.parametrize("char", ["\u00a1", "\u00c2", "\u2010", "\u200b", "\u3001", "\u2080", "Ω"])
def test_neighbours_of_the_refused_sequences_still_pass(tmp_path, char):
    # Same lead bytes as a refused sequence, but not a space: encoded as any UTF-8
    value = f"a{char}b"
    assert url_value(tmp_path, value.encode()) == quote(value, safe="")


def test_the_first_bad_file_is_named(tmp_path):
    secrets = {"A_PASSWORD": b"fine@", "B_PASSWORD": b'bad"one'}
    result = run(tmp_path, secrets, ["true"])
    assert result.returncode != 0
    assert result.stderr.decode() == MESSAGE.format(name="B_PASSWORD")


def test_the_script_is_lf_and_posix_sh():
    data = SCRIPT.read_bytes()
    assert b"\r" not in data
    assert data.startswith(b"#!/bin/sh\n")
