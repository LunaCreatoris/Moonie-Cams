"""The ONVIF proxy against a fake Tapo: every rewrite, the relay, PTZ arbitration and faults."""

import base64
import json
import socket
import threading
import time

import pytest
from lxml import etree

import onvif_proxy
from fake_tapo import ENVELOPE, HOST, LINK_LOCAL, MAC, RESPONSES, FakeTapo, password_digest

COPY_IP = "198.51.100.20"
COPY_MAC = "02:11:22:33:44:55"
PROTECT = "198.51.100.5"
HA = "198.51.100.6"
NS = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "tt": "http://www.onvif.org/ver10/schema",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "wsa5": "http://www.w3.org/2005/08/addressing",
}

# A client request as zeep (Home Assistant) sends it: SOAP 1.2 with a WS-Security digest.
REQUEST = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<soap-env:Envelope xmlns:soap-env="http://www.w3.org/2003/05/soap-envelope">'
    "<soap-env:Header><wsse:Security"
    ' xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">'
    "<wsse:UsernameToken><wsse:Username>cams</wsse:Username>"
    "<wsse:Password>c2VjcmV0ZGlnZXN0</wsse:Password><wsse:Nonce>bm9uY2U=</wsse:Nonce>"
    "<wsu:Created xmlns:wsu='http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd'>"
    "2026-01-01T00:00:00Z</wsu:Created></wsse:UsernameToken></wsse:Security></soap-env:Header>"
    "<soap-env:Body>{}</soap-env:Body></soap-env:Envelope>"
)


def soap(op: str, ns: str = "http://www.onvif.org/ver10/device/wsdl", inner: str = "") -> bytes:
    return REQUEST.format(f'<ns0:{op} xmlns:ns0="{ns}">{inner}</ns0:{op}>').encode()


PTZ_NS = "http://www.onvif.org/ver20/ptz/wsdl"
MEDIA_NS = "http://www.onvif.org/ver10/media/wsdl"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def tapo():
    with FakeTapo() as fake:
        yield fake


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def client(tapo, clock):
    app = onvif_proxy.create_app(
        camera=tapo.url,
        camera_host=HOST,
        camera_port=2020,
        copy_ip=COPY_IP,
        copy_mac=COPY_MAC,
        hostname="cams-c210",
        streams={"/stream1": "c210", "/stream2": "c210_sub"},
        ptz_hold=5,
        clock=clock,
    )
    return app.test_client()


def call(client, body: bytes, source: str = PROTECT, port: int = 2020, path: str = "/onvif/service", **headers):
    headers.setdefault("Content-Type", "application/soap+xml; charset=utf-8")
    return client.post(
        path,
        data=body,
        headers=headers,
        base_url=f"http://{COPY_IP}:{port}" if port != 80 else f"http://{COPY_IP}",
        environ_base={"REMOTE_ADDR": source},
    )


def texts(response, xpath: str) -> list[str]:
    return etree.fromstring(response.data).xpath(xpath, namespaces=NS)


def test_get_capabilities_points_every_xaddr_to_the_copy(client):
    response = call(client, soap("GetCapabilities"), path="/onvif/device_service")
    assert response.status_code == 200
    assert texts(response, "//tt:XAddr/text()") == [
        f"http://{COPY_IP}:2020/onvif/device_service",
        *[f"http://{COPY_IP}:2020/onvif/service"] * 4,
    ]


def test_xaddrs_follow_the_port_the_client_used(client):
    response = call(client, soap("GetCapabilities"), port=80)
    assert texts(response, "//tt:Device/tt:XAddr/text()") == [f"http://{COPY_IP}/onvif/device_service"]


def test_get_services_points_to_the_copy(client):
    response = call(client, soap("GetServices"))
    assert texts(response, "//tds:XAddr/text()") == [
        f"http://{COPY_IP}:2020/onvif/device_service",
        f"http://{COPY_IP}:2020/onvif/service",
        f"http://{COPY_IP}:2020/onvif/service",
    ]


@pytest.mark.parametrize(("profile", "stream"), [("profile_1", "c210"), ("profile_2", "c210_sub")])
def test_stream_uri_points_to_the_restreamer(client, profile, stream):
    body = soap("GetStreamUri", MEDIA_NS, f"<ns0:ProfileToken>{profile}</ns0:ProfileToken>")
    response = call(client, body)
    assert texts(response, "//tt:Uri/text()") == [f"rtsp://{COPY_IP}:554/{stream}"]


def test_snapshot_uri_points_to_the_restreamer(client):
    response = call(client, soap("GetSnapshotUri", MEDIA_NS))
    assert texts(response, "//tt:Uri/text()") == [f"http://{COPY_IP}:1984/api/frame.jpeg?src=c210"]


def test_pull_point_address_points_to_the_copy_and_is_relayed_back(client, tapo):
    events = "http://www.onvif.org/ver10/events/wsdl"
    response = call(client, soap("CreatePullPointSubscription", events))
    address = texts(response, "//wsa5:Address/text()")
    assert address == [f"http://{COPY_IP}:2020/event-0_2020"]
    # The client then long-polls that address; the proxy sends it to the same path on the camera.
    response = call(client, soap("PullMessages", events), path="/event-0_2020")
    assert response.status_code == 200
    assert tapo.requests[-1]["path"].rstrip("?") == "/event-0_2020"


def test_network_interfaces_show_the_copy_identity(client):
    response = call(client, soap("GetNetworkInterfaces"))
    assert texts(response, "//tt:HwAddress/text()") == [COPY_MAC]
    assert texts(response, "//tt:Manual/tt:Address/text()") == [COPY_IP]
    assert HOST.encode() not in response.data
    assert MAC.encode() not in response.data


def test_hostname_is_the_copy_name(client):
    response = call(client, soap("GetHostname"))
    assert texts(response, "//tt:Name/text()") == ["cams-c210"]


def test_network_protocols_are_the_ports_the_copy_serves(client):
    response = call(client, soap("GetNetworkProtocols"))
    protocols = {
        p.findtext("tt:Name", namespaces=NS): (
            p.findtext("tt:Enabled", namespaces=NS),
            [int(x) for x in p.xpath("tt:Port/text()", namespaces=NS)],
        )
        for p in etree.fromstring(response.data).xpath("//tds:NetworkProtocols", namespaces=NS)
    }
    assert protocols == {"HTTP": ("true", [80, 2020]), "HTTPS": ("false", [443]), "RTSP": ("true", [554])}


def test_every_other_occurrence_of_the_camera_address_is_rewritten(client):
    response = call(client, soap("GetSystemLog"))
    log = etree.fromstring(response.data).xpath("//tds:SystemLog", namespaces=NS)[0]
    assert log.get("origin") == COPY_IP
    # host:port becomes the copy's address and the port the client used; a longer address is left alone.
    assert log.findtext("tt:String", namespaces=NS) == f"boot ok on {COPY_IP}:2020, peer 192.0.2.100 kept"


def c14n(body: bytes) -> bytes:
    return etree.tostring(etree.fromstring(body), method="c14n")


