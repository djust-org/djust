"""
Management command for static template-variable validation against LiveView contexts.

Walks every LiveView (and LiveComponent) subclass in the project, locates its
template, extracts every variable reference from the template, and reports
names that appear in the template but not in the view's declared context.

"Declared context" is the union of:
- Public class attributes set directly on the class body (non-underscore names)
- Names returned by a literal ``return {"k": ...}`` or ``return {**x, "k": ...}``
  in ``get_context_data`` (best-effort AST extraction — falls back to ignoring
  dynamic returns)
- Names bound by the template itself via ``{% for x in ... %}`` / ``{% with x=... %}``
- Names in django.conf.settings.DJUST_TEMPLATE_GLOBALS (optional project-wide
  escape hatch for globals set by context processors)
- ``user``, ``request``, ``perms``, ``csrf_token`` and the standard Django
  template defaults (always considered valid)
- ``djust``, ``is_dirty``, ``changed_fields``, ``async_pending`` and the djust
  framework-injected names

Usage::

    python manage.py djust_typecheck              # pretty terminal output
    python manage.py djust_typecheck --json       # machine-readable JSON
    python manage.py djust_typecheck --strict     # non-zero exit if any warnings
    python manage.py djust_typecheck --app myapp  # filter to one Django app
    python manage.py djust_typecheck --view MyView  # check one view only

Per-view opt-in: setting ``strict_context = True`` on a LiveView class makes
that view's output count as errors regardless of the global ``--strict`` flag.

Limitations: this is a static check, so dynamic context keys (anything computed
in a loop, conditionally assigned, or built from variables the checker can't
follow) are invisible. The command errs on the side of false positives over
false negatives — add the missing name to the class body, the globals setting,
or silence via ``# djust_typecheck: noqa`` in the template.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from django.core.management.base import BaseCommand

from djust.management._introspect import (
    app_label_for_class as _app_label,
)
from djust.management._introspect import (
    is_user_class as _is_user_class,
)
from djust.management._introspect import (
    walk_subclasses as _walk_subclasses,
)


# Names the framework always provides.
_ALWAYS_AVAILABLE: Set[str] = {
    # Django defaults
    "user",
    "request",
    "perms",
    "csrf_token",
    "messages",
    "True",
    "False",
    "None",
    "block",
    # djust-injected
    "djust",
    "is_dirty",
    "changed_fields",
    "async_pending",
    "view",
    "view_id",
    "dj_id",
    # Template-tag-introduced locals from djust tags (loop vars, etc)
    "forloop",
    "inputs_for_loop",
}


# Match `{{ expression|filter }}` — we want the leading identifier of
# the expression, before any dot or filter pipe.
_VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][\w]*)")

# Match `{% tag ... %}` so we can extract identifiers per-tag-type.
_TAG_RE = re.compile(r"\{%\s*(\w+)\s*([^%]*?)\s*%\}")

# Match a bare identifier at the start of a fragment (used inside tag args).
_IDENT_RE = re.compile(r"([A-Za-z_][\w]*)")

# Silence pragma inline in templates.
_NOQA_RE = re.compile(r"\{#\s*djust_typecheck:\s*noqa(?:\s+(\w+(?:\s*,\s*\w+)*))?\s*#\}")


def _public_class_attrs(cls) -> Set[str]:
    """Names declared directly on the class body (or inherited from user bases)."""
    names: Set[str] = set()
    for klass in cls.__mro__:
        mod = getattr(klass, "__module__", "") or ""
        if mod.startswith("djust.") and "test" not in mod and "example" not in mod:
            continue
        for name in vars(klass):
            if name.startswith("_") or name.isupper():
                continue
            names.add(name)
    return names


def _extract_context_keys_from_ast(cls) -> Set[str]:
    """Best-effort static extraction of context-provided names.

    Collects three sources from the class source, walking the MRO so keys
    set on any user-code ancestor (mixins, base views) are visible to the
    checker:

    1. Literal dict keys in ``get_context_data`` ``return {...}``.
    2. ``self.foo = ...`` attribute assignments anywhere in the class
       (``mount``, event handlers, helper methods — they all populate
       public state that templates can read).
    3. Properties declared on the class (``@property``-decorated methods).

    Framework base classes (``djust.*`` / ``djust_*``) are skipped to avoid
    surfacing internal helpers as user-visible context keys.
    """
    keys: Set[str] = set()
    import inspect
    import textwrap

    for klass in cls.__mro__:
        if klass is object:
            continue
        mod = getattr(klass, "__module__", "") or ""
        # Skip framework classes: djust internals, Django, DRF, and builtins.
        # Test / example modules that live under djust.* are kept so our own
        # test helpers work.
        if (mod.startswith("djust.") or mod.startswith("djust_")) and (
            "test" not in mod and "example" not in mod
        ):
            continue
        if mod.startswith("django.") or mod == "django":
            continue
        if mod.startswith("rest_framework.") or mod == "rest_framework":
            continue
        if mod == "builtins":
            continue
        try:
            src = inspect.getsource(klass)
        except (OSError, TypeError):
            continue
        try:
            tree = ast.parse(textwrap.dedent(src))
        except SyntaxError:
            continue
        _collect_context_keys_from_tree(tree, keys)
    return keys


def _collect_context_keys_from_tree(tree: ast.AST, keys: Set[str]) -> None:
    """Walk a parsed class body and add discoverable context keys to ``keys``."""
    for node in ast.walk(tree):
        # 1. get_context_data literal returns
        if isinstance(node, ast.FunctionDef) and node.name == "get_context_data":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
                    for k in sub.value.keys:
                        if isinstance(k, ast.Constant) and isinstance(k.value, str):
                            keys.add(k.value)
        # 2. self.attr = ... assignments
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                    and not target.attr.startswith("_")
                ):
                    keys.add(target.attr)
        if isinstance(node, ast.AugAssign):
            target = node.target
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
                and not target.attr.startswith("_")
            ):
                keys.add(target.attr)
        # Also cover annotated assignments: `self.counter: int = 0`
        if isinstance(node, ast.AnnAssign):
            target = node.target
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
                and not target.attr.startswith("_")
            ):
                keys.add(target.attr)
        # 3. @property methods
        if isinstance(node, ast.FunctionDef):
            for deco in node.decorator_list:
                name = None
                if isinstance(deco, ast.Name):
                    name = deco.id
                elif isinstance(deco, ast.Attribute):
                    name = deco.attr
                elif isinstance(deco, ast.Call):
                    if isinstance(deco.func, ast.Name):
                        name = deco.func.id
                    elif isinstance(deco.func, ast.Attribute):
                        name = deco.func.attr
                if name == "property" and not node.name.startswith("_"):
                    keys.add(node.name)


def _extract_template_locals(src: str) -> Set[str]:
    """Names bound by the template itself (for/with/blocktrans loops)."""
    locals_: Set[str] = set()
    for match in _TAG_RE.finditer(src):
        tag = match.group(1)
        args = match.group(2)
        if tag == "for":
            # `{% for x, y in items %}` → bind x, y
            parts = args.split(" in ", 1)
            if parts:
                for ident in _IDENT_RE.findall(parts[0]):
                    locals_.add(ident)
        elif tag == "with":
            # `{% with x=expr y=expr %}` → bind x, y
            for piece in args.split():
                if "=" in piece:
                    name = piece.split("=", 1)[0].strip()
                    m = _IDENT_RE.match(name)
                    if m:
                        locals_.add(m.group(1))
        elif tag == "blocktrans" or tag == "blocktranslate":
            # `{% blocktrans with x=foo y=bar %}` → bind x, y.
            # Also supports `count var=expr` which binds `var` the same way.
            if "with" in args.split() or "count" in args.split():
                for piece in args.split():
                    if "=" in piece:
                        name = piece.split("=", 1)[0].strip()
                        m = _IDENT_RE.match(name)
                        if m:
                            locals_.add(m.group(1))
        elif tag == "inputs_for":
            # djust block tag: `{% inputs_for formset as form %}` → bind form
            parts = args.split()
            if len(parts) >= 3 and parts[1] == "as":
                locals_.add(parts[2])
        elif tag == "cycle":
            # `{% cycle "odd" "even" as row_class %}` → bind row_class locally.
            # The cycle args themselves are references (handled in
            # _extract_referenced_names); the name after `as` is a local.
            parts = args.split()
            if "as" in parts:
                idx = parts.index("as")
                if idx + 1 < len(parts):
                    m = _IDENT_RE.match(parts[idx + 1])
                    if m:
                        locals_.add(m.group(1))
    return locals_


def _extract_referenced_names(src: str) -> List[tuple]:
    """Return [(name, line_number)] for every root identifier referenced."""
    refs: List[tuple] = []

    # `{{ ... }}` — grab the first identifier of each expression.
    for match in _VAR_RE.finditer(src):
        name = match.group(1)
        if name in {"True", "False", "None"}:
            continue
        line = src.count("\n", 0, match.start()) + 1
        refs.append((name, line))

    # `{% if name %}`, `{% elif name %}`, `{% for x in name %}` etc.
    for match in _TAG_RE.finditer(src):
        tag = match.group(1)
        args = match.group(2)
        line = src.count("\n", 0, match.start()) + 1
        if tag in {"if", "elif", "while"}:
            for ident in _IDENT_RE.findall(args):
                if ident in {"and", "or", "not", "in", "is", "True", "False", "None"}:
                    continue
                if ident.isdigit():
                    continue
                refs.append((ident, line))
        elif tag == "for":
            parts = args.split(" in ", 1)
            if len(parts) == 2:
                m = _IDENT_RE.match(parts[1].strip())
                if m:
                    refs.append((m.group(1), line))
        elif tag in {"include", "extends"}:
            # The first argument may be a variable (rare) or a string literal — skip.
            continue
        elif tag in {"url", "static"}:
            # Positional args are name-strings, not context vars.
            continue
        elif tag in {"firstof", "cycle"}:
            # `{% firstof a b c %}` / `{% cycle a b c %}` — each non-literal token
            # is a context variable. Skip string literals and numbers.
            for token in args.split():
                if not token or token[0] in {"'", '"'} or token.isdigit():
                    continue
                # `cycle` supports `as name` suffix which binds a loop var — skip
                # the "as X" clause itself; X is a local, not a reference.
                if token == "as":
                    break
                m = _IDENT_RE.match(token)
                if m:
                    refs.append((m.group(1), line))
        elif tag in {"blocktrans", "blocktranslate"}:
            # `{% blocktrans with x=expr y=expr %}` — the right-hand side of each
            # x=expr pair is a context variable reference. `x` itself is a local
            # (handled in _extract_template_locals).
            for piece in args.split():
                if "=" in piece:
                    rhs = piece.split("=", 1)[1].strip()
                    if not rhs or rhs[0] in {"'", '"'} or rhs.isdigit():
                        continue
                    m = _IDENT_RE.match(rhs)
                    if m:
                        refs.append((m.group(1), line))
    return refs


def _template_noqa_names(src: str) -> Set[str]:
    silenced: Set[str] = set()
    for match in _NOQA_RE.finditer(src):
        arg = match.group(1)
        if arg is None:
            silenced.add("*")
        else:
            for part in arg.split(","):
                silenced.add(part.strip())
    return silenced


def _find_template_path(template_name: str) -> Optional[Path]:
    """Resolve template_name via Django's template loaders."""
    from django.template.loader import get_template

    try:
        tpl = get_template(template_name)
    except Exception:
        return None
    origin = getattr(tpl, "origin", None)
    if origin is None or not getattr(origin, "name", None):
        return None
    return Path(origin.name)


