"""Render Frankfurt connectivity probe. It contains no order endpoint."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .binance_account import verify_from_environment
STATUS = {"ready": False, "binance_connected": False, "orders_enabled": False}

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_error(404)
            return
        body = json.dumps(STATUS).encode()
        self.send_response(200 if STATUS["ready"] else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        return

def main():
    verify_from_environment()
    STATUS.update(ready=True, binance_connected=True)
    print("Binance read-only connection verified; order endpoints are disabled", flush=True)
    port = int(os.environ.get("PORT", "10000"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()

if __name__ == "__main__":
    main()