def test_device_information_is_relayed_as_the_same_tree(client, tapo):
    body = soap("GetDeviceInformation")
    response = call(client, body, SOAPAction='"http://www.onvif.org/ver10/device/wsdl/GetDeviceInformation"')
    assert b"<tds:Model>Tapo C210</tds:Model>" in response.data
    assert b"<tds:Manufacturer>tp-link</tds:Manufacturer>" in response.data
    sent = tapo.requests[-1]
    # Serialized again from the checked tree: the same document, prefixes and header included.
    assert c14n(sent["body"]) == c14n(body)
    assert b"<soap-env:Header><wsse:Security" in sent["body"]
    assert sent["headers"]["Soapaction"] == '"http://www.onvif.org/ver10/device/wsdl/GetDeviceInformation"'
    assert sent["headers"]["Content-Type"] == "application/soap+xml; charset=utf-8"


def test_untouched_answer_keeps_its_bytes(client):
    response = call(client, soap("GetDeviceInformation"))
    assert response.data == ENVELOPE.format(RESPONSES["GetDeviceInformation"]).encode()


def test_rewritten_answer_keeps_the_camera_prefixes(client):
    response = call(client, soap("GetCapabilities"))
    assert b"<SOAP-ENV:Envelope" in response.data
    assert b"<tt:XAddr>" in response.data


def test_http_digest_is_passed_both_ways(client, tapo):
    tapo.challenge = True
    response = call(client, soap("GetDeviceInformation"))
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == 'Digest realm="tapo", nonce="abc", qop="auth"'
    auth = 'Digest username="cams", realm="tapo", nonce="abc", uri="/onvif/service", response="0123abcd"'
    call(client, soap("GetDeviceInformation"), Authorization=auth)
    assert tapo.requests[-1]["headers"]["Authorization"] == auth


# Every Device-service call that is not a Get: none is relayed, nor nested anywhere in the Body.
REFUSED = [
    "AddIPAddressFilter",
    "AddScopes",
    "CreateCertificate",
    "CreateDot1XConfiguration",
    "CreateStorageConfiguration",
    "CreateUsers",
    "DeleteCertificates",
    "DeleteDot1XConfiguration",
    "DeleteGeoLocation",
    "DeleteStorageConfiguration",
    "DeleteUsers",
    "LoadCACertificates",
    "LoadCertificateWithPrivateKey",
    "LoadCertificates",
    "RemoveIPAddressFilter",
    "RemoveScopes",
    "RestoreSystem",
    "ScanAvailableDot11Networks",
    "SendAuxiliaryCommand",
    "SetAccessPolicy",
    "SetAuthFailureWarningConfiguration",
    "SetCertificatesStatus",
    "SetClientCertificateMode",
    "SetDNS",
    "SetDPAddresses",
    "SetDiscoveryMode",
    "SetDot1XConfiguration",
    "SetDynamicDNS",
    "SetGeoLocation",
    "SetHashingAlgorithm",
    "SetHostname",
    "SetHostnameFromDHCP",
    "SetIPAddressFilter",
    "SetNTP",
    "SetNetworkDefaultGateway",
    "SetNetworkInterfaces",
    "SetNetworkProtocols",
    "SetPasswordComplexityConfiguration",
    "SetPasswordHistoryConfiguration",
    "SetRelayOutputSettings",
    "SetRelayOutputState",
    "SetRemoteDiscoveryMode",
    "SetRemoteUser",
    "SetScopes",
    "SetStorageConfiguration",
    "SetSystemDateAndTime",
    "SetSystemFactoryDefault",
    "SetUser",
    "SetZeroConfiguration",
    "StartFirmwareUpgrade",
    "StartSystemRestore",
    "SystemReboot",
    "UpgradeSystemFirmware",
]


def assert_refused(response, tapo, op: str | None = None):
    assert response.status_code == 500
    assert response.content_type.startswith("application/soap+xml")
    assert texts(response, "//s:Fault/s:Code/s:Value/text()") == ["env:Sender"]
    if op is not None:
        assert op.encode() in response.data
    assert tapo.requests == []


def test_refused_list_is_every_device_call_the_proxy_knows():
    assert set(REFUSED) == onvif_proxy.DEVICE_COMMANDS


@pytest.mark.parametrize("op", REFUSED)
def test_device_calls_that_are_not_reads_are_refused(client, tapo, op):
    assert_refused(call(client, soap(op)), tapo, op)


@pytest.mark.parametrize("op", REFUSED)
def test_device_calls_are_refused_in_any_namespace(client, tapo, op):
    assert_refused(call(client, soap(op, "urn:anything")), tapo, op)


def test_an_unknown_device_call_is_refused(client, tapo):
    # Fail closed: a vendor or future Device call that is not a Get never reaches the camera.
    assert_refused(call(client, soap("FlashVendorConfig")), tapo, "FlashVendorConfig")


def test_device_reads_are_relayed(client, tapo):
    # Any Get on the Device service is relayed, even one the proxy does not know.
    call(client, soap("GetVendorThing"))
    assert len(tapo.requests) == 1


SOAP11 = "http://schemas.xmlsoap.org/soap/envelope/"
DEVICE = "http://www.onvif.org/ver10/device/wsdl"
BILLION_LAUGHS = (
    '<?xml version="1.0"?><!DOCTYPE e [<!ENTITY a "aaaaaaaaaa">'
    '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;"><!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]>'
    f'<e:Envelope xmlns:e="{NS["s"]}"><e:Body><d:GetHostname xmlns:d="{DEVICE}"><d:Name>&c;</d:Name>'
    "</d:GetHostname></e:Body></e:Envelope>"
)

# SetHostname wrapped every way the first guard let through, and the bodies
# it relayed raw (a DOCTYPE, entity expansion).
BYPASSES = {
    "soap 1.1": (
        f'<e:Envelope xmlns:e="{SOAP11}"><e:Body><d:SetHostname xmlns:d="{DEVICE}"><d:Name>x</d:Name>'
        "</d:SetHostname></e:Body></e:Envelope>"
    ),
    "other namespace": soap("SetHostname", "urn:vendor").decode(),
    "no namespace": REQUEST.format("<SetHostname><Name>x</Name></SetHostname>"),
    "second child": REQUEST.format(
        f'<d:GetHostname xmlns:d="{DEVICE}"/><d:SetHostname xmlns:d="{DEVICE}"><d:Name>x</d:Name></d:SetHostname>'
    ),
    "second body": REQUEST.format(f'<d:GetHostname xmlns:d="{DEVICE}"/>').replace(
        "</soap-env:Envelope>",
        f'<soap-env:Body><d:SetHostname xmlns:d="{DEVICE}"/></soap-env:Body></soap-env:Envelope>',
    ),
    "trailing junk": soap("SetHostname").decode() + "<junk/>",
    "trailing text": soap("GetHostname").decode() + "junk",
    "trailing comment": soap("GetHostname").decode() + "<!-- SetHostname -->",
    "undeclared prefix": REQUEST.format("<d:SetHostname><d:Name>x</d:Name></d:SetHostname>"),
    # A DOCTYPE around a harmless read: the camera's own parser would load or expand it.
    "doctype": '<!DOCTYPE e SYSTEM "http://192.0.2.1/x.dtd">' + soap("GetHostname").decode().split("\n", 1)[1],
    "billion laughs": BILLION_LAUGHS,
    "nested in a get": REQUEST.format(
        f'<d:GetHostname xmlns:d="{DEVICE}"><d:SetHostname><d:Name>x</d:Name></d:SetHostname></d:GetHostname>'
    ),
    "action header": soap("GetHostname").decode(),
}


