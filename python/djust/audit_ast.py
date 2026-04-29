"""
AST-based security anti-pattern scanner for ``djust_audit --ast`` (#660).

This module walks user Python source code looking for five specific
anti-patterns that the 2026-04-10 pentest found either as live
vulnerabilities or as near-misses in a downstream consumer's application. Each
checker is intentionally narrow — false positives are worse than false
negatives for a linter that runs on every push.

Findings use stable codes ``djust.X001``–``djust.X099``. (The ``X``
prefix is separate from the ``P0xx`` permissions-document codes from
#657 — ``X`` stands for "e**X**amine", the anti-pattern scanner.)

* **X001 — IDOR** — ``Model.objects.get(pk=...)`` inside a view-like
  class using a URL param, without a sibling ``.filter(...)`` or auth
  call that scopes access to the request user.
* **X002 — Unauthenticated state-mutating handler** — an
  ``@event_handler`` that performs a DB write (``create``, ``update``,
  ``delete``, ``save``) without any permission check (class-level
  ``login_required``, ``permission_required``, a
  ``check_permissions`` override, ``@permission_required``, or
  ``@login_required``).
* **X003 — SQL string formatting** — ``.raw()`` / ``.extra()`` /
  ``cursor.execute()`` passed an f-string, a ``.format()`` call, or a
  ``%`` binary-op expression.
* **X004 — Open redirect** — ``HttpResponseRedirect(...)`` /
  ``redirect(...)`` fed directly from ``request.GET`` / ``request.POST``
  without an ``url_has_allowed_host_and_scheme`` /
  ``is_safe_url`` guard in the same function.
* **X005 — Unsafe ``mark_safe``** — ``mark_safe(...)`` / ``SafeString(...)``
  wrapping an f-string, a ``.format()`` call, or a ``%`` binary-op — the
  exact pattern that produces an injection when the interpolated value
  is user-controlled.
* **X006 / X007 — Template |safe / autoescape off** — a light regex
  scan of ``.html`` files that flags ``{{ var|safe }}`` and
  ``{% autoescape off %}`` blocks for human review.

Suppression: add ``# djust: noqa XNNN`` on the offending line (or bare
``# djust: noqa`` to suppress any djust.X finding on that line).

Integration: ``djust_audit --ast`` turns the scanner on; findings share
the same ``--strict`` exit-code semantics as ``--permissions`` and
``--live``. The scanner has zero runtime dependencies beyond the Python
stdlib.

See issue #660.
"""

from __future__ import annotations

import ast
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Finding model
# ---------------------------------------------------------------------------


AST_FINDING_CODES: Dict[str, Tuple[str, str]] = {
    "X001": ("error", "Possible IDOR — object lookup by URL param without auth scoping"),
    "X002": (
        "warning",
        "Event handler mutates state without a permission check",
    ),
    "X003": ("error", "SQL string formatting in raw()/extra()/execute() — SQLi risk"),
    "X004": ("error", "Open redirect — request data fed to redirect without is_safe_url"),
    "X005": ("error", "mark_safe() with interpolated value — XSS risk"),
    "X006": ("warning", "Template uses |safe on a view variable"),
    "X007": ("warning", "Template uses {% autoescape off %}"),
}


@dataclass
class ASTFinding:
    """A single AST scanner finding."""

    code: str
    severity: str
    message: str
    path: str
    lineno: int
    col: int = 0
    details: Optional[str] = None

    @classmethod
    def make(
        cls,
        code: str,
        path: str,
        lineno: int,
        col: int = 0,
        details: Optional[str] = None,
        message: Optional[str] = None,
    ) -> "ASTFinding":
        severity, default_message = AST_FINDING_CODES.get(code, ("error", code))
        return cls(
            code=code,
            severity=severity,
            message=message or default_message,
            path=path,
            lineno=lineno,
            col=col,
            details=details,
        )

    def format_line(self) -> str:
        prefix = {"error": "ERROR", "warning": "WARN", "info": "INFO"}.get(
            self.severity, self.severity.upper()
        )
        line = f"{prefix} [djust.{self.code}] {self.path}:{self.lineno}:{self.col} {self.message}"
        if self.details:
            line += f" ({self.details})"
        return line

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "lineno": self.lineno,
            "col": self.col,
            "details": self.details,
        }


