"""djust system checks — security checks (S0xx).

Mostly AST-based (S001-S009); S011 is a template-source scan (inline-script /
CSP). Split from the former monolithic ``checks.py`` (#1822).
"""

import ast
import logging
import os
import re
from collections.abc import Iterator
from typing import Any, Optional, Union

from django.core.checks import CheckMessage, register

import djust.checks as _root
from djust.checks.utils import (
    DjustError,
    DjustWarning,
    _get_template_dirs,
    _has_noqa,
    _is_check_suppressed,
    _iter_python_files,
    _iter_template_files,
    _parse_python_file,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Security checks (S0xx) -- AST-based
# ---------------------------------------------------------------------------


@register("djust")
def check_security(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """AST-based security checks on project Python files."""
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
                            if isinstance(doc, str) and "csrf" in doc.lower():
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

                # S009 (#1854) -- a LiveView that declares VIEW-level auth
                # but exposes PUBLIC @event_handler methods with NO per-handler
                # gate. A user who passes the view's mount-time auth could call
                # a sensitive handler that needed finer (per-action)
                # authorization. Conservative by design: only fires when the
                # view *clearly* has view-auth AND a public, mutating-looking
                # handler with no gate (no @permission_required on the handler,
                # no class-level check_permissions/has_object_permission).
                if not _is_check_suppressed("djust.S009"):
                    for handler in _ungated_event_handlers(node):
                        # Honor a "noqa S009" comment on the def line OR any of
                        # the handler's decorator lines (the author may annotate
                        # the @event_handler line rather than the def).
                        noqa_lines = [handler.lineno] + [d.lineno for d in handler.decorator_list]
                        if any(_has_noqa(source_lines, ln, "S009") for ln in noqa_lines):
                            continue
                        errors.append(
                            DjustWarning(
                                "%s:%d -- LiveView %r declares view-level auth "
                                "but exposes the public @event_handler %r with "
                                "no per-handler authorization gate. A user who "
                                "passes the view's mount auth can call this "
                                "handler." % (relpath, handler.lineno, node.name, handler.name),
                                hint=(
                                    "View-level auth (login_required / "
                                    "permission_required / a Django auth mixin) "
                                    "only gates the mount; it does NOT gate "
                                    "individual events. If this handler needs "
                                    "finer authorization, add "
                                    "@permission_required(...) to it (or a "
                                    "check_permissions() override that inspects "
                                    "the event), or rename it with a leading "
                                    "underscore if it is not meant to be a "
                                    "client-callable handler. If the view-level "
                                    "auth is sufficient for every handler, "
                                    "suppress via DJUST_CONFIG "
                                    "{'suppress_checks': ['S009']} or a "
                                    "`# noqa: S009` on the handler."
                                ),
                                id="djust.S009",
                                fix_hint=(
                                    "Add @permission_required('app.perm') above "
                                    "@event_handler on `%s` (line %d in `%s`), "
                                    "or rename it `_%s` if it is not a "
                                    "client-callable event handler."
                                    % (handler.name, handler.lineno, relpath, handler.name)
                                ),
                                file_path=filepath,
                                line_number=handler.lineno,
                            )
                        )

    return errors


# ---------------------------------------------------------------------------
# S008 (#15) -- upload `client_name` interpolated into a storage path/key.
#
# `UploadEntry.client_name` is the RAW, attacker-controlled original filename.
# Using it directly in a storage destination is a path / object-key injection
# sink (CWE-22 / CWE-73): `../../../etc/x` raises SuspiciousFileOperation on
# FileSystemStorage (DoS) and is a *valid* key on object stores (overwrite /
# mis-place). The safe accessor is `entry.safe_client_name`.
#
# This is a Python-AST check (S007 is the TEMPLATE-side sibling for the
# `{{ ...client_name|safe }}` stored-XSS sink). It is deliberately PRECISE to
# keep false positives near zero: it only flags `client_name` (NOT
# `safe_client_name`) attribute access that lands as an argument to a
# `<...>.save(...)` call (Django storage) or an `os.path.join(...)` call —
# the two canonical path/key-construction sinks. Plain display interpolation
# (`f'Uploaded: {entry.client_name}'`) is NOT flagged.
# ---------------------------------------------------------------------------

# Call-func attribute names treated as path/key-construction sinks.
_PATH_SINK_ATTRS = frozenset({"save"})  # default_storage.save / storage.save


def _is_raw_client_name_attr(node: "ast.AST") -> bool:
    """True if *node* is an attribute access of ``.client_name`` (not ``safe_*``)."""
    return isinstance(node, ast.Attribute) and node.attr == "client_name"


def _joinedstr_has_raw_client_name(node: "ast.AST") -> bool:
    """True if an f-string interpolates ``<expr>.client_name`` anywhere."""
    if not isinstance(node, ast.JoinedStr):
        return False
    for value in node.values:
        if isinstance(value, ast.FormattedValue):
            for sub in ast.walk(value.value):
                if _is_raw_client_name_attr(sub):
                    return True
    return False


def _arg_uses_raw_client_name(arg: "ast.AST") -> bool:
    """True if *arg* is (or interpolates) a raw ``.client_name``."""
    if _is_raw_client_name_attr(arg):
        return True
    if _joinedstr_has_raw_client_name(arg):
        return True
    # String concatenation: 'avatars/' + entry.client_name
    if isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Add):
        return any(_is_raw_client_name_attr(s) for s in ast.walk(arg))
    return False


def _is_os_path_join(func: "ast.AST") -> bool:
    """True if *func* is ``os.path.join`` (or ``path.join`` / a bare ``join``)."""
    return isinstance(func, ast.Attribute) and func.attr == "join"


def _scan_client_name_path_sink(
    tree: ast.AST,
    source_lines: list[str],
    relpath: str,
) -> list[DjustWarning]:
    """Return DjustWarnings for raw ``client_name`` used in a path/key sink.

    Extracted as a standalone, side-effect-free function so it can be unit
    tested directly against a synthetic AST (empirical-canary discipline, #1459).
    """
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Sink 1: <...>.save(<arg>, ...) where save is a storage save.
        is_save_sink = isinstance(func, ast.Attribute) and func.attr in _PATH_SINK_ATTRS
        # Sink 2: os.path.join(..., <arg>) / path.join(...).
        is_join_sink = _is_os_path_join(func)
        if not (is_save_sink or is_join_sink):
            continue
        flagged = any(_arg_uses_raw_client_name(a) for a in node.args)
        if not flagged:
            continue
        if _has_noqa(source_lines, node.lineno, "S008"):
            continue
        findings.append(
            DjustWarning(
                "%s:%d -- upload `client_name` used in a storage path/key. "
                "`client_name` is the raw attacker-controlled original filename "
                "(path/object-key injection: CWE-22 / CWE-73)." % (relpath, node.lineno),
                hint=(
                    "Use `entry.safe_client_name` (basename-only, "
                    "traversal-neutralised) for the path/key; keep `client_name` "
                    "only for display (auto-escaped). Suppress with "
                    "DJUST_CONFIG = {'suppress_checks': ['S008']} if the value is "
                    "pre-sanitised."
                ),
                id="djust.S008",
                fix_hint=(
                    "Replace `client_name` with `safe_client_name` in the storage "
                    "path at line %d in `%s`." % (node.lineno, relpath)
                ),
                line_number=node.lineno,
            )
        )
    return findings


@register("djust")
def check_upload_client_name_path_sink(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """S008 -- raw upload ``client_name`` interpolated into a storage path/key."""
    errors: list[CheckMessage] = []
    if _is_check_suppressed("djust.S008"):
        return errors
    app_dirs = _root._get_project_app_dirs()  # type: ignore[attr-defined]  # _root.* is dynamic re-export (patch-by-path; #1822 split)
    if not app_dirs:
        return errors

    for filepath in _iter_python_files(app_dirs):
        tree, source_lines = _parse_python_file(filepath)
        if tree is None:
            continue
        relpath = os.path.relpath(filepath)
        for finding in _scan_client_name_path_sink(tree, source_lines, relpath):
            finding.file_path = filepath
            errors.append(finding)

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


def _liveview_auth_dispatch_method(
    node: "ast.ClassDef",
) -> Union[ast.FunctionDef, ast.AsyncFunctionDef, None]:
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


def _is_dispatch_auth_method_decorator(deco: ast.expr) -> bool:
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


# ---------------------------------------------------------------------------
# S009 (#1854) -- event-handler-needs-auth
# ---------------------------------------------------------------------------
#
# A LiveView that gates its *mount* (view-level auth) but exposes public
# @event_handler methods with no per-handler authorization gate. The view-auth
# only runs once at mount; individual events are dispatched afterwards without
# re-checking unless the handler itself carries @permission_required (which
# ``djust.auth.check_handler_permission`` enforces). So a user who passes the
# mount gate can call any public handler. S009 is conservative: it fires only
# when the class CLEARLY declares view-auth AND has a public, mutating-looking
# handler with no gate.

# Django auth mixins (AccessMixin family) that, in a LiveView's bases, signal
# view-level authorization.
_AUTH_MIXIN_BASE_NAMES = frozenset(
    {
        "AccessMixin",
        "LoginRequiredMixin",
        "PermissionRequiredMixin",
        "UserPassesTestMixin",
        # djust's own auth mixins (python/djust/auth.py)
        "LiveViewLoginRequiredMixin",
        "LiveViewPermissionRequiredMixin",
        "LiveViewUserPassesTestMixin",
    }
)

# Method names that, when overridden on the view, implement a PER-EVENT
# authorization gate (so S009 should NOT fire — the view gates events itself).
_EVENT_GATE_METHOD_NAMES = frozenset({"check_handler_permission", "check_event_permission"})

# Heuristic: a public handler is "sensitive"/"mutating-looking" unless its name
# is clearly a read-only accessor. We stay conservative — better to under-fire
# than to train developers to blanket-suppress. A handler is exempt (treated as
# read-only) when its name starts with one of these verbs.
_READ_ONLY_HANDLER_PREFIXES = (
    "get_",
    "load_",
    "fetch_",
    "list_",
    "show_",
    "view_",
    "search_",
    "filter_",
    "render_",
    "display_",
    "refresh_",
)


def _decorator_callable_name(deco: ast.expr) -> Optional[str]:
    """Return the simple callable name of a decorator node (Name/Call/Attribute)."""
    target = deco.func if isinstance(deco, ast.Call) else deco
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _is_event_handler_decorator(deco: ast.expr) -> bool:
    """True if ``deco`` is ``@event_handler`` / ``@event_handler(...)`` / ``@action(...)``."""
    return _decorator_callable_name(deco) in ("event_handler", "action")


def _is_permission_required_decorator(deco: ast.expr) -> bool:
    """True if ``deco`` is ``@permission_required(...)`` (the per-handler gate)."""
    return _decorator_callable_name(deco) == "permission_required"


def _class_attr_is_truthy(node: "ast.ClassDef", attr_name: str) -> bool:
    """True if the class body assigns ``attr_name = <truthy-literal>``.

    Conservative: only counts an assignment whose value is a constant we can
    statically evaluate as truthy (``True``, a non-empty string, a non-zero
    number) OR a non-empty list/tuple/set of perms. A bare-name / call value is
    treated as truthy too (e.g. ``permission_required = SOME_PERMS``) since the
    author clearly intends to gate.
    """
    for item in node.body:
        if isinstance(item, ast.Assign):
            targets = item.targets
        elif isinstance(item, ast.AnnAssign):
            targets = [item.target] if item.target is not None else []
        else:
            continue
        for tgt in targets:
            if isinstance(tgt, ast.Name) and tgt.id == attr_name:
                value = item.value
                if value is None:
                    return False
                if isinstance(value, ast.Constant):
                    return bool(value.value)
                if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
                    return len(value.elts) > 0
                # Name / Call / Attribute reference -> assume an intentional gate.
                return True
    return False


def _class_declares_view_auth(node: "ast.ClassDef") -> bool:
    """True if this LiveView clearly declares view-level (mount) authorization.

    Signals (any one suffices):
      * a truthy ``login_required`` / ``permission_required`` class attribute;
      * an overridden ``check_permissions`` method;
      * a Django/djust auth mixin (AccessMixin family) in the bases;
      * an auth-performing ``dispatch`` override or
        ``@method_decorator(login_required, name="dispatch")`` on the class.
    """
    if _class_attr_is_truthy(node, "login_required") or _class_attr_is_truthy(
        node, "permission_required"
    ):
        return True
    for item in node.body:
        if (
            isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            and item.name == "check_permissions"
        ):
            return True
    for base in node.bases:
        base_name = None
        if isinstance(base, ast.Name):
            base_name = base.id
        elif isinstance(base, ast.Attribute):
            base_name = base.attr
        if base_name in _AUTH_MIXIN_BASE_NAMES:
            return True
    if _liveview_auth_dispatch_method(node) is not None:
        return True
    for deco in node.decorator_list:
        if _is_dispatch_auth_method_decorator(deco):
            return True
    return False


def _class_gates_events(node: "ast.ClassDef") -> bool:
    """True if the class overrides a per-event authorization hook.

    A custom ``check_handler_permission`` / ``check_event_permission`` override
    means the view authorizes individual events itself, so S009 must stay
    silent (avoid false positives on views with their own event-auth layer).
    """
    for item in node.body:
        if (
            isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            and item.name in _EVENT_GATE_METHOD_NAMES
        ):
            return True
    return False


def _ungated_event_handlers(
    node: "ast.ClassDef",
) -> Iterator[Union[ast.FunctionDef, ast.AsyncFunctionDef]]:
    """Yield public ``@event_handler`` method nodes with no per-handler auth gate.

    Yields nothing unless the class declares view-level auth (so the gap is
    real) and does not gate events itself. A handler is yielded only when it is
    public (name does not start with ``_``), is decorated with
    ``@event_handler`` / ``@action``, carries no ``@permission_required``, and
    does not look read-only (see ``_READ_ONLY_HANDLER_PREFIXES``).
    """
    if not _class_declares_view_auth(node):
        return
    if _class_gates_events(node):
        return
    for item in node.body:
        if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if item.name.startswith("_"):
            continue
        decos = item.decorator_list
        if not any(_is_event_handler_decorator(d) for d in decos):
            continue
        if any(_is_permission_required_decorator(d) for d in decos):
            continue
        if item.name.startswith(_READ_ONLY_HANDLER_PREFIXES):
            continue
        yield item


# ---------------------------------------------------------------------------
# S011 (#1854 / #1848) -- inline <script> in a LiveView template without CSP
# ---------------------------------------------------------------------------
#
# An inline <script> with executable content emitted inside a LiveView template
# (dj-view / dj-root region) is doubly problematic:
#   1. CSP: shipping inline executable JS means a strict Content-Security-Policy
#      (no 'unsafe-inline', no per-script nonce) would block it.
#   2. #1848: morphdom does NOT execute <script> tags it inserts/re-creates, so
#      an inline <script> inside the dj-root silently never runs after the
#      #1610 WS-mount morph -- handlers it registers never fire, no console
#      error.
# Both point the developer to the same fix: move page JS to a static module /
# a base-template block rendered AFTER the dj-root, or add a CSP nonce.
#
# S011 fires ONLY when (a) the template is a LiveView template (has dj-view or
# dj-root), (b) it contains an inline executable <script> (NOT src-include, NOT
# a data block like type="application/json" / "text/template"), AND (c) no CSP
# is configured project-wide (no django-csp middleware, no CSP/SECURE_CSP
# setting). Low false-positive by construction; suppress with
# ``{# noqa: S011 #}`` on the script line or globally via suppress_checks.

# Matches a <script ...> OPEN tag (captures the attribute text up to '>').
_SCRIPT_OPEN_RE = re.compile(r"<script\b([^>]*)>", re.IGNORECASE)
# Extract a type="..."/'...' attribute value from a script open tag's attrs.
# Anchor with ``(?<![\w-])`` not a bare ``\b`` so ``data-type`` / a custom
# ``x-type`` attribute is not mistaken for the real ``type`` attribute (#1517).
_SCRIPT_TYPE_RE = re.compile(r"""(?<![\w-])type\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
# Detect a src= attribute (external script — has no inline body to block/skip).
# ``(?<![\w-])`` so ``data-src`` does not count as a real external src.
_SCRIPT_SRC_RE = re.compile(r"""(?<![\w-])src\s*=\s*["']""", re.IGNORECASE)
# Detect a nonce= attribute (author has opted into a CSP nonce — exempt).
# ``(?<![\w-])`` so ``data-nonce`` is not mistaken for a real CSP nonce.
_SCRIPT_NONCE_RE = re.compile(r"(?<![\w-])nonce\s*=", re.IGNORECASE)
# A real HTML OPEN tag (``<div ...>``) that bears a dj-root / dj-view attribute.
# Group 1 is the element name so we can balance the subtree. Restricted to
# real tags (``<name``) so escaped doc examples (``&lt;div dj-root&gt;``) and
# attribute references never match.
_DJ_ROOT_OPEN_TAG_RE = re.compile(
    r"<([a-zA-Z][\w-]*)\b[^>]*\bdj-(?:root|view)\b[^>]*>", re.IGNORECASE
)
# ``<pre>``/``<code>`` regions hold escaped example markup, not live DOM — any
# script inside them is documentation, never executed. Blanked before scanning
# (newlines preserved so line numbers stay accurate), mirroring
# ``_strip_verbatim_blocks`` for verbatim regions.
_PRE_CODE_BLOCK_RE = re.compile(r"<(pre|code)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)

# script "type" values that hold DATA, not executable JS (CSP-irrelevant,
# #1848-irrelevant). Anything NOT in this set (or absent / a JS type) is
# treated as executable.
_NON_EXECUTABLE_SCRIPT_TYPES = frozenset(
    {
        "application/json",
        "application/ld+json",
        "text/template",
        "text/html",
        "text/x-template",
        "text/x-handlebars-template",
        "text/markdown",
        "importmap",
        "speculationrules",
    }
)


def _script_open_is_executable_inline(attrs: str) -> bool:
    """True if a ``<script ...>`` open tag is inline executable JS.

    Excludes external scripts (``src=``), nonce-bearing scripts (author opted
    into CSP), and data blocks (``type="application/json"`` etc.). A bare
    ``<script>`` (no type) or a JS type (``text/javascript`` / ``module``) is
    executable.
    """
    if _SCRIPT_SRC_RE.search(attrs):
        return False
    if _SCRIPT_NONCE_RE.search(attrs):
        return False
    type_match = _SCRIPT_TYPE_RE.search(attrs)
    if type_match is not None:
        type_val = type_match.group(1).strip().lower()
        if type_val in _NON_EXECUTABLE_SCRIPT_TYPES:
            return False
    return True


def _blank_pre_code(content: str) -> str:
    """Replace ``<pre>``/``<code>`` block bodies with spaces (newlines kept).

    Escaped example markup inside docs lives in these blocks; scripts there are
    never executed. Keeping newlines preserves line numbers for the outer scan.
    Returns ``content`` unchanged when no such block exists (common case).
    """
    if "<pre" not in content.lower() and "<code" not in content.lower():
        return content

    def _redact(match: "re.Match") -> str:
        body = match.group(0)
        return "".join("\n" if ch == "\n" else " " for ch in body)

    return _PRE_CODE_BLOCK_RE.sub(_redact, content)


def _dj_root_ranges(content: str) -> list[tuple[int, int]]:
    """Return ``[(start, end), ...]`` char ranges of each dj-root/dj-view subtree.

    For each real opening tag bearing ``dj-root`` / ``dj-view`` we balance the
    SAME element name forward to find its matching close, so a script is only
    "inside the dj-root" if it falls within one of these ranges. This is what
    makes S011 precise: page scripts AFTER ``</div> <!-- close dj-root -->`` or
    inside a post-root ``{% block extra_scripts %}`` fall outside every range
    and are not flagged. Void/self-closing roots and unbalanced markup degrade
    safely (the range simply extends to end-of-document or is skipped).
    """
    ranges = []
    for open_match in _DJ_ROOT_OPEN_TAG_RE.finditer(content):
        tag_name = open_match.group(1).lower()
        start = open_match.start()
        # Self-closing (``<x ... />``) — empty subtree, nothing to contain.
        if open_match.group(0).rstrip().endswith("/>"):
            continue
        # Balance <tag>/</tag> from just after this opening tag.
        depth = 1
        pos = open_match.end()
        same_tag_re = re.compile(r"<(/?)" + re.escape(tag_name) + r"\b[^>]*?(/?)>", re.IGNORECASE)
        end = len(content)
        for m in same_tag_re.finditer(content, pos):
            is_close = m.group(1) == "/"
            is_self_close = m.group(2) == "/"
            if is_close:
                depth -= 1
                if depth == 0:
                    end = m.end()
                    break
            elif not is_self_close:
                depth += 1
        ranges.append((start, end))
    return ranges


def _csp_is_configured() -> bool:
    """True if the project configures a Content-Security-Policy.

    Detects (any one): django-csp middleware in ``MIDDLEWARE``; a django-csp
    ``CONTENT_SECURITY_POLICY`` setting (>=4.0); any legacy ``CSP_*`` directive
    setting; or a ``SECURE_CSP*`` setting. Conservative: any CSP signal
    silences S011 (the author has a CSP story; let them own it).
    """
    try:
        from django.conf import settings
    except Exception:  # pragma: no cover - settings always importable in practice
        return False

    middleware = getattr(settings, "MIDDLEWARE", None) or []
    for mw in middleware:
        if "csp" in str(mw).lower():
            return True

    if getattr(settings, "CONTENT_SECURITY_POLICY", None):
        return True

    for name in dir(settings):
        if name.startswith("CSP_") or name.startswith("SECURE_CSP"):
            try:
                if getattr(settings, name, None):
                    return True
            except Exception:  # pragma: no cover - defensive
                continue
    return False


@register("djust")
def check_inline_script_csp(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """S011 (#1854 / #1848): inline executable <script> in a LiveView template
    shipped without a CSP setting.

    Separate registered check (mirrors ``check_upload_client_name_path_sink``)
    so the template-scan stays out of the AST-only ``check_security`` walk.
    """
    errors: list[CheckMessage] = []
    if _is_check_suppressed("djust.S011"):
        return errors
    if _csp_is_configured():
        # The project has a CSP; inline-script handling is the author's call.
        return errors

    tpl_dirs = _get_template_dirs()
    if not tpl_dirs:
        return errors

    for filepath in _iter_template_files(tpl_dirs):
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            continue

        # Blank <pre>/<code> example markup so escaped/doc scripts never match.
        scan = _blank_pre_code(content)

        # Only flag scripts that fall INSIDE a real dj-root/dj-view subtree —
        # that is the #1610/#1848 morph region. A page script after the dj-root
        # closes (or in a post-root {% block extra_scripts %}) is outside every
        # range and correctly ignored. This is what keeps S011 precise.
        ranges = _dj_root_ranges(scan)
        if not ranges:
            continue

        relpath = os.path.relpath(filepath)
        source_lines = [""] + content.splitlines()
        for match in _SCRIPT_OPEN_RE.finditer(scan):
            if not _script_open_is_executable_inline(match.group(1)):
                continue
            pos = match.start()
            if not any(lo <= pos < hi for lo, hi in ranges):
                continue
            lineno = content[:pos].count("\n") + 1
            if _has_noqa(source_lines, lineno, "S011"):
                continue
            errors.append(
                DjustWarning(
                    "%s:%d -- inline <script> with executable JS inside a "
                    "LiveView template and no Content-Security-Policy is "
                    "configured. Inline scripts inside the dj-root are not "
                    "re-executed after djust morphs the mount HTML (#1848), "
                    "and a strict CSP would block them." % (relpath, lineno),
                    hint=(
                        "Move page JS into a static module (served from "
                        "static/, registered on DOMContentLoaded + a "
                        "MutationObserver for morph-managed regions), or into a "
                        "base-template block rendered AFTER the dj-root "
                        "</div>. If the inline script is intentional, add a CSP "
                        'nonce (nonce="{{ request.csp_nonce }}" with '
                        "django-csp) or place it outside the dj-root. Suppress "
                        "with `{# noqa: S011 #}` on the script line or via "
                        "DJUST_CONFIG {'suppress_checks': ['S011']}."
                    ),
                    id="djust.S011",
                    fix_hint=(
                        "Move the inline <script> at line %d in `%s` to a "
                        "static JS module or a post-dj-root base block, or add "
                        "a CSP nonce." % (lineno, relpath)
                    ),
                    file_path=filepath,
                    line_number=lineno,
                )
            )

    return errors
