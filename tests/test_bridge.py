"""Мост поверх поддельного upstream: Host подменяется, тело и статус проходят насквозь."""

import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

from agents_news_ouroboros.bridge import serve

seen: dict = {}


class Upstream(BaseHTTPRequestHandler):
    def do_POST(self):
        seen["host"] = self.headers["Host"]
        seen["auth"] = self.headers.get("Authorization")
        seen["body"] = self.rfile.read(int(self.headers["Content-Length"]))
        payload = json.dumps({"ok": True, "path": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self.send_response(405)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


def run(server):
    threading.Thread(target=server.serve_forever, daemon=True).start()


def test_bridge_rewrites_host_and_passes_through():
    upstream = HTTPServer(("127.0.0.1", 0), Upstream)
    run(upstream)
    bridge = serve("127.0.0.1:0", f"http://127.0.0.1:{upstream.server_port}", None)
    run(bridge)
    url = f"http://127.0.0.1:{bridge.server_port}/v1/chat/completions"

    req = urllib.request.Request(url, data=b'{"model":"x"}', method="POST",
                                 headers={"Authorization": "Bearer k", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200 and json.loads(r.read()) == {"ok": True, "path": "/v1/chat/completions"}
    assert seen == {"host": f"127.0.0.1:{upstream.server_port}", "auth": "Bearer k", "body": b'{"model":"x"}'}

    try:
        urllib.request.urlopen(url, timeout=5)
    except urllib.error.HTTPError as exc:
        assert exc.code == 405
    else:
        raise AssertionError("upstream 405 must pass through")
    bridge.shutdown()
    upstream.shutdown()
