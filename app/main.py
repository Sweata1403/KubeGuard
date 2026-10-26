"""Small demo workload: v1 works; v2 returns application errors."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        version = os.getenv("APP_VERSION", "v1")
        # Probes remain healthy: this demonstrates a business endpoint failure
        # that a simple process-health probe does not detect.
        if self.path in ("/live", "/ready"):
            status, body = 200, {"status": "healthy"}
        elif self.path == "/":
            status = 500 if version == "v2" else 200
            body = {"version": version, "message": "broken release" if status == 500 else "orders available"}
        else:
            status, body = 404, {"error": "not found"}
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
