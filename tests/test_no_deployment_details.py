"""Tracked files describe the code, never a real deployment.

Two checks:

- A generic one, always on: no private (RFC 1918) IPv4 address in any tracked file. Examples use
  the documentation ranges (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24) instead.
- A private one: the words that would identify a real deployment (hosts, names, rooms, ranges)
  are read from the file named by PRIVATE_WORDS_FILE, kept outside the repository, one word per
  line, `#` starting a comment. The list never lives in the repository, and a hit is reported by
  the word's number in that list and the file:line, never by the word itself. Without the
  variable the check is skipped (run it with `just privacy-check`).

Both checks read each line folded first (NFKC, then every Unicode digit to its ASCII digit), so
full-width or Arabic-Indic digits cannot hide an address or a word.
"""

import ipaddress
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ENV_VAR = "PRIVATE_WORDS_FILE"
IPV4 = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3}){3})(?![\w.]*\d)")
# Split so that this file never matches its own list
PRIVATE_NETWORKS = [
    ipaddress.IPv4Network(net) for net in ("10" + ".0.0.0/8", "172" + ".16.0.0/12", "192" + ".168.0.0/16")
]


def fold(text: str) -> str:
    """NFKC, then every Unicode decimal digit to its ASCII digit: what a reader would see."""
    text = unicodedata.normalize("NFKC", text)
    return "".join(str(unicodedata.digit(char)) if unicodedata.digit(char, None) is not None else char for char in text)