@dataclass
class ASTAuditReport:
    """Aggregate result of an ``--ast`` scan."""

    findings: List[ASTFinding] = field(default_factory=list)
    files_scanned: int = 0
    files_skipped: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def errors(self) -> List[ASTFinding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> List[ASTFinding]:
        return [f for f in self.findings if f.severity == "warning"]

    @property
    def infos(self) -> List[ASTFinding]:
        return [f for f in self.findings if f.severity == "info"]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": "ast",
            "files_scanned": self.files_scanned,
            "files_skipped": [{"path": p, "reason": r} for p, r in self.files_skipped],
            "summary": {
                "errors": len(self.errors),
                "warnings": len(self.warnings),
                "info": len(self.infos),
            },
            "findings": [f.to_dict() for f in self.findings],
        }


# ---------------------------------------------------------------------------
# Source helpers
# ---------------------------------------------------------------------------


_NOQA_RE = re.compile(r"#\s*djust\s*:\s*noqa(?:\s*[:\s]\s*([A-Za-z0-9, ]+))?", re.IGNORECASE)


def _read_source_lines(path: str) -> List[str]:
    """Read file into 1-indexed lines list ([""] + file lines)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return [""] + fh.read().splitlines()
    except OSError:
        return [""]


def _is_suppressed(source_lines: Sequence[str], lineno: int, code: str) -> bool:
    """Return True if ``# djust: noqa [CODE]`` appears on ``lineno``."""
    if lineno < 1 or lineno >= len(source_lines):
        return False
    line = source_lines[lineno]
    match = _NOQA_RE.search(line)
    if not match:
        return False
    codes = match.group(1)
    if not codes:
        return True  # bare noqa suppresses everything
    wanted = {c.strip().upper() for c in codes.split(",") if c.strip()}
    return code.upper() in wanted


