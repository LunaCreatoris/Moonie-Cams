# Copyright (C) 2026 LunaCreatoris
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ONVIF proxy: presents itself as the camera and relays every ONVIF call to the real one.

Run next to a go2rtc copy (same network namespace, so the copy keeps one IP). A request is relayed
as the tree the proxy checked, serialized again: comments are dropped and CDATA becomes escaped
text, so the camera's parser cannot see an operation the proxy did not. Auth is kept (WS-Security
in the SOAP Header, whose digest covers no body bytes; HTTP digest in the headers).
Answers are rewritten only where they name the camera:
- RTSP URIs point to the go2rtc restreamer, which holds the camera's single session per stream;
- snapshot URIs point to go2rtc's frame.jpeg;
- every other occurrence of the camera address (XAddrs, PullPoint addresses, free text)
  becomes the copy's address, and its ONVIF port the port the client used; an https URL to the
  camera becomes an http one on that port (the copy serves no HTTPS);
- GetNetworkInterfaces, GetHostname and GetNetworkProtocols give the copy's own identity
  (no IPv6: a link-local address carries the camera's MAC);
- an answer that is not SOAP (an HTML error page) gets the same text rewrite.

Requests fail closed (see inspect): a body that is not one clean SOAP envelope never reaches the
camera, nor does an ambiguous action header, and a call that is not a read (Get...) is relayed only
when it is a PTZ command or an event call. PTZ goes to the first client that sends a command, until
it has been quiet for PTZ_HOLD seconds.

Each request writes one line on stdout (see create_app and README, "Proxy log"): UTC time, client
IP, path, the Body's operations (a PTZ command's followed by a short summary of what it asks for,
see ptz_summary), the decision, the camera's status and the duration. Never a body, a header or a
query: they carry accounts, digests and nonces. PROXY_LOG picks all, writes (the default: no plain
reads, no repeated event polls) or off.

Docker joins the go2rtc container's network namespace only when this container starts. If go2rtc
restarts after a crash, it gets a new namespace and this process is left alone in the old one
(loopback only). watch_go2rtc notices that go2rtc is no longer on localhost and exits non-zero,
so that the restart policy starts this container again, into go2rtc's current namespace.