@pytest.mark.parametrize("name", list(BYPASSES))
def test_a_wrapped_or_unsafe_body_never_reaches_the_camera(client, tapo, name):
    headers = {}
    if name == "action header":
        headers["SOAPAction"] = f'"{DEVICE}/SetHostname"'
    assert_refused(call(client, BYPASSES[name].encode(), **headers), tapo)


def envelope(inner: str, ns: str = NS["s"]) -> bytes:
    return f'<e:Envelope xmlns:e="{ns}">{inner}</e:Envelope>'.encode()


@pytest.mark.parametrize(
    "body",
    [
        b"not soap at all",
        b"   ",
        b"<a/>",
        f'<e:Envelope xmlns:e="{NS["s"]}"/>'.encode(),
        f'<e:Envelope xmlns:e="{NS["s"]}"><e:Body/></e:Envelope>'.encode(),
        envelope(f'<e:Body>text<x:GetHostname xmlns:x="{DEVICE}"/></e:Body>'),
        envelope(f'<e:Body><x:GetHostname xmlns:x="{DEVICE}"/></e:Body>', "urn:not-soap"),
        envelope(f'<e:Other/><e:Body><x:GetHostname xmlns:x="{DEVICE}"/></e:Body>'),
        envelope(f'<e:Body><x:GetHostname xmlns:x="{DEVICE}"/></e:Body><e:Header/>'),
        envelope(f'<e:Header/><e:Header/><e:Body><x:GetHostname xmlns:x="{DEVICE}"/></e:Body>'),
        envelope(f'<e:Body><x:GetHostname xmlns:x="{DEVICE}"/></e:Body>tail'),
        envelope(f'<e:Body><?pi SetHostname?><x:GetHostname xmlns:x="{DEVICE}"/></e:Body>'),
        "<e:Envelope xmlns:e='http://www.w3.org/2003/05/soap-envelope'><e:Body>\udcff</e:Body></e:Envelope>".encode(
            "utf-8", "surrogatepass"
        ),
    ],
)
def test_a_body_that_is_not_one_clean_soap_envelope_is_never_relayed(client, tapo, body):
    assert_refused(call(client, body), tapo)


def test_a_request_without_a_body_is_relayed(client, tapo):
    response = client.get("/", base_url=f"http://{COPY_IP}:2020")
    assert response.status_code == 404
    assert response.data == b"not found"
    assert len(tapo.requests) == 1


def test_a_request_over_one_mebibyte_is_refused(client, tapo):
    body = soap("GetDeviceInformation", inner="<!--" + "x" * onvif_proxy.MAX_BODY + "-->")
    response = call(client, body)
    assert response.status_code == 413
    assert tapo.requests == []


def test_a_request_of_one_mebibyte_is_relayed(client, tapo):
    head = soap("GetDeviceInformation", inner="<!---->")
    body = head.replace(b"<!---->", b"<!--" + b"x" * (onvif_proxy.MAX_BODY - len(head)) + b"-->")
    assert len(body) == onvif_proxy.MAX_BODY
    assert call(client, body).status_code == 200


def test_waitress_caps_the_body_like_flask():
    options = onvif_proxy.serve_options({"http_ports": (80, 2020)})
    assert options["max_request_body_size"] == onvif_proxy.MAX_BODY == 1024 * 1024
    assert options["listen"] == "0.0.0.0:80 0.0.0.0:2020"


@pytest.mark.parametrize("host", [f"{COPY_IP}:abc", f"{COPY_IP}:99999", f"{COPY_IP}:0", f"{COPY_IP}:-1", "[::1", "a b"])
def test_a_malformed_host_is_refused_before_the_call_is_relayed(client, tapo, host):
    response = client.post(
        "/onvif/service", data=soap("GetCapabilities"), headers={"Host": host}, environ_base={"REMOTE_ADDR": PROTECT}
    )
    assert_refused(response, tapo)


def test_camera_cookies_are_never_kept_between_calls(client, tapo):
    tapo.cookie = True
    call(client, soap("GetDeviceInformation"), source=PROTECT)
    call(client, soap("GetDeviceInformation"), source=HA)
    assert "Cookie" not in tapo.requests[-1]["headers"]
    assert "Set-Cookie" not in call(client, soap("GetDeviceInformation")).headers


def move(client, source, op="ContinuousMove"):
    return call(client, soap(op, PTZ_NS, "<ns0:ProfileToken>profile_1</ns0:ProfileToken>"), source=source)


def test_ptz_belongs_to_the_first_client_until_it_goes_quiet(client, tapo, clock):
    assert move(client, PROTECT).status_code == 200
    clock.now += 4
    busy = move(client, HA)
    assert busy.status_code == 500
    assert b"PTZ is in use" in busy.data
    assert len([r for r in tapo.requests if b"ContinuousMove" in r["body"]]) == 1
    # Reads are never blocked.
    assert call(client, soap("GetStatus", PTZ_NS), source=HA).status_code == 200
    # Stop is a PTZ command: it keeps the hold for its sender, and is refused to the other client.
    assert move(client, PROTECT, "Stop").status_code == 200
    clock.now += 4
    assert move(client, HA, "Stop").status_code == 500
    # Five seconds after the holder's last command, the other client may take PTZ.
    clock.now += 1.1
    assert move(client, HA).status_code == 200
    assert move(client, PROTECT).status_code == 500


def test_a_refused_command_does_not_take_ptz(client, tapo, clock):
    tapo.challenge = True
    assert move(client, HA).status_code == 401
    tapo.challenge = False
    assert move(client, PROTECT).status_code == 200


def ptz_count(tapo, op="ContinuousMove") -> int:
    return len([r for r in tapo.requests if op.encode() in r["body"]])


def test_a_fault_answered_with_200_does_not_take_ptz(client, tapo):
    fault = (
        "<SOAP-ENV:Fault><SOAP-ENV:Code><SOAP-ENV:Value>SOAP-ENV:Receiver</SOAP-ENV:Value></SOAP-ENV:Code>"
        "<SOAP-ENV:Reason><SOAP-ENV:Text>busy</SOAP-ENV:Text></SOAP-ENV:Reason></SOAP-ENV:Fault>"
    )
    tapo.answers["ContinuousMove"] = (200, ENVELOPE.format(fault), "application/soap+xml")
    assert move(client, HA).status_code == 200
    del tapo.answers["ContinuousMove"]
    assert move(client, PROTECT).status_code == 200


def test_a_non_soap_success_does_not_take_ptz(client, tapo):
    tapo.answers["ContinuousMove"] = (200, "ok", "text/plain")
    assert move(client, HA).status_code == 200
    del tapo.answers["ContinuousMove"]
    assert move(client, PROTECT).status_code == 200


