import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from horus.adapters.k6 import K6Adapter
from horus.core.models import LoadStep
from tests.fixtures.reference_api import ReferenceServer


@pytest.mark.parametrize(
    "mode,verdict",
    [
        ("normal", "passed"),
        ("data-error", "failed"),
        ("delay", "failed"),
    ],
)
async def test_real_k6_metrics_and_thresholds(mode, verdict):
    server = ReferenceServer(0, mode=mode, username="test", password="hidden")
    server.records["sample"] = {"id": "sample", "name": "sample", "value": 42}
    server.tokens.add("private-token")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    step = LoadStep.model_validate(
        {
            "kind": "load",
            "id": "read",
            "operationId": "getRecord",
            "request": {
                "pathParams": {"id": "sample"},
                "headers": {"Authorization": "Bearer private-token"},
            },
            "assertions": [
                {"id": "status", "status": 200, "evidenceRefs": ["contract"]},
                {
                    "id": "value",
                    "jsonPointer": "/value",
                    "expected": 42,
                    "evidenceRefs": ["contract"],
                },
            ],
            "loadProfile": {"vus": 2, "iterations": 20, "maxDurationMs": 10000},
            "thresholds": {
                "latencyP95Ms": {"lte": 50 if mode == "delay" else 1000},
                "httpErrorRate": {"lte": 0},
                "dataCheckFailureRate": {"lte": 0},
                "completedIterations": {"eq": 20},
            },
        }
    )
    try:
        result = await K6Adapter().run(
            {"method": "GET", "path": "/records/{id}"},
            f"http://127.0.0.1:{server.server_port}",
            step,
        )
        assert result["status"] == verdict
        assert result["metrics"]["completedIterations"] == 20
        assert result["metrics"]["requests"] == 20
        assert result["metrics"]["httpErrorRate"] == 0
        assert result["metrics"]["latencyP99Ms"] > 0
        assert result["metrics"]["dataCheckFailureRate"] == (1 if mode == "data-error" else 0)
        assert len(result["failureSamples"]) <= 10
    finally:
        server.shutdown()
        server.server_close()


async def test_http_500_fails_default_http_threshold_without_status_assertion():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = b'{"message":"available"}'
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    step = LoadStep.model_validate({
        "kind": "load", "id": "read", "operationId": "read", "request": {},
        "assertions": [{"id": "body", "jsonPointer": "/message", "expected": "available",
                        "evidenceRefs": ["contract"]}],
        "loadProfile": {"vus": 1, "iterations": 3, "maxDurationMs": 5000},
        "thresholds": {"httpErrorRate": {"lte": 0}, "dataCheckFailureRate": {"lte": 0}},
    })
    try:
        result = await K6Adapter().run(
            {"method": "GET", "path": "/"}, f"http://127.0.0.1:{server.server_port}", step
        )
        assert result["status"] == "failed"
        assert result["metrics"]["httpErrorRate"] == 1
        assert result["metrics"]["dataCheckFailureRate"] == 0
        assert len(result["failureSamples"]) == 3
    finally:
        server.shutdown()
        server.server_close()
