"""djust system checks — security checks (S0xx) — AST-based.

Split from the former monolithic ``checks.py`` (#1822). No behavior change.
"""

import ast
import logging
import os

from django.core.checks import register

import djust.checks as _root
from djust.checks.utils import (
    DjustError,
    DjustWarning,
    _has_noqa,
    _iter_python_files,
    _parse_python_file,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Security checks (S0xx) -- AST-based
# ---------------------------------------------------------------------------


@register("djust")
def check_security(app_configs, **kwargs):
    """AST-based security checks on project Python files."""
    errors = []
    app_dirs = _root._get_project_app_dirs()
    if not app_dirs:
        return errors

    for filepath in _iter_python_files(app_dirs):
        tree, source_lines = _parse_python_file(filepath)
        if tree is None:
            continue

        relpath = os.path.relpath(filepath)

        for node in ast.walk(tree):
            # S001 -- mark_safe(f'...') with interpolated values
            if isinstance(node, ast.Call):
                func = node.func
                func_name = None
                if isinstance(func, ast.Name):
                    func_name = func.id
                elif isinstance(func, ast.Attribute):
                    func_name = func.attr

                if func_name == "mark_safe" and node.args:
                    arg = node.args[0]
                    if isinstance(arg, ast.JoinedStr) and not _has_noqa(
                        source_lines, node.lineno, "S001"
                    ):
                        errors.append(
                            DjustError(
                                "%s:%d -- mark_safe() with f-string is a XSS risk."
                                % (relpath, node.lineno),
                                hint="Use format_html() instead of mark_safe(f'...').",
                                id="djust.S001",
                                fix_hint=(
                                    "Replace `mark_safe(f'...')` with `format_html()` "
                                    "at line %d in `%s`." % (node.lineno, relpath)
                                ),
                                file_path=filepath,
                                line_number=node.lineno,
                            )
                        )

            # S002 -- @csrf_exempt without justification comment
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for deco in node.decorator_list:
                    deco_name = None
                    if isinstance(deco, ast.Name):
                        deco_name = deco.id
                    elif isinstance(deco, ast.Attribute):
                        deco_name = deco.attr
                    if deco_name == "csrf_exempt":
                        # Check for a comment/docstring justification
                        has_justification = False
                        if (
                            node.body
                            and isinstance(node.body[0], ast.Expr)
                            and isinstance(node.body[0].value, ast.Constant)
                        ):
                            doc = node.body[0].value.value
                            if "csrf" in doc.lower():
                                has_justification = True
                        if not has_justification and not _has_noqa(
                            source_lines, deco.lineno, "S002"
                        ):
                            errors.append(
                                DjustWarning(
                                    "%s:%d -- @csrf_exempt without justification."
                                    % (relpath, node.lineno),
                                    hint="Add a docstring explaining why CSRF protection is disabled.",
                                    id="djust.S002",
                                    fix_hint=(
                                        "Add a docstring mentioning 'csrf' to function "
                                        "`%s` at line %d in `%s`."
                                        % (node.name, node.lineno, relpath)
                                    ),
                                    file_path=filepath,
                                    line_number=node.lineno,
                                )
                            )

            # S003 -- bare except: pass
            if isinstance(node, ast.ExceptHandler):
                if node.type is None:  # bare except
                    if (
                        len(node.body) == 1
                        and isinstance(node.body[0], ast.Pass)
                        and not _has_noqa(source_lines, node.lineno, "S003")
                    ):
                        errors.append(
                            DjustWarning(
                                "%s:%d -- bare 'except: pass' swallows all exceptions."
                                % (relpath, node.lineno),
                                hint="Catch a specific exception and log it, or re-raise.",
                                id="djust.S003",
                                fix_hint=(
                                    "Replace bare `except: pass` with a specific exception "
                                    "type (e.g., `except Exception:`) and add logging, "
                                    "at line %d in `%s`." % (node.lineno, relpath)
                                ),
                                file_path=filepath,
                                line_number=node.lineno,
                            )
                        )

    return errors