def test_two_clients_moving_at_once_get_one_hold(client, tapo):
    # The busy check and the claim are one step: a second command sent while the first is still
    # with the camera must wait for its answer, then find PTZ taken.
    tapo.gate.clear()
    results = {}

    def send(source):
        results[source] = move(client.application.test_client(), source).status_code

    first = threading.Thread(target=send, args=(PROTECT,))
    first.start()
    while ptz_count(tapo) < 1:
        time.sleep(0.01)
    second = threading.Thread(target=send, args=(HA,))
    second.start()
    time.sleep(0.3)
    tapo.gate.set()
    first.join(10)
    second.join(10)
    assert results == {PROTECT: 200, HA: 500}
    assert ptz_count(tapo) == 1


PTZ_BYPASSES = {
    "soap 1.1": (
        f'<e:Envelope xmlns:e="{SOAP11}"><e:Body><p:ContinuousMove xmlns:p="{PTZ_NS}">'
        "<p:ProfileToken>profile_1</p:ProfileToken></p:ContinuousMove></e:Body></e:Envelope>"
    ),
    "other namespace": soap("ContinuousMove", "urn:vendor", "<ns0:ProfileToken>profile_1</ns0:ProfileToken>").decode(),
    "no namespace": REQUEST.format("<ContinuousMove><ProfileToken>profile_1</ProfileToken></ContinuousMove>"),
    "second child": REQUEST.format(
        f'<p:GetStatus xmlns:p="{PTZ_NS}"/><p:ContinuousMove xmlns:p="{PTZ_NS}"><p:ProfileToken>profile_1'
        "</p:ProfileToken></p:ContinuousMove>"
    ),
}


@pytest.mark.parametrize("name", list(PTZ_BYPASSES))
def test_ptz_arbitration_holds_however_the_command_is_wrapped(client, tapo, name):
    assert move(client, PROTECT).status_code == 200
    response = call(client, PTZ_BYPASSES[name].encode(), source=HA)
    assert response.status_code == 500
    assert ptz_count(tapo) == 1


@pytest.mark.parametrize("hold", ["inf", "nan", "-1", "0", "600.5", "1e9", "five"])
def test_a_bad_ptz_hold_stops_the_proxy(monkeypatch, hold):
    set_env(monkeypatch, PTZ_HOLD=hold)
    with pytest.raises(ValueError, match="PTZ_HOLD"):
        onvif_proxy.config_from_env()


@pytest.mark.parametrize("hold", ["0.5", "600"])
def test_ptz_hold_up_to_ten_minutes_is_accepted(monkeypatch, hold):
    set_env(monkeypatch, PTZ_HOLD=hold)
    assert onvif_proxy.config_from_env()["ptz_hold"] == float(hold)


def test_camera_down_gives_a_clean_fault(clock):
    app = onvif_proxy.create_app(
        camera="http://127.0.0.1:9",
        camera_host=HOST,
        camera_port=2020,
        copy_ip=COPY_IP,
        copy_mac=COPY_MAC,
        hostname="cams-c210",
        streams={"/stream1": "c210"},
        clock=clock,
    )
    response = call(app.test_client(), soap("GetCapabilities"))
    assert response.status_code == 500
    assert response.content_type.startswith("application/soap+xml")
    assert texts(response, "//s:Fault/s:Code/s:Value/text()") == ["env:Receiver"]
    assert b"camera unreachable" in response.data


def test_a_non_soap_answer_gets_the_text_rewrite(client, tapo):
    page = f"<html><body>No page at http://{HOST}:2020/x on {HOST}</body></html>"
    tapo.answers["GetHostname"] = (404, page, "text/html")
    response = call(client, soap("GetHostname"))
    assert response.status_code == 404
    assert response.data == f"<html><body>No page at http://{COPY_IP}:2020/x on {COPY_IP}</body></html>".encode()


def test_an_answer_with_a_doctype_gets_the_text_rewrite(client, tapo):
    body = f'<!DOCTYPE x [<!ENTITY h "{HOST}">]>' + ENVELOPE.format("<tds:GetHostnameResponse/>").split("\n", 1)[1]
    tapo.answers["GetHostname"] = (200, body, "application/soap+xml")
    response = call(client, soap("GetHostname"))
    assert HOST.encode() not in response.data
    assert f'<!ENTITY h "{COPY_IP}">'.encode() in response.data


def test_text_after_an_element_is_rewritten(client, tapo):
    tapo.answers["GetSystemLog"] = (
        200,
        ENVELOPE.format(
            f"<tds:GetSystemLogResponse><tt:String>a</tt:String>seen {HOST} <!-- {HOST} --></tds:GetSystemLogResponse>"
        ),
        "application/soap+xml",
    )
    response = call(client, soap("GetSystemLog"))
    assert HOST.encode() not in response.data
    assert f"seen {COPY_IP} <!-- {COPY_IP} -->".encode() in response.data


def test_stream_uri_with_an_account_points_to_the_restreamer_without_it(client, tapo):
    for path, stream in (("stream1", "c210"), ("stream2", "c210_sub")):
        uri = f"rtsp://cams:secret@{HOST}:554/{path}"
        answer = (
            f"<trt:GetStreamUriResponse><trt:MediaUri><tt:Uri>{uri}</tt:Uri></trt:MediaUri></trt:GetStreamUriResponse>"
        )
        tapo.answers["GetStreamUri"] = (200, ENVELOPE.format(answer), "application/soap+xml")
        response = call(client, soap("GetStreamUri", MEDIA_NS))
        assert texts(response, "//tt:Uri/text()") == [f"rtsp://{COPY_IP}:554/{stream}"]
        assert b"secret" not in response.data


def test_ipv6_link_local_does_not_give_the_camera_mac_away(client):
    response = call(client, soap("GetNetworkInterfaces"))
    assert LINK_LOCAL.encode() not in response.data
    assert texts(response, "//tt:IPv6") == []
    assert texts(response, "//tt:IPv4/tt:Enabled/text()") == ["true"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("tapo", COPY_IP),
        ("at tapo:2020.", f"at {COPY_IP}:2020."),
        ("http://tapo/x and TAPO", f"http://{COPY_IP}/x and {COPY_IP}"),
        ("tapo-backup tapo.lan mytapo tapo_2 x.tapo", "tapo-backup tapo.lan mytapo tapo_2 x.tapo"),
        ("http://tapo.lan/x rtsp://tapo-2/stream1", "http://tapo.lan/x rtsp://tapo-2/stream1"),
    ],
)
def test_a_camera_hostname_is_matched_on_its_boundaries_only(text, expected):
    rewrite = onvif_proxy.Rewriter("tapo", 2020, COPY_IP, COPY_MAC, "cams-c210", {"/stream1": "c210"}, 554, 1984, [80])
    assert rewrite.text(text, "", 2020) == expected


ENV = {
    "ONVIF_CAMERA": "http://192.0.2.10:2020",
    "ONVIF_STREAMS": json.dumps({"/stream1": "c210", "/stream2": "c210_sub"}),
    "COPY_IP": COPY_IP,
    "COPY_MAC": COPY_MAC,
    "COPY_HOSTNAME": "cams-c210",
    "PTZ_HOLD": "7.5",
}


def set_env(monkeypatch, **changes):
    for key, value in {**ENV, **changes}.items():
        monkeypatch.setenv(key, value)


