"""Find markup built out of raw values that is then marked safe (#3766).

``mark_safe(f"<svg width={size}>")`` is the banned shape (CLAUDE.md, Security Rules 1). The first
guard for it matched that literal shape only, and it would not have caught #3766 itself, where the
f-strings sat in a dict and the call was ``mark_safe(icons.get(name, ""))``. This module follows
the value one step further: through local variables, containers, ``+=`` / ``append`` and
``join``, within ONE function (or the module body).

It is NAME-based and SINGLE-FUNCTION. It reads the syntax tree; it does no type inference and it
does not follow a call into another function. Read its verdict as "this function builds markup out
of something it cannot show is escaped", never as "this function is safe".

What it flags
-------------
A call to ``mark_safe`` / ``SafeString`` / ``SafeText`` (any alias: ``from x import mark_safe as
ms``, ``ms = mark_safe``, ``safestring.mark_safe``; matched by NAME, so a local function with that
name is judged too), or the FORMAT STRING of ``format_html``, whose argument (positional or
keyword) is a string BUILT FROM A RAW VALUE:

* an f-string, ``%``, ``+``, ``str.format`` / ``format_map`` or ``Template.substitute`` with a piece
  that is not clean (below);
* ``"".join(items)`` where an item is not clean;
* a local variable that holds such a string (``s = f"..."``, ``s += x``, ``a, b = f"..", 1``,
  ``(s := f"..")``), a local list that such a string was ``append``-ed / ``extend``-ed /
  ``insert``-ed to, or a local dict or comprehension holding one (``icons.get(name, "")``,
  ``icons[name]``, ``{k: f"..." for k in xs}``).

A piece is clean when it is:

* a literal;
* the result of ``escape`` / ``conditional_escape`` / ``format_html`` / ``format_html_join`` from
  ``django.utils.html`` or ``html.escape``, or of ``int`` / ``float`` / ``bool`` / ``len``. These are
  resolved through the module's imports (``from django.utils.html import escape as e`` counts;
  ``from re import escape``, ``shlex.quote`` and a locally defined ``def escape(v): return v`` do
  NOT: only the ORIGIN is trusted, never the spelling);
* ``min`` / ``max`` / ``round`` / ``abs`` / ``str`` / ``string.Template`` of clean arguments;
* ``uuid.uuid4()`` / ``uuid.uuid1()`` / ``secrets.token_hex`` / ``.token_urlsafe`` and ``.hex`` of
  them (a fixed alphabet), a slice of a clean value;
* an f-string field with a numeric format spec (``{n:.2f}``, ``{n:d}``: digits only);
* an enumerate index (``for i, x in enumerate(xs)``), a ``range`` variable;
* the output of a ``.render(...)`` method or ``render_to_string`` (autoescaped by the template
  engine; matched by NAME, so a local ``def render`` is not trusted but ``obj.render()`` is);
* ``.strip()`` / ``.lower()`` / ``.replace()`` ... of a clean value, a local that only ever holds
  clean values, or a lookup in a container of clean values (a module-level constant dict).

Anything else, including a function parameter, an attribute (``obj.id``) and the result of an
unknown call, is raw.

What it does NOT see (probed, and pinned in ``KNOWN_MISSES`` of its test)
--------------------------------------------------------------------------
* Anything that crosses a function: a string built in one function and marked safe in another
  (``build_theme_head_context`` builds ``css_block``; ``ThemeMixin`` marks it safe; the inline theme
  switcher builds ``preset_options`` into a returned f-string that ``LazyThemeHTML`` marks safe, so
  the switcher is protected by its own tests, not by this guard), a module global appended to in
  one function and joined in another, a value stored on ``self``, a helper that returns an
  interpolated string.
* ``mark_safe(x)`` where ``x`` is a bare parameter or an unknown call's result: that is how trusted
  markup is passed in, and no syntax says whether ``x`` was escaped.
* A sink reached without a call to it by name: ``partial(mark_safe, f"..")``,
  ``map(mark_safe, [..])``, ``getattr(safestring, "mark_safe")(..)``.
* ``functools.reduce`` over raw items, and any builder not listed above.
* Whether a name it trusts is the real thing: ``x.render()`` and ``.strip()`` are trusted by method
  name, whatever ``x`` is.

Each of those needs reading, which is what an audited allowlist entry records.

Refs #3766, #3775, #1859, #1468.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SINKS = frozenset({"mark_safe", "SafeString", "SafeText"})
#: Sinks whose FIRST argument is a markup template: an interpolated one is the same hole.
FORMAT_SINKS = frozenset({"format_html"})

#: Resolved ORIGINS (through the module's imports) of calls whose result holds no raw markup.
CLEAN_ORIGINS = frozenset(
    {
        "django.utils.html.escape",
        "django.utils.html.conditional_escape",
        "django.utils.html.format_html",
        "django.utils.html.format_html_join",
        "html.escape",  # the standard library's escapes quotes by default
        "int",
        "float",
        "bool",
        "len",
    }
)
#: Origins that are clean when EVERY argument is clean (a number out of numbers, text out of text).
CLEAN_IF_ARGS_CLEAN = frozenset({"min", "max", "round", "abs", "str", "string.Template"})
#: Generators over a fixed alphabet (hex digits, base64url): their output cannot hold markup.
SAFE_ALPHABET_ORIGINS = frozenset(
    {"uuid.uuid4", "uuid.uuid1", "secrets.token_hex", "secrets.token_urlsafe"}
)
#: ``x.<method>(...)`` builders judged like ``str.format``: raw arguments make a built string.
FORMAT_METHODS = frozenset({"format", "format_map", "substitute", "safe_substitute"})
#: An f-string field spec that can only print digits: ``.2f``, ``d``, ``05.1f``, ``,d`` (no ``c``,
#: which prints the character with that code, and no ``s``).
_NUMERIC_SPEC = re.compile(r"(?:.?[<>=^])?[+\- ]?z?#?0?[0-9]*[,_]?(?:\.[0-9]+)?[bdoxXeEfFgGn%]")
#: A template engine's output is autoescaped; it is a clean piece, never a built string.
RENDER_CALLS = frozenset({"render", "render_to_string", "render_to_response"})
#: ``x.<method>(...)`` returns clean text when ``x`` and every argument are clean.
STR_METHODS = frozenset(
    {
        "strip",
        "lstrip",
        "rstrip",
        "lower",
        "upper",
        "title",
        "capitalize",
        "casefold",
        "replace",
        "removeprefix",
        "removesuffix",
        "zfill",
        "ljust",
        "rjust",
        "center",
    }
)
#: Calls that return (a view of) the elements of their argument unchanged.
ELEMENT_CALLS = frozenset({"list", "tuple", "set", "frozenset", "sorted", "reversed", "iter"})
CONTAINER_METHODS = frozenset({"get", "pop", "setdefault", "copy"})
ITERATION_METHODS = frozenset({"values", "keys", "items"})
_DEPTH = 40


class _Raw:
    """A binding whose value is not known: a parameter, a loop variable, a ``with`` target."""


RAW = _Raw()


class _ElementOf:
    """A name bound by iterating ``iterable`` (a ``for`` target or a comprehension variable)."""

    def __init__(self, iterable: ast.expr) -> None:
        self.iterable = iterable


@dataclass(frozen=True)
class Finding:
    lineno: int
    function: str
    sink: str
    why: str

    def __str__(self) -> str:
        return f"{self.function}:{self.lineno} {self.sink}() <- {self.why}"


class _Scope:
    def __init__(self, node: ast.AST, name: str, parent: _Scope | None) -> None:
        self.node = node
        self.name = name
        self.parent = parent
        #: name -> every value bound to it (``ast.expr`` | RAW | _ElementOf)
        self.assigns: dict[str, list[object]] = {}
        #: name -> every value put INTO it (append / extend / insert / ``d[k] = v`` / ``+= [..]``)
        self.contents: dict[str, list[ast.expr]] = {}
        #: name -> every value ``+=``-ed onto it as a string piece
        self.pieces: dict[str, list[ast.expr]] = {}
        self.sinks: list[ast.Call] = []

    def find(self, name: str) -> _Scope | None:
        scope: _Scope | None = self
        while scope is not None:
            if name in scope.assigns or name in scope.contents or name in scope.pieces:
                return scope
            scope = scope.parent
        return None


def _callee(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _own_nodes(scope_node: ast.AST):
    """Every node of a scope, not descending into a nested function, lambda or class body."""
    stack = list(ast.iter_child_nodes(scope_node))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _target_names(target: ast.expr) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [n for t in target.elts for n in _target_names(t)]
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    return []


class _Module:
    def __init__(self, source: str) -> None:
        self.source = source
        self.tree = ast.parse(source)
        self.sinks = set(SINKS)
        self.format_sinks = set(FORMAT_SINKS)
        self.imports: dict[str, str] = {}
        self._collect_imports()
        self._collect_aliases()
        self.scopes: list[_Scope] = []
        self._build(self.tree, "<module>", None)

    # -- imports and aliases --------------------------------------------------------------
    def _collect_imports(self) -> None:
        """local name -> the dotted name it was imported as (``escape`` -> ``django.utils.html.escape``)."""
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        self.imports[alias.asname] = alias.name
                    else:
                        top = alias.name.split(".")[0]
                        self.imports[top] = top
            elif isinstance(node, ast.ImportFrom):
                module = "." * node.level + (node.module or "")
                for alias in node.names:
                    self.imports[alias.asname or alias.name] = f"{module}.{alias.name}"

    def origin(self, e: ast.expr, sc: _Scope, depth: int = 0) -> str | None:
        """The dotted name ``e`` resolves to through imports and plain aliases, else ``None``.

        A name bound in the code (a parameter, ``def escape``, ``escape = something_else``) is NOT
        what it is spelled like: it resolves to ``None``. A name bound nowhere keeps its own
        spelling (a builtin such as ``int``).
        """
        if depth > 8:
            return None
        if isinstance(e, ast.Name):
            owner = sc.find(e.id)
            if owner is None:
                return self.imports.get(e.id, e.id)
            values = owner.assigns.get(e.id, [])
            if (
                values
                and not owner.contents.get(e.id)
                and not owner.pieces.get(e.id)
                and all(isinstance(v, (ast.Name, ast.Attribute)) for v in values)
            ):
                found = {self.origin(v, owner, depth + 1) for v in values}  # type: ignore[arg-type]
                if len(found) == 1 and None not in found:
                    return found.pop()
            return None
        if isinstance(e, ast.Attribute):
            base = self.origin(e.value, sc, depth + 1)
            return None if base is None else f"{base}.{e.attr}"
        return None

    def _collect_aliases(self) -> None:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.asname is None:
                        continue
                    for names, group in (
                        (SINKS, self.sinks),
                        (FORMAT_SINKS, self.format_sinks),
                    ):
                        if alias.name in names:
                            group.add(alias.asname)
        changed = True
        while changed:  # ``ms = mark_safe``; ``m2 = ms``
            changed = False
            for node in ast.walk(self.tree):
                if (
                    isinstance(node, ast.Assign)
                    and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and _callee(node.value) is not None
                    and isinstance(node.value, (ast.Name, ast.Attribute))
                ):
                    src, dst = _callee(node.value), node.targets[0].id
                    for group in (self.sinks, self.format_sinks):
                        if src in group and dst not in group:
                            group.add(dst)
                            changed = True

    # -- scopes -------------------------------------------------------------------------
    def _build(self, node: ast.AST, name: str, parent: _Scope | None) -> None:
        scope = _Scope(node, name, parent)
        self.scopes.append(scope)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = node.args
            every = [*args.posonlyargs, *args.args, *args.kwonlyargs]
            if args.vararg:
                every.append(args.vararg)
            if args.kwarg:
                every.append(args.kwarg)
            for arg in every:
                scope.assigns.setdefault(arg.arg, []).append(RAW)
        for child in _own_nodes(node):
            self._record(scope, child)
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                prefix = "" if name == "<module>" else name + "."
                self._build(child, prefix + child.name, scope)
            elif isinstance(child, ast.Lambda):
                self._build(child, name + ".<lambda>", scope)
            elif isinstance(child, ast.ClassDef):
                prefix = "" if name == "<module>" else name + "."
                self._build(child, prefix + child.name, scope)

    def _record(self, scope: _Scope, node: ast.AST) -> None:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    scope.assigns.setdefault(target.id, []).append(node.value)
                elif isinstance(target, (ast.Tuple, ast.List)):
                    self._bind_unpack(scope, target, node.value)
                elif isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
                    scope.contents.setdefault(target.value.id, []).append(node.value)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if isinstance(node.target, ast.Name):
                scope.assigns.setdefault(node.target.id, []).append(node.value)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            n = node.target.id
            if isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
                scope.contents.setdefault(n, []).extend(node.value.elts)
            elif isinstance(node.op, (ast.Add, ast.Mod)):
                scope.pieces.setdefault(n, []).append(node.value)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            self._bind_iteration(scope, node.target, node.iter)
        elif isinstance(node, ast.comprehension):
            # a comprehension variable is visible to a sink inside the comprehension
            self._bind_iteration(scope, node.target, node.iter)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # a ``def escape`` is not django's ``escape``: it shadows what it is named like
            scope.assigns.setdefault(node.name, []).append(RAW)
        elif isinstance(node, ast.With):
            for item in node.items:
                if item.optional_vars is not None:
                    for n in _target_names(item.optional_vars):
                        scope.assigns.setdefault(n, []).append(RAW)
        elif isinstance(node, ast.NamedExpr):
            scope.assigns.setdefault(node.target.id, []).append(node.value)
        elif isinstance(node, (ast.ExceptHandler,)) and node.name:
            scope.assigns.setdefault(node.name, []).append(RAW)
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
                n, method = call.func.value.id, call.func.attr
                if method in ("append", "add") and call.args:
                    scope.contents.setdefault(n, []).append(call.args[0])
                elif method == "insert" and len(call.args) >= 2:
                    scope.contents.setdefault(n, []).append(call.args[1])
                elif method in ("extend", "update") and call.args:
                    arg = call.args[0]
                    if isinstance(arg, (ast.List, ast.Tuple, ast.Set)):
                        scope.contents.setdefault(n, []).extend(arg.elts)
                    else:  # an iterable of unknown elements: judge it as the elements
                        scope.contents.setdefault(n, []).append(
                            ast.Subscript(value=arg, slice=ast.Constant(0), ctx=ast.Load())
                        )
        # a sink call is recorded wherever it appears in this scope's own nodes
        if isinstance(node, ast.Call):
            name = _callee(node.func)
            if name in self.sinks or name in self.format_sinks:
                scope.sinks.append(node)

    def _bind_unpack(self, scope: _Scope, target: ast.expr, value: ast.expr) -> None:
        """``a, b = x, y`` binds each name to its own element; any other unpack binds RAW."""
        if (
            isinstance(target, (ast.Tuple, ast.List))
            and isinstance(value, (ast.Tuple, ast.List))
            and len(target.elts) == len(value.elts)
            and not any(isinstance(x, ast.Starred) for x in [*target.elts, *value.elts])
        ):
            for t, v in zip(target.elts, value.elts):
                if isinstance(t, (ast.Tuple, ast.List)):
                    self._bind_unpack(scope, t, v)
                elif isinstance(t, ast.Name):
                    scope.assigns.setdefault(t.id, []).append(v)
            return
        for n in _target_names(target):
            scope.assigns.setdefault(n, []).append(RAW)

    def _bind_iteration(self, scope: _Scope, target: ast.expr, iterable: ast.expr) -> None:
        """Bind a ``for`` / comprehension target; ``for i, x in enumerate(xs)`` gives ``i`` a number."""
        if (
            isinstance(iterable, ast.Call)
            and self.origin(iterable.func, scope) == "enumerate"
            and iterable.args
            and isinstance(target, ast.Tuple)
            and len(target.elts) == 2
        ):
            index, item = target.elts
            for n in _target_names(index):
                scope.assigns.setdefault(n, []).append(ast.Constant(0))
            for n in _target_names(item):
                scope.assigns.setdefault(n, []).append(_ElementOf(iterable.args[0]))
            return
        for n in _target_names(target):
            scope.assigns.setdefault(n, []).append(_ElementOf(iterable))

    # -- judging ------------------------------------------------------------------------
    def findings(self) -> list[Finding]:
        out: list[Finding] = []
        for scope in self.scopes:
            for call in scope.sinks:
                name = _callee(call.func) or "?"
                if name in self.format_sinks and name not in self.sinks:
                    args = call.args[:1] or [kw.value for kw in call.keywords][:1]
                else:
                    args = [*call.args, *(kw.value for kw in call.keywords)]
                for arg in args:
                    if self.tainted(arg, scope, 0, frozenset()):
                        out.append(
                            Finding(
                                call.lineno,
                                scope.name,
                                name,
                                (ast.get_source_segment(self.source, arg) or ast.dump(arg))[:90],
                            )
                        )
                        break
        return sorted(set(out), key=lambda f: (f.lineno, f.function))

    # clean: no raw data anywhere in this expression
    def clean(self, e: object, sc: _Scope, depth: int, seen: frozenset) -> bool:
        if depth > _DEPTH or e is RAW:
            return False
        if isinstance(e, _ElementOf):
            return self.elements_clean(e.iterable, sc, depth + 1, seen)
        if not isinstance(e, ast.expr):
            return False
        d = depth + 1
        if isinstance(e, ast.Constant):
            return True
        if isinstance(e, ast.JoinedStr):
            return all(
                self._field_clean(v, sc, d, seen)
                for v in e.values
                if isinstance(v, ast.FormattedValue)
            )
        if isinstance(e, ast.NamedExpr):
            return self.clean(e.value, sc, d, seen)
        if isinstance(e, ast.BinOp):
            return self.clean(e.left, sc, d, seen) and self.clean(e.right, sc, d, seen)
        if isinstance(e, ast.UnaryOp):
            return self.clean(e.operand, sc, d, seen)
        if isinstance(e, (ast.Compare,)):
            return True
        if isinstance(e, ast.BoolOp):
            return all(self.clean(v, sc, d, seen) for v in e.values)
        if isinstance(e, ast.IfExp):
            return self.clean(e.body, sc, d, seen) and self.clean(e.orelse, sc, d, seen)
        if isinstance(e, (ast.List, ast.Tuple, ast.Set)):
            return all(self.clean(x, sc, d, seen) for x in e.elts)
        if isinstance(e, ast.Dict):
            return all(self.clean(v, sc, d, seen) for v in e.values if v is not None)
        if isinstance(e, ast.Subscript):
            if isinstance(e.slice, ast.Slice):  # a substring of clean text is clean text
                return self.clean(e.value, sc, d, seen)
            return self.elements_clean(e.value, sc, d, seen)
        if isinstance(e, ast.Attribute):
            # ``uuid.uuid4().hex``: a fixed alphabet
            return (
                e.attr == "hex"
                and isinstance(e.value, ast.Call)
                and self.origin(e.value.func, sc) in SAFE_ALPHABET_ORIGINS
            )
        if isinstance(e, ast.Name):
            return self._name_clean(e.id, sc, d, seen)
        if isinstance(e, ast.Call):
            return self._call_clean(e, sc, d, seen)
        return False

    def _field_clean(self, field: ast.FormattedValue, sc: _Scope, d: int, seen: frozenset) -> bool:
        """One ``{value!c:spec}`` of an f-string: clean if the value is, or the spec prints digits."""
        spec = field.format_spec
        if (
            field.conversion == -1
            and isinstance(spec, ast.JoinedStr)
            and spec.values
            and all(isinstance(v, ast.Constant) and isinstance(v.value, str) for v in spec.values)
            and _NUMERIC_SPEC.fullmatch("".join(v.value for v in spec.values))  # type: ignore[attr-defined]
        ):
            return True
        return self.clean(field.value, sc, d, seen)

    def _trusted_call(self, e: ast.Call, sc: _Scope) -> bool:
        """A call whose result is clean whatever its arguments (an escaper, a number, an engine)."""
        origin = self.origin(e.func, sc)
        if origin in CLEAN_ORIGINS or origin in SAFE_ALPHABET_ORIGINS:
            return True
        name = _callee(e.func)
        if isinstance(e.func, ast.Name):
            # ``render_to_string(...)``: trusted unless this code defines something of that name
            return name in RENDER_CALLS and sc.find(name) is None  # type: ignore[arg-type]
        return (
            name in RENDER_CALLS
        )  # ``tmpl.render(...)``: by method name, see the module docstring

    def _call_clean(self, e: ast.Call, sc: _Scope, d: int, seen: frozenset) -> bool:
        if self._trusted_call(e, sc):
            return True
        name = _callee(e.func)
        args = [*e.args, *(kw.value for kw in e.keywords)]
        if self.origin(e.func, sc) in CLEAN_IF_ARGS_CLEAN:
            return all(self.clean(a, sc, d, seen) for a in args)
        if isinstance(e.func, ast.Attribute):
            recv = e.func.value
            if name == "join":
                return self.clean(recv, sc, d, seen) and all(
                    self.elements_clean(a, sc, d, seen) for a in args
                )
            if name in FORMAT_METHODS:
                return self.clean(recv, sc, d, seen) and all(
                    self.clean(a, sc, d, seen) for a in args
                )
            if name in STR_METHODS:
                return self.clean(recv, sc, d, seen) and all(
                    self.clean(a, sc, d, seen) for a in args
                )
            if name in CONTAINER_METHODS:
                return self.elements_clean(recv, sc, d, seen)
        return False

    def _name_clean(self, name: str, sc: _Scope, d: int, seen: frozenset) -> bool:
        owner = sc.find(name)
        if owner is None:
            return False
        key = ("clean", name, id(owner))
        if key in seen:
            return True  # a cycle adds nothing new
        seen = seen | {key}
        values = owner.assigns.get(name, [])
        if not values and not owner.pieces.get(name) and not owner.contents.get(name):
            return False
        return (
            all(self.clean(v, owner, d, seen) for v in values)
            and all(self.clean(p, owner, d, seen) for p in owner.pieces.get(name, []))
            and all(self.clean(c, owner, d, seen) for c in owner.contents.get(name, []))
        )

    # elements_clean: every element / value pulled out of this container is clean
    def elements_clean(self, e: object, sc: _Scope, depth: int, seen: frozenset) -> bool:
        if depth > _DEPTH or e is RAW or isinstance(e, _ElementOf) or not isinstance(e, ast.expr):
            return False
        d = depth + 1
        if isinstance(e, ast.Constant):
            return True
        if isinstance(e, (ast.List, ast.Tuple, ast.Set)):
            return all(self.clean(x, sc, d, seen) for x in e.elts)
        if isinstance(e, ast.Dict):
            return all(self.clean(v, sc, d, seen) for v in e.values if v is not None)
        if isinstance(e, (ast.ListComp, ast.GeneratorExp, ast.SetComp, ast.DictComp)):
            inner = self._comprehension_scope(e, sc)
            elt = e.value if isinstance(e, ast.DictComp) else e.elt
            return self.clean(elt, inner, d, seen)
        if isinstance(e, ast.Subscript):
            return self.elements_clean(e.value, sc, d, seen)
        if isinstance(e, ast.IfExp):
            return self.elements_clean(e.body, sc, d, seen) and self.elements_clean(
                e.orelse, sc, d, seen
            )
        if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Add):
            return self.elements_clean(e.left, sc, d, seen) and self.elements_clean(
                e.right, sc, d, seen
            )
        if isinstance(e, ast.Name):
            owner = sc.find(e.id)
            if owner is None:
                return False
            key = ("elements", e.id, id(owner))
            if key in seen:
                return True
            seen = seen | {key}
            values = owner.assigns.get(e.id, [])
            if not values and not owner.contents.get(e.id):
                return False
            return all(self.elements_clean(v, owner, d, seen) for v in values) and all(
                self.clean(c, owner, d, seen) for c in owner.contents.get(e.id, [])
            )
        if isinstance(e, ast.Call):
            name = _callee(e.func)
            if isinstance(e.func, ast.Name) and name in ELEMENT_CALLS and e.args:
                return self.elements_clean(e.args[0], sc, d, seen)
            if isinstance(e.func, ast.Attribute):
                if name in CONTAINER_METHODS or name in ITERATION_METHODS:
                    return self.elements_clean(e.func.value, sc, d, seen)
            if isinstance(e.func, ast.Name) and name == "range":
                return True
        return False

    def _comprehension_scope(self, e: ast.expr, sc: _Scope) -> _Scope:
        inner = _Scope(e, sc.name, sc)
        for gen in e.generators:  # type: ignore[attr-defined]
            self._bind_iteration(inner, gen.target, gen.iter)
        return inner

    # tainted: a string BUILT from raw pieces
    def tainted(self, e: object, sc: _Scope, depth: int, seen: frozenset) -> bool:
        if depth > _DEPTH or not isinstance(e, ast.expr):
            return False
        d = depth + 1
        if isinstance(e, ast.JoinedStr):
            return any(
                not self._field_clean(v, sc, d, seen)
                for v in e.values
                if isinstance(v, ast.FormattedValue)
            )
        if isinstance(e, ast.NamedExpr):
            return self.tainted(e.value, sc, d, seen)
        if isinstance(e, (ast.ListComp, ast.GeneratorExp, ast.SetComp, ast.DictComp)):
            inner = self._comprehension_scope(e, sc)
            elt = e.value if isinstance(e, ast.DictComp) else e.elt
            return self.tainted(elt, inner, d, seen)
        if isinstance(e, ast.BinOp):
            if isinstance(e.op, (ast.Add, ast.Mod)):
                return not self.clean(e, sc, d, seen)
            return False
        if isinstance(e, ast.IfExp):
            return self.tainted(e.body, sc, d, seen) or self.tainted(e.orelse, sc, d, seen)
        if isinstance(e, ast.BoolOp):
            return any(self.tainted(v, sc, d, seen) for v in e.values)
        if isinstance(e, (ast.List, ast.Tuple, ast.Set)):
            return any(self.tainted(x, sc, d, seen) for x in e.elts)
        if isinstance(e, ast.Dict):
            return any(self.tainted(v, sc, d, seen) for v in e.values if v is not None)
        if isinstance(e, ast.Subscript):
            return self.tainted(e.value, sc, d, seen)
        if isinstance(e, ast.Name):
            return self._name_tainted(e.id, sc, d, seen)
        if isinstance(e, ast.Call):
            name = _callee(e.func)
            if self._trusted_call(e, sc):
                return False
            if isinstance(e.func, ast.Attribute):
                recv = e.func.value
                if name == "join" and e.args:
                    return not self.clean(recv, sc, d, seen) or not all(
                        self.elements_clean(a, sc, d, seen) for a in e.args
                    )
                if name in FORMAT_METHODS:
                    return not self.clean(e, sc, d, seen)
                if self.tainted(recv, sc, d, seen):
                    return True
            return any(
                self.tainted(a, sc, d, seen) for a in [*e.args, *(kw.value for kw in e.keywords)]
            )
        return False

    def _name_tainted(self, name: str, sc: _Scope, d: int, seen: frozenset) -> bool:
        owner = sc.find(name)
        if owner is None:
            return False
        key = ("tainted", name, id(owner))
        if key in seen:
            return False
        seen = seen | {key}
        return (
            any(self.tainted(v, owner, d, seen) for v in owner.assigns.get(name, []))
            or any(not self.clean(p, owner, d, seen) for p in owner.pieces.get(name, []))
            or any(self.tainted(c, owner, d, seen) for c in owner.contents.get(name, []))
        )


def find_unsafe_markup(source: str) -> list[Finding]:
    """Every sink in ``source`` that is handed a string built from a raw value."""
    return _Module(source).findings()


def _main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv]
    files = [f for p in paths for f in (sorted(p.rglob("*.py")) if p.is_dir() else [p])]
    total = 0
    for path in files:
        for finding in find_unsafe_markup(path.read_text()):
            print(f"{path}:{finding}")
            total += 1
    print(f"{total} finding(s) in {len(files)} file(s)", file=sys.stderr)
    return 1 if total else 0


if __name__ == "__main__":  # pragma: no cover - a CLI over the same function
    raise SystemExit(_main(sys.argv[1:]))