Configured from the environment (see config_from_env); app.py writes it in compose.yml.
"""

import datetime
import ipaddress
import json
import math
import os
import re
import socket
import sys
import threading
import time
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

import requests
from flask import Flask, Response, request
from lxml import etree
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.http import parse_options_header

SOAP_NS = "http://www.w3.org/2003/05/soap-envelope"
SOAP11_NS = "http://schemas.xmlsoap.org/soap/envelope/"
PTZ_NS = "http://www.onvif.org/ver20/ptz/wsdl"
EVENTS_NS = "http://www.onvif.org/ver10/events/wsdl"
SOAP_XML = "application/soap+xml; charset=utf-8"


def ns_key(namespace: str | None) -> str:
    """A namespace as the proxy compares it: case and trailing slashes do not count.

    A camera's parser may be lenient with either, so every check is made on this form.
    """
    return (namespace or "").strip().casefold().rstrip("/")


# Namespaces an operation (a child of Body) may be in: the ONVIF services, and WS-BaseNotification
# for the PullPoint subscription's Renew and Unsubscribe. Anything else is refused.
SERVICE_NS_PREFIXES = ("http://www.onvif.org/ver10/", "http://www.onvif.org/ver20/")
SERVICE_NS = {ns_key("http://docs.oasis-open.org/wsn/b-2")}

# Every Device-service operation that is not a Get (ONVIF devicemgmt.wsdl): each one changes the
# real camera (network, users, certificates, clock, firmware, relays, storage) or takes it down.
# Every call that is not a Get is refused anyway unless it is a PTZ or an event call (see
# check_operation); this list also refuses these names nested anywhere in the Body, for a camera
# whose parser would act on an element below the operation.
DEVICE_COMMANDS = frozenset(
    {
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
    }
)
DEVICE_COMMANDS_FOLDED = {name.casefold() for name in DEVICE_COMMANDS}

# WS-BaseNotification Subscribe (push mode) makes the real camera POST its notifications, carrying
# its real identity, to an address the client chooses, and so open connections to any host. It is
# refused like a Device command, nested anywhere included; Home Assistant falls back to PullPoint.
REFUSED_ANYWHERE = DEVICE_COMMANDS_FOLDED | {"subscribe"}

# Calls that are not a Get and are still relayed. There is one real camera behind every copy and
# client, so every other write (Device, Media, Media2, Imaging, DeviceIO, Recording, Analytics, any
# vendor or unknown service) is refused: one client's SetVideoEncoderConfiguration would change the
# main stream for all of them. Protect and Home Assistant only read those services to adopt and
# stream a camera; Home Assistant's reboot, set-time, IR lamp and autofocus controls are operator
# actions on the real camera, not on its copy.
#
# PTZ (ver20 ptz.wsdl) moves the camera and changes its presets: relayed through the arbitration.
# A PTZ name nested anywhere in the Body also sends the request through it.
PTZ_COMMANDS = frozenset(
    {
        "AbsoluteMove",
        "ContinuousMove",
        "CreatePresetTour",
        "GeoMove",
        "GotoHomePosition",
        "GotoPreset",
        "ModifyPresetTour",
        "MoveAndStartTracking",
        "OperatePresetTour",
        "RelativeMove",
        "RemovePreset",
        "RemovePresetTour",
        "SetConfiguration",
        "SetHomePosition",
        "SetPreset",
        "Stop",
    }
)

# PullPoint events (ver10 event.wsdl, and WS-BaseNotification for Renew and Unsubscribe, which
# PullPoint subscriptions use too): Protect and Home Assistant subscribe to motion events this way.
# None of them changes the camera. Subscribe (push mode) is not one of them: see REFUSED_ANYWHERE.
EVENT_COMMANDS = frozenset(
    {
        "CreatePullPointSubscription",
        "PullMessages",
        "Renew",
        "SetSynchronizationPoint",
        "Unsubscribe",
    }
)

# Where each relayed write may live. A namespace matches itself and anything below it, since
# action URIs carry extra segments (.../events/wsdl/PullPointSubscription/PullMessagesRequest).
# WS-BaseNotification puts its elements in b-2 and its action URIs under bw-2.
ALLOWED_WRITES = (
    ((ns_key(PTZ_NS),), PTZ_COMMANDS),
    (
        (
            ns_key(EVENTS_NS),
            ns_key("http://docs.oasis-open.org/wsn/b-2"),
            ns_key("http://docs.oasis-open.org/wsn/bw-2"),
        ),
        EVENT_COMMANDS,
    ),
)

# Headers relayed as they are, in each direction; requests sets the transport ones.
REQUEST_HEADERS = ("Authorization", "Content-Type", "SOAPAction")
RESPONSE_HEADERS = ("Content-Type", "WWW-Authenticate")

# Longer than any PullMessages long poll a client asks for (usually up to a minute).
TIMEOUT = (5, 120)

# No ONVIF request comes near this; Flask and waitress both refuse a larger body.
MAX_BODY = 1024 * 1024

# Seconds a PTZ command waits for another one still with the camera before it is told PTZ is busy.
PTZ_WAIT = 10
MAX_PTZ_HOLD = 600.0

# Seconds between two checks that go2rtc is on localhost, and failed checks in a row before exiting:
# long enough for go2rtc to come up, short enough to bring ONVIF back soon after a crash.
WATCH_INTERVAL = 5.0
WATCH_FAILURES = 3

# Entities are never expanded, nothing is fetched, and no DTD is loaded; inspect refuses any DTD.
# CDATA sections become plain text (strip_cdata, also lxml's default), written back escaped.
PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False, strip_cdata=True)
# The same, with comments dropped: the request tree that is checked, then relayed. Processing
# instructions are kept so that inspect can refuse them.
REQUEST_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    load_dtd=False,
    huge_tree=False,
    strip_cdata=True,
    remove_comments=True,
    remove_pis=False,
)

BROADCAST = ipaddress.IPv4Address("255.255.255.255")
CAMERA_URL_RE = re.compile(r"http://([0-9]{1,3}(?:\.[0-9]{1,3}){3})(?::([1-9][0-9]{0,4}))?/?", re.ASCII)


def camera_address(url: str) -> tuple[str, int]:
    """The (IPv4, port) of an http://<IPv4>[:port] camera URL; ValueError for anything else.

    app.py checks onvif_url the same way (tests/test_onvif_url.py holds both to one table).
    """
    match = CAMERA_URL_RE.fullmatch(url) if isinstance(url, str) else None
    if match:
        try:
            host = ipaddress.IPv4Address(match.group(1))
        except ValueError:
            host = None
        port = int(match.group(2) or 80)
        # No camera answers on 0.0.0.0, a multicast group or the broadcast address.
        if host is not None and not (host.is_unspecified or host.is_multicast or host == BROADCAST) and port <= 65535:
            return str(host), port
    raise ValueError(
        "the camera URL must be http://<IPv4>[:port], port 1-65535, with no account, path, query or fragment"
    )


def fault(code: str, reason: str) -> Response:
    """A SOAP 1.2 fault; HTTP 500 as the SOAP 1.2 HTTP binding asks for."""
    envelope = etree.Element(f"{{{SOAP_NS}}}Envelope", nsmap={"env": SOAP_NS})
    fault_el = etree.SubElement(etree.SubElement(envelope, f"{{{SOAP_NS}}}Body"), f"{{{SOAP_NS}}}Fault")
    etree.SubElement(etree.SubElement(fault_el, f"{{{SOAP_NS}}}Code"), f"{{{SOAP_NS}}}Value").text = f"env:{code}"
    text = etree.SubElement(etree.SubElement(fault_el, f"{{{SOAP_NS}}}Reason"), f"{{{SOAP_NS}}}Text")
    text.set("{http://www.w3.org/XML/1998/namespace}lang", "en")
    text.text = reason
    return Response(etree.tostring(envelope, xml_declaration=True, encoding="UTF-8"), 500, content_type=SOAP_XML)


def parse(body: bytes, parser=PARSER):
    """The root element of a well-formed XML document, or None."""
    try:
        return etree.fromstring(body, parser)
    except (etree.XMLSyntaxError, ValueError):
        return None


def has_dtd(root) -> bool:
    info = root.getroottree().docinfo
    return bool(info.doctype) or info.internalDTD is not None or info.externalDTD is not None


def localname(el) -> str:
    return etree.QName(el).localname


def body_of(root):
    """The first Body of a SOAP envelope, any namespace, or None."""
    return next((el for el in root if isinstance(el.tag, str) and localname(el) == "Body"), None)


def answer_operation(root) -> str:
    """The local name of the first element in an answer's Body, or "" when there is none."""
    body = body_of(root) if root is not None else None
    first = next((el for el in body if isinstance(el.tag, str)), None) if body is not None else None
    return localname(first) if first is not None else ""


