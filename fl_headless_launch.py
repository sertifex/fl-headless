"""Starts the fl-headless MCP server (written by install.py; also ships in the release)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))  # the code
import fl_headless_mcp  # noqa: E402

fl_headless_mcp.main()
