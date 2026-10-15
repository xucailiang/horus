"""Permanent workflow fixture. Contracts stay fixed across deliberately faulty modes."""

import argparse
import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


class WorkflowServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        port: int,
        *,
        users: dict[str, str],
        mode: str = "normal",
        job_delay: float = 0.1,
        read_delay: float = 0,
    ) -> None:
        super().__init__(("127.0.0.1", port), Handler)
        self.users, self.mode, self.job_delay = users, mode, job_delay
        self.read_delay = read_delay
        self.tokens: dict[str, str] = {}
        self.tickets: dict[str, dict[str, Any]] = {}
        self.jobs: dict[str, dict[str, Any]] = {}
        self.requests: list[tuple[str, str, str | None]] = []
        self.lock = threading.RLock()

    def settle(self) -> None:
        if self.mode == "stuck-job":
            return
        for job in self.jobs.values():
            if time.monotonic() >= job["readyAt"]:
                job["state"] = "done"
                if job["ticketId"] in self.tickets:
                    self.tickets[job["ticketId"]]["state"] = "submitted"


class Handler(BaseHTTPRequestHandler):
    server: WorkflowServer

    def log_message(self, *args: Any) -> None:
        pass

    def respond(self, status: int, value: Any = None) -> None:
        body = json.dumps(value).encode() if value is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def dispatch(self) -> None:
        route = urlsplit(self.path).path
        token = self.headers.get("Authorization", "").removeprefix("Bearer ")
        actor = self.server.tokens.get(token)
        with self.server.lock:
            self.server.requests.append((self.command, route, actor))
        if self.command == "GET" and route == "/openapi.yaml":
            body = (Path(__file__).parent / "workflow-api" / "openapi.yaml").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/yaml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body: Any = None
        if self.command in {"POST", "PATCH"} and self.headers.get("Content-Length"):
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            except (ValueError, TypeError):
                self.respond(400, {"error": "invalid-json"})
                return
        if self.command == "GET" and route.startswith("/tickets/") and self.server.read_delay:
            time.sleep(self.server.read_delay)
        with self.server.lock:
            if route == "/auth/token" and self.command == "POST":
                if (
                    not isinstance(body, dict)
                    or not isinstance(body.get("username"), str)
                    or not isinstance(body.get("password"), str)
                    or self.server.users.get(body.get("username")) != body.get("password")
                    or body.get("username") not in self.server.users
                ):
                    self.respond(401, {"error": "invalid-credentials"})
                    return
                credential = uuid.uuid4().hex
                self.server.tokens[credential] = body["username"]
                self.respond(200, {"token": credential})
                return
            if actor is None:
                self.respond(401, {"error": "unauthorized"})
                return
            if route == "/auth/logout" and self.command == "POST":
                del self.server.tokens[token]
                self.respond(204)
                return
            self.server.settle()
            if route == "/tickets" and self.command == "POST":
                if not isinstance(body, dict) or not isinstance(body.get("title"), str):
                    self.respond(400, {"error": "title-required"})
                    return
                ticket = {
                    "id": uuid.uuid4().hex,
                    "title": body["title"],
                    "owner": actor,
                    "state": "draft",
                }
                self.server.tickets[ticket["id"]] = ticket
                self.respond(201, ticket)
                return
            parts = route.strip("/").split("/")
            if len(parts) not in {2, 3} or parts[0] not in {"tickets", "jobs"}:
                self.respond(404)
                return
            collection = self.server.tickets if parts[0] == "tickets" else self.server.jobs
            resource = collection.get(parts[1])
            if resource is None:
                self.respond(404, {"error": "not-found"})
                return
            if resource["owner"] != actor and self.server.mode != "permission-error":
                self.respond(403, {"error": "forbidden"})
                return
            if len(parts) == 2 and self.command == "DELETE":
                del collection[parts[1]]
                if parts[0] == "tickets":
                    for key in [
                        key for key, job in self.server.jobs.items() if job["ticketId"] == parts[1]
                    ]:
                        del self.server.jobs[key]
                self.respond(204)
                return
            if len(parts) == 2 and self.command == "GET":
                self.respond(
                    200, {key: value for key, value in resource.items() if key != "readyAt"}
                )
                return
            if parts[0] == "tickets":
                if resource["state"] != "draft" and self.server.mode != "illegal-state":
                    self.respond(409, {"error": "not-draft"})
                    return
                if len(parts) == 2 and self.command == "PATCH":
                    if not isinstance(body, dict) or not isinstance(body.get("title"), str):
                        self.respond(400, {"error": "title-required"})
                        return
                    resource["title"] = body["title"]
                    self.respond(200, resource)
                    return
                if len(parts) == 3 and parts[2] == "submit" and self.command == "POST":
                    job = {
                        "id": uuid.uuid4().hex,
                        "ticketId": resource["id"],
                        "owner": actor,
                        "state": "pending",
                        "readyAt": time.monotonic() + self.server.job_delay,
                    }
                    self.server.jobs[job["id"]] = job
                    resource["state"] = "processing"
                    self.respond(202, {"jobId": job["id"], "ticketId": resource["id"]})
                    return
            self.respond(405)

    do_GET = dispatch
    do_POST = dispatch
    do_PATCH = dispatch
    do_DELETE = dispatch


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument(
        "--mode",
        choices=["normal", "permission-error", "illegal-state", "stuck-job"],
        default="normal",
    )
    args = parser.parse_args()
    server = WorkflowServer(
        args.port,
        mode=args.mode,
        users={
            os.environ["HORUS_OWNER_USERNAME"]: os.environ["HORUS_OWNER_PASSWORD"],
            os.environ["HORUS_OUTSIDER_USERNAME"]: os.environ["HORUS_OUTSIDER_PASSWORD"],
        },
    )
    print(f"http://127.0.0.1:{server.server_port}", flush=True)
    server.serve_forever()