def test_config_comes_from_the_environment(monkeypatch):
    set_env(monkeypatch)
    config = onvif_proxy.config_from_env()
    assert config["camera"] == "http://192.0.2.10:2020"
    assert (config["camera_host"], config["camera_port"]) == ("192.0.2.10", 2020)
    assert config["streams"] == {"/stream1": "c210", "/stream2": "c210_sub"}
    assert config["ptz_hold"] == 7.5
    assert config["copy_mac"] == COPY_MAC
    assert onvif_proxy.create_app(**config).name == "onvif_proxy"


def test_a_bad_camera_url_stops_the_proxy(monkeypatch):
    set_env(monkeypatch, ONVIF_CAMERA="http://cams:pw@192.0.2.10:2020")
    with pytest.raises(ValueError, match="ONVIF_CAMERA must be http://<IPv4>"):
        onvif_proxy.config_from_env()


class Stopped(Exception):
    pass


def watch(results: list[bool], failures: int = 3) -> tuple[list[int], int]:
    """Run watch_go2rtc over scripted check results; return the exit codes and the checks made."""
    checks = iter(results)
    codes: list[int] = []

    def reachable(port):
        assert port == 554
        try:
            return next(checks)
        except StopIteration:
            raise Stopped from None

    def stop(code):
        codes.append(code)

    try:
        onvif_proxy.watch_go2rtc(
            554, interval=0, failures=failures, reachable=reachable, sleep=lambda s: None, stop=stop
        )
    except Stopped:
        pass
    return codes, len(results) - sum(1 for _ in checks)


def test_watchdog_exits_once_go2rtc_left_the_namespace():
    codes, checked = watch([True, True, False, False, False, True])
    assert codes == [1]
    assert checked == 5


def test_watchdog_forgives_a_go2rtc_that_comes_back():
    # go2rtc still starting, or a slow check: fewer misses in a row than the limit never exit.
    codes, checked = watch([False, False, True, False, False, True, True])
    assert codes == []
    assert checked == 7


def test_go2rtc_reachable_needs_a_listener_on_localhost():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        assert onvif_proxy.go2rtc_reachable(port)
    assert not onvif_proxy.go2rtc_reachable(port)


# Comments and CDATA were relayed as they came, so the camera's parser could
# see an operation that lxml did not. The proxy now relays the checked tree, serialized again.
GET_USERS = f'<d:GetUsers xmlns:d="{DEVICE}"/>'
SMUGGLED = {
    "comment": REQUEST.format(f"{GET_USERS}<!--<d:SystemReboot/>-->"),
    "cdata": REQUEST.format(f'<d:GetUsers xmlns:d="{DEVICE}"><![CDATA[<d:SystemReboot/>]]></d:GetUsers>'),
    "comment in the header": REQUEST.format(GET_USERS).replace(
        "<soap-env:Header>", "<soap-env:Header><!--<d:SystemReboot/>-->"
    ),
}


@pytest.mark.parametrize("name", list(SMUGGLED))
def test_the_camera_receives_no_comment_and_no_cdata(client, tapo, name):
    call(client, SMUGGLED[name].encode())
    sent = tapo.requests[-1]["body"]
    assert b"<!--" not in sent
    assert b"CDATA" not in sent
    assert b"<d:SystemReboot" not in sent
    assert etree.fromstring(sent).xpath("//*[local-name()='SystemReboot']") == []
    assert len(etree.fromstring(sent).xpath("//*[local-name()='GetUsers']")) == 1


def test_cdata_reaches_the_camera_as_escaped_text(client, tapo):
    call(client, SMUGGLED["cdata"].encode())
    assert b"&lt;d:SystemReboot/&gt;</d:GetUsers>" in tapo.requests[-1]["body"]


def test_content_length_is_the_relayed_body(client, tapo):
    body = SMUGGLED["comment"].encode()
    call(client, body)
    sent = tapo.requests[-1]
    assert len(sent["body"]) < len(body)
    assert sent["headers"]["Content-Length"] == str(len(sent["body"]))


def signed(op: str, password: str, ns: str = DEVICE) -> bytes:
    """A request with a real WS-Security UsernameToken digest, as zeep builds it."""
    nonce, created = b"0123456789abcdef", "2026-01-01T00:00:00Z"
    body = soap(op, ns).decode()
    body = body.replace("c2VjcmV0ZGlnZXN0", password_digest(nonce, created, password))
    body = body.replace("bm9uY2U=", base64.b64encode(nonce).decode())
    return body.encode()


def test_ws_security_digest_still_validates_after_the_rewrite(client, tapo):
    tapo.wsse = {"cams": "secret"}
    # Comments around the token: the proxy drops them, and the digest covers none of the bytes.
    body = signed("GetDeviceInformation", "secret").replace(b"<wsse:UsernameToken>", b"<!--x--><wsse:UsernameToken>")
    response = call(client, body)
    assert response.status_code == 200
    assert tapo.requests[-1]["body"] != body
    # The check is real: a wrong password is rejected by the fake camera.
    assert call(client, signed("GetDeviceInformation", "wrong")).status_code == 400


def test_the_relayed_charset_is_utf8(client, tapo):
    body = REQUEST.format(f'<d:GetVendorThing xmlns:d="{DEVICE}"><d:Name>caf\u00e9</d:Name></d:GetVendorThing>')
    body = body.replace('encoding="utf-8"', 'encoding="iso-8859-1"').encode("iso-8859-1")
    call(client, body, **{"Content-Type": "application/soap+xml; charset=iso-8859-1"})
    sent = tapo.requests[-1]
    assert sent["headers"]["Content-Type"] == "application/soap+xml; charset=utf-8"
    assert "caf\u00e9".encode() in sent["body"]


# An empty POST carried its command in the SOAPAction header alone.
@pytest.mark.parametrize(
    "headers",
    [
        {"SOAPAction": f'"{DEVICE}/SystemReboot"'},
        {"SOAPAction": f"{DEVICE}/GetDeviceInformation"},
        {"SOAPAction": f'"{PTZ_NS}/ContinuousMove"'},
        {"Content-Type": f'application/soap+xml; charset=utf-8; action="{DEVICE}/SystemReboot"'},
    ],
)
def test_an_empty_body_that_names_an_action_is_refused(client, tapo, headers):
    assert_refused(call(client, b"", **headers), tapo)


# Namespaces were matched exactly, so a trailing slash or a case variant
# took a write past the read-only check.
DEVICE_VARIANTS = [DEVICE + "/", DEVICE.upper(), "HTTP://www.ONVIF.org/ver10/Device/WSDL//"]


@pytest.mark.parametrize("ns", DEVICE_VARIANTS)
def test_a_device_namespace_variant_is_still_the_device_service(client, tapo, ns):
    assert_refused(call(client, soap("SetUserRole", ns)), tapo, "SetUserRole")


@pytest.mark.parametrize("ns", DEVICE_VARIANTS)
def test_a_device_action_variant_is_still_the_device_service(client, tapo, ns):
    assert_refused(call(client, soap("GetHostname"), SOAPAction=f'"{ns.rstrip("/")}/SetUserRole"'), tapo)


