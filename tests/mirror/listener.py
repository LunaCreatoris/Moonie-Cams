"""Stands in for a camera or for a copy in `just mirror-check`: answers its name over HTTP on 554."""

import http.server
import sys

BODY = sys.argv[1].encode()


class Answer(http.server.BaseHTTPRequestHandler):
    # Keep-alive: a client can hold one connection open across the copy closing
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)

    def log_message(self, *args):
        pass


http.server.ThreadingHTTPServer(("0.0.0.0", 554), Answer).serve_forever()