def _globals_from_settings() -> Set[str]:
    from django.conf import settings

    raw = getattr(settings, "DJUST_TEMPLATE_GLOBALS", None) or ()
    return {str(x) for x in raw}


def _check_view(cls, verbose: bool = False) -> Optional[Dict[str, Any]]:
    template_name = getattr(cls, "template_name", None)
    if not template_name or not isinstance(template_name, str):
        return None
    path = _find_template_path(template_name)
    if path is None:
        return {
            "view": f"{cls.__module__}.{cls.__qualname__}",
            "template": template_name,
            "error": "template not found by Django loaders",
            "missing": [],
            "strict": bool(getattr(cls, "strict_context", False)),
        }
    try:
        src = path.read_text(encoding="utf-8")
    except OSError as e:
        return {
            "view": f"{cls.__module__}.{cls.__qualname__}",
            "template": template_name,
            "error": f"could not read template: {e}",
            "missing": [],
            "strict": bool(getattr(cls, "strict_context", False)),
        }

    available = set(_ALWAYS_AVAILABLE)
    available |= _public_class_attrs(cls)
    available |= _extract_context_keys_from_ast(cls)
    available |= _extract_template_locals(src)
    available |= _globals_from_settings()

    silenced = _template_noqa_names(src)
    refs = _extract_referenced_names(src)

    missing: List[Dict[str, Any]] = []
    seen: Set[tuple] = set()
    for name, line in refs:
        if name in available:
            continue
        if "*" in silenced or name in silenced:
            continue
        key = (name, line)
        if key in seen:
            continue
        seen.add(key)
        missing.append({"name": name, "line": line})

    if not missing:
        return None
    return {
        "view": f"{cls.__module__}.{cls.__qualname__}",
        "template": template_name,
        "template_path": str(path),
        "missing": missing,
        "strict": bool(getattr(cls, "strict_context", False)),
    }