@pytest.mark.parametrize("ns", [PTZ_NS + "/", PTZ_NS.upper()])
def test_a_ptz_namespace_variant_still_goes_through_the_arbitration(client, tapo, ns):
    assert move(client, PROTECT).status_code == 200
    response = call(client, soap("ContinuousMove", ns), source=HA)
    assert response.status_code == 500
    assert b"PTZ is in use" in response.data
    assert len(tapo.requests) == 1


@pytest.mark.parametrize("ns", ["HTTP://WWW.ONVIF.ORG/VER10/media/wsdl", "http://www.onvif.org/ver20/ptz/wsdl/"])
def test_an_onvif_namespace_variant_is_still_an_onvif_service(client, tapo, ns):
    call(client, soap("GetProfiles", ns))
    assert len(tapo.requests) == 1


# Writes on Media, Media2, Imaging and DeviceIO changed the one real camera
# (its main stream, image, OSD, outputs) for every client. Only their reads are relayed now.
MEDIA2_NS = "http://www.onvif.org/ver20/media/wsdl"
IMAGING_NS = "http://www.onvif.org/ver20/imaging/wsdl"
DEVICEIO_NS = "http://www.onvif.org/ver10/deviceIO/wsdl"
WRITES = [
    (MEDIA_NS, "SetVideoEncoderConfiguration"),
    (MEDIA_NS, "SetVideoSourceConfiguration"),
    (MEDIA_NS, "CreateOSD"),
    (MEDIA_NS, "SetOSD"),
    (MEDIA_NS, "DeleteOSD"),
    (MEDIA_NS, "CreateProfile"),
    (MEDIA_NS, "AddVideoEncoderConfiguration"),
    (MEDIA_NS, "StartMulticastStreaming"),
    (MEDIA2_NS, "SetVideoEncoderConfiguration"),
    (MEDIA2_NS, "CreateOSD"),
    (MEDIA2_NS, "AddConfiguration"),
    (IMAGING_NS, "SetImagingSettings"),
    (IMAGING_NS, "Move"),
    (IMAGING_NS, "Stop"),
    (DEVICEIO_NS, "SetVideoOutputConfiguration"),
    (DEVICEIO_NS, "SetSerialPortConfiguration"),
    (DEVICEIO_NS.lower() + "/", "SetDigitalInputConfigurations"),
]


@pytest.mark.parametrize(("ns", "op"), WRITES)
def test_writes_on_media_imaging_and_deviceio_are_refused(client, tapo, ns, op):
    assert_refused(call(client, soap(op, ns)), tapo, op)


@pytest.mark.parametrize(("ns", "op"), WRITES)
def test_writes_named_by_soapaction_are_refused(client, tapo, ns, op):
    assert_refused(call(client, soap("GetProfiles", MEDIA_NS), SOAPAction=f'"{ns}/{op}"'), tapo)


@pytest.mark.parametrize(
    ("ns", "op"),
    [
        (MEDIA_NS, "GetProfiles"),
        (MEDIA_NS, "GetVideoEncoderConfigurationOptions"),
        (MEDIA2_NS, "GetProfiles"),
        (IMAGING_NS, "GetImagingSettings"),
        (DEVICEIO_NS, "GetVideoOutputs"),
        ("http://www.onvif.org/ver10/events/wsdl", "SetSynchronizationPoint"),
        ("http://www.onvif.org/ver10/events/wsdl", "Renew"),
    ],
)
def test_reads_and_event_calls_are_still_relayed(client, tapo, ns, op):
    call(client, soap(op, ns))
    assert len(tapo.requests) == 1


# An https URL to the camera pointed at a port the copy does not serve.
def test_https_urls_point_to_the_copy_over_http_on_the_client_port(client, tapo):
    answer = (
        "<tds:GetCapabilitiesResponse><tds:Capabilities>"
        f"<tt:Device><tt:XAddr>https://{HOST}:443/onvif/device_service</tt:XAddr></tt:Device>"
        f"<tt:Media><tt:XAddr>https://{HOST}/onvif/service</tt:XAddr></tt:Media>"
        f"<tt:PTZ><tt:XAddr>HTTPS://admin:pw@{HOST}:8443/onvif/ptz</tt:XAddr></tt:PTZ>"
        "</tds:Capabilities></tds:GetCapabilitiesResponse>"
    )
    tapo.answers["GetCapabilities"] = (200, ENVELOPE.format(answer), "application/soap+xml")
    response = call(client, soap("GetCapabilities"))
    assert texts(response, "//tt:XAddr/text()") == [
        f"http://{COPY_IP}:2020/onvif/device_service",
        f"http://{COPY_IP}:2020/onvif/service",
        f"http://{COPY_IP}:2020/onvif/ptz",
    ]
    assert b"https" not in response.data.lower()
    response = call(client, soap("GetCapabilities"), port=80)
    assert texts(response, "//tt:Device/tt:XAddr/text()") == [f"http://{COPY_IP}/onvif/device_service"]


def test_an_https_url_in_free_text_points_to_the_copy_over_http():
    rewrite = onvif_proxy.Rewriter(HOST, 2020, COPY_IP, COPY_MAC, "cams-c210", {"/stream1": "c210"}, 554, 1984, [80])
    assert rewrite.text(f"see https://{HOST}:443/x", "", 80) == f"see http://{COPY_IP}/x"


# The action headers were split naively, so a value the proxy read as a Get
# could name SystemReboot to a camera that dispatches on its action header.
REBOOT = f"{DEVICE}/SystemReboot"
DEVICE_INFO = f"{DEVICE}/GetDeviceInformation"
ACTION_BYPASSES = {
    "space": {"SOAPAction": f'"{REBOOT}" {DEVICE_INFO}'},
    "comma": {"SOAPAction": f"{REBOOT}, {DEVICE_INFO}"},
    "query": {"SOAPAction": f"{REBOOT}?x=/GetDeviceInformation"},
    "quoted semicolon": {"Content-Type": f'application/soap+xml; action="{DEVICE_INFO};x={REBOOT}"'},
    "fragment": {"SOAPAction": f"{REBOOT}#/GetDeviceInformation"},
    "inner quote": {"SOAPAction": f'"{REBOOT}"/"GetDeviceInformation"'},
    "tab": {"SOAPAction": f"{REBOOT}\t/GetDeviceInformation"},
    "two action parameters": {"Content-Type": f"application/soap+xml; action={DEVICE_INFO}; Action={REBOOT}"},
    "extended action parameter": {"Content-Type": f"application/soap+xml; action*=UTF-8''{DEVICE_INFO}"},
    "extended action continuation": {"Content-Type": f"application/soap+xml; action*0={DEVICE_INFO}"},
    "percent escape": {"SOAPAction": f"{REBOOT}%3Fx=/GetDeviceInformation"},
    "unquoted space in the parameter": {"Content-Type": f"application/soap+xml; action={DEVICE_INFO} {REBOOT}"},
    "unterminated quote": {"Content-Type": f'application/soap+xml; action="{DEVICE_INFO}'},
    "quote in the media type": {"Content-Type": f'application/soap+xml"x; action="{DEVICE_INFO}"'},
    "semicolon quoted in another parameter": {
        "Content-Type": f'application/soap+xml; charset="utf-8;action={REBOOT}"; action="{DEVICE_INFO}"'
    },
    "headers disagree": {
        "SOAPAction": f'"{REBOOT}"',
        "Content-Type": f'application/soap+xml; action="{DEVICE_INFO}"',
    },
    "headers disagree the other way": {
        "SOAPAction": f'"{DEVICE_INFO}"',
        "Content-Type": f'application/soap+xml; action="{DEVICE}/GetHostname"',
    },
    # An action the proxy did not read as the action parameter.
    "action as the media type": {"Content-Type": f"action={REBOOT}; charset=utf-8"},
    "xaction parameter": {"Content-Type": f"application/soap+xml; charset=utf-8; xaction={REBOOT}"},
    "action without a semicolon": {"Content-Type": f"application/soap+xml action={REBOOT}"},
    "action suffix parameter": {"Content-Type": f"application/soap+xml; soap-action={REBOOT}"},
    "action in another parameter value": {"Content-Type": f"application/soap+xml; charset=action{REBOOT}"},
}


