"""
hub.py — MCP Client Hub

WHAT DOES THIS DO?
  The hub connects to multiple MCP servers (each running as a subprocess via stdio),
  discovers their tools, and provides a single unified interface to call any tool
  by name. The agent never needs to know WHICH server owns a tool — the hub routes
  the call automatically.

MCP CONCEPT — DYNAMIC TOOL DISCOVERY:
  Unlike importing a Python function directly, the agent discovers tools at runtime
  by calling tools/list on each server. This is the key MCP innovation: the agent
  can work with ANY server without code changes, as long as the server speaks the
  protocol.

INTERVIEW TALKING POINT:
  "The hub implements the client side of MCP. It performs the protocol handshake,
  fetches tool schemas (which include the name, description, and JSON input schema
  for each tool), and merges them into a single catalog. When the agent wants to call
  a tool, the hub routes the call to whichever server registered that tool name."
"""

import asyncio
import sys
from pathlib import Path
from typing import Any

# fastmcp's Client handles session lifecycle and transport selection automatically
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

# Rich for pretty console output during the demo
from rich.console import Console
from rich.table import Table

console = Console()

# Base directory of the project (parent of client/)
BASE_DIR = Path(__file__).parent.parent
# Path to the venv python executable
PYTHON = sys.executable


class MCPHub:
    """
    Manages connections to all MCP servers and provides a unified tool
    call interface. Acts as a router + registry.
    """

    def __init__(self):
        # Maps tool_name -> server_name (for routing)
        self._tool_registry: dict[str, str] = {}
        # Maps tool_name -> full tool schema (for LLM consumption)
        self._tool_schemas: dict[str, dict] = {}
        # Maps server_name -> connected Client
        self._clients: dict[str, Client] = {}
        # Maps server_name -> transport config
        self._server_configs: dict[str, dict] = {}

    def register_server(self, name: str, script_path: str | Path, transport: str = "stdio"):
        """
        Register a server to be connected when the hub starts.

        Args:
            name: Human-readable server name (used for routing logs)
            script_path: Absolute path to the server's Python script
            transport: "stdio" (local subprocess) or "http" (remote URL)
        """
        self._server_configs[name] = {
            "script_path": str(script_path),
            "transport": transport,
        }

    def register_http_server(self, name: str, url: str):
        """Register a remote server accessible over Streamable HTTP."""
        self._server_configs[name] = {"url": url, "transport": "http"}

    async def connect_all(self):
        """
        Connect to all registered servers and discover their tools.
        This calls tools/list on each server and merges into one registry.
        """
        for server_name, config in self._server_configs.items():
            await self._connect_server(server_name, config)

    async def _connect_server(self, server_name: str, config: dict):
        """Connect to one server and discover its tools."""
        transport_type = config["transport"]

        if transport_type == "stdio":
            # StdioTransport spawns the script as a subprocess.
            # The Client communicates via that process's stdin/stdout.
            transport = StdioTransport(
                command=PYTHON,
                args=[config["script_path"]],
            )
            client = Client(transport)
        else:
            # HTTP transport: client connects to a running web server
            client = Client(config["url"])

        # Store client BEFORE connecting — we need a reference for cleanup
        self._clients[server_name] = client

        # Open the connection (performs MCP handshake: initialize → initialized)
        # We use __aenter__ manually so we can keep the connection open across calls
        await client.__aenter__()

        # Discover tools: tools/list returns all tool names, descriptions, schemas
        tools_response = await client.list_tools()

        for tool in tools_response:
            tool_name = tool.name
            if tool_name in self._tool_registry:
                console.print(
                    f"[yellow]Warning: Tool '{tool_name}' already registered "
                    f"from '{self._tool_registry[tool_name]}'. "
                    f"Server '{server_name}' will override it.[/yellow]"
                )
            self._tool_registry[tool_name] = server_name
            # Store the schema: input_schema is the JSON Schema for arguments
            # (MCP SDK v2 renamed inputSchema → input_schema)
            self._tool_schemas[tool_name] = {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.input_schema,
                "server": server_name,
            }

        console.print(
            f"[green]✓[/green] Connected to [bold]{server_name}[/bold] | "
            f"Tools: {[t.name for t in tools_response]}"
        )

    async def disconnect_all(self):
        """Cleanly close all server connections."""
        for name, client in self._clients.items():
            try:
                await client.__aexit__(None, None, None)
            except Exception:
                pass  # Best-effort cleanup

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """
        Route a tool call to the correct server.

        Args:
            tool_name: Name of the tool to call.
            arguments: Dict of argument name -> value.

        Returns:
            The parsed result from the tool.

        Raises:
            ValueError: If the tool name is not registered.
        """
        if tool_name not in self._tool_registry:
            raise ValueError(
                f"Tool '{tool_name}' not found. "
                f"Available tools: {list(self._tool_registry.keys())}"
            )

        server_name = self._tool_registry[tool_name]
        client = self._clients[server_name]

        # call_tool sends a tools/call request over the protocol
        result = await client.call_tool(tool_name, arguments)

        # FastMCP returns a list of content blocks; extract the first text value
        return _extract_result(result)

    def list_tools(self) -> list[dict]:
        """Return all discovered tool schemas (for the LLM or for display)."""
        return list(self._tool_schemas.values())

    def get_tool_schema(self, tool_name: str) -> dict | None:
        """Return schema for a specific tool."""
        return self._tool_schemas.get(tool_name)

    def print_tool_table(self):
        """Pretty-print all discovered tools using Rich."""
        table = Table(title="Discovered MCP Tools", show_header=True, header_style="bold cyan")
        table.add_column("Tool Name", style="green")
        table.add_column("Server")
        table.add_column("Description")

        for schema in self._tool_schemas.values():
            table.add_row(
                schema["name"],
                schema["server"],
                (schema["description"] or "")[:80],
            )

        console.print(table)


