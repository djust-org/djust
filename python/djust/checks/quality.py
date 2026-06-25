"""djust system checks — code-quality checks (Q0xx) — AST-based.

Split from the former monolithic ``checks.py`` (#1822). No behavior change.
"""

import ast
import logging
import os
import re
from typing import Any

from django.core.checks import CheckMessage, register

import djust.checks as _root
from djust.checks.utils import (
    DjustInfo,
    DjustWarning,
    _has_noqa,
    _iter_js_files,
    _iter_python_files,
    _parse_python_file,
)

logger = logging.getLogger(__name__)


def _collect_patch_param_names(class_node: ast.ClassDef, original_source: str) -> set[str]:
    """Collect URL param names from self.patch() calls in a class.

    Inspects all methods in the class for ``self.patch(...)`` calls and extracts
    param names from dict-style (``{"tab": ...}``) and query-string-style
    (``"?tab=value"`` or f-strings) arguments.

    Returns a set of lowercase param name strings, e.g. ``{"tab", "view"}``.
    """
    param_names = set()
    for method in class_node.body:
        if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call_node in ast.walk(method):
            if not isinstance(call_node, ast.Call):
                continue
            func = call_node.func
            # Match self.patch(...)
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "patch"
                and isinstance(func.value, ast.Name)
                and func.value.id == "self"
            ):
                continue
            if not call_node.args:
                continue
            first_arg = call_node.args[0]
            # Dict-style: self.patch({"tab": val, ...})
            if isinstance(first_arg, ast.Dict):
                for key in first_arg.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        param_names.add(key.value)
            # Constant string: self.patch("?tab=value")
            elif isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                for m in re.finditer(r"[?&](\w+)=", first_arg.value):
                    param_names.add(m.group(1))
            # f-string: self.patch(f"?tab={val}") — extract from the source segment
            elif isinstance(first_arg, ast.JoinedStr):
                seg = ast.get_source_segment(original_source, first_arg) or ""
                for m in re.finditer(r"[?&](\w+)=", seg):
                    param_names.add(m.group(1))
    return param_names


def _nav_var_matches_patch_params(var_name: str, param_names: set[str]) -> bool:
    """Return True if *var_name* plausibly corresponds to a URL param in *param_names*.

    Checks direct match and simple prefix/suffix stripping so that, for example,
    ``active_tab`` matches a param named ``tab``.
    """
    if var_name in param_names:
        return True
    # Strip common adjective prefixes: active_, current_, selected_
    base = var_name.split("_")[-1]  # "active_tab" → "tab", "current_view" → "view"
    return base in param_names


def _check_navigation_state_in_handlers(errors: list[CheckMessage]) -> None:
    """Q010: Heuristic to detect event handlers that set navigation state without patching.

    Lower-confidence check that looks for @event_handler methods whose body primarily
    sets navigation state variables (self.active_view, self.current_tab, etc.) without
    using patch() or handle_params(). Suggests converting to dj-patch pattern.

    To reduce false positives, Q010 only fires when the class already uses
    ``self.patch()`` with URL params somewhere, AND the nav variable name matches
    one of those param names.  Variables that merely sound like navigation but are
    not URL params (e.g. ``self.active_tab`` for CSS toggling) are therefore
    silently skipped.

    This is INFO level as it's a heuristic and may have false positives.
    """
    app_dirs = _root._get_project_app_dirs()  # type: ignore[attr-defined]  # _root.* is dynamic re-export (patch-by-path; #1822 split)
    if not app_dirs:
        return

    # Navigation state variable patterns
    NAV_STATE_VARS = re.compile(
        r"self\.(active_view|current_tab|selected_page|current_section|active_tab|selected_view)"
    )

    for filepath in _iter_python_files(app_dirs):
        tree, source_lines = _parse_python_file(filepath)
        if tree is None:
            continue

        # Read the original source for ast.get_source_segment
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
                original_source = fh.read()
        except OSError:
            continue

        relpath = os.path.relpath(filepath)

        # Look for classes that might be LiveViews
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue

            # Cross-reference: collect URL param names from self.patch() calls in
            # this class.  Only flag variables whose names appear in this set so
            # we avoid false positives for nav-sounding names that are not URL state.
            patch_params = _collect_patch_param_names(node, original_source)

            # Check each method in the class
            for item in node.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue

                # Skip non-event-handler methods
                if not any(
                    isinstance(deco, ast.Name)
                    and deco.id == "event_handler"
                    or isinstance(deco, ast.Call)
                    and isinstance(deco.func, ast.Name)
                    and deco.func.id == "event_handler"
                    for deco in item.decorator_list
                ):
                    continue

                # Check if the method body sets navigation state
                method_source = ast.get_source_segment(original_source, item) or ""
                nav_match = NAV_STATE_VARS.search(method_source)

                if not nav_match:
                    continue

                var_name = nav_match.group(1)

                # Only flag when the variable name is confirmed to be a URL param
                # used elsewhere via self.patch() — prevents false positives for
                # nav-sounding names that are not URL state.
                if not patch_params or not _nav_var_matches_patch_params(var_name, patch_params):
                    continue

                # Check if it uses patch() or handle_params (indicators it's already using patching)
                has_patch_pattern = "patch(" in method_source or "handle_params" in method_source

                if not has_patch_pattern:
                    # Check for noqa on function definition line or any decorator line
                    has_noqa_suppression = False
                    for deco in item.decorator_list:
                        if _has_noqa(source_lines, deco.lineno, "Q010"):
                            has_noqa_suppression = True
                            break
                    if not has_noqa_suppression and _has_noqa(source_lines, item.lineno, "Q010"):
                        has_noqa_suppression = True

                    if not has_noqa_suppression:
                        errors.append(
                            DjustInfo(
                                "%s:%d -- Event handler '%s.%s()' sets %s without using patch(). "
                                "Consider using dj-patch for URL updates."
                                % (relpath, item.lineno, node.name, item.name, var_name),
                                hint=(
                                    "Navigation state changes are better handled with dj-patch + handle_params(). "
                                    "This enables URL updates and back-button support. "
                                    'Example: Replace dj-click with dj-patch="?tab=value" and handle in handle_params().'
                                ),
                                id="djust.Q010",
                                fix_hint=(
                                    "Convert method `%s` to use handle_params() instead of direct state "
                                    "assignment at line %d in `%s`."
                                    % (item.name, item.lineno, relpath)
                                ),
                                file_path=filepath,
                                line_number=item.lineno,
                            )
                        )