class Refused(Exception):
    """A request that must not reach the camera; its message is the fault reason.

    `code` is the short reason the log gives (refused:<code>), and `ops` the Body's operations
    once the envelope was checked far enough to name them, with their PTZ `summary`.
    """

    def __init__(self, message: str, code: str, ops: tuple[str, ...] = (), summary: str = ""):
        super().__init__(message)
        self.code, self.ops, self.summary = code, ops, summary


@dataclass(frozen=True)
class Call:
    ops: tuple[str, ...]  # local names of the Body's children, in order
    ptz: bool  # a PTZ command, which goes through the arbitration
    body: bytes = b""  # what is relayed: the checked tree, serialized again (empty when there is none)
    on_ptz: bool = False  # an operation of the PTZ service, a read included (logged at level writes)
    summary: str = ""  # what a PTZ command asks for (see ptz_summary), "" for any other call


def is_get(name: str) -> bool:
    return name.startswith("Get")


# Characters an action URI may not hold once its outer quotes are stripped: whitespace, controls and
# anything outside printable ASCII, the separators a camera may split a header on, and "%", which a
# camera may decode into one. A camera reading `"<SystemReboot>" <GetDeviceInformation>` may act on
# either one, so neither is relayed.
ACTION_UNSAFE_RE = re.compile(r'[^\x21-\x7e]|[,;?#"%]')


def clean_action(action: str) -> str:
    """An action URI with its outer quotes stripped ("" when empty); Refused when it is ambiguous."""
    value = action.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    if ACTION_UNSAFE_RE.search(value):
        raise Refused(
            "an action URI may not hold a space, a comma, a semicolon, a query, a fragment, a quote or a %", "action"
        )
    return value


def split_action(action: str) -> tuple[str, str]:
    """(namespace, operation) of a clean action URI such as http://www.onvif.org/ver10/device/wsdl/GetUsers."""
    namespace, _, name = action.rstrip("/").rpartition("/")
    return namespace, name


def in_namespace(namespace: str, base: str) -> bool:
    """True when `namespace` is `base` (an ns_key) or a path below it."""
    key = ns_key(namespace)
    return key == base or key.startswith(base + "/")


def check_operation(namespace: str, name: str, action: bool = False) -> bool:
    """Refuse a call that is not a Get, a PTZ command or an event call; True for a PTZ command.

    An action URI names its operation with a Request suffix in event.wsdl (PullMessagesRequest).
    """
    if is_get(name):
        return False
    key = name.removesuffix("Request") if action else name
    for bases, names in ALLOWED_WRITES:
        if key in names and any(in_namespace(namespace, base) for base in bases):
            return names is PTZ_COMMANDS
    raise Refused(f"{name} is refused: only reads, PTZ commands and event calls are relayed", "write")


def check_names(names: list[str]) -> bool:
    """Refuse a Device command or a Subscribe named anywhere; True when one of the names is a PTZ command."""
    ptz = False
    for name in names:
        if name.casefold() in REFUSED_ANYWHERE:
            raise Refused(f"{name} is refused: it would change the real camera", "camera-command")
        ptz = ptz or name in PTZ_COMMANDS
    return ptz


def wsa_action(el) -> str:
    """The clean URI of a WS-Addressing Action element; Refused when it holds anything but text.

    A child element splits the text: the proxy would read the first piece, a camera the whole.
    """
    if len(el) or "".join(el.itertext()) != (el.text or ""):
        raise Refused("a WS-Addressing Action holds its URI as plain text only", "action")
    return clean_action(el.text or "")


