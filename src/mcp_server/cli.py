"""The command line entry: every tool group's tools, served over stdio."""

import logging
import sys
from importlib import import_module

from mcp.server import MCPServer

from mcp_server.server import server

GROUPS = ("metrics",)


def registered() -> MCPServer:
    """Return the server after importing every tool group, which registers its tools."""
    for group in GROUPS:
        import_module(f"mcp_server.{group}")
    return server


def main() -> None:
    """Serve over stdio. stdout is the MCP wire, so logs go to stderr."""
    # MCPServer set up the root logger when it was built; force replaces that setup.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, force=True)
    registered().run("stdio")
