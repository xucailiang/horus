import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from horus.adapters.hurl import HurlAdapter
from horus.adapters.k6 import K6Adapter
from horus.core.models import LoadStep
from tests.support import FIXTURES


@pytest.mark.parametrize("include_mismatches", [False, True])
async def test_hurl_and_real_k6_agree_on_ecmascript_regex_corpus(include_mismatches):
    cases = [
        row
        for row in json.loads((FIXTURES / "regex-cases.json").read_bytes())
        if include_mismatches or row[2]
    ]
    body = json.dumps({str(i): row[1] for i, row in enumerate(cases)}).encode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    step = LoadStep.model_validate(
        {
            "kind": "load",
            "id": "read",
            "operationId": "read",
            "request": {},
            "assertions": [
                {
                    "id": str(i),
                    "jsonPointer": f"/{i}",
                    "operator": "regex",
                    "expected": row[0],
                    "evidenceRefs": ["contract"],
                }
                for i, row in enumerate(cases)
            ],
            "loadProfile": {"vus": 2, "iterations": 4, "maxDurationMs": 5000},
            "thresholds": {"httpErrorRate": {"lte": 0}, "dataCheckFailureRate": {"lte": 0}},
        }
    )
    operation = {"method": "GET", "path": "/"}
    target = f"http://127.0.0.1:{server.server_port}"
    try:
        response = await HurlAdapter().request(operation, target, step.request)
        checks = await response.check(step.assertions)
        expected_failed = [str(i) for i, row in enumerate(cases) if not row[2]]
        assert [check["id"] for check in checks if check["status"] == "failed"] == expected_failed
        result = await K6Adapter().run(operation, target, step)
        assert result["status"] == ("failed" if include_mismatches else "passed")
        assert result["metrics"]["completedIterations"] == 4
        assert result["metrics"]["httpErrorRate"] == 0
        assert result["metrics"]["dataCheckFailureRate"] == int(include_mismatches)
        for sample in result["failureSamples"]:
            assert sample["failedAssertions"] == expected_failed
    finally:
        server.shutdown()
        server.server_close()