@pytest.mark.parametrize("name", list(ACTION_BYPASSES))
def test_an_ambiguous_action_header_is_refused(client, tapo, name):
    assert_refused(call(client, soap("GetDeviceInformation"), **ACTION_BYPASSES[name]), tapo)


def test_two_soapaction_headers_are_refused(client, tapo):
    response = client.post(
        "/onvif/service",
        data=soap("GetDeviceInformation"),
        headers=[("Content-Type", onvif_proxy.SOAP_XML), ("SOAPAction", DEVICE_INFO), ("SOAPAction", REBOOT)],
        base_url=f"http://{COPY_IP}:2020",
        environ_base={"REMOTE_ADDR": PROTECT},
    )
    assert_refused(response, tapo)


@pytest.mark.parametrize(
    "headers",
    [
        {"SOAPAction": f'"{DEVICE_INFO}"'},
        {"SOAPAction": DEVICE_INFO},
        {"SOAPAction": '""'},
        {"Content-Type": f'application/soap+xml; charset=utf-8; action="{DEVICE_INFO}"'},
        {"Content-Type": f"application/soap+xml;action={DEVICE_INFO};charset=utf-8"},
        {"SOAPAction": f'"{DEVICE_INFO}"', "Content-Type": f'application/soap+xml; action="{DEVICE_INFO}/"'},
        {
            "SOAPAction": f'"{DEVICE_INFO}"',
            "Content-Type": f"application/soap+xml; action={DEVICE.upper()}/GetDeviceInformation",
        },
    ],
)
def test_a_clean_action_header_is_relayed(client, tapo, headers):
    assert call(client, soap("GetDeviceInformation"), **headers).status_code == 200
    assert len(tapo.requests) == 1


# The read-only check was a denylist of services, so a write on any other
# namespace (events, recording, analytics, an unknown one) reached the camera. Now a call that is
# not a Get is relayed only when it is a PTZ command or an event call.
EVENTS_NS = "http://www.onvif.org/ver10/events/wsdl"
WSN_NS = "http://docs.oasis-open.org/wsn/b-2"
WSN_ACTIONS = "http://docs.oasis-open.org/wsn/bw-2"
CROSS_WRITES = [
    (EVENTS_NS, "SetVideoEncoderConfiguration"),
    (EVENTS_NS, "SystemReboot"),
    (EVENTS_NS, "VendorWrite"),
    (WSN_NS, "SetHostname"),
    ("http://www.onvif.org/ver10/imaging/wsdl", "SetImagingSettings"),
    ("http://www.onvif.org/ver10/nonexistent/wsdl", "SetOSD"),
    ("http://www.onvif.org/ver10/recording/wsdl", "DeleteRecording"),
    ("http://www.onvif.org/ver20/analytics/wsdl", "DeleteRules"),
    ("http://www.onvif.org/ver10/search/wsdl", "EndSearch"),
    (PTZ_NS, "SetVideoEncoderConfiguration"),
    (PTZ_NS, "VendorMove"),
    (PTZ_NS, "continuousmove"),
    (DEVICE, "getHostname"),
]


@pytest.mark.parametrize(("ns", "op"), CROSS_WRITES)
def test_a_write_outside_ptz_and_events_is_refused(client, tapo, ns, op):
    assert_refused(call(client, soap(op, ns)), tapo, op)


@pytest.mark.parametrize(("ns", "op"), CROSS_WRITES)
def test_a_write_outside_ptz_and_events_named_by_its_action_is_refused(client, tapo, ns, op):
    assert_refused(call(client, soap("GetDeviceInformation"), SOAPAction=f'"{ns}/{op}"'), tapo)
    assert_refused(
        call(client, soap("GetDeviceInformation"), **{"Content-Type": f'application/soap+xml; action="{ns}/{op}"'}),
        tapo,
    )


# Each event call with the action URI that ONVIF's event.wsdl gives it.
EVENT_CALLS = [
    (EVENTS_NS, "CreatePullPointSubscription", f"{EVENTS_NS}/EventPortType/CreatePullPointSubscriptionRequest"),
    (EVENTS_NS, "PullMessages", f"{EVENTS_NS}/PullPointSubscription/PullMessagesRequest"),
    (EVENTS_NS, "SetSynchronizationPoint", f"{EVENTS_NS}/PullPointSubscription/SetSynchronizationPointRequest"),
    (WSN_NS, "Renew", f"{WSN_ACTIONS}/SubscriptionManager/RenewRequest"),
    (WSN_NS, "Unsubscribe", f"{WSN_ACTIONS}/SubscriptionManager/UnsubscribeRequest"),
]


@pytest.mark.parametrize(("ns", "op", "action"), EVENT_CALLS)
def test_event_calls_are_relayed(client, tapo, ns, op, action):
    call(client, soap(op, ns))
    call(client, soap(op, ns), SOAPAction=f'"{action}"')
    call(client, soap(op, ns), **{"Content-Type": f'application/soap+xml; charset=utf-8; action="{action}"'})
    wsa = REQUEST.format(f'<ns0:{op} xmlns:ns0="{ns}"/>').replace(
        "<soap-env:Header>", f'<soap-env:Header><wsa:Action xmlns:wsa="{NS["wsa5"]}">{action}</wsa:Action>'
    )
    call(client, wsa.encode())
    assert len(tapo.requests) == 4


@pytest.mark.parametrize("op", sorted(onvif_proxy.PTZ_COMMANDS))
def test_ptz_commands_are_relayed_through_the_arbitration(client, tapo, op):
    assert call(client, soap(op, PTZ_NS), SOAPAction=f'"{PTZ_NS}/{op}"').status_code != 500
    assert len(tapo.requests) == 1
    busy = call(client, soap(op, PTZ_NS), source=HA)
    if op in ("ContinuousMove", "Stop"):
        # The fake answers these with success, so the first client now holds PTZ.
        assert b"PTZ is in use" in busy.data
        assert len(tapo.requests) == 1


GET_SERVICES = [
    DEVICE,
    MEDIA_NS,
    "http://www.onvif.org/ver20/media/wsdl",
    "http://www.onvif.org/ver20/imaging/wsdl",
    "http://www.onvif.org/ver10/deviceIO/wsdl",
    EVENTS_NS,
    PTZ_NS,
    "http://www.onvif.org/ver20/analytics/wsdl",
    "http://www.onvif.org/ver10/recording/wsdl",
    "http://www.onvif.org/ver10/search/wsdl",
    "http://www.onvif.org/ver10/replay/wsdl",
    "http://www.onvif.org/ver10/nonexistent/wsdl",
    WSN_NS,
]


