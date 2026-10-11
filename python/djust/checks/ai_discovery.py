"""Low-cost capability discovery hints (ADR-043 D1)."""

import sys
from typing import Any
from django.core.checks import CheckMessage, register

_I001_STATE = {"emitted": False}
_UI_REPLACEMENTS: dict[str, tuple[str, ...]] = {
    "X101": ("data_table", "data_grid", "infinite_scroll"),
    "X102": ("combobox", "rich_select"),
    "X103": ("sheet", "modal"),
    "X104": ("server_toast_container", "toast_container", "page_alert"),
}


def _suppressed(check_id: str) -> bool:
    from django.conf import settings
    from .utils import _is_check_suppressed

    # Django filters silenced messages at command output time. Respect the same
    # setting here too, so a silenced I001 does not consume the once flag.
    return check_id in getattr(settings, "SILENCED_SYSTEM_CHECKS", []) or _is_check_suppressed(
        check_id
    )


@register("djust")
def check_ai_discovery_hint(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    if not (len(sys.argv) > 1 and sys.argv[1] == "check"):
        return []
    from djust.ai_discovery.agents import ai_hints_disabled, is_agent_environment

    if ai_hints_disabled() or not is_agent_environment():
        return []
    if _suppressed("djust.I001") or _I001_STATE["emitted"]:
        return []
    from djust.ai_discovery.agents import available_discovery_commands, format_discovery_block
    from .utils import DjustInfo

    _I001_STATE["emitted"] = True
    return [
        DjustInfo(
            "Capability discovery for AI agents",
            hint="Before writing templates or views, see what djust already provides:\n"
            + format_discovery_block(available_discovery_commands()),
            id="djust.I001",
        )
    ]


def _user_liveview_count() -> int:
    from djust.live_view import LiveView
    from .components import _routed_liveview_classes
    from .utils import _is_framework_internal_class, _walk_subclasses

    found = set(_routed_liveview_classes()) | set(_walk_subclasses(LiveView))
    return sum(
        1
        for cls in found
        if not _is_framework_internal_class(cls) and cls.__dict__.get("abstract") is not True
    )


@register("djust")
def check_theming_not_installed(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    if _suppressed("djust.I002"):
        return []
    from django.apps import apps

    if apps.is_installed("djust.theming"):
        return []
    count = _user_liveview_count()
    if not count:
        return []
    from .utils import DjustInfo

    return [
        DjustInfo(
            f"{count} LiveView(s) found, but djust.theming is not in INSTALLED_APPS.",
            hint='Add "djust.theming" to INSTALLED_APPS and {% load theme_tags %}{% theme_head %} to '
            "your base template for design tokens, light/dark mode and presets; see "
            "`python manage.py djust_ai inventory`. Silence with DJUST_CONFIG = "
            "{'suppress_checks': ['I002']}.",
            id="djust.I002",
        )
    ]


@register("djust")
def check_hand_rolled_ui(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    if _suppressed("djust.I003"):
        return []
    from djust.ai_discovery.inventory import app_status, read_project_ui_summary

    state = read_project_ui_summary()
    if state.status != "fresh" or state.payload is None:
        return []
    counts = {code: int(state.payload["counts"].get(code, 0)) for code in _UI_REPLACEMENTS}
    if not sum(counts.values()):
        return []
    summary = ", ".join(
        f"{code}: {count} ({' / '.join(_UI_REPLACEMENTS[code])})"
        for code, count in counts.items()
        if count
    )
    components = next(app for app in app_status() if app["app"] == "djust.components")
    hint = ""
    if not components["available"]:
        hint = 'pip install "djust[components]", then add "djust.components" to INSTALLED_APPS. '
    elif not components["enabled"]:
        hint = 'Add "djust.components" to INSTALLED_APPS. '
    hint += (
        "Use "
        + ", ".join(
            name for code, count in counts.items() if count for name in _UI_REPLACEMENTS[code]
        )
        + "; run `python manage.py djust_audit --ast` for locations and "
        '`python manage.py djust_ai suggest "<intent>"` for snippets.'
    )
    from .utils import DjustInfo

    return [DjustInfo("Hand-rolled UI in the last audit: " + summary, hint=hint, id="djust.I003")]
