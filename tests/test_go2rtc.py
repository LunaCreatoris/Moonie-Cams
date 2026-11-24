"""The go2rtc build: pinned upstream commit, patches tested in the build, one image tag."""

import re
from pathlib import Path

import app

ROOT = Path(__file__).resolve().parent.parent
GO2RTC = ROOT / "go2rtc"
FULL_COMMIT = "7c05066c90ab6ad1fccb506d5f0408aa31ebe60f"


def dockerfile() -> str:
    return (GO2RTC / "Dockerfile").read_text(encoding="utf-8")


def test_go2rtc_tag_is_checked_against_the_full_commit():
    text = dockerfile()
    assert f"ARG GO2RTC_COMMIT={FULL_COMMIT}\n" in text
    # An exact comparison, not a prefix match
    assert '[ "$(git rev-parse HEAD)" = "$GO2RTC_COMMIT" ]' in text
    assert '"$GO2RTC_COMMIT"*' not in text


def test_patched_packages_are_tested_in_the_build():
    text = dockerfile()
    test_line = next(line for line in text.splitlines() if "go test" in line)
    packages = ("streams", "mjpeg", "webrtc", "onvif")
    for package in [f"./internal/{p}/" for p in packages] + ["./pkg/h264/", "./pkg/onvif/"]:
        assert package in test_line
    # Only the two upstream tests that already fail on plain v1.9.15 are skipped, by exact name
    assert "-skip '^(TestRecursion|TestTempate)$'" in test_line
    # The tests run after every patch is applied and before the binary is built
    assert text.index("git apply") < text.index("go test") < text.index("go build")


def test_no_api_source_patch_is_built():
    patches = sorted(p.name for p in GO2RTC.glob("*.patch"))
    assert "no-api-source.patch" in patches
    patch = (GO2RTC / "no-api-source.patch").read_text(encoding="utf-8")
    assert "func GetOrPatch(" in patch and "ErrPushDisabled" in patch
    # WHEP push (a sendonly offer on src=) and client ICE servers are closed too
    assert "func TestWHEPRefusesPush(" in patch and "func TestWSIgnoresClientICEServers(" in patch


def test_onvif_reboot_is_refused_in_the_patch():
    patch = (GO2RTC / "onvif-profile.patch").read_text(encoding="utf-8")
    assert "-\t\t\tos.Exit(0)" in patch
    assert "func TestSystemRebootIsRefused(" in patch


def test_image_tag_is_the_same_everywhere():
    tag = app.GO2RTC_IMAGE
    assert tag == "moonie-cams-go2rtc:1.9.15-p6"
    assert f'go2rtc_image := "{tag}"' in (ROOT / "justfile").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert set(re.findall(r"moonie-cams-go2rtc:[\w.-]+", readme)) == {tag}


def test_onvif_identity_patch_is_built():
    patches = sorted(p.name for p in GO2RTC.glob("*.patch"))
    # It changes lines onvif-profile.patch also touches, so it must sort (and apply) after it
    assert patches.index("onvif-profile.patch") < patches.index("onvif-vendor-identity.patch")
    patch = (GO2RTC / "onvif-vendor-identity.patch").read_text(encoding="utf-8")
    for env in ("ONVIF_NAME", "ONVIF_MANUFACTURER", "ONVIF_MODEL", "ONVIF_LOCATION"):
        assert env in patch
    assert "func TestScopeValuesAreURLEscaped(" in patch and "func TestDeviceInformationIsXMLEscaped(" in patch
    assert "func TestDeviceServiceAnnouncesTheIdentity(" in patch


def test_image_starts_through_the_secret_check():
    text = dockerfile()
    assert "COPY check-secrets.sh /usr/local/bin/check-secrets.sh\n" in text
    # The upstream image's own entrypoint (tini) and command, with the check in between
    assert 'ENTRYPOINT ["/sbin/tini", "--", "sh", "/usr/local/bin/check-secrets.sh"]\n' in text
    assert 'CMD ["go2rtc", "-config", "/config/go2rtc.yaml"]\n' in text


def test_onvif_check_covers_the_identity_and_the_secret_check():
    justfile = (ROOT / "justfile").read_text(encoding="utf-8")
    recipe = justfile[justfile.index("onvif-check platform") :].split("\n\n")[0]
    identity = ("ONVIF_NAME=cams-check", "ONVIF_MANUFACTURER=Moonie-Cams", "ONVIF_MODEL=Cams")
    identity += ('"ONVIF_LOCATION=Hall 2"',)
    for env in identity:
        assert f"-e {env}" in recipe
    check = (ROOT / "tests" / "onvif" / "check.sh").read_text(encoding="utf-8")
    assert "device GetDeviceInformation" in check and "device GetScopes" in check
    assert "onvif://www.onvif.org/location/Hall%202" in check
    # go2rtc itself starts through the image's entrypoint script
    assert "sh /usr/local/bin/check-secrets.sh go2rtc -config /check/go2rtc.yaml &" in check
    # A secret holding a no-break space is refused by the image's own busybox sh
    assert "printf 'bad\\302\\240value'" in check
    # A camera password holding @ ^ : / and UTF-8 (Zone, Omega, U umlaut) reaches a camera through ${KEY_URL}
    assert "printf 'p@ss^wo:rd/1 %%#?[] Zone \\316\\251 \\303\\234\\n'" in check
    assert 'good: ["rtsp://check:\\${CHECK_SOURCE_PASSWORD_URL}@$ip:8556/cam"]' in check