def inspect(body: bytes, actions: list[str]) -> Call:
    """Classify a request, or raise Refused. Fails closed: whatever is not understood is refused.

    `actions` are the clean action URIs the request names in its HTTP headers (see
    request_actions); with the WS-Addressing Action, each one is checked like an operation.

    The checks run on the tree with comments dropped and CDATA turned into text, and that tree is
    what Call.body relays: the camera never parses bytes the proxy did not see as it saw them.
    """
    if not body:
        # Nothing to parse, but the action headers still name an operation: refuse any of them,
        # so that an empty POST cannot carry a command in its SOAPAction alone.
        names = [split_action(a)[1] for a in actions if a]
        if names:
            raise Refused(f"{names[0]} is refused: an action needs a SOAP envelope", "action-without-body")
        return Call((), False)
    raw = parse(body)
    if raw is None:
        raise Refused("the request is not well-formed XML", "not-xml")
    if has_dtd(raw):
        raise Refused("a DOCTYPE is not accepted", "doctype")
    if raw.getprevious() is not None or raw.getnext() is not None:
        raise Refused("nothing may come before or after the SOAP envelope", "not-soap")
    root = parse(body, REQUEST_PARSER)
    if root is None:
        raise Refused("the request is not well-formed XML", "not-xml")
    if etree.QName(root).namespace not in (SOAP_NS, SOAP11_NS) or localname(root) != "Envelope":
        raise Refused("the request is not a SOAP envelope", "not-soap")
    for el in root.iter():
        if not isinstance(el.tag, str):
            # Entity references and processing instructions: a camera's parser may act on them.
            raise Refused("entities and processing instructions are not accepted", "entity")
    if sum(1 for el in root.iter("{*}Body")) != 1:
        raise Refused("a SOAP envelope has exactly one Body", "not-soap")
    parts = [el for el in root if isinstance(el.tag, str)]
    names = [localname(el) for el in parts]
    if names not in (["Body"], ["Header", "Body"]) or (root.text or "").strip():
        raise Refused("a SOAP envelope holds one optional Header, then its Body", "not-soap")
    if any((el.tail or "").strip() for el in root):
        raise Refused("a SOAP envelope holds one optional Header, then its Body", "not-soap")
    if any(etree.QName(el).namespace != etree.QName(root).namespace for el in parts):
        # A camera may read a Header or Body by its local name alone: one the proxy does not
        # check as such must not reach it.
        raise Refused("the SOAP Header and Body are in the envelope's namespace", "not-soap")
    body_el = parts[-1]
    ops = [el for el in body_el if isinstance(el.tag, str)]
    if not ops:
        raise Refused("the SOAP Body is empty", "empty-body")
    names = tuple(localname(op) for op in ops)
    # Read from the Body's operations only: the Header carries WS-Security.
    summary = ptz_summary(ops)
    try:
        ptz = check_calls(root, body_el, ops, actions)
    except Refused as refused:
        # The log names the operations of a refused request too, once they are known.
        refused.ops, refused.summary = names, summary
        raise
    on_ptz = any(in_namespace(etree.QName(op).namespace, ns_key(PTZ_NS)) for op in ops)
    # No XML declaration, as most clients send it: UTF-8 is XML's default encoding.
    relayed = etree.tostring(root, xml_declaration=False, encoding="utf-8")
    return Call(names, ptz, relayed, on_ptz, summary)


def check_calls(root, body_el, ops: list, actions: list[str]) -> bool:
    """The checks of inspect once the Body's operations are known; True for a PTZ command."""
    if (body_el.text or "").strip() or any((el.tail or "").strip() for el in body_el):
        raise Refused("the SOAP Body holds text outside its operations", "not-soap")
    for op in ops:
        namespace = ns_key(etree.QName(op).namespace)
        if not ((namespace + "/").startswith(SERVICE_NS_PREFIXES) or namespace in SERVICE_NS):
            raise Refused(f"{localname(op)} is not a call of an ONVIF service", "not-onvif")

    # Every Action element of the envelope, not only the Header's: a camera may find wsa:Action by
    # a document-wide local-name search. No legitimate request carries one in its Body.
    actions = actions + [wsa_action(el) for el in root.iter("{*}Action")]
    pairs = [split_action(a) for a in actions if a]

    names = [localname(el) for el in body_el.iterdescendants() if isinstance(el.tag, str)]
    ptz = check_names(names + [name for _, name in pairs])
    for op in ops:
        ptz = check_operation(etree.QName(op).namespace, localname(op)) or ptz
    for namespace, name in pairs:
        ptz = check_operation(namespace, name, action=True) or ptz
    return ptz


def relayed_content_type(value: str | None) -> str | None:
    """The request's Content-Type with its charset set to utf-8, the encoding of the relayed body."""
    if value is None:
        return None
    media, *params = value.split(";")
    kept = [p for p in params if p.partition("=")[0].strip().lower() != "charset"]
    if len(kept) == len(params):
        return value
    return ";".join([media, " charset=utf-8", *kept])


def content_type_action(value: str) -> str:
    """The raw action parameter of a Content-Type ("" when there is none); Refused when ambiguous.

    A camera may split the header naively on ";", or parse its quoted strings: both must read the
    same parameters, so a quoted string may not hold a ";". One plain action parameter at most,
    and no extended one (action*=). A quoted value must be read by werkzeug's quote-aware parse
    as it is written. An unquoted URI is not an RFC 9110 token (werkzeug stops at its ":"), but
    clients send it that way: clean_action then refuses any separator in it.

    "action" may appear nowhere else in the header (casefold): not in the media type, nor in another
    parameter's key or value. A lenient camera may find an action there (`action=...` as the media
    type, `xaction=...`, `soap+xml action=...`) that the proxy would not read.
    """
    pieces = value.split(";")
    if any(re.sub(r"\\.", "", piece).count('"') % 2 for piece in pieces):
        raise Refused("a Content-Type may not hold a quoted semicolon nor an unterminated quote", "action")
    if "action" in pieces[0].casefold():
        raise Refused("a Content-Type names an action in its action parameter only", "action")
    found = []
    for param in pieces[1:]:
        key, _, raw = param.partition("=")
        key = key.strip().casefold()
        if key == "action":
            found.append(raw.strip())
        elif key.partition("*")[0] == "action":
            # RFC 2231 (action*=UTF-8''..., action*0=): a camera that decodes it reads
            # percent-escaped separators the proxy cannot see.
            raise Refused("an extended Content-Type action parameter is not accepted", "action")
        elif "action" in param.casefold():
            raise Refused("a Content-Type names an action in its action parameter only", "action")
    if not found:
        return ""
    if len(found) > 1:
        raise Refused("a Content-Type carries one action parameter at most", "action")
    raw = found[0]
    if raw.startswith('"') and parse_options_header(value)[1].get("action") != raw[1:-1]:
        raise Refused("the Content-Type action parameter is malformed", "action")
    return raw


