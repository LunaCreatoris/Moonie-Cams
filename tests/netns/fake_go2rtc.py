"""Stands in for go2rtc in `just proxy-check`: listens on go2rtc's RTSP port, and crashes once.

The first boot exits with an error after CRASH_AFTER seconds, so that the restart policy starts
the same container again, in a new network namespace. Later boots run until stopped. The boot
count survives the restart in the container's own filesystem, and is served on the port.
"""

import http.server
import os
import pathlib
import threading
import time

BOOTS = pathlib.Path("/tmp/boots")
PORT = 554
CRASH_AFTER = float(os.environ.get("CRASH_AFTER", "8"))


class Boots(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = BOOTS.read_bytes()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


boot = int(BOOTS.read_text(encoding="utf-8")) + 1 if BOOTS.exists() else 1
BOOTS.write_text(str(boot), encoding="utf-8")
print(f"boot {boot}", flush=True)
server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Boots)
threading.Thread(target=server.serve_forever, daemon=True).start()
if boot == 1:
    time.sleep(CRASH_AFTER)
    print("crashing", flush=True)
    os._exit(1)
threading.Event().wait()
