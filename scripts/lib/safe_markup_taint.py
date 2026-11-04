"""Find markup built out of raw values that is then marked safe (#3766).

``mark_safe(f"<svg width={size}>")`` is the banned shape (CLAUDE.md, Security Rules 1). The first
guard for it matched that literal shape only, and it would not have caught #3766 itself, where the
f-strings sat in a dict and the call was ``mark_safe(icons.get(name, ""))``. This module follows
the value one step further: through local variables, containers, ``+=`` / ``append`` and
``join``, within ONE function (or the module body).

What it flags
-------------
A call to ``mark_safe`` / ``SafeString`` / ``SafeText`` (any alias: ``from x import mark_safe as
ms``, ``ms = mark_safe``, ``safestring.mark_safe``), or the FORMAT STRING of ``format_html``, whose
argument (positional or keyword) is a string BUILT FROM A RAW VALUE:

* an f-string, ``%``, ``+`` or ``str.format`` with a piece that is not clean (below);
* ``"".join(items)`` where an item is not clean;
* a local variable that holds such a string (``s = f"..."``, ``s += x``), a local list that such a
  string was ``append``-ed / ``extend``-ed / ``insert``-ed to, or a local dict holding one
  (``icons.get(name, "")``, ``icons[name]``).

A piece is clean when it is: a literal; the result of ``escape`` / ``conditional_escape`` /
``format_html`` / ``format_html_join`` / ``escapejs`` / ``urlencode`` / ``quote`` / ``int`` /
``float`` / ``bool`` / ``len``; the output of a template engine (``render`` / ``render_to_string``,
autoescaped); ``str`` / ``.strip()`` / ``.lower()`` ... of a clean value; a local that only ever
holds clean values; or a lookup in a container of clean values (a module-level constant dict of
literals). Anything else, including a function parameter, an attribute (``obj.id``) and the result
of an unknown call, is raw.

What it does NOT see
--------------------
It reads one function at a time. A string built in one function and marked safe in another
(``build_theme_head_context`` builds ``css_block``; ``ThemeMixin`` marks it safe), a value stored
on ``self`` and read back, and a helper that returns an interpolated string are all invisible.
``mark_safe(x)`` where ``x`` is a bare parameter or an unknown call's result is not flagged
either: that is how trusted markup is passed in, and no syntax says whether ``x`` was escaped.
Each of those needs reading, which is what an audited allowlist entry records.

Refs #3766, #1859, #1468.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

SINKS = frozenset({"mark_safe", "SafeString", "SafeText"})
#: Sinks whose FIRST argument is a markup template: an interpolated one is the same hole.
FORMAT_SINKS = frozenset({"format_html"})

#: A call to one of these yields text that holds no raw markup, whatever its arguments.
CLEAN_CALLS = frozenset(
    {
        "escape",
        "conditional_escape",
        "format_html",
        "format_html_join",
        "escapejs",
        "urlencode",
        "quote",
        "quote_plus",
        "int",
        "float",
        "bool",
        "len",
        "ord",
        "hash",
        "id",
    }
)
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
        self.clean_calls = set(CLEAN_CALLS)
        self._collect_aliases()
        self.scopes: list[_Scope] = []
        self._build(self.tree, "<module>", None)

    # -- aliases ------------------------------------------------------------------------
    def _collect_aliases(self) -> None:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.asname is None:
                        continue
                    for names, group in (
                        (SINKS, self.sinks),
                        (FORMAT_SINKS, self.format_sinks),
                        (CLEAN_CALLS, self.clean_calls),
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
                    for group in (self.sinks, self.format_sinks, self.clean_calls):
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
                    for n in _target_names(target):
                        scope.assigns.setdefault(n, []).append(RAW)
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
            for n in _target_names(node.target):
                scope.assigns.setdefault(n, []).append(_ElementOf(node.iter))
        elif isinstance(node, ast.comprehension):
            # a comprehension variable is visible to a sink inside the comprehension
            for n in _target_names(node.target):
                scope.assigns.setdefault(n, []).append(_ElementOf(node.iter))
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
                self.clean(v.value, sc, d, seen)
                for v in e.values
                if isinstance(v, ast.FormattedValue)
            )
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
            return self.elements_clean(e.value, sc, d, seen)
        if isinstance(e, ast.Name):
            return self._name_clean(e.id, sc, d, seen)
        if isinstance(e, ast.Call):
            return self._call_clean(e, sc, d, seen)
        return False

    def _call_clean(self, e: ast.Call, sc: _Scope, d: int, seen: frozenset) -> bool:
        name = _callee(e.func)
        if name in self.clean_calls or name in RENDER_CALLS:
            return True
        args = [*e.args, *(kw.value for kw in e.keywords)]
        if name == "str" and isinstance(e.func, ast.Name):
            return all(self.clean(a, sc, d, seen) for a in args)
        if isinstance(e.func, ast.Attribute):
            recv = e.func.value
            if name == "join":
                return self.clean(recv, sc, d, seen) and all(
                    self.elements_clean(a, sc, d, seen) for a in args
                )
            if name == "format":
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
            for n in _target_names(gen.target):
                inner.assigns.setdefault(n, []).append(_ElementOf(gen.iter))
        return inner

    # tainted: a string BUILT from raw pieces
    def tainted(self, e: object, sc: _Scope, depth: int, seen: frozenset) -> bool:
        if depth > _DEPTH or not isinstance(e, ast.expr):
            return False
        d = depth + 1
        if isinstance(e, ast.JoinedStr):
            return any(
                not self.clean(v.value, sc, d, seen)
                for v in e.values
                if isinstance(v, ast.FormattedValue)
            )
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
            if name in self.clean_calls or name in RENDER_CALLS:
                return False
            if isinstance(e.func, ast.Attribute):
                recv = e.func.value
                if name == "join" and e.args:
                    return not self.clean(recv, sc, d, seen) or not all(
                        self.elements_clean(a, sc, d, seen) for a in e.args
                    )
                if name == "format":
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
