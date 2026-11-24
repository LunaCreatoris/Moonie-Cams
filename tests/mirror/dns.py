"""Run by `just mirror-check`: a DNS server on the test network that knows one name.

    dns.py NAME ADDRESS

Answers an A query for NAME with ADDRESS, anything else with NXDOMAIN. The copy's namespace uses it
through Docker's embedded resolver, which forwards from inside that namespace: the answer comes
back on the copy's own interface, as from the LAN's DNS server.
"""

import socket
import struct
import sys

NAME, ADDRESS = sys.argv[1].lower().rstrip("."), socket.inet_aton(sys.argv[2])


def question(packet: bytes) -> tuple[str, int, int]:
    """(name, type, end of the question) of the first question."""
    labels, i = [], 12
    while packet[i]:
        labels.append(packet[i + 1 : i + 1 + packet[i]].decode("ascii", "replace"))
        i += 1 + packet[i]
    kind = struct.unpack("!H", packet[i + 1 : i + 3])[0]
    return ".".join(labels).lower(), kind, i + 5


def answer(packet: bytes) -> bytes:
    name, kind, end = question(packet)
    found = name == NAME and kind == 1
    flags = 0x8180 if found else 0x8183
    header = packet[:2] + struct.pack("!HHHHH", flags, 1, int(found), 0, 0)
    body = packet[12:end]
    if found:
        body += struct.pack("!HHHIH", 0xC00C, 1, 1, 5, 4) + ADDRESS
    return header + body


with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
    server.bind(("0.0.0.0", 53))
    while True:
        packet, client = server.recvfrom(512)
        try:
            server.sendto(answer(packet), client)
        except (IndexError, struct.error):
            pass
