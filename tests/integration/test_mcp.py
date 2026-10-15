import sys
import threading

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from horus.core.runs import Runs
from horus.core.service import Service
from horus.core.store import HorusError
from horus.mcp.server import create_server
from tests.fixtures.reference_api import ReferenceServer
from tests.support import FIXTURES, reviewed_regression_plan


async def test_empty_workspace_stdio_discovers_tools_and_preserves_project(tmp_path):
    transport = StdioTransport(
        command=sys.executable,
        args=[
            "-m",
            "horus",
            "mcp",
            "--workspace",
            str(tmp_path / "workspace"),
        ],
    )
    async with Client(transport) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert "horus_get_capabilities" in tools
        assert "horus_prepare_review" in tools
        assert "horus_cleanup_run" in tools
        assert tools["horus_save_plan"].output_schema is not None
        result = await client.call_tool(
            "horus_open_project",
            {
                "name": "from-agent",
                "createIfMissing": True,
            },
        )
        project_ref = result.structured_content["projectRef"]
    async with Client(transport) as client:
        result = await client.call_tool("horus_open_project", {"name": project_ref})
        assert result.structured_content["projectRef"] == project_ref


async def test_cleanup_of_active_run_returns_waitable_business_result(tmp_path, monkeypatch):
    def active(*args):
        raise HorusError("run_not_finished")

    monkeypatch.setattr(Runs, "cleanup", active)
    async with Client(create_server(tmp_path)) as client:
        result = await client.call_tool(
            "horus_cleanup_run", {"projectRef": "project", "runId": "run", "requestId": "cleanup"}
        )
    assert not result.is_error
    assert result.structured_content["status"] == "blocked"
    assert result.structured_content["issues"][0]["needsUserInput"] is False
    assert "horus_wait_run" in result.structured_content["nextActions"]


async def test_mcp_plan_review_execution_and_report_survive_client_disconnect(tmp_path):
    server = ReferenceServer(0, mode="normal", username="mcp-user", password="mcp-private")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    workspace = tmp_path / "workspace"
    service = Service(workspace, read_roots=[FIXTURES])
    project, original_plan, _ = reviewed_regression_plan(
        service, f"http://127.0.0.1:{server.server_port}"
    )
    raw = service.project(project).get("plans", original_plan)
    raw["name"] = "mcp-round-trip"
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "horus", "mcp", "--workspace", str(workspace), "--read-root", str(FIXTURES)],
        env={"HORUS_USERNAME": "mcp-user", "HORUS_PASSWORD": "mcp-private", "HORUS_TEST_MODE": "1"},
    )
    try:
        async with Client(transport) as client:
            saved = (
                await client.call_tool("horus_save_plan", {"projectRef": project, "plan": raw})
            ).structured_content["payload"]
            config_ref = service.open_project(project)["refs"]["configs"][0]
            bundle = (
                await client.call_tool(
                    "horus_prepare_review",
                    {"projectRef": project, "planRef": saved["planRef"], "configRef": config_ref},
                )
            ).structured_content["payload"]
            await client.call_tool(
                "horus_get_artifact", {"projectRef": project, "artifactRef": bundle["artifactRef"]}
            )
            unreviewed = await client.call_tool(
                "horus_start_run",
                {
                    "projectRef": project,
                    "planRef": saved["planRef"],
                    "reviewRef": None,
                    "requestId": "unreviewed",
                },
            )
            assert unreviewed.structured_content["status"] == "blocked"
            assert server.requests == []
            # Internal regression approval is explicitly marked; never impersonate a user over MCP.
            review = service.record_decision(
                project,
                bundle["reviewBundleRef"],
                saved["planRef"],
                bundle["scopeHash"],
                "approved",
                "test-only",
                "regression",
            )
            run = (
                await client.call_tool(
                    "horus_start_run",
                    {
                        "projectRef": project,
                        "planRef": saved["planRef"],
                        "reviewRef": review["reviewRef"],
                        "requestId": "mcp-retry",
                    },
                )
            ).structured_content["payload"]
        async with Client(transport) as client:
            result = (
                await client.call_tool(
                    "horus_wait_run",
                    {"projectRef": project, "runId": run["runId"], "waitMs": 30000},
                )
            ).structured_content["payload"]
            assert result["status"] == "completed"
            assert result["verdict"] == "passed"
            report = (
                await client.call_tool(
                    "horus_get_artifact",
                    {"projectRef": project, "artifactRef": result["artifactRef"]},
                )
            ).structured_content["payload"]
            assert "PASSED" in report["content"]
            assert not server.tokens and not server.records
    finally:
        server.shutdown()
        server.server_close()
