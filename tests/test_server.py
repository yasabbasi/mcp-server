import pytest
from mcp import Client
from mcp.types import Tool, ToolAnnotations

from mcp_server.cli import GROUPS

pytestmark = pytest.mark.anyio


@pytest.fixture
async def tools(client: Client) -> list[Tool]:
    return (await client.list_tools()).tools


async def test_the_server_lists_tools(tools: list[Tool]) -> None:
    assert tools


async def test_every_tool_name_starts_with_the_prefix_of_a_group(tools: list[Tool]) -> None:
    prefixes = tuple(f"{group}_" for group in GROUPS)
    assert [tool.name for tool in tools if not tool.name.startswith(prefixes)] == []


async def test_every_tool_is_read_only(tools: list[Tool]) -> None:
    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=False)
    assert [tool.name for tool in tools if tool.annotations != read_only] == []


async def test_every_tool_has_an_output_schema(tools: list[Tool]) -> None:
    assert [tool.name for tool in tools if not tool.output_schema] == []


async def test_every_tool_has_a_description(tools: list[Tool]) -> None:
    assert [tool.name for tool in tools if not tool.description] == []


async def test_tools_are_listed_in_name_order(tools: list[Tool]) -> None:
    names = [tool.name for tool in tools]
    assert names == sorted(names)
