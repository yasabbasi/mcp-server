import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters
from mcp.client.session import IncomingMessage

pytestmark = pytest.mark.anyio


async def test_the_cli_entry_answers_a_tool_call_with_only_mcp_on_stdout(samples: Path) -> None:
    """The real transport. The client reports any stdout line that is not MCP, such as a print."""
    not_mcp: list[Exception] = []

    async def collect(message: IncomingMessage) -> None:
        if isinstance(message, Exception):
            not_mcp.append(message)

    # Unbuffered, so a print made before the server starts reaches the client during the test.
    env = {"DATA_OUTPUT_DIR": str(samples), "PYTHONUNBUFFERED": "1"}
    entry = Path(sys.executable).with_name("mcp-server")
    server = StdioServerParameters(command=str(entry), env=env)
    async with Client(server, message_handler=collect) as client:
        result = await client.call_tool("metrics_freshness")
    assert not result.is_error
    assert result.structured_content["run_id"] == f"sample-{samples.name}"
    assert not_mcp == []
