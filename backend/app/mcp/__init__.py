"""The MCP server this app exposes at `POST /mcp` (see `app.mcp.server`).

`app.main` imports exactly these two: the lifespan that runs the streamable
HTTP session manager, and the function that registers the route.
"""

from app.mcp.server import MCP_PATH, mcp_lifespan, mcp_server, mount_mcp

__all__ = ["MCP_PATH", "mcp_lifespan", "mcp_server", "mount_mcp"]