def _dotted_name(node: ast.AST) -> Optional[str]:
    """Collapse an ``ast.Attribute`` chain into ``'a.b.c'`` (or ``None``)."""
    parts: List[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return None


def _call_target_name(node: ast.Call) -> Optional[str]:
    """Return ``'foo'`` for ``foo(...)`` or ``'a.b.c'`` for ``a.b.c(...)``."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return _dotted_name(node.func)
    return None


def _is_user_input_expr(node: ast.AST) -> bool:
    """Heuristic: does ``node`` ultimately read from the HTTP request?

    Matches:
      - ``request.GET[...]`` / ``request.GET.get(...)`` / ``request.POST.get(...)``
      - ``request.headers.get(...)`` / ``request.COOKIES[...]``
      - ``self.kwargs[...]`` / ``self.kwargs.get(...)``
      - ``kwargs.get('pk')`` / ``kwargs['pk']`` at handler scope
      - ``self.request.GET[...]``
    """
    # Unwrap Call wrapping (e.g. request.GET.get("next") -> look at func's value)
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute):
            return _is_user_input_expr(node.func.value)
        if isinstance(node.func, ast.Name):
            return False
        return False

    if isinstance(node, ast.Subscript):
        return _is_user_input_expr(node.value)

    if isinstance(node, ast.Attribute):
        dotted = _dotted_name(node) or ""
        parts = dotted.split(".")
        # request.GET / request.POST / request.FILES / request.COOKIES / request.headers
        if (
            len(parts) >= 2
            and parts[-2] == "request"
            and parts[-1]
            in {
                "GET",
                "POST",
                "FILES",
                "COOKIES",
                "headers",
                "META",
                "body",
            }
        ):
            return True
        if (
            dotted.endswith("self.kwargs")
            or dotted.endswith("self.request.GET")
            or dotted.endswith("self.request.POST")
        ):
            return True
        # Walk into nested attribute
        return _is_user_input_expr(node.value)

    if isinstance(node, ast.Name):
        return node.id in {"request"}

    return False


def _is_format_string(node: ast.AST) -> bool:
    """Return True if ``node`` is an f-string, ``.format()`` call, or ``%`` formatting."""
    if isinstance(node, ast.JoinedStr):
        # f-string with at least one FormattedValue is dangerous; a plain
        # JoinedStr of only Constants is effectively a static string.
        return any(isinstance(part, ast.FormattedValue) for part in node.values)
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute) and node.func.attr == "format":
            return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        # "..." % (x, y)  — only concerning when LHS is a string literal
        left = node.left
        if isinstance(left, ast.Constant) and isinstance(left.value, str):
            return True
    return False


# ---------------------------------------------------------------------------
# Base checker
# ---------------------------------------------------------------------------


class _FileContext:
    """Per-file state passed to every checker."""

    def __init__(self, path: str, tree: ast.AST, source_lines: Sequence[str]):
        self.path = path
        self.tree = tree
        self.source_lines = source_lines
        self.findings: List[ASTFinding] = []

    def emit(
        self,
        code: str,
        node: ast.AST,
        details: Optional[str] = None,
    ) -> None:
        lineno = getattr(node, "lineno", 0) or 0
        col = getattr(node, "col_offset", 0) or 0
        if _is_suppressed(self.source_lines, lineno, code):
            return
        self.findings.append(
            ASTFinding.make(
                code=code,
                path=self.path,
                lineno=lineno,
                col=col,
                details=details,
            )
        )


# ---------------------------------------------------------------------------
# X001 — IDOR
# ---------------------------------------------------------------------------


_AUTH_SCOPE_KEYWORDS = {
    "user",
    "owner",
    "created_by",
    "author",
    "tenant",
    "tenant_id",
    "organization",
    "organization_id",
    "team",
    "team_id",
    "workspace",
}


def _function_has_auth_scope(func: ast.AST) -> bool:
    """Return True if ``func`` contains a ``.filter(user=...)`` /
    ``check_permissions`` / ``has_perm`` call, or any ORM call using one
    of the auth-scoping kwargs.

    We also consider the function "scoped" if it calls ``get_object_or_404``
    with a model that already has ``user=``/``owner=`` kwargs applied.
    """
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            # Any call passing one of the scope kwargs
            for kw in node.keywords or []:
                if kw.arg and kw.arg in _AUTH_SCOPE_KEYWORDS:
                    return True
            # Explicit permission calls
            name = _call_target_name(node) or ""
            if name.endswith("check_permissions") or name.endswith("has_perm"):
                return True
            if name in {"permission_required", "login_required"}:
                return True
            # Objects manager filter with scoping
            if isinstance(node.func, ast.Attribute) and node.func.attr == "filter":
                for kw in node.keywords or []:
                    if kw.arg and kw.arg in _AUTH_SCOPE_KEYWORDS:
                        return True
    return False


def _class_has_permission_marker(cls: ast.ClassDef) -> bool:
    """Detect class-level auth markers set in the class body.

    Matches assignments like::

        login_required = True
        permission_required = "app.view_thing"
        permission_required = ["app.view_thing"]
    """
    for stmt in cls.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id in {
                    "login_required",
                    "permission_required",
                    "permissions_required",
                }:
                    if isinstance(stmt.value, ast.Constant) and stmt.value.value in (
                        False,
                        None,
                        "",
                    ):
                        continue
                    return True
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            if stmt.target.id in {"login_required", "permission_required"}:
                return True
    return False


def _iter_class_bases(cls: ast.ClassDef) -> Iterable[str]:
    for base in cls.bases:
        name = _dotted_name(base) if not isinstance(base, ast.Name) else base.id
        if name:
            yield name


def _looks_like_detail_view(cls: ast.ClassDef) -> bool:
    if cls.name.endswith("DetailView") or cls.name.endswith("EditView"):
        return True
    for base_name in _iter_class_bases(cls):
        tail = base_name.rsplit(".", 1)[-1]
        if tail in {"LiveView", "DetailView", "UpdateView", "DeleteView", "FormView"}:
            return True
    return False


def _get_call_has_user_input_pk(node: ast.Call) -> bool:
    """Return True if ``node`` is ``.get(pk=X, ...)`` or ``.get(id=X)`` where
    X is clearly request-derived.
    """
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "get":
        return False
    for kw in node.keywords or []:
        if kw.arg in {"pk", "id"} or (kw.arg and kw.arg.endswith("_id")):
            if _is_user_input_expr(kw.value):
                return True
            # Unwrap simple Name -> check if name came from kwargs
            if isinstance(kw.value, ast.Name) and kw.value.id in {
                "pk",
                "id",
                "object_id",
            }:
                return True
    # Positional: .get(pk) where pk is a URL param
    for arg in node.args:
        if _is_user_input_expr(arg):
            return True
    return False


def _check_idor(ctx: _FileContext) -> None:
    for cls in ast.walk(ctx.tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        if not _looks_like_detail_view(cls):
            continue
        has_class_level_auth = _class_has_permission_marker(cls)
        for item in cls.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            method_has_scope = _function_has_auth_scope(item)
            if has_class_level_auth and method_has_scope:
                # Both scoped -- still flag nothing; class-level auth alone
                # isn't enough for IDOR, but combined it usually is.
                continue
            # Walk for .get(pk=...) with user input
            for call in ast.walk(item):
                if not isinstance(call, ast.Call):
                    continue
                if not _get_call_has_user_input_pk(call):
                    continue
                # If method has sibling scope, it's fine
                if method_has_scope:
                    continue
                # If class has permission_required AND method does not mutate,
                # we'd still fail because permission_required gates access to
                # the view, not to the specific object.
                ctx.emit(
                    "X001",
                    call,
                    details=(
                        f"{cls.name}.{item.name}: add .filter(owner=request.user) "
                        f"or override check_permissions() to scope by owner"
                    ),
                )


# ---------------------------------------------------------------------------
# X002 — Unauthenticated state-mutating handler
# ---------------------------------------------------------------------------


_WRITE_METHODS = {"create", "update", "delete", "bulk_create", "bulk_update", "save"}


def _decorator_name(dec: ast.AST) -> str:
    if isinstance(dec, ast.Call):
        return _call_target_name(dec) or ""
    if isinstance(dec, ast.Name):
        return dec.id
    if isinstance(dec, ast.Attribute):
        return _dotted_name(dec) or dec.attr
    return ""


def _is_event_handler(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for dec in func.decorator_list:
        name = _decorator_name(dec)
        tail = name.rsplit(".", 1)[-1] if name else ""
        if tail == "event_handler":
            return True
    return False


def _handler_has_permission_decorator(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    for dec in func.decorator_list:
        name = _decorator_name(dec)
        tail = name.rsplit(".", 1)[-1] if name else ""
        if tail in {
            "permission_required",
            "login_required",
            "user_passes_test",
            "staff_member_required",
            "superuser_required",
        }:
            return True
    return False


def _function_mutates_state(func: ast.AST) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in _WRITE_METHODS:
                return True
    return False


def _check_unprotected_mutating_handler(ctx: _FileContext) -> None:
    for cls in ast.walk(ctx.tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        class_has_auth = _class_has_permission_marker(cls)
        for item in cls.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _is_event_handler(item):
                continue
            if not _function_mutates_state(item):
                continue
            if class_has_auth or _handler_has_permission_decorator(item):
                continue
            ctx.emit(
                "X002",
                item,
                details=(
                    f"{cls.name}.{item.name}: add @permission_required('...'), "
                    f"set login_required=True on the view, or override "
                    f"check_permissions()"
                ),
            )


# ---------------------------------------------------------------------------
# X003 — SQL string formatting
# ---------------------------------------------------------------------------


_SQL_SINK_NAMES = {"raw", "extra", "execute", "executemany"}


def _check_sql_formatting(ctx: _FileContext) -> None:
    for node in ast.walk(ctx.tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _SQL_SINK_NAMES:
            continue
        if not node.args:
            continue
        first = node.args[0]
        if _is_format_string(first):
            ctx.emit(
                "X003",
                node,
                details=f".{node.func.attr}(...) with interpolated string",
            )


# ---------------------------------------------------------------------------
# X004 — Open redirect
# ---------------------------------------------------------------------------


_REDIRECT_FUNCS = {"HttpResponseRedirect", "redirect"}
_REDIRECT_GUARD_NAMES = {
    "url_has_allowed_host_and_scheme",
    "is_safe_url",
}


def _function_guards_redirect(func: ast.AST) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            name = _call_target_name(node) or ""
            tail = name.rsplit(".", 1)[-1]
            if tail in _REDIRECT_GUARD_NAMES:
                return True
    return False


def _check_open_redirect(ctx: _FileContext) -> None:
    for parent in ast.walk(ctx.tree):
        if not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call in ast.walk(parent):
            if not isinstance(call, ast.Call):
                continue
            name = _call_target_name(call) or ""
            tail = name.rsplit(".", 1)[-1]
            if tail not in _REDIRECT_FUNCS:
                continue
            if not call.args:
                continue
            first = call.args[0]
            if not _is_user_input_expr(first):
                continue
            if _function_guards_redirect(parent):
                continue
            ctx.emit(
                "X004",
                call,
                details=(
                    f"{parent.name}: wrap the target in "
                    f"url_has_allowed_host_and_scheme() before redirecting"
                ),
            )


# ---------------------------------------------------------------------------
# X005 — Unsafe mark_safe
# ---------------------------------------------------------------------------


_MARK_SAFE_SINKS = {"mark_safe", "SafeString"}


def _check_mark_safe(ctx: _FileContext) -> None:
    for node in ast.walk(ctx.tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_target_name(node) or ""
        tail = name.rsplit(".", 1)[-1]
        if tail not in _MARK_SAFE_SINKS:
            continue
        if not node.args:
            continue
        first = node.args[0]
        if _is_format_string(first):
            ctx.emit(
                "X005",
                node,
                details=f"{tail}(...) wraps an interpolated string",
            )


# ---------------------------------------------------------------------------
# X006 / X007 — Template scanners (regex-based, not AST)
# ---------------------------------------------------------------------------


_SAFE_FILTER_RE = re.compile(r"\{\{\s*([a-zA-Z_][\w\.]*)\s*\|\s*safe\b")
_AUTOESCAPE_OFF_RE = re.compile(r"\{%\s*autoescape\s+off\s*%\}")
_SAFE_SUPPRESSION_RE = re.compile(
    r"\{#\s*djust\s*:\s*noqa(?:\s*[:\s]\s*([A-Za-z0-9, ]+))?\s*#\}",
    re.IGNORECASE,
)


def _template_suppressed(line: str, code: str) -> bool:
    match = _SAFE_SUPPRESSION_RE.search(line)
    if not match:
        return False
    codes = match.group(1)
    if not codes:
        return True
    wanted = {c.strip().upper() for c in codes.split(",") if c.strip()}
    return code.upper() in wanted


def _scan_template_file(path: str) -> List[ASTFinding]:
    findings: List[ASTFinding] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            source = fh.read()
    except OSError:
        return findings
    for lineno, line in enumerate(source.splitlines(), start=1):
        for match in _SAFE_FILTER_RE.finditer(line):
            if _template_suppressed(line, "X006"):
                continue
            var = match.group(1)
            findings.append(
                ASTFinding.make(
                    "X006",
                    path=path,
                    lineno=lineno,
                    col=match.start(),
                    details=f"{{{{ {var}|safe }}}}",
                )
            )
        if _AUTOESCAPE_OFF_RE.search(line):
            if _template_suppressed(line, "X007"):
                continue
            findings.append(
                ASTFinding.make(
                    "X007",
                    path=path,
                    lineno=lineno,
                    col=0,
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Public API — scan a file, scan a project
# ---------------------------------------------------------------------------


ALL_CHECKERS = (
    _check_idor,
    _check_unprotected_mutating_handler,
    _check_sql_formatting,
    _check_open_redirect,
    _check_mark_safe,
)


_SKIP_DIRS: Set[str] = {
    ".git",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    "build",
    "dist",
    "target",
    ".pipeline-state",
    "migrations",
}


def scan_python_source(path: str, source: Optional[str] = None) -> List[ASTFinding]:
    """Scan a single Python file and return its findings.

    ``source`` is optional — if omitted the file is read from disk. Useful
    for testing and for the CLI to pass already-read source.
    """
    if source is None:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                source = fh.read()
        except OSError as exc:
            logger.debug("Cannot read %s: %s", path, exc)
            return []
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        return []
    lines = [""] + source.splitlines()
    ctx = _FileContext(path=path, tree=tree, source_lines=lines)
    for checker in ALL_CHECKERS:
        checker(ctx)
    return ctx.findings


def _iter_project_files(
    root: str,
    include_templates: bool = True,
) -> Iterable[str]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)
            elif include_templates and name.endswith(".html"):
                yield os.path.join(dirpath, name)


def run_ast_audit(
    root: str = ".",
    include_templates: bool = True,
    exclude: Optional[Sequence[str]] = None,
) -> ASTAuditReport:
    """Walk ``root`` and run every checker on every eligible file.

    ``exclude`` is a sequence of path prefixes (relative or absolute) to
    omit from the scan — used by the CLI ``--exclude`` flag and by tests.
    """
    report = ASTAuditReport()
    exclude_normalised: List[str] = []
    if exclude:
        exclude_normalised = [os.path.normpath(e) for e in exclude]
    root_abs = os.path.abspath(root)
    for path in _iter_project_files(root_abs, include_templates=include_templates):
        rel = os.path.relpath(path, root_abs)
        if any(rel == e or rel.startswith(e + os.sep) for e in exclude_normalised):
            continue
        report.files_scanned += 1
        try:
            if path.endswith(".py"):
                report.findings.extend(scan_python_source(path))
            else:
                report.findings.extend(_scan_template_file(path))
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("Scanner failed on %s: %s", path, exc)
            report.files_skipped.append((path, str(exc)))
    # Sort for stable output
    report.findings.sort(key=lambda f: (f.path, f.lineno, f.code))
    return report
