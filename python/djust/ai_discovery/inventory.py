"""Installed project capabilities and cached UI audit state."""

from dataclasses import dataclass
import importlib.util
import logging
import os
from typing import Any, Literal


@dataclass(frozen=True)
class UISummaryState:
    """Shared immutable cache state; kept lightweight for missing-cache checks.

    Re-exported by audit_ui, which owns the reader and cache schema.
    """

    status: Literal["missing", "invalid", "stale", "fresh"]
    path: str
    payload: dict[str, Any] | None
    reason: str = ""
    stale_paths: tuple[str, ...] = ()


logger = logging.getLogger(__name__)

INVENTORY_VERSION = 1


@dataclass(frozen=True)
class BundledApp:
    app: str
    purpose: str
    extra: str
    requires: tuple[str, ...]


BUNDLED_APPS: tuple[BundledApp, ...] = (
    BundledApp(
        "djust.components",
        "~190 template-tag UI components (tables, forms, overlays, feedback)",
        "djust[components]",
        ("markdown", "nh3"),
    ),
    BundledApp(
        "djust.theming",
        "design systems + color presets via CSS tokens; {% theme_head %}",
        "djust[theming]",
        (),
    ),
    BundledApp("djust.auth", "account backends + page kit (ADR-039)", "djust[auth]", ()),
    BundledApp("djust.admin_ext", "LiveView-powered admin extensions", "djust[admin]", ()),
)


def app_status() -> list[dict[str, Any]]:
    from django.apps import apps

    result = []
    for app in BUNDLED_APPS:
        missing = [module for module in app.requires if importlib.util.find_spec(module) is None]
        result.append(
            {
                "app": app.app,
                "purpose": app.purpose,
                "extra": app.extra,
                "available": not missing,
                "enabled": apps.is_installed(app.app),
                "missing_requirements": missing,
            }
        )
    return result


def theming_status() -> dict[str, Any]:
    from django.apps import apps
    from django.core.exceptions import ImproperlyConfigured
    from djust.theming._config import get_theme_config
    from djust.theming._builtin_presets import THEME_PRESETS

    enabled = apps.is_installed("djust.theming")
    try:
        config = get_theme_config()
    except ImproperlyConfigured as exc:
        return {"enabled": enabled, "error": str(exc)}
    return {
        "enabled": enabled,
        **{key: config[key] for key in ("theme", "preset", "default_mode")},
        "presets_available": len(THEME_PRESETS),
    }


def project_classes() -> dict[str, Any]:
    from djust.schema import get_project_schema

    from django.core.exceptions import ImproperlyConfigured
    from django.urls import get_resolver

    try:
        # URL-only views are not necessarily imported by app initialization.
        get_resolver().url_patterns
    except (ImproperlyConfigured, ImportError, AttributeError):
        # Projects without a usable URLconf can still inventory loaded classes.
        logger.debug("UI inventory URLconf unavailable")
    schema = get_project_schema()
    return {
        key: [
            {
                "name": item["class"].removeprefix(item["module"] + "."),
                "module": item["module"],
                "template": item.get("template"),
            }
            for item in schema[key]
        ]
        for key in ("views", "components")
    }


def ui_summary_root_candidates() -> list[str]:
    from django.conf import settings

    roots = []
    base = getattr(settings, "BASE_DIR", None)
    if base is not None:
        roots.append(os.path.realpath(base))
    cwd = os.path.realpath(os.getcwd())
    if cwd not in roots:
        roots.append(cwd)
    return roots


def read_project_ui_summary() -> "UISummaryState":
    roots = ui_summary_root_candidates()
    # No audit module import until a cache file exists. A missing state uses the
    # same immutable record without running the reader or any template scan.
    for root in roots:
        path = os.path.join(root, ".djust", "audit-ui.json")
        directory = os.path.dirname(path)
        invalid_directory = os.path.lexists(directory) and (
            os.path.islink(directory) or not os.path.isdir(directory)
        )
        if os.path.lexists(path) or invalid_directory:
            from djust.audit_ui import read_ui_summary

            state = read_ui_summary(root)
            if state.status != "missing":
                return state
    return UISummaryState(
        "missing", os.path.join(roots[0], ".djust", "audit-ui.json"), None, "no cache"
    )


def catalog_summary() -> dict[str, Any]:
    from djust.ai_discovery.catalog import categories, load_catalog

    return {
        "count": len(load_catalog()),
        "categories": [
            {
                "slug": slug,
                "label": label,
                "components": [
                    {
                        "name": e.name,
                        "component_kind": e.kind,
                        "label": e.label,
                        "purpose": e.purpose,
                    }
                    for e in entries
                ],
            }
            for slug, label, entries in categories()
        ],
    }


def build_inventory() -> dict[str, Any]:
    from djust import __version__
    from djust.ai_discovery.agents import DISCOVERY_COMMANDS

    state = read_project_ui_summary()
    payload = state.payload or {}
    return {
        "version": INVENTORY_VERSION,
        "kind": "inventory",
        "djust_version": __version__,
        "apps": app_status(),
        "theming": theming_status(),
        "components": catalog_summary(),
        "project": project_classes(),
        "ui_audit": {
            "status": state.status,
            "path": state.path,
            "reason": state.reason,
            "generated_at": payload.get("generated_at"),
            "counts": payload.get("counts"),
            "total": payload.get("total"),
            "stale_paths": list(state.stale_paths),
        },
        "discovery": [
            {"invocation": c.invocation, "purpose": c.purpose} for c in DISCOVERY_COMMANDS
        ],
    }


def render_inventory(data: dict[str, Any]) -> str:
    from djust.ai_discovery.agents import DISCOVERY_COMMANDS, format_discovery_block

    lines = [f"djust {data['djust_version']}", "", "Bundled apps"]
    for app in data["apps"]:
        if app["enabled"]:
            status = "enabled"
        elif app["available"]:
            status = f'available, not enabled (add "{app["app"]}" to INSTALLED_APPS)'
        else:
            status = f'not available (pip install "{app["extra"]}")'
        lines.append(f"  {app['app']}: {status} — {app['purpose']}")
    theme = data["theming"]
    lines.extend(
        [
            "",
            "Theme: "
            + (
                theme["error"]
                if "error" in theme
                else f"{theme['theme']} / {theme['preset']}, mode {theme['default_mode']} (enabled: {theme['enabled']})"
            ),
            "",
            f"Components ({data['components']['count']})",
        ]
    )
    for category in data["components"]["categories"]:
        lines.append(category["label"])
        lines.extend(f"  {e['name']} — {e['purpose']}" for e in category["components"])
    lines.extend(["", "Project"])
    for key in ("views", "components"):
        items = data["project"][key]
        lines.append(
            f"  {key}: {len(items)}"
            + (" — " + ", ".join(i["name"] for i in items) if items else "")
        )
    audit = data["ui_audit"]
    if audit["status"] == "fresh":
        status = (
            "fresh: "
            + ", ".join(f"{k} {v}" for k, v in (audit["counts"] or {}).items())
            + f" (generated {audit['generated_at']})"
        )
    elif audit["status"] == "missing":
        status = "none: run python manage.py djust_audit --ast"
    else:
        status = f"{audit['status']}: {audit['reason']}; rerun python manage.py djust_audit --ast"
    lines.extend(["", "UI audit: " + status, "", "Discovery"])
    # Remove terminal controls from every derived field, retaining our own line breaks.
    content = "\n".join(
        "".join(char for char in line if ord(char) >= 32 and not 127 <= ord(char) <= 159)
        for line in lines
    )
    return content + "\n" + format_discovery_block(DISCOVERY_COMMANDS)
