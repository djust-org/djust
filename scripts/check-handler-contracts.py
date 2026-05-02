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
    "clear_notifications",
    "copy_code",
    "date_next_month",
    "date_prev_month",
    "date_select",
    "dismiss_toast",
    "grid_cell_edit",
    "kanban_add_card",
    "kanban_add_column",
    "kanban_move",
    "load_more",
    "mark_notification_read",
    "page_next",
    "page_prev",
    "toggle_notifications",
    "toggle_split_menu",
    "tree_expand",
    "tree_select",
}


def extract_event_defaults(path: Path) -> dict:
    """Return {default_value: [site]} for every kwarg ending in _event."""
    tree = ast.parse(path.read_text())
    out = {}
    for node in ast.walk(tree):
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


def extract_handler_names(path: Path) -> set:
    """Return every method def name in this file."""
    tree = ast.parse(path.read_text())
    return {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef)
    }


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

    exit_code, msg = run(tag_files, handler_files)
    print(msg)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