@pytest.mark.parametrize("ns", GET_SERVICES)
def test_a_get_on_any_service_is_relayed(client, tapo, ns):
    call(client, soap("GetServiceCapabilities", ns))
    call(client, soap("GetServiceCapabilities", ns), SOAPAction=f'"{ns}/GetServiceCapabilities"')
    assert len(tapo.requests) == 2


# The WS-Addressing Action was read from its first text node only, so a child
# element split it and the camera could read the whole text as SystemReboot.
def with_wsa_action(op: str, action_xml: str, ns: str = DEVICE) -> bytes:
    return (
        REQUEST.format(f'<ns0:{op} xmlns:ns0="{ns}"/>')
        .replace(
            "<soap-env:Header>", f'<soap-env:Header><wsa:Action xmlns:wsa="{NS["wsa5"]}">{action_xml}</wsa:Action>'
        )
        .encode()
    )


@pytest.mark.parametrize(
    "action",
    [
        f"{DEVICE}/GetX<x/>/../SystemReboot",
        f"{DEVICE_INFO}<x/>",
        f"<x>{REBOOT}</x>",
        f"<x/>{DEVICE_INFO}",
    ],
)
def test_a_wsa_action_with_a_child_element_is_refused(client, tapo, action):
    assert_refused(call(client, with_wsa_action("GetDeviceInformation", action)), tapo)


def test_a_plain_wsa_action_is_relayed(client, tapo):
    assert call(client, with_wsa_action("GetDeviceInformation", DEVICE_INFO)).status_code == 200
    assert len(tapo.requests) == 1


# Only the Action elements inside the Header were checked, so an Action naming
# SystemReboot inside the Body was relayed, and a camera that finds wsa:Action by a document-wide
# local-name search would dispatch it.
def test_an_action_inside_the_body_is_checked(client, tapo):
    body = REQUEST.format(
        f'<d:GetDeviceInformation xmlns:d="{DEVICE}"><a:Action xmlns:a="{NS["wsa5"]}">{REBOOT}</a:Action>'
        "</d:GetDeviceInformation>"
    ).encode()
    assert_refused(call(client, body), tapo)


# A Header or a Body in another namespace than the envelope's was accepted,
# so a camera that reads any Header element could act on a command the proxy never checked.
@pytest.mark.parametrize(
    "inner",
    [
        f'<x:Header xmlns:x="urn:x"><d:SystemReboot xmlns:d="{DEVICE}"/></x:Header>'
        f'<e:Body><d:GetDeviceInformation xmlns:d="{DEVICE}"/></e:Body>',
        f'<Header><d:SystemReboot xmlns:d="{DEVICE}"/></Header>'
        f'<e:Body><d:GetDeviceInformation xmlns:d="{DEVICE}"/></e:Body>',
        f'<x:Body xmlns:x="urn:x"><d:GetDeviceInformation xmlns:d="{DEVICE}"/></x:Body>',
        f'<x:Header xmlns:x="{SOAP11}"/><e:Body><d:GetDeviceInformation xmlns:d="{DEVICE}"/></e:Body>',
    ],
)
def test_a_header_or_body_outside_the_envelope_namespace_is_refused(client, tapo, inner):
    assert_refused(call(client, envelope(inner)), tapo)


@pytest.mark.parametrize("ns", [NS["s"], SOAP11])
def test_a_header_and_body_in_the_envelope_namespace_are_relayed(client, tapo, ns):
    inner = f'<e:Header/><e:Body><d:GetDeviceInformation xmlns:d="{DEVICE}"/></e:Body>'
    assert call(client, envelope(inner, ns)).status_code == 200
    assert len(tapo.requests) == 1


# WS-BaseNotification Subscribe (push mode) made the real camera POST its
# notifications, with its own identity, to any address the client chose. It is refused; Home
# Assistant falls back to PullPoint, which is relayed.
SUBSCRIBE_ACTION = f"{WSN_ACTIONS}/NotificationProducer/SubscribeRequest"


def test_subscribe_is_refused(client, tapo):
    assert_refused(call(client, soap("Subscribe", WSN_NS)), tapo, "Subscribe")
    assert_refused(call(client, soap("Subscribe", EVENTS_NS)), tapo, "Subscribe")
    assert_refused(call(client, soap("GetEventProperties", EVENTS_NS), SOAPAction=f'"{SUBSCRIBE_ACTION}"'), tapo)
    content_type = {"Content-Type": f'application/soap+xml; action="{SUBSCRIBE_ACTION}"'}
    assert_refused(call(client, soap("GetEventProperties", EVENTS_NS), **content_type), tapo)
    assert_refused(call(client, with_wsa_action("GetEventProperties", SUBSCRIBE_ACTION, EVENTS_NS)), tapo)


def test_subscribe_nested_in_a_read_is_refused(client, tapo):
    nested = f'<w:Subscribe xmlns:w="{WSN_NS}"><w:ConsumerReference/></w:Subscribe>'
    assert_refused(call(client, soap("GetEventProperties", EVENTS_NS, nested)), tapo, "Subscribe")


def test_subscribe_is_not_an_event_call():
    assert "Subscribe" not in onvif_proxy.EVENT_COMMANDS
    assert {"Renew", "Unsubscribe"} <= onvif_proxy.EVENT_COMMANDS


def test_the_pull_point_flow_is_relayed(client, tapo):
    flow = [
        (EVENTS_NS, "CreatePullPointSubscription", f"{EVENTS_NS}/EventPortType/CreatePullPointSubscriptionRequest"),
        (EVENTS_NS, "PullMessages", f"{EVENTS_NS}/PullPointSubscription/PullMessagesRequest"),
        (WSN_NS, "Renew", f"{WSN_ACTIONS}/SubscriptionManager/RenewRequest"),
        (EVENTS_NS, "SetSynchronizationPoint", f"{EVENTS_NS}/PullPointSubscription/SetSynchronizationPointRequest"),
        (WSN_NS, "Unsubscribe", f"{WSN_ACTIONS}/SubscriptionManager/UnsubscribeRequest"),
    ]
    for ns, op, action in flow:
        response = call(client, with_wsa_action(op, action, ns), SOAPAction=f'"{action}"')
        assert b"env:Sender" not in response.data, op
    assert [op.encode() in r["body"] for (_, op, _), r in zip(flow, tapo.requests, strict=True)] == [True] * 5


# The relayed body carried an XML declaration that real clients may not send.
def test_the_relayed_body_has_no_xml_declaration(client, tapo):
    body = REQUEST.format(f'<d:GetVendorThing xmlns:d="{DEVICE}"><d:Name>caf\u00e9</d:Name></d:GetVendorThing>')
    call(client, body.encode())
    sent = tapo.requests[-1]["body"]
    assert sent.startswith(b"<soap-env:Envelope")
    assert b"<?xml" not in sent
    assert "caf\u00e9".encode() in sent
