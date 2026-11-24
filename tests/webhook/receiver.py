"""Run by `just webhook-check`: a webhook endpoint that records every POST and lists them on GET."""

import http.server
import json

RECEIVED = []


class Endpoint(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        RECEIVED.append(
            {
                "path": self.path,
                "type": self.headers.get("Content-Type"),
                "auth": self.headers.get("Authorization"),
                "body": json.loads(body),
            }
        )
        self.answer(200, b"")

    def do_GET(self):
        self.answer(200, json.dumps(RECEIVED).encode())

    def answer(self, status, body):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


http.server.ThreadingHTTPServer(("0.0.0.0", 8080), Endpoint).serve_forever()
