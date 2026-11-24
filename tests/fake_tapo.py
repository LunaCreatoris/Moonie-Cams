"""A tiny ONVIF server that answers like a Tapo C210 on port 2020, for the proxy tests.

The responses follow the shape a Tapo returns (SOAP 1.2, its namespace prefixes, its
service paths): every address in them points to the camera itself, which is what the
proxy has to rewrite. Every request is recorded byte for byte.
"""

import base64
import hashlib
import re
import threading

from lxml import etree
from werkzeug.serving import make_server
from werkzeug.wrappers import Request, Response

HOST = "192.0.2.10"
BASE = f"http://{HOST}:2020"
MAC = "a8:42:a1:00:00:01"
LINK_LOCAL = "fe80::aa42:a1ff:fe00:1"

ENVELOPE = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope"'
    ' xmlns:tds="http://www.onvif.org/ver10/device/wsdl" xmlns:trt="http://www.onvif.org/ver10/media/wsdl"'
    ' xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl" xmlns:tev="http://www.onvif.org/ver10/events/wsdl"'
    ' xmlns:wsa5="http://www.w3.org/2005/08/addressing" xmlns:tt="http://www.onvif.org/ver10/schema">'
    "<SOAP-ENV:Body>{}</SOAP-ENV:Body></SOAP-ENV:Envelope>"
)

RESPONSES = {
    "GetCapabilities": (
        "<tds:GetCapabilitiesResponse><tds:Capabilities>"
        f"<tt:Device><tt:XAddr>{BASE}/onvif/device_service</tt:XAddr></tt:Device>"
        f"<tt:Events><tt:XAddr>{BASE}/onvif/service</tt:XAddr>"
        "<tt:WSPullPointSupport>true</tt:WSPullPointSupport></tt:Events>"
        f"<tt:Imaging><tt:XAddr>{BASE}/onvif/service</tt:XAddr></tt:Imaging>"
        f"<tt:Media><tt:XAddr>{BASE}/onvif/service</tt:XAddr></tt:Media>"
        f"<tt:PTZ><tt:XAddr>{BASE}/onvif/service</tt:XAddr></tt:PTZ>"
        "</tds:Capabilities></tds:GetCapabilitiesResponse>"
    ),
    "GetServices": (
        "<tds:GetServicesResponse>"
        "<tds:Service><tds:Namespace>http://www.onvif.org/ver10/device/wsdl</tds:Namespace>"
        f"<tds:XAddr>{BASE}/onvif/device_service</tds:XAddr></tds:Service>"
        "<tds:Service><tds:Namespace>http://www.onvif.org/ver10/media/wsdl</tds:Namespace>"
        f"<tds:XAddr>{BASE}/onvif/service</tds:XAddr></tds:Service>"
        "<tds:Service><tds:Namespace>http://www.onvif.org/ver20/ptz/wsdl</tds:Namespace>"
        f"<tds:XAddr>{BASE}/onvif/service</tds:XAddr></tds:Service>"
        "</tds:GetServicesResponse>"
    ),
    "GetDeviceInformation": (
        "<tds:GetDeviceInformationResponse><tds:Manufacturer>tp-link</tds:Manufacturer>"
        "<tds:Model>Tapo C210</tds:Model><tds:FirmwareVersion>1.3.11</tds:FirmwareVersion>"
        "<tds:SerialNumber>12345678</tds:SerialNumber><tds:HardwareId>1.0</tds:HardwareId>"
        "</tds:GetDeviceInformationResponse>"
    ),
    "SetHostname": "<tds:SetHostnameResponse/>",
    "GetSnapshotUri": (
        f"<trt:GetSnapshotUriResponse><trt:MediaUri><tt:Uri>{BASE}/onvif/snapshot</tt:Uri>"
        "<tt:InvalidAfterConnect>false</tt:InvalidAfterConnect></trt:MediaUri></trt:GetSnapshotUriResponse>"
    ),
    "CreatePullPointSubscription": (
        "<tev:CreatePullPointSubscriptionResponse><tev:SubscriptionReference>"
        f"<wsa5:Address>{BASE}/event-0_2020</wsa5:Address></tev:SubscriptionReference>"
        "<wsnt:CurrentTime xmlns:wsnt='http://docs.oasis-open.org/wsn/b-2'>2026-01-01T00:00:00Z</wsnt:CurrentTime>"
        "</tev:CreatePullPointSubscriptionResponse>"
    ),
    "PullMessages": (
        "<tev:PullMessagesResponse><tev:CurrentTime>2026-01-01T00:00:10Z</tev:CurrentTime>"
        "<tev:TerminationTime>2026-01-01T00:01:00Z</tev:TerminationTime></tev:PullMessagesResponse>"
    ),
    "GetNetworkInterfaces": (
        '<tds:GetNetworkInterfacesResponse><tds:NetworkInterfaces token="eth0">'
        "<tt:Enabled>true</tt:Enabled><tt:Info><tt:Name>eth0</tt:Name>"
        f"<tt:HwAddress>{MAC}</tt:HwAddress><tt:MTU>1500</tt:MTU></tt:Info>"
        "<tt:IPv4><tt:Enabled>true</tt:Enabled><tt:Config>"
        f"<tt:Manual><tt:Address>{HOST}</tt:Address><tt:PrefixLength>24</tt:PrefixLength></tt:Manual>"
        "<tt:DHCP>false</tt:DHCP></tt:Config></tt:IPv4>"
        # The EUI-64 link-local address a Tapo derives from its MAC: it would give the camera away.
        "<tt:IPv6><tt:Enabled>true</tt:Enabled><tt:Config><tt:AcceptRouterAdvert>false</tt:AcceptRouterAdvert>"
        f"<tt:LinkLocal><tt:Address>{LINK_LOCAL}</tt:Address><tt:PrefixLength>64</tt:PrefixLength></tt:LinkLocal>"
        "<tt:DHCP>Off</tt:DHCP></tt:Config></tt:IPv6>"
        "</tds:NetworkInterfaces></tds:GetNetworkInterfacesResponse>"
    ),
    "GetHostname": (
        "<tds:GetHostnameResponse><tds:HostnameInformation><tt:FromDHCP>false</tt:FromDHCP>"
        "<tt:Name>C210</tt:Name></tds:HostnameInformation></tds:GetHostnameResponse>"
    ),
    "GetNetworkProtocols": (
        "<tds:GetNetworkProtocolsResponse>"
        "<tds:NetworkProtocols><tt:Name>HTTP</tt:Name><tt:Enabled>true</tt:Enabled><tt:Port>2020</tt:Port>"
        "</tds:NetworkProtocols>"
        "<tds:NetworkProtocols><tt:Name>HTTPS</tt:Name><tt:Enabled>true</tt:Enabled><tt:Port>443</tt:Port>"
        "</tds:NetworkProtocols>"
        "<tds:NetworkProtocols><tt:Name>RTSP</tt:Name><tt:Enabled>true</tt:Enabled><tt:Port>554</tt:Port>"
        "</tds:NetworkProtocols>"
        "</tds:GetNetworkProtocolsResponse>"
    ),
    # A vendor call carrying the camera address in free text and in an attribute.
    "GetSystemLog": (
        f'<tds:GetSystemLogResponse><tds:SystemLog origin="{HOST}"><tt:String>boot ok on {HOST}:2020, '
        f"peer 192.0.2.100 kept</tt:String></tds:SystemLog></tds:GetSystemLogResponse>"
    ),
    "GetStatus": "<tptz:GetStatusResponse><tptz:PTZStatus/></tptz:GetStatusResponse>",
    "ContinuousMove": "<tptz:ContinuousMoveResponse/>",
    "Stop": "<tptz:StopResponse/>",
}


WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
WSU = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"


def password_digest(nonce: bytes, created: str, password: str) -> str:
    """The WS-Security UsernameToken PasswordDigest: Base64(SHA-1(nonce + created + password))."""
    return base64.b64encode(hashlib.sha1(nonce + created.encode() + password.encode()).digest()).decode()


def wsse_valid(body: bytes, users: dict[str, str]) -> bool:
    """True when the request's UsernameToken digest matches a known account, as a Tapo checks it."""
    try:
        root = etree.fromstring(body)
    except etree.XMLSyntaxError:
        return False
    token = root.find(f".//{{{WSSE}}}UsernameToken")
    if token is None:
        return False
    username = token.findtext(f"{{{WSSE}}}Username")
    digest = token.findtext(f"{{{WSSE}}}Password")
    nonce = token.findtext(f"{{{WSSE}}}Nonce")
    created = token.findtext(f"{{{WSU}}}Created")
    if None in (username, digest, nonce, created) or username not in users:
        return False
    return digest == password_digest(base64.b64decode(nonce), created, users[username])


def stream_uri(body: bytes) -> str:
    # profile_1 is the main stream, profile_2 the small one, as on a Tapo.
    path = "stream2" if b"profile_2" in body else "stream1"
    return (
        f"<trt:GetStreamUriResponse><trt:MediaUri><tt:Uri>rtsp://{HOST}:554/{path}</tt:Uri>"
        "<tt:InvalidAfterConnect>false</tt:InvalidAfterConnect></trt:MediaUri></trt:GetStreamUriResponse>"
    )


class FakeTapo:
    def __init__(self):
        self.requests: list[dict] = []
        self.challenge = False  # answer 401 with an HTTP digest challenge
        self.cookie = False  # set a cookie on every answer, as a session-keeping camera would
        # Accounts whose WS-Security digest every request must carry (None: no check)
        self.wsse: dict[str, str] | None = None
        # Answers that replace the usual ones: operation name (or "" for any other) -> (status, body, content type)
        self.answers: dict[str, tuple[int, str, str]] = {}
        # Cleared to hold every answer back, as a slow camera would, until it is set again
        self.gate = threading.Event()
        self.gate.set()
        self._server = make_server("127.0.0.1", 0, self._app, threaded=True)
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(target=self._server.serve_forever, args=(0.01,), daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()

    @Request.application
    def _app(self, request: Request) -> Response:
        body = request.get_data()
        self.requests.append({"path": request.full_path, "headers": dict(request.headers), "body": body})
        self.gate.wait(10)
        xml = "application/soap+xml; charset=utf-8"
        if self.wsse is not None and not wsse_valid(body, self.wsse):
            return Response("not authorized", 400, content_type="text/plain")
        if self.challenge:
            return Response(
                "", 401, headers={"WWW-Authenticate": 'Digest realm="tapo", nonce="abc", qop="auth"'}, content_type=xml
            )
        match = re.search(rb"<(?:[\w-]+:)?Body[^>]*>\s*<(?:[\w-]+:)?(\w+)", body)
        op = match.group(1).decode() if match else ""
        if op in self.answers or ("" in self.answers and op not in RESPONSES):
            status, text, content_type = self.answers.get(op) or self.answers[""]
            answer = Response(text, status, content_type=content_type)
        elif op == "GetStreamUri":
            answer = Response(ENVELOPE.format(stream_uri(body)), content_type=xml)
        elif op in RESPONSES:
            answer = Response(ENVELOPE.format(RESPONSES[op]), content_type=xml)
        else:
            answer = Response("not found", 404)
        if self.cookie:
            answer.set_cookie("session", "tapo-session")
        return answer