# ---------------------------------------------------------------------------
# Code Quality checks (Q0xx)
# ---------------------------------------------------------------------------


@register("djust")
def check_code_quality(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """AST-based code quality checks on project Python files."""
    errors: list[CheckMessage] = []
    app_dirs = _root._get_project_app_dirs()  # type: ignore[attr-defined]  # _root.* is dynamic re-export (patch-by-path; #1822 split)
    if not app_dirs:
        return errors

    for filepath in _iter_python_files(app_dirs):
        tree, source_lines = _parse_python_file(filepath)
        if tree is None:
            continue

        relpath = os.path.relpath(filepath)

        for node in ast.walk(tree):
            # Q001 -- print() in production code
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id == "print":
                    if not _has_noqa(source_lines, node.lineno, "Q001"):
                        errors.append(
                            DjustInfo(
                                "%s:%d -- print() statement found." % (relpath, node.lineno),
                                hint="Use logging module instead of print() in production code.",
                                id="djust.Q001",
                                fix_hint=(
                                    "Replace `print(...)` with `logger.info(...)` "
                                    "at line %d in `%s`." % (node.lineno, relpath)
                                ),
                                file_path=filepath,
                                line_number=node.lineno,
                            )
                        )

            # Q002 -- f-string in logger calls
            if isinstance(node, ast.Call):
                func = node.func
                attr_name = None
                if isinstance(func, ast.Attribute):
                    attr_name = func.attr
                if attr_name in ("debug", "info", "warning", "error", "critical", "exception"):
                    # Check if receiver looks like a logger
                    receiver = func.value if isinstance(func, ast.Attribute) else None
                    is_logger = False
                    if isinstance(receiver, ast.Name) and receiver.id in (
                        "logger",
                        "log",
                        "logging",
                    ):
                        is_logger = True
                    elif isinstance(receiver, ast.Attribute) and receiver.attr in ("logger", "log"):
                        is_logger = True
                    if is_logger and node.args:
                        if isinstance(node.args[0], ast.JoinedStr) and not _has_noqa(
                            source_lines, node.lineno, "Q002"
                        ):
                            errors.append(
                                DjustWarning(
                                    "%s:%d -- f-string in logger call." % (relpath, node.lineno),
                                    hint="Use %%s-style formatting: logger.%s('message %%s', value)"
                                    % attr_name,
                                    id="djust.Q002",
                                    fix_hint=(
                                        "Replace f-string with %%s-style formatting in "
                                        "logger.%s() call at line %d in `%s`."
                                        % (attr_name, node.lineno, relpath)
                                    ),
                                    file_path=filepath,
                                    line_number=node.lineno,
                                )
                            )

    # Q003 -- console.log without djustDebug guard in JS
    for filepath in _iter_js_files(app_dirs):
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            continue

        relpath = os.path.relpath(filepath)
        for i, line in enumerate(lines, 1):
            if "console.log" in line and "djustDebug" not in line:
                # Check previous line for guard
                prev_line = lines[i - 2].strip() if i >= 2 else ""
                if "djustDebug" not in prev_line:
                    errors.append(
                        DjustInfo(
                            "%s:%d -- console.log without djustDebug guard." % (relpath, i),
                            hint="Wrap in: if (globalThis.djustDebug) { console.log(...); }",
                            id="djust.Q003",
                            fix_hint=(
                                "Wrap `console.log(...)` with "
                                "`if (globalThis.djustDebug) { ... }` "
                                "at line %d in `%s`." % (i, relpath)
                            ),
                            file_path=filepath,
                            line_number=i,
                        )
                    )

    # Q010 -- event handlers that set navigation state without patching (heuristic)
    _check_navigation_state_in_handlers(errors)

    return errors
