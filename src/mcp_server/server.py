"""The one MCP server. Tool groups import it and register their tools on it."""

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)

server = MCPServer("mcp-server")
