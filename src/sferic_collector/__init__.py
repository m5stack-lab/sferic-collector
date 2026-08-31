"""sferic fleet collector — the companion MCP server (ADR-0003).

Host-side control plane: stations connect to it, MCP clients (agents) connect
to it, it holds fleet state and the strike/flash store and dispatches
`<toolset>.<verb>` calls to a target station's headless interface.
"""

__version__ = "0.1.0"