def request_actions() -> list[str]:
    """The clean action URIs a request names in its HTTP headers; Refused when they are ambiguous.

    Each header once at most, and when both SOAPAction and the Content-Type action are given they
    must name the same operation: a camera may dispatch on either one.
    """
    for name in ("SOAPAction", "Content-Type"):
        if len(request.headers.getlist(name)) > 1:
            raise Refused(f"a request carries one {name} header at most", "action")
    soap_action = clean_action(request.headers.get("SOAPAction", ""))
    type_action = clean_action(content_type_action(request.headers.get("Content-Type", "")))
    if soap_action and type_action:
        (ns_a, op_a), (ns_b, op_b) = split_action(soap_action), split_action(type_action)
        if (ns_key(ns_a), op_a) != (ns_key(ns_b), op_b):
            raise Refused("SOAPAction and the Content-Type action name different operations", "action")
    return [a for a in (soap_action, type_action) if a]


def is_success(answer) -> bool:
    """A 2xx SOAP answer whose Body is not a Fault."""
    if not 200 <= answer.status_code < 300:
        return False
    root = parse(answer.content)
    return root is not None and not has_dtd(root) and answer_operation(root) not in ("", "Fault")


class PtzHold:
    """PTZ belongs to the first client that commands it, until it has been quiet for `hold` seconds.

    `lock` is held from the busy check to the claim, across the relay of the command: a second
    command that arrives meanwhile waits, then sees who holds PTZ.
    """

    def __init__(self, hold: float, clock):
        self.hold, self.clock = hold, clock
        self.holder, self.until = None, 0.0
        self.lock = threading.Lock()

    def busy_for(self, client: str) -> bool:
        return self.holder not in (None, client) and self.clock() < self.until

    def claim(self, client: str) -> None:
        self.holder, self.until = client, self.clock() + self.hold


class Rewriter:
    """Rewrites every place an answer names the camera so that it names the copy."""

    def __init__(self, camera_host, camera_port, copy_ip, copy_mac, hostname, streams, rtsp_port, snapshot_port, ports):
        self.camera_port = camera_port
        self.copy_ip, self.copy_mac, self.hostname = copy_ip, copy_mac, hostname
        self.streams, self.main = streams, next(iter(streams.values()))
        self.rtsp_port, self.snapshot_port, self.http_ports = rtsp_port, snapshot_port, ports
        host = re.escape(camera_host)
        # The camera address as a whole name: 192.0.2.1 must not match 192.0.2.10, nor tapo match
        # tapo-2, tapo.lan or mytapo. A dot that ends a sentence still ends the name.
        before, after = r"(?<![\w.-])", r"(?![\w-]|\.[\w-])"
        # A URL to the camera, with or without an account (dropped: the copy has its own).
        self.url_re = re.compile(
            rf"\b(rtsp|https?)://(?:[^\s/?#@<>\"']*@)?{host}(?::(\d+))?{after}([^\s<>\"']*)", re.IGNORECASE
        )
        self.host_re = re.compile(rf"{before}{host}(?::(\d+))?{after}", re.IGNORECASE)

    def __call__(self, body: bytes, client_port: int) -> bytes:
        if not body:
            return body
        root = parse(body)
        if root is None or has_dtd(root) or localname(root) != "Envelope":
            # Not SOAP (an HTML error page), or XML whose DTD may carry the address: plain text.
            # latin-1 maps every byte to one character, so untouched bytes stay as they were.
            text = body.decode("latin-1")
            out = self.text(text, "", client_port)
            return body if out == text else out.encode("latin-1")
        op = answer_operation(root)
        before = etree.tostring(root)
        if op == "GetNetworkProtocolsResponse":
            self.protocols(root)
        if op == "GetNetworkInterfacesResponse":
            # The copy has no IPv6, and a link-local address is derived from the camera's MAC.
            for ipv6 in list(root.iter("{*}IPv6")):
                ipv6.getparent().remove(ipv6)
        for el in root.iter():
            name = localname(el) if isinstance(el.tag, str) else ""
            if name == "HwAddress":
                el.text = self.copy_mac
            elif name == "Name" and op == "GetHostnameResponse":
                el.text = self.hostname
            elif el.text:
                el.text = self.text(el.text, op, client_port)
            if el.tail:
                el.tail = self.text(el.tail, op, client_port)
            if isinstance(el.tag, str):
                for key, value in el.attrib.items():
                    el.set(key, self.text(value, op, client_port))
        if etree.tostring(root) == before:
            return body
        return etree.tostring(root, xml_declaration=True, encoding="UTF-8")

    def text(self, value: str, op: str, client_port: int) -> str:
        def url(match: re.Match) -> str:
            scheme, path = match.group(1).lower(), match.group(3)
            if scheme == "rtsp":
                stream = self.streams.get(urlsplit(path).path, self.main)
                return f"rtsp://{self.copy_ip}:{self.rtsp_port}/{stream}"
            if op == "GetSnapshotUriResponse":
                return f"http://{self.copy_ip}:{self.snapshot_port}/api/frame.jpeg?src={self.main}"
            if scheme == "https":
                # The copy serves no HTTPS: the same path over http, on the port the client used,
                # which the proxy relays to the camera's ONVIF port.
                return f"http://{self.address(str(self.camera_port), client_port)}{path}"
            return f"http://{self.address(match.group(2), client_port)}{path}"

        value = self.url_re.sub(url, value)
        return self.host_re.sub(lambda m: self.address(m.group(1), client_port), value)

    def address(self, port: str | None, client_port: int) -> str:
        if port is None:
            return self.copy_ip
        port = client_port if int(port) == self.camera_port else int(port)
        return self.copy_ip if port == 80 else f"{self.copy_ip}:{port}"

    def protocols(self, root) -> None:
        for proto in root.iter("{*}NetworkProtocols"):
            ports = {"HTTP": self.http_ports, "RTSP": [self.rtsp_port]}.get(proto.findtext("{*}Name"))
            if ports is None:
                # The copy serves no HTTPS (nor anything else the camera may list).
                for enabled in proto.findall("{*}Enabled"):
                    enabled.text = "false"
                continue
            old = proto.findall("{*}Port")
            if not old:
                continue
            for extra in old[1:]:
                proto.remove(extra)
            old[0].text = str(ports[0])
            for port in reversed(ports[1:]):
                copy = etree.Element(old[0].tag)
                copy.text, copy.tail = str(port), old[0].tail
                old[0].addnext(copy)


