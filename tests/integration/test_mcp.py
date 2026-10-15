import sys

from fastmcp import Client
from fastmcp.client.transports import StdioTransport


async def test_empty_workspace_stdio_discovers_tools_and_preserves_project(tmp_path):
    transport = StdioTransport(command=sys.executable, args=[
        "-m", "horus", "mcp", "--workspace", str(tmp_path / "workspace"),
    ])
    async with Client(transport) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert "horus_get_capabilities" in tools
        assert "horus_prepare_review" in tools
        assert tools["horus_save_plan"].output_schema is not None
        result = await client.call_tool("horus_open_project", {
            "name": "from-agent", "createIfMissing": True,
        })
        project_ref = result.structured_content["projectRef"]
    async with Client(transport) as client:
        result = await client.call_tool("horus_open_project", {"name": project_ref})
        assert result.structured_content["projectRef"] == project_ref
