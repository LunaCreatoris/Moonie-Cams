"""onvif_url, the real camera's ONVIF address: app.py and the proxy accept exactly the same ones."""

import pytest

import app
import onvif_proxy

GOOD = [
    ("http://192.0.2.10:2020", ("192.0.2.10", 2020)),
    ("http://192.0.2.10", ("192.0.2.10", 80)),
    ("http://192.0.2.10:2020/", ("192.0.2.10", 2020)),
    ("http://192.0.2.10:1", ("192.0.2.10", 1)),
    ("http://192.0.2.10:65535", ("192.0.2.10", 65535)),
]

BAD = [
    "192.0.2.10:2020",
    "https://192.0.2.10:2020",
    "HTTP://192.0.2.10:2020",
    "rtsp://192.0.2.10:554",
    "http://tapo.lan:2020",
    "http://cams:secret@192.0.2.10:2020",
    "http://@192.0.2.10:2020",
    "http://192.0.2.10:2020/onvif/device_service",
    "http://192.0.2.10:2020?x=1",
    "http://192.0.2.10:2020#top",
    "http://192.0.2.10:0",
    "http://192.0.2.10:65536",
    "http://192.0.2.10:99999999",
    "http://192.0.2.10:",
    "http://192.0.2.10:02020",
    "http://192.0.2.10:-1",
    "http://192.0.2.256:2020",
    "http://0192.0.2.10:2020",
    "http://192.0.2:2020",
    "http://[::1]:2020",
    "http://0.0.0.0:2020",
    "http://224.0.0.1:2020",
    "http://255.255.255.255:2020",
    "http://192.0.2.10:2020\n",
    " http://192.0.2.10:2020",
    "http://192.0.2.10:2020 ",
    "http://١٩٢.0.2.10:2020",
    "http://192.0.2.10:２０２０",
    "",
]


@pytest.mark.parametrize(("url", "address"), GOOD)
def test_good_camera_urls_are_accepted_by_both(url, address):
    assert onvif_proxy.camera_address(url) == address
    app.check_onvif_url(url, "camera cam")


@pytest.mark.parametrize("url", BAD)
def test_bad_camera_urls_are_refused_by_both(url):
    with pytest.raises(ValueError, match=r"must be http://<IPv4>\[:port\]"):
        onvif_proxy.camera_address(url)
    with pytest.raises(app.ConfigError, match=r"camera cam: onvif_url must be http://<IPv4>\[:port\]"):
        app.check_onvif_url(url, "camera cam")


def test_the_refusal_never_echoes_the_url():
    # A URL with an account would print its password.
    with pytest.raises(app.ConfigError) as err:
        app.check_onvif_url("http://cams:secret@192.0.2.10:2020", "camera cam")
    assert "secret" not in str(err.value)