# PROXY_LOG: every request, the ones worth reading (the default), or none.
LOG_LEVELS = ("all", "writes", "off")
# Calls left out of level writes when they go through cleanly, besides the reads: the event calls
# a client repeats forever (the PullMessages long poll and its subscription's Renew).
QUIET_CALLS = frozenset({"PullMessages", "Renew"})
# A log field is at most this long; a client chooses the path and the operation names.
LOG_FIELD_MAX = 160
_log_lock = threading.Lock()


def log_field(value: str) -> str:
    """A client-chosen value as one log field: no space, control or non-ASCII character, bounded.

    Percent-encoding keeps a path readable and makes a forged second line impossible.
    """
    text = quote(value, safe="/-._~,:", errors="replace") or "-"
    return text if len(text) <= LOG_FIELD_MAX else text[: LOG_FIELD_MAX - 3] + "..."


# The PTZ summary: what a PTZ command asks for, from the checked Body only (never the Header, which
# carries WS-Security). Each command lists its fields in the order written, as (label, path, kind):
# the path is the local names of the elements below the operation, then "@" and an attribute's
# local name for a vector component. Nothing else from the request is ever written.
SPEED = (("speed.pan", "Speed/PanTilt/@x", "num"), ("speed.tilt", "Speed/PanTilt/@y", "num"))
SPEED += (("speed.zoom", "Speed/Zoom/@x", "num"),)


def vector_fields(element: str) -> tuple:
    return (
        ("pan", f"{element}/PanTilt/@x", "num"),
        ("tilt", f"{element}/PanTilt/@y", "num"),
        ("zoom", f"{element}/Zoom/@x", "num"),
    )


PTZ_SUMMARY_FIELDS = {
    "ContinuousMove": (*vector_fields("Velocity"), ("timeout", "Timeout", "text")),
    "RelativeMove": (*vector_fields("Translation"), *SPEED),
    "AbsoluteMove": (*vector_fields("Position"), *SPEED),
    "Stop": (("pantilt", "PanTilt", "bool"), ("zoom", "Zoom", "bool")),
    "GotoPreset": (("preset", "PresetToken", "text"),),
    "SetPreset": (("preset", "PresetToken", "text"), ("name", "PresetName", "text")),
    "RemovePreset": (("preset", "PresetToken", "text"),),
    "GotoHomePosition": SPEED,
    "SetHomePosition": (),
}
# A value, and the whole summary, are at most this long once written.
SUMMARY_VALUE_MAX = 40
SUMMARY_MAX = 200
# xs:float as a camera reads it, ASCII digits only: no inf, nan, hex, "_" or locale separators.
FLOAT_RE = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", re.ASCII)
XML_SPACE = " \t\r\n"
UNKNOWN = object()


def cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def summary_text(value: str) -> str:
    """A client-chosen token or name: percent-encoded, cut at whole characters to SUMMARY_VALUE_MAX."""
    # Each character takes one place at least: the first ones are all that can be written.
    pieces = [quote(char, safe="-._~", errors="replace") for char in value[: SUMMARY_VALUE_MAX + 1]]
    if sum(map(len, pieces)) <= SUMMARY_VALUE_MAX:
        return "".join(pieces) or "-"
    # Room for the "..." marker, cut at a whole character: never half a percent escape.
    out = ""
    for piece in pieces:
        if len(out) + len(piece) > SUMMARY_VALUE_MAX - 3:
            break
        out += piece
    return out + "..."


def summary_number(value: str) -> str:
    """A number as a parsed float rounded to 3 decimals, or "?" when it is not a finite number."""
    text = value.strip(XML_SPACE)
    number = float(text) if FLOAT_RE.fullmatch(text) else math.nan
    if not math.isfinite(number):
        return "?"
    written = f"{round(number, 3):.3f}".rstrip("0").rstrip(".")
    return cut("0" if written in ("-0", "") else written, SUMMARY_VALUE_MAX)


def summary_bool(value: str) -> str:
    """An xs:boolean as true or false, or "?"."""
    return {"true": "true", "1": "true", "false": "false", "0": "false"}.get(value.strip(XML_SPACE), "?")


