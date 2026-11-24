"""Run by `just mirror-check`: a LAN client of the copy, waiting for it to answer or to go dark.

    client.py online|offline|held [SECONDS]

online: waits until the copy answers over TCP and ICMP. offline: waits until a TCP connection
times out (dropped, not refused) and a ping gets no reply, as from an unplugged camera. held:
opens one connection while the copy answers and keeps it, waits until the copy is offline, then
asks again on that same connection: nothing may come back, since a client's flow is never a reply
to one the copy opened.
"""

import os
import socket
import struct
import sys
import time
import urllib.request

COPY = "198.51.100.20"


def answers() -> bool:
    try:
        with urllib.request.urlopen(f"http://{COPY}:554/", timeout=2) as answer:
            return answer.read() == b"copy"
    except OSError:
        return False


def dropped() -> bool:
    """True when a connection attempt times out: nothing came back, not even a reset."""
    try:
        with socket.create_connection((COPY, 554), timeout=2):
            return False
    except TimeoutError:
        return True
    except OSError:
        return False


def checksum(data: bytes) -> int:
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    total = (total >> 16) + (total & 0xFFFF)
    return ~(total + (total >> 16)) & 0xFFFF


def pings() -> bool:
    """One ICMP echo request; True when the copy replies within a second."""
    ident = os.getpid() & 0xFFFF
    header = struct.pack("!BBHHH", 8, 0, 0, ident, 1)
    packet = struct.pack("!BBHHH", 8, 0, checksum(header + b"mirror"), ident, 1) + b"mirror"
    with socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP) as sock:
        sock.settimeout(1)
        sock.sendto(packet, (COPY, 0))
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            try:
                data, (address, _) = sock.recvfrom(1024)
            except TimeoutError:
                return False
            kind, _, _, reply_ident = struct.unpack("!BBHH", data[20:26])
            if address == COPY and kind == 0 and reply_ident == ident:
                return True
    return False


REQUEST = b"GET / HTTP/1.1\r\nHost: copy\r\n\r\n"


def ask(sock: socket.socket) -> bytes:
    """One request on a held connection; the response body, or b"" when nothing came back."""
    sock.sendall(REQUEST)
    data = b""
    try:
        while b"\r\n\r\n" not in data or not data.endswith(b"copy"):
            chunk = sock.recv(1024)
            if not chunk:
                break
            data += chunk
    except TimeoutError:
        pass
    return data.rpartition(b"\r\n\r\n")[2]


def held(limit: float) -> None:
    started = time.monotonic()
    while True:
        try:
            sock = socket.create_connection((COPY, 554), timeout=2)
            if ask(sock) == b"copy":
                break
            sock.close()
        except OSError:
            pass
        if time.monotonic() - started > limit:
            sys.exit("FAIL: no connection to hold while the copy answers")
        time.sleep(1)
    print("ok: holding one connection the copy answered", flush=True)
    while not (dropped() and not pings()):
        if time.monotonic() - started > limit:
            sys.exit(f"FAIL: the copy is not offline within {limit:.0f} s")
        time.sleep(1)
    sock.settimeout(5)
    try:
        body = ask(sock)
    except OSError as error:
        sys.exit(f"FAIL: the held connection got an answer while the copy is closed: {error!r}")
    if body:
        sys.exit("FAIL: the held connection still answers while the copy is closed")
    print("ok: the held connection is cut while the copy is closed", flush=True)


def main() -> None:
    want, limit = sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 30
    if want == "held":
        held(limit)
        return
    check = (lambda: answers() and pings()) if want == "online" else (lambda: dropped() and not pings())
    started = time.monotonic()
    while not check():
        if time.monotonic() - started > limit:
            sys.exit(f"FAIL: the copy is not {want} within {limit:.0f} s")
        time.sleep(1)
    print(f"ok: the copy is {want} after {time.monotonic() - started:.0f} s", flush=True)


main()
