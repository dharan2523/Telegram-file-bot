import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


class _HealthRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/":
            self.send_error(404)
            return

        body = b"Telegram file bot is running"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class _HealthHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False


def health_port() -> int:
    return int(os.environ.get("PORT", "8000"))


def start_health_server(port: int | None = None) -> _HealthHTTPServer:
    selected_port = health_port() if port is None else port
    server = _HealthHTTPServer(("0.0.0.0", selected_port), _HealthRequestHandler)
    thread = Thread(
        target=server.serve_forever,
        name="health-server",
        daemon=True,
    )
    thread.start()
    return server