def summary_value(op, path: str):
    """The text at `path` below the operation: None when absent, UNKNOWN when ambiguous.

    Each step must be one element with that local name: two of them, or a field holding an
    element, is ambiguous, since a camera may read either one.
    """
    steps, _, attribute = path.partition("/@")
    el = op
    for step in steps.split("/"):
        found = [child for child in el if isinstance(child.tag, str) and localname(child) == step]
        if not found:
            return None
        if len(found) > 1:
            return UNKNOWN
        el = found[0]
    if attribute:
        values = [v for k, v in el.attrib.items() if etree.QName(k).localname == attribute]
        return None if not values else values[0] if len(values) == 1 else UNKNOWN
    if any(isinstance(child.tag, str) for child in el):
        return UNKNOWN
    return el.text or ""


def ptz_summary(ops: list) -> str:
    """The summary of the first PTZ command among the Body's operations, or "" when there is none."""
    op = next(
        (op for op in ops if localname(op) in PTZ_COMMANDS and in_namespace(etree.QName(op).namespace, ns_key(PTZ_NS))),
        None,
    )
    if op is None:
        return ""
    fields = (*PTZ_SUMMARY_FIELDS.get(localname(op), ()), ("profile", "ProfileToken", "text"))
    write = {"num": summary_number, "bool": summary_bool, "text": summary_text}
    parts = []
    for label, path, kind in fields:
        value = summary_value(op, path)
        if value is not None:
            parts.append(f"{label}={'?' if value is UNKNOWN else write[kind](value)}")
    return cut(" ".join(parts), SUMMARY_MAX)


def worth_logging(level: str, ops: tuple[str, ...], on_ptz: bool, decision: str, status: int | None) -> bool:
    """True when a request's line is written at `level`.

    Level writes keeps every refusal, PTZ decision and error, every call of the PTZ service, and
    every call that is not a read or a repeated event call. A camera status of 400 or more is an
    error, except 401: the challenge every HTTP digest exchange starts with.
    """
    if level != "writes":
        return level == "all"
    if decision != "relayed" or on_ptz:
        return True
    if status is not None and status >= 400 and status != 401:
        return True
    return not all(is_get(op) or op in QUIET_CALLS for op in ops)


def emit(line: str) -> None:
    """One line on stdout, flushed, never mixed with another thread's."""
    with _log_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


HOST_RE = re.compile(r"(?:\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+)(?::([0-9]{1,5}))?", re.ASCII)


def client_port_of(environ: dict) -> int:
    """The port the client used: its Host header's, else the listening port. ValueError when malformed."""
    host = environ.get("HTTP_HOST")
    if host is None:
        return int(environ.get("SERVER_PORT") or 80)
    match = HOST_RE.fullmatch(host)
    port = int(match.group(1) or 80) if match else 0
    if not 1 <= port <= 65535:
        raise ValueError(f"malformed Host header {host!r}")
    return port


def create_app(
    camera: str,
    camera_host: str,
    camera_port: int,
    copy_ip: str,
    copy_mac: str,
    hostname: str,
    streams: dict[str, str],
    ptz_hold: float = 5.0,
    rtsp_port: int = 554,
    snapshot_port: int = 1984,
    http_ports: tuple[int, ...] = (80, 2020),
    clock=time.monotonic,
    log_level: str = "writes",
    wall=lambda: datetime.datetime.now(datetime.UTC),
) -> Flask:
    """The proxy app. `clock` times PTZ holds and requests; `wall` gives each log line its UTC time."""
    if log_level not in LOG_LEVELS:
        raise ValueError(f"PROXY_LOG must be one of {', '.join(LOG_LEVELS)}")
    app = Flask("onvif_proxy")
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY
    camera = camera.rstrip("/")
    rewrite = Rewriter(
        camera_host, camera_port, copy_ip, copy_mac, hostname, streams, rtsp_port, snapshot_port, list(http_ports)
    )
    ptz = PtzHold(ptz_hold, clock)

    def forward(body: bytes):
        url = camera + request.path
        if request.query_string:
            url += "?" + request.query_string.decode("latin-1")
        headers = {k: v for k in REQUEST_HEADERS if (v := request.headers.get(k)) is not None}
        if body and "Content-Type" in headers:
            headers["Content-Type"] = relayed_content_type(headers["Content-Type"])
        # requests sets Content-Length from the relayed body, not from the client's.
        # One plain request per call: no session, so no cookie, proxy setting or .netrc is kept or read.
        with requests.Session() as session:
            session.trust_env = False
            return session.request(
                request.method, url, data=body, headers=headers, timeout=TIMEOUT, allow_redirects=False
            )

    def busy(client: str) -> str:
        """The ptz-busy decision: who holds PTZ and for how many seconds more ("-" when unknown)."""
        holder, left = ptz.holder, ptz.until - clock()
        if holder in (None, client) or left <= 0:
            return "ptz-busy:holder=-,left=-"
        return f"ptz-busy:holder={log_field(holder)},left={left:.1f}s"

    @app.route("/", defaults={"path": ""}, methods=["GET", "POST"])
    @app.route("/<path:path>", methods=["GET", "POST"])
    def relay(path: str) -> Response:
        start = clock()
        client = request.remote_addr or ""

        def done(response: Response, decision: str, ops=(), on_ptz=False, status=None, summary="") -> Response:
            # Never a body, a header or the query: they carry accounts, digests and nonces. The PTZ
            # summary holds only allow-listed fields of the Body, each one already made safe.
            if worth_logging(log_level, ops, on_ptz, decision, status):
                emit(
                    " ".join(
                        (
                            wall().astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            log_field(client),
                            log_field(request.path),
                            log_field(",".join(ops)) + (f"[{summary}]" if summary else ""),
                            decision,
                            "-" if status is None else str(status),
                            f"{max(0, int((clock() - start) * 1000))}ms",
                        )
                    )
                )
            return response

        try:
            client_port = client_port_of(request.environ)
        except ValueError:
            return done(fault("Sender", "malformed Host header"), "refused:host")
        try:
            data = request.get_data()
        except RequestEntityTooLarge:
            done(None, "refused:too-large")
            raise
        try:
            call = inspect(data, request_actions())
        except Refused as refused:
            return done(fault("Sender", str(refused)), f"refused:{refused.code}", refused.ops, summary=refused.summary)
        body = call.body
        seen = {"ops": call.ops, "on_ptz": call.on_ptz or call.ptz, "summary": call.summary}
        decision = "relayed"

        try:
            if not call.ptz:
                answer = forward(body)
            elif not ptz.lock.acquire(timeout=PTZ_WAIT):
                reply = fault("Receiver", "PTZ is in use by another client; try again in a few seconds")
                return done(reply, busy(client), **seen)
            else:
                try:
                    if ptz.busy_for(client):
                        reply = fault("Receiver", "PTZ is in use by another client; try again in a few seconds")
                        return done(reply, busy(client), **seen)
                    held = ptz.holder == client and clock() < ptz.until
                    answer = forward(body)
                    if is_success(answer):
                        ptz.claim(client)
                        decision = "ptz-hold" if held else "ptz-claim"
                finally:
                    ptz.lock.release()
        except requests.RequestException:
            return done(fault("Receiver", "camera unreachable"), "error:camera-unreachable", **seen)

        out = Response(rewrite(answer.content, client_port), answer.status_code)
        for key in RESPONSE_HEADERS:
            if key in answer.headers:
                out.headers[key] = answer.headers[key]
        return done(out, decision, status=answer.status_code, **seen)

    return app


