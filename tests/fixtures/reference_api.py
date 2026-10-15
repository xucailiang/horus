"""Permanent HTTP fixture; faults are controlled outside horus core."""

import argparse
import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class ReferenceServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, *, mode: str, username: str, password: str) -> None:
        super().__init__(("127.0.0.1", port), Handler)
        self.mode = mode
        self.username = username
        self.password = password
        self.records: dict[str, dict[str, Any]] = {}
        self.tokens: set[str] = set()
        self.requests: list[tuple[str, str]] = []
        self.lock = threading.RLock()


class Handler(BaseHTTPRequestHandler):
    server: ReferenceServer

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def respond(self, status: int, value: Any = None) -> None:
        body = json.dumps(value).encode() if value is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def dispatch(self) -> None:
        route = self.path.split("?", 1)[0]
        with self.server.lock:
            self.server.requests.append((self.command, route))
        if route == "/healthz" and self.command == "GET":
            self.respond(200, {"status": "ok"})
            return
        body: Any = None
        if self.headers.get("Content-Length"):
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            except (ValueError, TypeError):
                self.respond(400, {"error": "invalid-json"})
                return
        with self.server.lock:
            if route == "/auth/token" and self.command == "POST":
                if body != {"username": self.server.username, "password": self.server.password}:
                    self.respond(401, {"error": "invalid-credentials"})
                    return
                token = uuid.uuid4().hex
                self.server.tokens.add(token)
                self.respond(200, {"token": token})
                return
            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            if token not in self.server.tokens:
                self.respond(401, {"error": "unauthorized"})
                return
            if route == "/auth/logout" and self.command == "POST":
                self.server.tokens.remove(token)
                self.respond(204)
                return
            if route == "/records" and self.command == "POST":
                if (
                    not isinstance(body, dict)
                    or not isinstance(body.get("name"), str)
                    or type(body.get("value")) is not int
                ):
                    self.respond(400, {"error": "invalid-record"})
                    return
                record = {"id": uuid.uuid4().hex, **body}
                self.server.records[record["id"]] = record
                self.respond(201, record)
                return
            if route == "/records" and self.command == "GET":
                self.respond(200, list(self.server.records.values()))
                return
            if route.startswith("/records/"):
                record_id = route.removeprefix("/records/")
                record = self.server.records.get(record_id)
                if record is None:
                    self.respond(404, {"error": "not-found"})
                    return
                if self.command == "DELETE":
                    del self.server.records[record_id]
                    self.respond(204)
                    return
                value = dict(record)
            else:
                self.respond(404)
                return
        # Delay outside the lock so independent reads actually run concurrently.
        if self.server.mode == "delay":
            time.sleep(0.15)
        if self.server.mode == "data-error":
            value["value"] += 1
        self.respond(200, value)

    do_GET = dispatch
    do_POST = dispatch
    do_DELETE = dispatch


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--mode", choices=["normal", "data-error", "delay"], default="normal")
    args = parser.parse_args()
    server = ReferenceServer(
        args.port,
        mode=args.mode,
        username=os.environ["HORUS_USERNAME"],
        password=os.environ["HORUS_PASSWORD"],
    )
    print(f"http://127.0.0.1:{server.server_port}", flush=True)
    server.serve_forever()
