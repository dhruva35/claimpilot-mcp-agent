"""
guidelines_server_http.py — Milestone 7: Guidelines server over Streamable HTTP

This runs the SAME guidelines server but via HTTP instead of stdio.
Demonstrates that MCP servers can be deployed as independent web services.

Usage:
  # Start the HTTP server:
  python servers/guidelines_server_http.py

  # Then run main.py with HTTP transport:
  python main.py --transport http

WHAT IS STREAMABLE HTTP TRANSPORT?
  Unlike stdio (where the client spawns a subprocess), Streamable HTTP lets the
  server run independently as a web service. The client connects to it like any
  REST API — but speaks the MCP protocol over HTTP POST requests (with optional
  SSE streaming for long responses).

  This is the production deployment model: MCP servers as microservices that
  any agent can connect to over the network.

INTERVIEW TALKING POINT:
  "Local stdio is great for development and local tools. But in production,
  you want MCP servers deployed as cloud services — maybe the policy server
  runs on AWS near the database, the guidelines server runs wherever the
  vector store is. Streamable HTTP transport makes this possible. The agent
  code and tool schemas are identical — only the transport layer changes."
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Re-use the same FastMCP instance from guidelines_server.py
# This avoids duplicating the tool definitions
from servers.guidelines_server import mcp

if __name__ == "__main__":
    import uvicorn

    # Run with Streamable HTTP transport
    # The MCP endpoint will be at: http://127.0.0.1:8001/mcp
    mcp.run(
        transport="streamable-http",
        host="127.0.0.1",
        port=8001,
        path="/mcp",
        log_level="info",
    )

