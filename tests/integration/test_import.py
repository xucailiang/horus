import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from horus.core.service import Service


@pytest.mark.parametrize("url_path", ["/openapi.json", "/swagger/", "/spring/", "/dotnet/"])
def test_imports_direct_spec_and_swagger_ui_without_executing_javascript(tmp_path, url_path):
    spec = {
        "openapi": "3.1.0",
        "info": {"title": "remote", "version": "1"},
        "paths": {
            "/items": {
                "get": {"operationId": "listItems", "responses": {"200": {"description": "OK"}}}
            }
        },
    }
    pages = {
        "/openapi.json": json.dumps(spec),
        "/swagger/": '<html><script>SwaggerUIBundle({url:"../openapi.json"})</script></html>',
        "/spring/": '<html><script src="swagger-initializer.js"></script></html>',
        "/spring/swagger-initializer.js": 'SwaggerUIBundle({configUrl:"/swagger-config"});',
        "/swagger-config": json.dumps({"urls": [{"url": "/openapi.json", "name": "api"}]}),
        "/dotnet/": (
            '<html><script>var c=JSON.parse(\'{"urls":[{"url":"/openapi.json"}]}\');'
            "</script></html>"
        ),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in pages:
                self.send_error(404)
                return
            data = pages[self.path].encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        service = Service(tmp_path)
        project = service.open_project("remote", create_if_missing=True)["projectRef"]
        imported = service.import_api(project, f"http://127.0.0.1:{server.server_port}{url_path}")
        assert imported["status"] == "ready"
        context = service.get_context(project, imported["sourceRef"])
        assert context["catalog"]["listItems"]["method"] == "GET"
    finally:
        server.shutdown()
        server.server_close()
