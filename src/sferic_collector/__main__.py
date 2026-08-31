"""Entry point. Runs the MCP server over stdio (for `claude mcp add` and
other local MCP clients).
"""

from __future__ import annotations

from .server import build_server


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