def tracked_files() -> list[Path]:
    # safe.directory: the tool container's user does not own the mounted checkout
    result = subprocess.run(
        ["git", "-c", "safe.directory=*", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"git ls-files failed, the scan needs a git checkout: {result.stderr.decode(errors='replace')}")
    return [ROOT / name for name in result.stdout.decode().split("\0") if name]


def tracked_lines():
    """Yield (relative path, line number, line) for every line of every tracked text file."""
    files = tracked_files()
    assert files, "git ls-files listed no file"
    for path in files:
        if not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue  # binary file
        relative = path.relative_to(ROOT).as_posix()
        for number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            yield relative, number, line


def read_words(path: Path) -> list[tuple[int, str]]:
    """The words of a list file as (line number, case-folded word): blank lines and # comments skipped."""
    words = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        word = line.split("#", 1)[0].strip()
        if word:
            words.append((number, fold(word).casefold()))
    return words


def word_hits(words: list[tuple[int, str]], line: str) -> list[int]:
    """The list line numbers of the words found in a line, case-insensitively."""
    folded = fold(line).casefold()
    return [number for number, word in words if word in folded]


def private_addresses(line: str) -> list[str]:
    found = []
    for match in IPV4.finditer(fold(line)):
        try:
            address = ipaddress.IPv4Address(match.group(1))
        except ValueError:
            continue  # not an address, e.g. a version number or 192.0.2.300 in a validation test
        if any(address in net for net in PRIVATE_NETWORKS):
            found.append(match.group(1))
    return found


def test_tracked_files_hold_no_private_address():
    found = [
        f"{path}:{number}: {address}" for path, number, line in tracked_lines() for address in private_addresses(line)
    ]
    assert not found, "private addresses in tracked files (use 192.0.2.x / 198.51.100.x):\n" + "\n".join(found)


def test_tracked_files_hold_no_private_word():
    name = os.environ.get(ENV_VAR, "")
    if not name:
        pytest.skip(f"{ENV_VAR} is not set: point it at your own word list, kept outside the repository")
    path = Path(name)
    if not path.is_file():
        pytest.fail(f"{ENV_VAR} does not name a readable file")
    words = read_words(path)
    if not words:
        pytest.fail(f"{ENV_VAR} names a list with no word")
    found = [
        f"{file}:{number}: word on line {word} of the list"
        for file, number, line in tracked_lines()
        for word in word_hits(words, line)
    ]
    if found:
        # pytest.fail, not assert: the assertion rewriter would print the words
        pytest.fail("private words in tracked files:\n" + "\n".join(found), pytrace=False)


def test_read_words_skips_comments_and_blank_lines(tmp_path):
    path = tmp_path / "words"
    path.write_text("# a comment\n\n  Alpha  \nbeta # trailing comment\n   # indented comment\nGamma Delta\n", "utf-8")
    assert read_words(path) == [(3, "alpha"), (4, "beta"), (6, "gamma delta")]


def test_word_hits_match_case_insensitively_and_give_the_list_line():
    words = [(3, "alpha"), (4, "beta"), (6, "gamma delta")]
    assert word_hits(words, "nothing here") == []
    assert word_hits(words, "the ALPHA host") == [3]
    assert word_hits(words, "AlphaBeta") == [3, 4]
    assert word_hits(words, "a Gamma Delta room") == [6]


def test_word_hits_see_through_unicode_digits_and_compatibility_forms(tmp_path):
    path = tmp_path / "words"
    path.write_text("HOST\uff14\uff12\nlab\n", "utf-8")
    words = read_words(path)
    assert words == [(1, "host42"), (2, "lab")]
    assert word_hits(words, "the HOST\u0664\u0662 box") == [1]
    assert word_hits(words, "the host\uff14\uff12 box") == [1]
    assert word_hits(words, "\uff2c\uff21\uff22 room") == [2]


def test_fold_maps_unicode_digits_to_ascii():
    assert fold("\u0661\u0669\u0662.0.2.10:\uff12\uff10\uff12\uff10") == "192.0.2.10:2020"
    assert fold("Caf\u00e9 menu") == "Caf\u00e9 menu"


def test_a_hit_never_prints_the_word(tmp_path, monkeypatch):
    words = tmp_path / "words"
    words.write_text("zz-secret-word\n", "utf-8")
    monkeypatch.setenv(ENV_VAR, str(words))
    monkeypatch.setattr(
        sys.modules[__name__],
        "tracked_lines",
        lambda: iter([("README.md", 7, "see ZZ-Secret-Word here")]),
    )
    with pytest.raises(pytest.fail.Exception) as failure:
        test_tracked_files_hold_no_private_word()
    message = str(failure.value)
    assert "README.md:7: word on line 1 of the list" in message
    assert "secret" not in message.casefold()


def test_without_the_variable_the_word_check_skips(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    with pytest.raises(pytest.skip.Exception, match=ENV_VAR):
        test_tracked_files_hold_no_private_word()


def test_a_missing_or_empty_list_fails(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "missing"))
    with pytest.raises(pytest.fail.Exception, match="readable file"):
        test_tracked_files_hold_no_private_word()
    empty = tmp_path / "empty"
    empty.write_text("# only a comment\n", "utf-8")
    monkeypatch.setenv(ENV_VAR, str(empty))
    with pytest.raises(pytest.fail.Exception, match="no word"):
        test_tracked_files_hold_no_private_word()


@pytest.mark.parametrize(
    "line",
    [
        # Each address is split so that this file never matches its own examples
        "source: rtsp://" + "10" + ".1.2.3:554/stream1",
        "gateway " + "172" + ".20.0.1",
        "ip: " + "192" + ".168.1.20",
        # Unicode digits fold to ASCII first: Arabic-Indic, full-width, and both mixed
        "url: http://" + "\u0661\u0660" + ".1.2.3:2020",
        "gateway " + "\uff11\uff17\uff12" + ".20.0.1",
        "ip: " + "\u0661\uff19\u0662" + ".168.1.20",
    ],
)
def test_private_addresses_are_caught(line):
    assert private_addresses(line)


@pytest.mark.parametrize(
    "line",
    [
        "source: rtsp://192.0.2.10:554/stream1",
        "ip: 198.51.100.20, client 203.0.113.9",
        "listen 127.0.0.1 and 0.0.0.0",
        "python:3.13.18-slim, busybox 1.37.0-6",
        "invalid ip 192.0.2.300",
        "multicast 224.0.0.1",
        "image tag 1.9.15-p6, version 10.1.2",
    ],
)
def test_generic_addresses_are_allowed(line):
    assert not private_addresses(line)
