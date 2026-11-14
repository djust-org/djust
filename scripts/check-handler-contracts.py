"""Cross-reference tag-emit defaults against handler-name registry.

Catches bugs of class #1275 at pre-push (or pre-commit) time.
Used by ``make check-handler-contracts`` and the pre-push hook.

Exit 0 = all defaults match a handler. Exit 1 = mismatches found.
"""

import argparse
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Events intentionally designed for user LiveView handlers — no framework
# mixin provides a default handler.  Adding a new app-level event here is
# the expected workflow; failing to add it will cause the linter to
# surface a possible #1275-class (stale/typo'd emit default) mismatch.
_APP_LEVEL_EVENTS = {
    # Block tags read their defaults with ``kw.get("x_event", "default")``;
    # the scan covers those since #4037. The defaults below are the app's
    # actions (approve, export, reorder ...) with no component to answer them.
    "accept_cookies",
    "add_row",
    "approve",
    "clear_notifications",
    "close_export",
    "close_lightbox",
    "close_palette",
    "copy_code",
    "counter_update",
    "dashboard_move",
    "dashboard_resize",
    "data_loaded",
    "date_next_month",
    "date_prev_month",
    "date_select",
    "dismiss_alert",
    "dismiss_announcement",
    "dismiss_toast",
    "export",
    "filter_change",
    "filter_clear",
    "grid_cell_edit",
    "import_data",
    "inline_edit",
    "kanban_add_card",
    "kanban_add_column",
    "kanban_move",
    "lightbox_navigate",
    "load_more",
    "mark_notification_read",
    "mark_read",
    "new_message",
    "page_next",
    "page_prev",
    "palette_search",
    "rate_response",
    "reject",
    "remove_row",
    "reorder",
    "save_crop",
    "save_signature",
    "section_changed",
    "select_model",
    "send",
    "set_step",
    "stream_chunk",
    "toggle_expand",
    "toggle_list",
    "toggle_menu",
    "toggle_nav",
    "toggle_notifications",
    "toggle_preview",
    "toggle_sidebar",
    "toggle_split_menu",
    "tree_expand",
    "tree_select",
    "upload_file",
}


def extract_event_defaults(path: Path) -> dict:
    """Return {default_value: [site]} for every kwarg ending in _event (or
    named ``event``): a function default, or a ``kw.get("x_event", "default")``
    read of a resolved tag-argument dict (#4037: the block tags, such as
    ``{% modal %}`` and ``{% sheet %}``, read their defaults that way)."""
    tree = ast.parse(path.read_text())
    out = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in ("kw", "kwargs")
            and len(node.args) == 2
            and all(isinstance(a, ast.Constant) for a in node.args)
        ):
            key, default = node.args[0].value, node.args[1].value
            if (
                isinstance(key, str)
                and (key == "event" or key.endswith("_event"))
                and isinstance(default, str)
                and default
            ):
                out.setdefault(default, []).append(f"{path.name}:{node.lineno} (kw.get)")
            continue
        if not isinstance(node, ast.FunctionDef):
            continue
        args_with_defaults = node.args.args[-len(node.args.defaults):]
        for arg, default in zip(args_with_defaults, node.args.defaults):
            if arg.arg.endswith("_event") and isinstance(default, ast.Constant):
                if isinstance(default.value, str) and default.value:
                    out.setdefault(default.value, []).append(
                        f"{path.name}:{node.lineno}"
                    )
        for arg, default in zip(
            node.args.kwonlyargs, node.args.kw_defaults
        ):
            if (
                default
                and arg.arg.endswith("_event")
                and isinstance(default, ast.Constant)
                and isinstance(default.value, str)
                and default.value
            ):
                out.setdefault(default.value, []).append(
                    f"{path.name}:{node.lineno} (kwonly)"
                )
    return out


def _str_constants(node) -> list:
    """The string constants of a str / tuple / list / set literal."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return [
            e.value
            for e in node.elts
            if isinstance(e, ast.Constant) and isinstance(e.value, str)
        ]
    return []


def extract_handler_names(path: Path) -> set:
    """Return every method def name in this file, plus the events a
    component's ``class Meta`` answers (``event = ...`` and
    ``events = (...)``, #4037): the descriptors register those on the view."""
    tree = ast.parse(path.read_text())
    names = {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Meta":
            for stmt in node.body:
                if not isinstance(stmt, ast.Assign):
                    continue
                for target in stmt.targets:
                    if isinstance(target, ast.Name) and target.id in ("event", "events"):
                        names.update(_str_constants(stmt.value))
    return names


def run(tag_files, handler_files, app_level_events=None):
    """Core logic exposed for testing.

    Returns (exit_code, message).
    """
    if app_level_events is None:
        app_level_events = _APP_LEVEL_EVENTS

    emit_defaults = {}
    for f in tag_files:
        for name, sites in extract_event_defaults(f).items():
            emit_defaults.setdefault(name, []).extend(sites)

    all_handlers = set()
    for f in handler_files:
        all_handlers |= extract_handler_names(f)

    mismatches = [
        (name, sites)
        for name, sites in emit_defaults.items()
        if name not in all_handlers and name not in app_level_events
    ]

    if mismatches:
        lines = [
            f"Found {len(mismatches)} tag-emit defaults "
            f"with no matching handler:"
        ]
        for name, sites in mismatches:
            lines.append(
                f"  '{name}' — emitted at {', '.join(sites)}; "
                f"no handler in mixins/ or components/"
            )
        return 1, "\n".join(lines)

    app_count = sum(1 for n in emit_defaults if n in app_level_events)
    return 0, (
        f"OK — {len(emit_defaults)} tag-emit defaults checked "
        f"({len(emit_defaults) - app_count} framework, "
        f"{app_count} app-level) against {len(all_handlers)} handler names"
    )


def build_arg_parser():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument(
        "--tag-files", nargs="*", action="extend",
        default=None,
        help="Tag/module files to scan for _event defaults",
    )
    p.add_argument(
        "--mixin-dir",
        default=None,
        help="Directory of mixin files providing handler methods",
    )
    p.add_argument(
        "--component-files", nargs="*", action="extend",
        default=None,
        help="Additional component files providing handler methods",
    )
    return p


def main(argv=None):
    args = build_arg_parser().parse_args(argv)

    tag_files = [Path(p) for p in (args.tag_files or [])]
    if not tag_files:
        tag_files = [
            ROOT / "python/djust/components/templatetags/djust_components.py",
        ]

    handler_files = []
    if args.mixin_dir:
        handler_files.extend(sorted(Path(args.mixin_dir).glob("*.py")))
    else:
        handler_files.extend(
            sorted((ROOT / "python/djust/components/mixins").glob("*.py"))
        )
    if args.component_files:
        handler_files.extend(Path(p) for p in args.component_files)
    else:
        handler_files.extend([
            ROOT / "python/djust/components/base.py",
            ROOT / "python/djust/components/rust_handlers.py",
        ])
        # The descriptors answer their tags' default events (#4037).
        handler_files.extend(
            sorted((ROOT / "python/djust/components/descriptors").glob("*.py"))
        )

    exit_code, msg = run(tag_files, handler_files)
    print(msg)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