def _extract_result(result) -> Any:
    """
    Extract a usable value from an MCP CallToolResult.

    MCP responses are structured as a list of content blocks, each with a 'type'.
    For text results, we parse JSON if possible (since tools often return dicts),
    otherwise return the raw string.
    """
    import json

    if not result:
        return None

    # result is a list of content objects from fastmcp
    contents = result if isinstance(result, list) else [result]

    for content in contents:
        if hasattr(content, "text"):
            text = content.text
            # Try to parse as JSON for structured results
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return text
        elif hasattr(content, "data"):
            return content.data

    return str(result)


# ---------------------------------------------------------------------------
# Milestone 1 smoke test — run this file directly to verify server + hub
# ---------------------------------------------------------------------------
async def _m1_smoke_test():
    """
    Quick validation for Milestone 1:
    1. Connect hub to policy server
    2. Print discovered tools
    3. Call get_policy("POL-001")
    4. Call check_coverage("POL-001", "collision")
    5. Call check_coverage("POL-002", "collision") — lapsed policy
    """
    console.rule("[bold blue]Milestone 1 — Policy Server Smoke Test[/bold blue]")

    hub = MCPHub()
    hub.register_server(
        name="policy-system",
        script_path=BASE_DIR / "servers" / "policy_server.py",
    )

    console.print("\n[bold]Step 1: Connecting to policy server...[/bold]")
    await hub.connect_all()

    console.print("\n[bold]Step 2: Discovered tools:[/bold]")
    hub.print_tool_table()

    console.print("\n[bold]Step 3: get_policy('POL-001')[/bold]")
    policy = await hub.call_tool("get_policy", {"policy_id": "POL-001"})
    console.print_json(data=policy)

    console.print("\n[bold]Step 4: check_coverage('POL-001', 'collision')[/bold]")
    cov = await hub.call_tool("check_coverage", {"policy_id": "POL-001", "peril": "collision"})
    console.print_json(data=cov)

    console.print("\n[bold]Step 5: check_coverage('POL-002', 'collision') — lapsed policy[/bold]")
    cov2 = await hub.call_tool("check_coverage", {"policy_id": "POL-002", "peril": "collision"})
    console.print_json(data=cov2)

    await hub.disconnect_all()
    console.rule("[green]Milestone 1 PASSED ✓[/green]")


if __name__ == "__main__":
    asyncio.run(_m1_smoke_test())
