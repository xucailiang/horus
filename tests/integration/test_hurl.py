import threading

from horus.adapters.hurl import HurlAdapter
from horus.core.models import Assertion, Request
from tests.fixtures.reference_api import ReferenceServer


async def test_hurl_returns_actual_body_and_checks_200_data_error():
    server = ReferenceServer(0, mode="data-error", username="test", password="hidden")
    server.records["sample"] = {"id": "sample", "name": "sample", "value": 42}
    server.tokens.add("private-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        response = await HurlAdapter().request(
            {"method": "GET", "path": "/records/{id}"},
            f"http://127.0.0.1:{server.server_port}",
            Request(pathParams={"id": "sample"}, headers={"Authorization": "Bearer private-token"}),
        )
        assert response.status == 200
        assert response.body["value"] == 43
        checks = await response.check(
            [
                Assertion(
                    id="value", jsonPointer="/value", expected=42, evidenceRefs=["fixture-contract"]
                )
            ]
        )
        assert checks[0]["status"] == "failed"
    finally:
        server.shutdown()
        server.server_close()
