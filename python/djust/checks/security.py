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

            # S004 -- LiveView subclass whose authorization is applied via
            # @method_decorator(<auth>, name="dispatch"). The WS/SSE mount
            # path authorizes through check_view_auth (not dispatch()), so a
            # decorated dispatch is enforced on the HTTP GET but NOT over
            # WebSocket. (Django auth MIXINS are auto-honored by
            # check_view_auth; only the decorated/overridden-dispatch pattern
            # is un-portable and flagged here.) See finding #14.
            if isinstance(node, ast.ClassDef) and _is_liveview_subclass(node):
                for deco in node.decorator_list:
                    if _is_dispatch_auth_method_decorator(deco) and not _has_noqa(
                        source_lines, deco.lineno, "S004"
                    ):
                        errors.append(
                            DjustError(
                                "%s:%d -- LiveView %r gates auth via "
                                "@method_decorator(..., name='dispatch'); this is "
                                "NOT enforced over WebSocket (only on the HTTP GET)."
                                % (relpath, node.lineno, node.name),
                                hint=(
                                    "LiveView authorization must use djust's "
                                    "login_required / permission_required class "
                                    "attributes or a check_permissions() method "
                                    "(honored on every transport), or a Django "
                                    "auth mixin (LoginRequiredMixin / "
                                    "PermissionRequiredMixin / UserPassesTestMixin, "
                                    "auto-honored). A decorated dispatch() is "
                                    "HTTP-only."
                                ),
                                id="djust.S004",
                                fix_hint=(
                                    "On `%s` (line %d in `%s`), replace the "
                                    "@method_decorator(..., name='dispatch') with "
                                    "`login_required = True` / "
                                    "`permission_required = ...` / a "
                                    "`check_permissions(self, request)` method, or "
                                    "subclass a Django auth mixin."
                                    % (node.name, node.lineno, relpath)
                                ),
                                file_path=filepath,
                                line_number=node.lineno,
                            )
                        )

                # Also flag an overridden ``def dispatch`` that performs auth
                # itself (e.g. ``if not request.user.is_authenticated: raise
                # PermissionDenied``). check_view_auth never calls dispatch(),
                # so such auth is HTTP-only too.
                auth_dispatch = _liveview_auth_dispatch_method(node)
                if auth_dispatch is not None and not _has_noqa(
                    source_lines, auth_dispatch.lineno, "S004"
                ):
                    errors.append(
                        DjustError(
                            "%s:%d -- LiveView %r overrides dispatch() with auth "
                            "logic; this is NOT enforced over WebSocket (only on "
                            "the HTTP GET)." % (relpath, auth_dispatch.lineno, node.name),
                            hint=(
                                "LiveView authorization must use djust's "
                                "login_required / permission_required class "
                                "attributes or a check_permissions() method "
                                "(honored on every transport), or a Django auth "
                                "mixin (LoginRequiredMixin / PermissionRequiredMixin "
                                "/ UserPassesTestMixin, auto-honored). Auth inside an "
                                "overridden dispatch() is HTTP-only."
                            ),
                            id="djust.S004",
                            fix_hint=(
                                "On `%s` (line %d in `%s`), move the dispatch() auth "
                                "into `login_required` / `permission_required` / a "
                                "`check_permissions(self, request)` method, or "
                                "subclass a Django auth mixin."
                                % (node.name, auth_dispatch.lineno, relpath)
                            ),
                            file_path=filepath,
                            line_number=auth_dispatch.lineno,
                        )
                    )

    return errors


_AUTH_REFERENCE_NAMES = frozenset(
    {
        "PermissionDenied",
        "is_authenticated",
        "is_staff",
        "is_superuser",
        "has_perm",
        "has_perms",
        "HttpResponseForbidden",
        "redirect_to_login",
        "login_required",
        "permission_required",
        "check_permissions",
    }
)


def _liveview_auth_dispatch_method(node: "ast.ClassDef"):
    """Return the overridden ``dispatch`` method node if it performs auth.

    Heuristic: the class defines ``def dispatch``/``async def dispatch`` whose
    body references an auth-ish name (``PermissionDenied``, ``is_authenticated``,
    ``has_perm``, ``HttpResponseForbidden``, …). Returns the FunctionDef node
    (for line reporting) or ``None``. Avoids flagging a benign dispatch override
    that does no authorization.
    """
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "dispatch":
            for sub in ast.walk(item):
                ref = None
                if isinstance(sub, ast.Name):
                    ref = sub.id
                elif isinstance(sub, ast.Attribute):
                    ref = sub.attr
                if ref in _AUTH_REFERENCE_NAMES:
                    return item
    return None


def _is_liveview_subclass(node: "ast.ClassDef") -> bool:
    """Heuristic: does this ClassDef directly list a ``*LiveView`` base?

    AST can't resolve cross-module inheritance, so this matches on the base
    name (``LiveView`` or a ``X.LiveView`` attribute). Sufficient for the
    common ``class FooView(LiveView)`` / ``class FooView(SomeMixin, LiveView)``
    shapes the S004 warning targets.
    """
    for base in node.bases:
        name = None
        if isinstance(base, ast.Name):
            name = base.id
        elif isinstance(base, ast.Attribute):
            name = base.attr
        if name and name.endswith("LiveView"):
            return True
    return False


_AUTH_DECORATOR_NAMES = frozenset(
    {
        "login_required",
        "permission_required",
        "user_passes_test",
        "staff_member_required",
        "active_account_required",
    }
)


def _is_dispatch_auth_method_decorator(deco) -> bool:
    """True if ``deco`` is ``@method_decorator(<auth-decorator>, name="dispatch")``."""
    if not isinstance(deco, ast.Call):
        return False
    fn = deco.func
    fn_name = (
        fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else None)
    )
    if fn_name != "method_decorator":
        return False
    # Must target dispatch: name="dispatch" kwarg, or "dispatch" as 2nd positional.
    targets_dispatch = any(
        kw.arg == "name" and isinstance(kw.value, ast.Constant) and kw.value.value == "dispatch"
        for kw in deco.keywords
    ) or (
        len(deco.args) >= 2
        and isinstance(deco.args[1], ast.Constant)
        and deco.args[1].value == "dispatch"
    )
    if not targets_dispatch or not deco.args:
        return False
    # First positional arg is the wrapped decorator: a Name (login_required) or
    # a Call (permission_required("x"), user_passes_test(fn), ...).
    inner = deco.args[0]
    inner_name = None
    if isinstance(inner, ast.Name):
        inner_name = inner.id
    elif isinstance(inner, ast.Call):
        ifn = inner.func
        inner_name = (
            ifn.id
            if isinstance(ifn, ast.Name)
            else (ifn.attr if isinstance(ifn, ast.Attribute) else None)
        )
    elif isinstance(inner, ast.Attribute):
        inner_name = inner.attr
    return inner_name in _AUTH_DECORATOR_NAMES