def go2rtc_reachable(port: int, timeout: float = 2.0) -> bool:
    """True when go2rtc's RTSP server answers on localhost, that is, when both share a namespace."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def watch_go2rtc(
    port: int,
    interval: float = WATCH_INTERVAL,
    failures: int = WATCH_FAILURES,
    reachable=go2rtc_reachable,
    sleep=time.sleep,
    stop=os._exit,
) -> None:
    """Exit the process once go2rtc has been unreachable on localhost `failures` checks in a row.

    os._exit, not sys.exit: this runs in a thread, and the waitress threads must go too.
    """
    missed = 0
    while True:
        sleep(interval)
        missed = 0 if reachable(port) else missed + 1
        if missed >= failures:
            print(f"go2rtc unreachable on 127.0.0.1:{port}; exiting to rejoin its network", file=sys.stderr, flush=True)
            stop(1)
            return


def config_from_env() -> dict:
    """The proxy's settings; ValueError names the first bad one."""
    camera = os.environ["ONVIF_CAMERA"]
    try:
        camera_host, camera_port = camera_address(camera)
    except ValueError:
        raise ValueError("ONVIF_CAMERA must be http://<IPv4>[:port], with no account, path or query") from None
    try:
        ptz_hold = float(os.environ.get("PTZ_HOLD", "5"))
    except ValueError:
        ptz_hold = math.nan
    if not (math.isfinite(ptz_hold) and 0 < ptz_hold <= MAX_PTZ_HOLD):
        raise ValueError(f"PTZ_HOLD must be a number of seconds above 0, at most {MAX_PTZ_HOLD:g}")
    log_level = os.environ.get("PROXY_LOG", "writes")
    if log_level not in LOG_LEVELS:
        raise ValueError(f"PROXY_LOG must be one of {', '.join(LOG_LEVELS)}")
    return {
        "camera": camera,
        "camera_host": camera_host,
        "camera_port": camera_port,
        "copy_ip": os.environ["COPY_IP"],
        "copy_mac": os.environ["COPY_MAC"],
        "hostname": os.environ["COPY_HOSTNAME"],
        "streams": json.loads(os.environ["ONVIF_STREAMS"]),
        "ptz_hold": ptz_hold,
        "rtsp_port": int(os.environ.get("RTSP_PORT", "554")),
        "snapshot_port": int(os.environ.get("SNAPSHOT_PORT", "1984")),
        "http_ports": tuple(int(p) for p in os.environ.get("HTTP_PORTS", "80 2020").split()),
        "log_level": log_level,
    }


def serve_options(config: dict) -> dict:
    """waitress settings: every HTTP port, and the same body cap as Flask's."""
    return {
        "listen": " ".join(f"0.0.0.0:{port}" for port in config["http_ports"]),
        # Threads, not async: each PullMessages long poll holds one for up to a minute.
        "threads": 32,
        "ident": None,
        "max_request_body_size": MAX_BODY,
    }


if __name__ == "__main__":
    from waitress import serve

    try:
        config = config_from_env()
    except ValueError as err:
        print(f"error: {err}", file=sys.stderr, flush=True)
        sys.exit(2)
    threading.Thread(target=watch_go2rtc, args=(config["rtsp_port"],), daemon=True).start()
    serve(create_app(**config), **serve_options(config))
