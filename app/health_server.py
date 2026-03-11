import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from app.health_state import health_state


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path != "/ping":
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "not_found"}).encode("utf-8"))
            return

        if health_state.initializing:
            self.send_response(204)
            self.end_headers()
            return

        if health_state.ready:
            self.send_response(200)
            self.end_headers()
            return

        self.send_response(503)
        self.end_headers()

    def log_message(self, format, *args):  # noqa: A003
        return


_server: Optional[ThreadingHTTPServer] = None
_thread: Optional[threading.Thread] = None


def start_health_server(host: str, port: int) -> bool:
    global _server, _thread
    if _server is not None:
        return False

    _server = ThreadingHTTPServer((host, port), _HealthHandler)
    _thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _thread.start()
    return True


def stop_health_server() -> None:
    global _server, _thread
    if _server is None:
        return

    _server.shutdown()
    _server.server_close()
    _server = None
    _thread = None
