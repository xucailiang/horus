"""Observe real request overlap without changing the permanent API's business contract."""

from collections import Counter
from typing import Any

from tests.fixtures.reference_api import Handler, ReferenceServer


class RecordingServer(ReferenceServer):
    def __init__(self, port: int, *, username: str, password: str) -> None:
        super().__init__(port, mode="delay", username=username, password=password)
        self.RequestHandlerClass = RecordingHandler
        self.active_cases: set[str] = set()
        self.reading: Counter[str] = Counter()
        self.peak_functional_cases = 0
        self.read_overlaps: list[set[str]] = []
        self.load_overlaps: list[set[str]] = []
        self.case_resources: dict[str, str] = {}
        self.case_tokens: dict[str, str] = {}
        self.tagged_requests: list[tuple[str, str, str]] = []
        self.credential_mismatches: list[str] = []


class RecordingHandler(Handler):
    server: RecordingServer

    def dispatch(self) -> None:
        name = self.headers.get("X-Horus-Case", "untagged")
        route = self.path.split("?", 1)[0]
        read = self.command == "GET" and route.startswith("/records/")
        with self.server.lock:
            self.server.tagged_requests.append((name, self.command, route))
            if (
                route != "/auth/token"
                and name in self.server.case_tokens
                and self.headers.get("Authorization") != "Bearer " + self.server.case_tokens[name]
            ):
                self.server.credential_mismatches.append(name)
            if read:
                self.server.reading[name] += 1
                if len(self.server.reading) > 1:
                    self.server.read_overlaps.append(set(self.server.reading))
        try:
            super().dispatch()
        finally:
            if read:
                with self.server.lock:
                    self.server.reading[name] -= 1
                    if self.server.reading[name] == 0:
                        del self.server.reading[name]

    def respond(self, status: int, value: Any = None) -> None:
        name = self.headers.get("X-Horus-Case", "untagged")
        with self.server.lock:
            if self.path == "/auth/token" and status == 200:
                self.server.active_cases.add(name)
                self.server.case_tokens[name] = value["token"]
                self.server.peak_functional_cases = max(
                    self.server.peak_functional_cases, len(self.server.active_cases - {"load"})
                )
                if "load" in self.server.active_cases and len(self.server.active_cases) > 1:
                    self.server.load_overlaps.append(set(self.server.active_cases))
            elif self.path == "/auth/logout" and status == 204:
                self.server.active_cases.discard(name)
            elif self.path == "/records" and self.command == "POST" and status == 201:
                self.server.case_resources[name] = value["id"]
        super().respond(status, value)

    do_GET = dispatch
    do_POST = dispatch
    do_DELETE = dispatch
