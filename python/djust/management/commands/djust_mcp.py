"""
Management command to start the djust MCP server.

Starts an MCP server over stdio that provides AI assistants with structured
access to project introspection, system checks, and code generation tools.

Usage:
    python manage.py djust_mcp

Configure in Claude Code:
    claude mcp add --transport stdio djust -- python manage.py djust_mcp

Or in .mcp.json:
    {
        "mcpServers": {
            "djust": {
                "type": "stdio",
                "command": "python",
                "args": ["manage.py", "djust_mcp"]
            }
        }
    }
"""

import logging
import sys

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Start the djust MCP server for AI assistant integration"

    def handle(self, *args, **options):
        # Suppress Django's default logging to stderr — MCP uses stdio
        logging.getLogger("django").setLevel(logging.WARNING)
        logging.getLogger("djust").setLevel(logging.WARNING)

        try:
            from djust.mcp.server import create_server
        except ImportError:
            self.stderr.write(
                self.style.ERROR(
                    "Error: mcp package not installed. " "Install with: pip install 'mcp[cli]'"
                )
            )
            sys.exit(1)

        # Mark Django as ready for the server
        from djust.mcp import server as mcp_server_module

        mcp_server_module._django_ready = True

        self.stderr.write(self.style.SUCCESS("Starting djust MCP server (stdio transport)..."))

        server = create_server()
        server.run(transport="stdio")
