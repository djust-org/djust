"""Agent shell detection and the shared discovery command list (stdlib only)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import os

AGENT_ENV_VARS: tuple[str, ...] = (
    "DJUST_AGENT",
    "CLAUDECODE",
    "CURSOR_AGENT",
    "CODEX_SANDBOX",
    "CODEX_SANDBOX_NETWORK_DISABLED",
    "CODEX_THREAD_ID",
)
_FALSY = frozenset({"", "0", "false", "no", "off"})


def is_agent_environment(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return any(env.get(var, "").lower() not in _FALSY for var in AGENT_ENV_VARS)


def ai_hints_disabled(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return env.get("DJUST_AI_HINTS", "").lower() in _FALSY - {""}


@dataclass(frozen=True)
class DiscoveryCommand:
    command: str
    invocation: str
    purpose: str


DISCOVERY_COMMANDS: tuple[DiscoveryCommand, ...] = (
    DiscoveryCommand(
        "djust_ai",
        "python manage.py djust_ai inventory --json",
        "what's installed/enabled here, component catalog, theme",
    ),
    DiscoveryCommand(
        "djust_ai",
        'python manage.py djust_ai suggest "<intent>"',
        "intent -> components + tag + snippet",
    ),
    DiscoveryCommand(
        "djust_ai",
        "python manage.py djust_ai manifest",
        "one llms.txt-style doc (directives, components, theming, best practices)",
    ),
    DiscoveryCommand("djust_audit", "python manage.py djust_audit --ast", "security + UI rules"),
    DiscoveryCommand(
        "djust_mcp",
        "python manage.py djust_mcp",
        "MCP server (list_ui_components / get_ui_component for the component catalog)",
    ),
)


def available_discovery_commands() -> list[DiscoveryCommand]:
    from django.core.management import get_commands

    commands = get_commands()
    return [c for c in DISCOVERY_COMMANDS if c.command in commands]


def format_discovery_block(commands: Sequence[DiscoveryCommand]) -> str:
    width = max((len(c.invocation) for c in commands), default=0)
    return "\n".join(f"  {c.invocation:<{width}}   # {c.purpose}" for c in commands)