class Command(BaseCommand):
    help = "Static template-variable validation for djust LiveView templates."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
        parser.add_argument(
            "--strict", action="store_true", help="Exit non-zero if any view has unresolved names."
        )
        parser.add_argument("--app", default=None, help="Filter to one Django app.")
        parser.add_argument(
            "--view", default=None, help="Filter to one view (matches on __qualname__)."
        )
        parser.add_argument("--verbose", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        from djust.live_view import LiveView

        app = options.get("app")
        view_filter = options.get("view")

        reports: List[Dict[str, Any]] = []
        for cls in _walk_subclasses(LiveView):
            if not _is_user_class(cls):
                continue
            if app and _app_label(cls) != app:
                continue
            if view_filter and view_filter not in cls.__qualname__:
                continue
            report = _check_view(cls, verbose=options.get("verbose", False))
            if report is not None:
                reports.append(report)

        # Separate strict (errors) from non-strict (warnings)
        any_strict = any(r.get("strict") for r in reports)

        if options.get("json"):
            self.stdout.write(json.dumps({"reports": reports}, indent=2))
        else:
            if not reports:
                self.stdout.write(self.style.SUCCESS("djust_typecheck: no issues found"))
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f"djust_typecheck: {len(reports)} view(s) with unresolved names\n"
                    )
                )
                for r in reports:
                    style = self.style.ERROR if r.get("strict") else self.style.WARNING
                    head = f"{r['view']} ({r['template']})"
                    self.stdout.write(style(head))
                    if "error" in r:
                        self.stdout.write(f"    error: {r['error']}")
                        continue
                    for miss in r["missing"]:
                        self.stdout.write(f"    line {miss['line']}: {miss['name']}")
                    self.stdout.write("")

        nonzero = options.get("strict") or any_strict
        if reports and nonzero:
            raise SystemExit(1)
