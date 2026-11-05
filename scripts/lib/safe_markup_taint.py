"""Find markup built out of raw values that is then marked safe (#3766, #3820).

``mark_safe(f"<svg width={size}>")`` is the banned shape (CLAUDE.md, Security Rules 1). The first
guard for it matched that literal shape only, and it would not have caught #3766 itself, where the
f-strings sat in a dict and the call was ``mark_safe(icons.get(name, ""))``. This module follows
the value one step further: through local variables, containers, ``+=`` / ``append`` and
``join``, within ONE function (or the module body).

It is NAME-based and SINGLE-FUNCTION. It reads the syntax tree; it does no type inference and it
does not follow a call into another module. The one thing it follows across a call is a function or
method THIS FILE defines (below). Read its verdict as "this function builds markup out of something
it cannot show is escaped", never as "this function is safe".

What it flags
-------------
A call to ``mark_safe`` / ``SafeString`` / ``SafeText`` or to one of their project wrappers
(``_safe`` in ``components/rust_handlers.py`` and ``components/templatetags/_dev_tools.py``,
``djust._html.safe_html``: ``cast(str, mark_safe(html))``, #3820) (any alias: ``from x import
mark_safe as ms``, ``ms = mark_safe``, ``safestring.mark_safe``; matched by NAME, so a local function
with that name is judged too), or the FORMAT STRING of ``format_html``, whose argument (positional or
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
  ``django.utils.html`` or ``html.escape``, or of ``int`` / ``float`` / ``bool`` / ``len``, or of one of
  the PROJECT's escapers: ``safe_url``, ``url_attr``, ``srcset_attr``, ``safe_css_color`` /
  ``_length`` / ``_ratio`` / ``_number`` (clean when their ``default`` is), ``attr_name``,
  ``escape_json_for_script``, and the record builders ``upload_attrs`` / ``voice_extras`` /
  ``map_picker_attrs`` / ``pad_extras`` / ``cropper_extras`` / ``menu_attrs`` / ``clamp_columns`` /
  ``clamp_cells`` / ``rich_html`` / ``render_markdown`` (and ``x.root`` of what they return). These
  are resolved through the module's imports to the module that DEFINES them (``from django.utils.html
  import escape as e`` counts, so does ``from djust.components.utils import safe_url`` or its relative
  spelling; ``from re import escape``, ``shlex.quote``, a re-export such as ``from ._registry import
  safe_url`` and a locally defined ``def escape(v): return v`` do NOT: only the ORIGIN is trusted,
  never the spelling). A name imported two different ways in one file is trusted nowhere. Each
  helper's contract is pinned by a hostile-payload test;
* ``min`` / ``max`` / ``round`` / ``abs`` / ``sum`` / ``str`` / ``string.Template`` of clean arguments;
* ``uuid.uuid4()`` / ``uuid.uuid1()`` / ``secrets.token_hex`` / ``.token_urlsafe`` and ``.hex`` of
  them (a fixed alphabet), a slice of a clean value;
* an f-string field with a numeric format spec (``{n:.2f}``, ``{n:d}``: digits only);
* an enumerate index (``for i, x in enumerate(xs)``), a ``range`` variable;
* the output of a ``.render(...)`` method or ``render_to_string`` (autoescaped by the template
  engine; matched by NAME, so a local ``def render`` is not trusted but ``obj.render()`` is), and the
  ``content`` parameter of a Rust block handler's ``render(self, args, content, context)`` in
  ``components/rust_handlers.py`` (the engine hands it over as an already-rendered ``SafeString``);
* ``.strip()`` / ``.lower()`` / ``.replace()`` ... of a clean value, a local that only ever holds
  clean values, or a lookup in a container of clean values (a module-level constant dict, or
  ``self.TABLE`` of a class that writes it out); ``d.get(k, DEFAULT)`` is clean only if ``DEFAULT``
  is (it is returned when ``k`` is missing);
* a call to a function or method THIS FILE defines exactly once (``helper(x)``, ``self.helper(x)``),
  when every value it returns is clean, judged in its own scope where its parameters are raw: so the
  verdict does not depend on the arguments.

FLOW (#3820). A name is judged by the bindings that REACH the use, not by every binding it has:
``width = kw.get("width", 400)`` then ``width = int(width)`` leaves ``f"{width}"`` clean, because the
only binding that reaches it is the ``int``. The analysis (``_Flow``) walks ``if`` / ``for`` / ``while``
/ ``try`` / ``with`` / ``match`` and merges what several paths leave behind, so a coercion in one
branch, a ``continue`` / ``break`` that skips it, a handler that runs after it half-ran, or a loop that
comes back round all keep the name raw. ``+=``, ``append`` and ``d[k] = v`` are not bindings and stay
flow-insensitive (they taint the name at every use). It gives a name up (the old, whole answer) rather
than guess for: ``global`` / ``nonlocal`` names, ``:=``, a binding inside ``finally``, ``import`` and
``match`` captures, ``locals()`` / ``exec``, a closure or lambda reading it, code the analysis never
reached, and any binding it did not record exactly as the guard did.

NARROWING (#3820). On the branch where it holds, ``x in COLLECTION`` (a tuple / list / set / dict /
``frozenset`` of literals, a module or local name that only holds one, or a class attribute the class
writes out), ``x == "a"`` and ``isinstance(x, int)`` (also ``float`` / ``bool``) make ``x`` a value the
developer wrote or a number. ``not``, ``and``, ``or``, ``if`` / ``else``, a conditional expression and
an early ``raise`` / ``return`` carry it to the right edge. ``assert`` narrows nothing (``python -O``
removes it), and a non-literal member, a collection that is mutated or reassigned anywhere in the
file, or ``self.X = ...`` shadowing the class attribute turns it off.

Anything else, including a function parameter, an attribute (``obj.id``) and the result of an
unknown call, is raw.

What it does NOT see (probed, and pinned in ``KNOWN_MISSES`` of its test)
--------------------------------------------------------------------------
* Anything that crosses a module or a file's own helper that is not clean: a string built in one
  function and marked safe in another (``build_theme_head_context`` builds ``css_block``;
  ``ThemeMixin`` marks it safe; the inline theme switcher builds ``preset_options`` into a returned
  f-string that ``LazyThemeHTML`` marks safe, so the switcher is protected by its own tests, not by
  this guard), a module global appended to in one function and joined in another, a value stored on
  ``self``, a helper that returns an interpolated string and is passed straight to the sink.
* ``mark_safe(x)`` where ``x`` is a bare parameter or an unknown call's result: that is how trusted
  markup is passed in, and no syntax says whether ``x`` was escaped.
* A sink reached without a call to it by name: ``partial(mark_safe, f"..")``,
  ``map(mark_safe, [..])``, ``getattr(safestring, "mark_safe")(..)``.
* ``functools.reduce`` over raw items, and any builder not listed above.
* Whether a name it trusts is the real thing: ``x.render()`` and ``.strip()`` are trusted by method
  name, whatever ``x`` is.
* WHERE a clean fragment is placed. ``format_html`` output, ``rich_html`` and the record builders are
  markup: right in an element, wrong inside an attribute value. The guard asks whether a value is raw,
  not which HTML context it lands in.
* A subclass in ANOTHER file that overrides a method or class attribute the guard resolved with
  something computed: that is the developer's own code, not request data. (One in the same file is
  seen: the name is no longer trusted.)

Each of those needs reading, which is what an audited allowlist entry records.

Refs #3766, #3775, #3820, #1859, #1468.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SINKS = frozenset({"mark_safe", "SafeString", "SafeText", "_safe", "safe_html"})
#: ``_safe`` (``components/rust_handlers.py``) and ``djust._html.safe_html`` are ``cast(str,
#: mark_safe(html))`` (#3820). They mark their argument safe exactly as ``mark_safe`` does, so an
#: interpolated string handed to one is the same hole; before #3820 they were not sinks and about 190
#: call sites were invisible to the guard, the audited baseline and the payload sweep.
WRAPPER_SINKS = frozenset({"_safe", "safe_html"})
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
        # The project's escapers (#3820), trusted by ORIGIN like django's: the module that DEFINES
        # them, never a re-export (a ``from ._registry import safe_url`` is not trusted, as for
        # ``conditional_escape``). Each contract is pinned by a hostile-payload test in
        # ``python/tests/test_safe_markup_guard_3820.py``.
        "djust.components.utils.safe_url",  # always conditional_escape()d, or "#"
        "djust.components.utils.url_attr",  # safe_url; or what the developer marked safe
        "djust.components.utils.srcset_attr",  # each URL through url_attr, descriptors escaped
        "djust._html.attr_name",  # a legal attribute NAME, or ValueError
        "djust.security.json_script.escape_json_for_script",  # < > & U+2028/9 as \uXXXX
    }
)
#: Origins whose RESULT is clean when the argument named ``default`` (by position) is: they return
#: the caller's value only if it matches a closed grammar (a colour, a length, a ratio, a number),
#: else ``default`` AS GIVEN, so a raw ``default`` is a raw result.
CLEAN_IF_DEFAULT_CLEAN = {
    "djust.components.utils.safe_css_color": 1,
    "djust.components.utils.safe_css_length": 1,
    "djust.components.utils.safe_css_ratio": 1,
    "djust.components.utils.safe_css_number": 1,
}
#: Origins whose result is a string, a tuple or a NamedTuple of strings built from constant markup,
#: ``html.escape``d text and ``int()``-coerced numbers: ``pad_extras(...)``, ``root, zone, field =
#: upload_attrs(...)`` and ``extras.root`` are clean. ``rich_html`` is here for its CONTRACT (a value
#: marked safe, else the Markdown component's allowlist sanitiser) and, like ``format_html``, is
#: trusted without regard to WHERE the guard cannot see it placed: its output is markup, right in an
#: element and wrong inside an attribute value.
CLEAN_RECORD_ORIGINS = frozenset(
    {
        "djust.components.components.signature_pad.pad_extras",
        "djust.components.components.voice_input.voice_extras",
        "djust.components.components.image_upload_preview.upload_attrs",
        "djust.components.components.map_picker.map_picker_attrs",
        "djust.components.components.image_cropper.cropper_extras",
        "djust.components.components.markdown_editor.menu_attrs",  # two fixed "true" / "false"
        "djust.markdown.render_markdown",  # the Rust renderer: raw tags escaped, script URLs "#"
        "djust.components.components.dashboard_grid.clamp_columns",  # an int
        "djust.components.components.dashboard_grid.clamp_cells",  # a tuple of ints
        "djust.components.utils.rich_html",
    }
)
#: ``module -> {(method, parameter)}``: a parameter the template engine hands a handler as the
#: already-rendered, already-escaped body of a block tag. ``call_block_handler_with_py_sidecar``
#: (``crates/djust_templates/src/registry.rs``) passes it as a ``SafeString`` ("Django's
#: ``simple_block_tag`` hands the handler ``nodelist.render(context)``", #2379); the Django twin's
#: ``self.nodelist.render(context)`` is trusted by name already (``RENDER_CALLS``). Applies to a
#: method with exactly the block-handler signature ``(self, args, content, context)`` of THAT module.
ENGINE_RENDERED_PARAMETERS = {"djust.components.rust_handlers": {("render", "content")}}
#: Names imported from the module that DEFINES them as a literal string: markup the developer wrote.
#: Pinned by ``test_safe_markup_guard_3820.py`` (the source still binds each to a string literal).
CLEAN_NAME_ORIGINS = frozenset({"djust.components.components.image_upload_preview.ITEMS_HTML"})
#: Builtins whose instances print only digits, ``True`` / ``False``, ``inf`` or ``nan``.
NUMERIC_TYPES = frozenset({"int", "float", "bool"})
#: Origins that are clean when EVERY argument is clean (a number out of numbers, text out of text).
CLEAN_IF_ARGS_CLEAN = frozenset({"min", "max", "round", "abs", "sum", "str", "string.Template"})
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


class _Def:
    """One binding of a name: what reaches a use is a SET of these (see :class:`_Flow`)."""

    __slots__ = ("name", "value")

    def __init__(self, name: str, value: object) -> None:
        self.name = name
        self.value = value


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
        #: id(AST node) -> the ``(name, value)`` pairs it appended to ``assigns``: the flow analysis
        #: reads the SAME value objects back, so a binding it passes is the binding that was recorded.
        self.node_defs: dict[int, list[tuple[str, object]]] = {}
        #: ids of values bound by a comprehension (their variable does not leak into the function).
        self.comp_values: set[int] = set()
        #: name -> the ``def`` / ``class`` statements that bind it.
        self.def_nodes: dict[str, list[ast.AST]] = {}
        #: names bound by something that gives them no recorded value: ``import x``, a ``match``
        #: capture. They count as one more RAW binding wherever the name's value is judged.
        self.opaque: set[str] = set()
        #: parameter name -> the value it is bound to on entry (RAW unless the engine vouches)
        self.params: dict[str, object] = {}
        #: names bound by an annotated assignment (``label: str = "x"``): in a dataclass, a NamedTuple
        #: or a model that is a DEFAULT the constructor replaces, not a constant
        self.annotated: set[str] = set()

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
    def __init__(self, source: str, module: str | None = None, is_package: bool = False) -> None:
        self.source = source
        #: The dotted name this source is importable as (``djust.components.utils``), when known.
        #: It resolves a relative import to its ORIGIN and names the functions this file defines.
        self.module = module
        self.is_package = is_package
        self.tree = ast.parse(source)
        #: names declared ``global`` / ``nonlocal`` anywhere, and attributes assigned to anywhere
        #: (``self.X = ...``): one walk, because the class-attribute rules ask per use
        self.rebound_names: set[str] = set()
        self.assigned_attrs: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                self.rebound_names.update(node.names)
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
                self.assigned_attrs.add(node.attr)
        self.sinks = set(SINKS)
        self.format_sinks = set(FORMAT_SINKS)
        #: local name -> dotted origin, or ``None`` when the file imports it as two different things
        self.imports: dict[str, str | None] = {}
        self._collect_imports()
        self._collect_aliases()
        self.scopes: list[_Scope] = []
        #: id(``ast.Name``) -> (scope, the definitions that can reach it); see :class:`_Flow`
        self.reach: dict[int, tuple[_Scope, tuple[_Def, ...]]] = {}
        #: id(function node) -> the scope it opens (to judge a call to it by what it returns)
        self.scope_of: dict[int, _Scope] = {}
        self._build(self.tree, "<module>", None)
        for scope in self.scopes:
            if isinstance(scope.node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _Flow(self, scope).run()

    # -- imports and aliases --------------------------------------------------------------
    def _collect_imports(self) -> None:
        """local name -> the dotted name it was imported as (``escape`` -> ``django.utils.html.escape``)."""
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        self._import(alias.asname, alias.name)
                    else:
                        top = alias.name.split(".")[0]
                        self._import(top, top)
            elif isinstance(node, ast.ImportFrom):
                module = self._absolute(node.level, node.module or "")
                for alias in node.names:
                    self._import(alias.asname or alias.name, f"{module}.{alias.name}")

    def _import(self, local: str, origin: str) -> None:
        """Record an import; a name imported as two different things has NO origin (``None``).

        ``imports`` is one map for the whole file, so ``from django.utils.html import escape`` in one
        function and ``from re import escape`` in another would otherwise let the last one win.
        """
        if local in self.imports and self.imports[local] != origin:
            self.imports[local] = None
        else:
            self.imports.setdefault(local, origin)

    def _absolute(self, level: int, module: str) -> str:
        """``from {'.' * level}{module}`` as a dotted path from the top, else the dots kept (unresolved)."""
        if not level:
            return module
        if self.module is None:
            return "." * level + module
        parts = self.module.split(".")
        keep = len(parts) - (level - 1 if self.is_package else level)
        if keep < 1:
            return "." * level + module
        return ".".join([*parts[:keep], *([module] if module else [])])

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
                self.module is not None
                and isinstance(owner.node, ast.Module)
                and e.id not in self.imports  # a name also imported is ambiguous: neither
                and len(values) == 1
                and len(owner.def_nodes.get(e.id, [])) == 1
                and not owner.contents.get(e.id)
                and not owner.pieces.get(e.id)
            ):
                # the one ``def`` this file gives the name at module level: ITS origin is here
                return f"{self.module}.{e.id}"
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
        self.scope_of[id(node)] = scope
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = node.args
            every = [*args.posonlyargs, *args.args, *args.kwonlyargs]
            if args.vararg:
                every.append(args.vararg)
            if args.kwarg:
                every.append(args.kwarg)
            engine = self._engine_parameters(node)
            for arg in every:
                value: object = ast.Constant("") if arg.arg in engine else RAW
                scope.assigns.setdefault(arg.arg, []).append(value)
                scope.params[arg.arg] = value
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

    def _engine_parameters(self, node: ast.AST) -> set[str]:
        """The parameters of a block-handler ``render`` the engine passes as rendered markup."""
        if self.module is None or not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return set()
        wanted = {p for m, p in ENGINE_RENDERED_PARAMETERS.get(self.module, ()) if m == node.name}
        shape = [a.arg for a in [*node.args.posonlyargs, *node.args.args]]
        if wanted and shape == ["self", "args", "content", "context"] and not node.args.vararg:
            return wanted
        return set()

    @staticmethod
    def _bind(scope: _Scope, node: ast.AST, name: str, value: object) -> None:
        """Bind ``name`` to ``value`` in ``scope`` on behalf of ``node`` (and remember which node)."""
        scope.assigns.setdefault(name, []).append(value)
        scope.node_defs.setdefault(id(node), []).append((name, value))

    def _record(self, scope: _Scope, node: ast.AST) -> None:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self._bind(scope, node, target.id, node.value)
                elif isinstance(target, (ast.Tuple, ast.List)):
                    self._bind_unpack(scope, node, target, node.value)
                elif isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
                    scope.contents.setdefault(target.value.id, []).append(node.value)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if isinstance(node.target, ast.Name):
                self._bind(scope, node, node.target.id, node.value)
                scope.annotated.add(node.target.id)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            n = node.target.id
            if isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
                scope.contents.setdefault(n, []).extend(node.value.elts)
            elif isinstance(node.op, (ast.Add, ast.Mod)):
                scope.pieces.setdefault(n, []).append(node.value)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            self._bind_iteration(scope, node, node.target, node.iter)
        elif isinstance(node, ast.comprehension):
            # a comprehension variable is visible to a sink inside the comprehension
            self._bind_iteration(scope, node, node.target, node.iter, comprehension=True)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # a ``def escape`` is not django's ``escape``: it shadows what it is named like
            self._bind(scope, node, node.name, RAW)
            scope.def_nodes.setdefault(node.name, []).append(node)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            scope.opaque.update((a.asname or a.name).split(".")[0] for a in node.names)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            scope.opaque.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            scope.opaque.add(node.rest)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    for n in _target_names(item.optional_vars):
                        self._bind(scope, node, n, RAW)
        elif isinstance(node, ast.NamedExpr):
            self._bind(scope, node, node.target.id, node.value)
        elif isinstance(node, (ast.ExceptHandler,)) and node.name:
            self._bind(scope, node, node.name, RAW)
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

    def _bind_unpack(self, scope: _Scope, key: ast.AST, target: ast.expr, value: ast.expr) -> None:
        """``a, b = x, y`` binds each name to its own element; any other unpack binds RAW.

        ``a, b = call()`` binds each name to an ELEMENT of the call's result, which is clean when the
        call is one of the record builders (``root, zone, field = upload_attrs(...)``) and raw
        otherwise, exactly as before.
        """
        if (
            isinstance(target, (ast.Tuple, ast.List))
            and isinstance(value, (ast.Tuple, ast.List))
            and len(target.elts) == len(value.elts)
            and not any(isinstance(x, ast.Starred) for x in [*target.elts, *value.elts])
        ):
            for t, v in zip(target.elts, value.elts):
                if isinstance(t, (ast.Tuple, ast.List)):
                    self._bind_unpack(scope, key, t, v)
                elif isinstance(t, ast.Name):
                    self._bind(scope, key, t.id, v)
            return
        for n in _target_names(target):
            self._bind(scope, key, n, _ElementOf(value) if isinstance(value, ast.Call) else RAW)

    def _bind_iteration(
        self,
        scope: _Scope,
        key: ast.AST,
        target: ast.expr,
        iterable: ast.expr,
        comprehension: bool = False,
    ) -> None:
        """Bind a ``for`` / comprehension target; ``for i, x in enumerate(xs)`` gives ``i`` a number."""
        before = {n: len(v) for n, v in scope.assigns.items()}
        if (
            isinstance(iterable, ast.Call)
            and self.origin(iterable.func, scope) == "enumerate"
            and iterable.args
            and isinstance(target, ast.Tuple)
            and len(target.elts) == 2
        ):
            index, item = target.elts
            for n in _target_names(index):
                self._bind(scope, key, n, ast.Constant(0))
            for n in _target_names(item):
                self._bind(scope, key, n, _ElementOf(iterable.args[0]))
        else:
            for n in _target_names(target):
                self._bind(scope, key, n, _ElementOf(iterable))
        if comprehension:
            for n, values in scope.assigns.items():
                scope.comp_values.update(id(v) for v in values[before.get(n, 0) :])

    # -- constant collections (what a membership test narrows against) ---------------------
    @staticmethod
    def _literal(e: ast.expr) -> bool:
        if isinstance(e, ast.Constant):
            return True
        return (
            isinstance(e, ast.UnaryOp)
            and isinstance(e.op, (ast.USub, ast.UAdd))
            and isinstance(e.operand, ast.Constant)
        )

    def _mutated_or_rebound(self, name: str) -> bool:
        """Whether anything in the file grows, fills or rebinds ``name`` from another scope."""
        if any(name in s.contents or name in s.pieces for s in self.scopes):
            return True
        return name in self.rebound_names

    def constant_set(self, e: ast.expr, sc: _Scope, depth: int = 0) -> bool:
        """``e`` is a collection the developer wrote out, every member (or key) a literal.

        ``x in e`` can then only hold when ``x`` equals one of those literals. A tuple, list, set or
        dict display; ``frozenset({...})`` and its kin; a name that only ever holds one (a module
        constant, a local); or ``self.NAME`` / ``cls.NAME`` / ``Class.NAME`` for a class attribute the
        class writes out and nothing in the file reassigns or fills. A subclass that overrides the
        attribute with something computed is the developer's own decision, not request data.
        """
        if depth > 6:
            return False
        if isinstance(e, (ast.Tuple, ast.List, ast.Set)):
            return all(self._literal(x) for x in e.elts)
        if isinstance(e, ast.Dict):
            return all(k is not None and self._literal(k) for k in e.keys)
        if (
            isinstance(e, ast.Call)
            and len(e.args) == 1
            and not e.keywords
            and self.origin(e.func, sc) in ("frozenset", "set", "tuple", "list")
        ):
            return self.constant_set(e.args[0], sc, depth + 1)
        if isinstance(e, ast.Name):
            owner = sc.find(e.id)
            if owner is None or self._mutated_or_rebound(e.id):
                return False
            values = owner.assigns.get(e.id, [])
            return bool(values) and all(
                isinstance(v, ast.expr) and self.constant_set(v, owner, depth + 1) for v in values
            )
        if isinstance(e, ast.Attribute):
            found = self.class_attribute(e, sc)
            if found is None:
                return False
            cls, values = found
            return all(
                isinstance(v, ast.expr) and self.constant_set(v, cls, depth + 1) for v in values
            )
        return False

    def class_attribute(self, e: ast.expr, sc: _Scope) -> tuple[_Scope, list[object]] | None:
        """``self.NAME`` / ``cls.NAME`` / ``Class.NAME`` in a method: the class scope and every value
        the class body binds ``NAME`` to, when nothing else can: no ``self.NAME = ...`` anywhere in
        the file shadows it, nothing fills or rebinds it. ``None`` when that is not so (or unknown).
        """
        if not (isinstance(e, ast.Attribute) and isinstance(e.value, ast.Name)):
            return None
        cls: _Scope | None = sc
        while cls is not None and not isinstance(cls.node, ast.ClassDef):
            cls = cls.parent
        if cls is None or e.value.id not in ("self", "cls", cls.node.name):  # type: ignore[attr-defined]
            return None
        if cls.node.decorator_list or e.attr in cls.annotated:  # type: ignore[attr-defined]
            return None  # a dataclass / NamedTuple field is a default the constructor replaces
        if self._mutated_or_rebound(e.attr) or e.attr in self.assigned_attrs:
            return None
        values = cls.assigns.get(e.attr, [])
        # a ``def`` / ``class`` of that name is a method or a nested class, not a constant
        if not values or e.attr in cls.def_nodes or self._overridden_in_file(cls, e.attr):
            return None
        return cls, values

    def _overridden_in_file(self, cls: _Scope, attr: str) -> bool:
        """A class of THIS file that inherits from ``cls`` (directly or through others of this file)
        and binds ``attr`` itself: ``self.attr`` may be its value, not ``cls``'s. A subclass in
        another file cannot be seen (the module docstring says so)."""
        names = {cls.node.name}  # type: ignore[attr-defined]
        pending = [x for x in self.scopes if isinstance(x.node, ast.ClassDef) and x is not cls]
        subclasses: list[_Scope] = []
        grew = True
        while grew:
            grew = False
            for scope in list(pending):
                bases = {
                    b.id if isinstance(b, ast.Name) else b.attr
                    for b in scope.node.bases  # type: ignore[attr-defined]
                    if isinstance(b, (ast.Name, ast.Attribute))
                }
                if bases & names:
                    pending.remove(scope)
                    subclasses.append(scope)
                    names.add(scope.node.name)  # type: ignore[attr-defined]
                    grew = True
        return any(attr in x.assigns or attr in x.def_nodes for x in subclasses)

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
        if isinstance(e, (ast.ListComp, ast.GeneratorExp, ast.SetComp, ast.DictComp)):
            # like a display: clean when what it produces is (``max(len(r) for r in rows)``)
            inner = self._comprehension_scope(e, sc)
            elt = e.value if isinstance(e, ast.DictComp) else e.elt
            return self.clean(elt, inner, d, seen)
        if isinstance(e, ast.Subscript):
            if isinstance(e.slice, ast.Slice):  # a substring of clean text is clean text
                return self.clean(e.value, sc, d, seen)
            return self.elements_clean(e.value, sc, d, seen)
        if isinstance(e, ast.Attribute):
            # ``uuid.uuid4().hex``: a fixed alphabet
            if (
                e.attr == "hex"
                and isinstance(e.value, ast.Call)
                and self.origin(e.value.func, sc) in SAFE_ALPHABET_ORIGINS
            ):
                return True
            # ``extras.root`` of ``extras = pad_extras(...)``: a field of a record of clean strings
            if self._is_record(e.value, sc, d, seen):
                return True
            # ``self.LABEL`` of a class body that writes it out
            found = self.class_attribute(e, sc)
            return found is not None and all(self.clean(v, found[0], d, seen) for v in found[1])
        if isinstance(e, ast.Name):
            if self._origin_of_import(e, sc) in CLEAN_NAME_ORIGINS:
                return True
            return self._name_clean(e, sc, d, seen)
        if isinstance(e, ast.Call):
            return self._call_clean(e, sc, d, seen)
        return False

    def _origin_of_import(self, e: ast.Name, sc: _Scope) -> str | None:
        """The origin of ``e`` when it is a bare imported name nothing in the code rebinds."""
        if sc.find(e.id) is not None:
            return None
        return self.imports.get(e.id)

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
        if origin in CLEAN_RECORD_ORIGINS:
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
        origin = self.origin(e.func, sc)
        if origin in CLEAN_IF_ARGS_CLEAN:
            return all(self.clean(a, sc, d, seen) for a in args)
        local = self._local_function(e.func, sc)
        if local is not None:
            return self._returns_clean(local, d, seen)
        if origin in CLEAN_IF_DEFAULT_CLEAN:
            default = self._default_arg(e, CLEAN_IF_DEFAULT_CLEAN[origin])
            return default is None or self.clean(default, sc, d, seen)
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
                # ``d.get(k, default)`` hands back ``default`` when ``k`` is missing: it is a result too
                return self.elements_clean(recv, sc, d, seen) and all(
                    self.clean(a, sc, d, seen) for a in self._defaults(e)
                )
        return False

    @staticmethod
    def _defaults(call: ast.Call) -> list[ast.expr]:
        """What ``get`` / ``pop`` / ``setdefault`` return when the key is absent: every argument after
        the key, and ``default=``."""
        return [*call.args[1:], *(kw.value for kw in call.keywords)]

    def _local_function(
        self, func: ast.expr, sc: _Scope
    ) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef, _Scope] | None:
        """The ``def`` a call reaches, when it is a function or method THIS file defines exactly once.

        ``helper(...)`` (a module function or one nested in the caller) or ``self.helper(...)`` /
        ``cls.helper(...)`` (a method of the enclosing class). Nothing else binds the name, no
        ``self.helper = ...`` shadows it, and it carries no decorator but ``staticmethod`` /
        ``classmethod`` (any other decorator may return something else). A subclass that overrides
        the method is the developer's own code, not request data.
        """
        if isinstance(func, ast.Name):
            owner = sc.find(func.id)
            if owner is None or func.id in owner.opaque:
                return None
            name, holder = func.id, owner
        elif (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and not (self._mutated_or_rebound(func.attr))
        ):
            cls: _Scope | None = sc
            while cls is not None and not isinstance(cls.node, ast.ClassDef):
                cls = cls.parent
            if cls is None or func.value.id not in ("self", "cls", cls.node.name):  # type: ignore[attr-defined]
                return None
            if cls.node.decorator_list:  # type: ignore[attr-defined]
                return None
            if func.attr in self.assigned_attrs or self._overridden_in_file(cls, func.attr):
                return None
            name, holder = func.attr, cls
        else:
            return None
        defs = holder.def_nodes.get(name, [])
        if (
            len(defs) != 1
            or len(holder.assigns.get(name, [])) != 1
            or holder.contents.get(name)
            or holder.pieces.get(name)
            or not isinstance(defs[0], (ast.FunctionDef, ast.AsyncFunctionDef))
        ):
            return None
        fn = defs[0]
        for deco in fn.decorator_list:
            if not (isinstance(deco, ast.Name) and deco.id in ("staticmethod", "classmethod")):
                return None
        if any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in _own_nodes(fn)):
            return None  # a generator: its call returns an iterator, not what it yields
        return fn, self.scope_of[id(fn)]

    def _returns_clean(
        self, local: tuple[ast.FunctionDef | ast.AsyncFunctionDef, _Scope], d: int, seen: frozenset
    ) -> bool:
        """Every value the function returns is clean, judged in ITS scope where the parameters are raw.

        So the verdict does not depend on the arguments of a call: ``def item(x): return f"<i>{x}</i>"``
        is raw at every call site, and one that escapes ``x`` itself is clean at every call site.
        """
        fn, fscope = local
        key = ("returns", id(fn))
        if key in seen or d > _DEPTH:
            return key in seen  # a recursive call adds nothing new
        seen = seen | {key}
        returns = [n for n in _own_nodes(fn) if isinstance(n, ast.Return)]
        return all(r.value is None or self.clean(r.value, fscope, d + 1, seen) for r in returns)

    @staticmethod
    def _default_arg(call: ast.Call, position: int) -> ast.expr | None:
        """The ``default`` of ``safe_css_*(value, default)``: by position, or by keyword."""
        if len(call.args) > position:
            return call.args[position]
        for kw in call.keywords:
            if kw.arg == "default":
                return kw.value
        return None

    def _bindings(
        self, node: ast.Name | None, name: str, owner: _Scope
    ) -> tuple[list[object], tuple[int, ...] | None]:
        """The values ``name`` can hold at this use, and a key that tells those apart.

        With a ``node`` the flow analysis answered for (:class:`_Flow`) it is only the bindings that
        REACH that use; otherwise (a closure, a lambda, the module body, a name the analysis could not
        vouch for) it is every binding the name has anywhere in its scope, as before.
        """
        if node is not None:
            hit = self.reach.get(id(node))
            if hit is not None and hit[0] is owner and hit[1]:
                return [x.value for x in hit[1]], tuple(sorted(id(x) for x in hit[1]))
        values = owner.assigns.get(name, [])
        return ([*values, RAW] if name in owner.opaque else values), None

    def _is_record(self, e: ast.expr, sc: _Scope, d: int, seen: frozenset) -> bool:
        """``e`` is a record builder's result (``pad_extras(...)``) or a name that only holds one."""
        if d > _DEPTH:
            return False
        if isinstance(e, ast.Call):
            return self.origin(e.func, sc) in CLEAN_RECORD_ORIGINS
        if isinstance(e, ast.Name):
            owner = sc.find(e.id)
            if owner is None:
                return False
            values, defs = self._bindings(e, e.id, owner)
            key = ("record", e.id, id(owner), defs)
            if key in seen:
                return True
            seen = seen | {key}
            return (
                bool(values)
                and not owner.pieces.get(e.id)
                and not owner.contents.get(e.id)
                and all(
                    isinstance(v, ast.expr) and self._is_record(v, owner, d + 1, seen)
                    for v in values
                )
            )
        return False

    def _name_clean(self, node: ast.Name, sc: _Scope, d: int, seen: frozenset) -> bool:
        name = node.id
        owner = sc.find(name)
        if owner is None:
            return False
        values, defs = self._bindings(node, name, owner)
        key = ("clean", name, id(owner), defs)
        if key in seen:
            return True  # a cycle adds nothing new
        seen = seen | {key}
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
            values, defs = self._bindings(e, e.id, owner)
            key = ("elements", e.id, id(owner), defs)
            if key in seen:
                return True
            seen = seen | {key}
            if not values and not owner.contents.get(e.id):
                return False
            return all(self.elements_clean(v, owner, d, seen) for v in values) and all(
                self.clean(c, owner, d, seen) for c in owner.contents.get(e.id, [])
            )
        if isinstance(e, ast.Attribute):
            found = self.class_attribute(e, sc)  # ``self.ICONS``: a table the class writes out
            return found is not None and all(
                self.elements_clean(v, found[0], d, seen) for v in found[1]
            )
        if isinstance(e, ast.Call):
            name = _callee(e.func)
            if self.origin(e.func, sc) in CLEAN_RECORD_ORIGINS:
                return True  # every field of the record is clean
            if isinstance(e.func, ast.Name) and name in ELEMENT_CALLS and e.args:
                return self.elements_clean(e.args[0], sc, d, seen)
            if isinstance(e.func, ast.Attribute):
                if name in CONTAINER_METHODS:
                    return self.elements_clean(e.func.value, sc, d, seen) and all(
                        self.elements_clean(a, sc, d, seen) for a in self._defaults(e)
                    )
                if name in ITERATION_METHODS:
                    return self.elements_clean(e.func.value, sc, d, seen)
            if isinstance(e.func, ast.Name) and name == "range":
                return True
        return False

    def _comprehension_scope(self, e: ast.expr, sc: _Scope) -> _Scope:
        inner = _Scope(e, sc.name, sc)
        for gen in e.generators:  # type: ignore[attr-defined]
            self._bind_iteration(inner, gen, gen.target, gen.iter)
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
            return self._name_tainted(e, sc, d, seen)
        if isinstance(e, ast.Call):
            name = _callee(e.func)
            if self._trusted_call(e, sc):
                return False
            origin = self.origin(e.func, sc)
            if origin in CLEAN_IF_DEFAULT_CLEAN:
                default = self._default_arg(e, CLEAN_IF_DEFAULT_CLEAN[origin])
                return default is not None and self.tainted(default, sc, d, seen)
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

    def _name_tainted(self, node: ast.Name, sc: _Scope, d: int, seen: frozenset) -> bool:
        name = node.id
        owner = sc.find(name)
        if owner is None:
            return False
        values, defs = self._bindings(node, name, owner)
        key = ("tainted", name, id(owner), defs)
        if key in seen:
            return False
        seen = seen | {key}
        return (
            any(self.tainted(v, owner, d, seen) for v in values)
            or any(not self.clean(p, owner, d, seen) for p in owner.pieces.get(name, []))
            or any(self.tainted(c, owner, d, seen) for c in owner.contents.get(name, []))
        )


class _Unsupported(Exception):
    """The flow analysis met something it will not reason about: the function keeps the old,
    flow-insensitive answer (every binding of a name reaches every use)."""


State = dict  # name -> frozenset[_Def]; treated as IMMUTABLE (a binding copies it)


def _merge(*states: State | None) -> State | None:
    """The state where several paths meet: a name can hold anything any path gave it."""
    live = [s for s in states if s is not None]
    if not live:
        return None
    if len(live) == 1:
        return live[0]
    out: State = {}
    for state in live:
        for name, defs in state.items():
            out[name] = out[name] | defs if name in out else defs
    return out


_TRY_TYPES = tuple({ast.Try, getattr(ast, "TryStar", ast.Try)})


class _Flow:
    """Which bindings of a name can reach each of its uses, inside ONE function (#3820).

    The guard used to treat every binding of a name as reaching every use, so ``width =
    kw.get("width", 400)`` followed by ``width = int(width)`` left ``f"{width}"`` flagged: the
    first binding is raw, and it does not matter that it is dead by then. This computes, for every
    ``Name`` read in the function's own statements, the SET of bindings that can reach it, over the
    statement structure (``if`` / ``for`` / ``while`` / ``try`` / ``with`` / ``match``), so a use
    after an unconditional ``width = int(width)`` sees only the ``int``.

    It errs towards NOT clearing a name, and gives the function up (``_Unsupported``) rather than
    guess:

    * a use is judged by the UNION of the bindings of every path that reaches it, so one path that
      leaves the raw value in place keeps the name raw (``if c: width = int(width)``);
    * ``+=``, ``append``, ``d[k] = v`` and the like are not bindings and stay flow-INSENSITIVE: they
      taint the name at every use (``contents`` / ``pieces``);
    * a name declared ``global`` / ``nonlocal``, bound by ``:=`` or inside a ``finally``, or in a
      function that calls ``locals()`` / ``vars()`` / ``globals()`` / ``exec`` / ``eval``, is never
      cleared; neither is a name bound by something the guard does not record (``import``,
      ``match`` captures, a binding in code the analysis never reached): the bindings the analysis
      found must be EXACTLY the ones the guard recorded for the name, or the name keeps every binding;
    * a closure, a lambda, a class body and the module body read the old, whole answer;
    * any exception can leave a ``try`` body anywhere, and a ``with`` can swallow one, so a handler
      (and what follows a ``with``) sees every binding made inside plus every binding from before.

    A condition can NARROW a name (``x in ALLOWED``, ``x == "a"``, ``isinstance(x, int)``) on the
    branch where it holds: the name then holds a value the developer wrote or a number, not the raw
    one. ``assert`` narrows nothing (``python -O`` removes it).
    """

    MAX_PASSES = 25

    def __init__(self, mod: _Module, scope: _Scope) -> None:
        self.mod = mod
        self.scope = scope
        self.fn = scope.node
        self.defs: dict[tuple, _Def] = {}
        self.created: dict[str, list[_Def]] = {}
        self.collectors: list[dict[str, set[_Def]]] = []
        self.loops: list[dict[str, list[State | None]]] = []
        self.uses: dict[int, tuple[str, set[_Def]]] = {}
        self.disabled: set[str] = set()

    # -- driver ---------------------------------------------------------------------------
    def run(self) -> None:
        self.disabled = self._disabled_names()
        try:
            self._analyse()
        except (_Unsupported, RecursionError):
            return
        bad = self._inconsistent_names() | self.disabled
        for key, (name, defs) in self.uses.items():
            if name not in bad:
                self.mod.reach[key] = (self.scope, tuple(defs))

    def _analyse(self) -> None:
        args = self.fn.args  # type: ignore[attr-defined]
        every = [*args.posonlyargs, *args.args, *args.kwonlyargs]
        if args.vararg:
            every.append(args.vararg)
        if args.kwarg:
            every.append(args.kwarg)
        state: State = {}
        for arg in every:
            value = self.scope.params[arg.arg]
            state[arg.arg] = frozenset({self._def(("param", arg.arg), arg.arg, value)})
        self.block(self.fn.body, state)  # type: ignore[attr-defined]

    def _disabled_names(self) -> set[str]:
        out: set[str] = set()
        for node in ast.walk(self.fn):
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                out.update(node.names)
            elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
                out.add(node.target.id)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ("locals", "vars", "globals", "exec", "eval")
            ):
                # nothing in this function can be vouched for
                return set(self.scope.assigns)
            elif isinstance(node, _TRY_TYPES):
                for stmt in node.finalbody:
                    for sub in ast.walk(stmt):
                        if isinstance(sub, ast.Name) and isinstance(sub.ctx, (ast.Store, ast.Del)):
                            out.add(sub.id)
                        elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                            out.add(sub.name)
                        elif isinstance(sub, ast.ExceptHandler) and sub.name:
                            out.add(sub.name)
                        elif isinstance(sub, (ast.Import, ast.ImportFrom)):
                            out.update((a.asname or a.name).split(".")[0] for a in sub.names)
        return out

    def _inconsistent_names(self) -> set[str]:
        """Names whose bindings the analysis did not see exactly as the guard recorded them."""
        bad: set[str] = set()
        scope = self.scope
        for name, values in scope.assigns.items():
            expected = sorted(id(v) for v in values if id(v) not in scope.comp_values)
            got = sorted(id(d.value) for d in self.created.get(name, []))
            if expected != got:
                bad.add(name)
        bad.update(set(self.created) - set(scope.assigns))
        return bad

    # -- bindings --------------------------------------------------------------------------
    def _def(self, key: tuple, name: str, value: object, counted: bool = True) -> _Def:
        found = self.defs.get(key)
        if found is None:
            found = self.defs[key] = _Def(name, value)
            if counted:
                self.created.setdefault(name, []).append(found)
        return found

    def _note(self, state: State, name: str, d: _Def) -> None:
        state[name] = frozenset({d})
        for coll in self.collectors:
            coll.setdefault(name, set()).add(d)

    def bind(self, node: ast.AST, state: State) -> State:
        pairs = self.scope.node_defs.get(id(node))
        if not pairs:
            return state
        new = dict(state)
        for i, (name, value) in enumerate(pairs):
            self._note(new, name, self._def((id(node), i), name, value))
        return new

    def bind_opaque(self, key: tuple, names: list[str], state: State) -> State:
        """Names bound by something the guard does not record (``import``, a ``match`` capture)."""
        new = dict(state)
        for name in names:
            self._note(new, name, self._def((*key, name), name, RAW))
        return new

    # -- uses ------------------------------------------------------------------------------
    def _load(self, node: ast.Name, state: State) -> None:
        defs = state.get(node.id)
        if defs:
            self.uses.setdefault(id(node), (node.id, set()))[1].update(defs)

    def loads(self, e: ast.AST | None, state: State | None) -> None:
        if e is None or state is None:
            return
        if isinstance(e, ast.Name):
            if isinstance(e.ctx, ast.Load):
                self._load(e, state)
        elif isinstance(e, ast.Lambda):
            # the body runs later, with whatever the names hold then: the old, whole answer
            for default in [*e.args.defaults, *(d for d in e.args.kw_defaults if d)]:
                self.loads(default, state)
        elif isinstance(e, ast.GeneratorExp):
            # lazy: only the first iterable is evaluated now. The rest runs when the generator is
            # CONSUMED, with whatever the names hold then (``w`` may be rebound in between)
            self.loads(e.generators[0].iter, state)
        elif isinstance(e, ast.BoolOp):
            cur: State | None = state
            for value in e.values:
                self.loads(value, cur)
                if cur is None:
                    break
                true, false = self.split(value, cur)
                cur = true if isinstance(e.op, ast.And) else false
        elif isinstance(e, ast.IfExp):
            self.loads(e.test, state)
            true, false = self.split(e.test, state)
            self.loads(e.body, true)
            self.loads(e.orelse, false)
        else:
            for child in ast.iter_child_nodes(e):
                self.loads(child, state)

    def loads_target(self, target: ast.expr, state: State) -> None:
        """The names an assignment target READS (``d[k] = v`` reads ``d`` and ``k``)."""
        if isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self.loads_target(elt, state)
        elif isinstance(target, ast.Starred):
            self.loads_target(target.value, state)
        elif isinstance(target, (ast.Subscript, ast.Attribute)):
            self.loads(target, state)

    # -- conditions ------------------------------------------------------------------------
    def narrow(self, state: State, name: ast.Name, key: ast.AST) -> State:
        """``name`` holds a value the developer wrote (or a number) from here: one clean binding."""
        if name.id not in state or name.id in self.disabled:
            return state
        new = dict(state)
        new[name.id] = frozenset(
            {self._def(("narrow", id(key), name.id), name.id, ast.Constant(""), counted=False)}
        )
        return new

    def split(self, test: ast.expr, state: State) -> tuple[State | None, State | None]:
        """The state where ``test`` is true and the state where it is false (``None``: never)."""
        if isinstance(test, ast.BoolOp):
            cur: State | None = state
            outs: list[State | None] = []
            for value in test.values:
                if cur is None:
                    break
                true, false = self.split(value, cur)
                if isinstance(test.op, ast.And):
                    outs.append(false)
                    cur = true
                else:
                    outs.append(true)
                    cur = false
            merged = _merge(*outs)
            return (cur, merged) if isinstance(test.op, ast.And) else (merged, cur)
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            true, false = self.split(test.operand, state)
            return false, true
        if isinstance(test, ast.Constant):
            return (state, None) if test.value else (None, state)
        if isinstance(test, ast.Compare) and len(test.ops) == 1:
            op, left, right = test.ops[0], test.left, test.comparators[0]
            if isinstance(op, (ast.In, ast.NotIn)) and isinstance(left, ast.Name):
                if self.mod.constant_set(right, self.scope):
                    held = self.narrow(state, left, test)
                    return (held, state) if isinstance(op, ast.In) else (state, held)
            if isinstance(op, (ast.Eq, ast.NotEq)):
                for name, other in ((left, right), (right, left)):
                    if isinstance(name, ast.Name) and isinstance(other, ast.Constant):
                        held = self.narrow(state, name, test)
                        return (held, state) if isinstance(op, ast.Eq) else (state, held)
        if (
            isinstance(test, ast.Call)
            and isinstance(test.func, ast.Name)
            and self.mod.origin(test.func, self.scope) == "isinstance"
            and len(test.args) == 2
            and not test.keywords
            and isinstance(test.args[0], ast.Name)
            and self._numeric_types(test.args[1])
        ):
            return self.narrow(state, test.args[0], test), state
        return state, state

    def _numeric_types(self, e: ast.expr) -> bool:
        if isinstance(e, ast.Tuple):
            return bool(e.elts) and all(self._numeric_types(x) for x in e.elts)
        return isinstance(e, ast.Name) and self.mod.origin(e, self.scope) in NUMERIC_TYPES

    # -- statements ------------------------------------------------------------------------
    def block(self, stmts: list[ast.stmt], state: State | None) -> State | None:
        for stmt in stmts:
            if state is None:
                return None
            state = self.stmt(stmt, state)
        return state

    def stmt(self, s: ast.stmt, st: State) -> State | None:
        t = type(s)
        if t is ast.Assign:
            self.loads(s.value, st)
            for target in s.targets:
                self.loads_target(target, st)
            return self.bind(s, st)
        if t is ast.AnnAssign:
            self.loads(s.value, st)
            self.loads_target(s.target, st)
            return self.bind(s, st)
        if t is ast.AugAssign:
            self.loads(s.value, st)
            self.loads_target(s.target, st)
            return st
        if t is ast.Expr:
            self.loads(s.value, st)
            return st
        if t is ast.Return:
            self.loads(s.value, st)
            return None
        if t is ast.Raise:
            self.loads(s.exc, st)
            self.loads(s.cause, st)
            return None
        if t is ast.Assert:
            # no narrowing: ``python -O`` removes an assert
            self.loads(s.test, st)
            self.loads(s.msg, st)
            return st
        if t in (ast.Pass, ast.Global, ast.Nonlocal):
            return st
        if t is ast.Delete:
            new = dict(st)
            for target in s.targets:
                if isinstance(target, ast.Name):
                    new.pop(target.id, None)
                else:
                    self.loads_target(target, st)
            return new
        if t in (ast.Import, ast.ImportFrom):
            names = [(a.asname or a.name).split(".")[0] for a in s.names]
            return self.bind_opaque(("import", id(s)), names, st)
        if t in (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef):
            return self.bind(s, st)
        if t is ast.If:
            self.loads(s.test, st)
            true, false = self.split(s.test, st)
            return _merge(self.block(s.body, true), self.block(s.orelse, false))
        if t in (ast.For, ast.AsyncFor):
            self.loads(s.iter, st)
            return self.loop(s, st, is_for=True)
        if t is ast.While:
            return self.loop(s, st, is_for=False)
        if t is ast.Break:
            if not self.loops:
                raise _Unsupported
            self.loops[-1]["break"].append(st)
            return None
        if t is ast.Continue:
            if not self.loops:
                raise _Unsupported
            self.loops[-1]["continue"].append(st)
            return None
        if t in _TRY_TYPES:
            return self.try_(s, st)
        if t in (ast.With, ast.AsyncWith):
            start = self.bind(s, st)
            for index, item in enumerate(s.items):
                # ``with a() as x, b(x) as y``: a later item can read what an earlier one bound. The
                # state with EVERY target bound is used for those (a name the item binds itself reads
                # as the target, which is raw: never less cautious than the truth)
                self.loads(item.context_expr, st if index == 0 else start)
            coll: dict[str, set[_Def]] = {}
            self.collectors.append(coll)
            end = self.block(s.body, start)
            self.collectors.pop()
            # a context manager can swallow an exception raised anywhere in the body
            return _merge(end, self._anywhere(start, coll))
        if t is ast.Match:
            return self.match(s, st)
        raise _Unsupported

    @staticmethod
    def _anywhere(state: State, coll: dict[str, set[_Def]]) -> State:
        """``state`` with, for every name, every binding made at any point in ``coll`` as well."""
        out = dict(state)
        for name, defs in coll.items():
            out[name] = out[name] | frozenset(defs) if name in out else frozenset(defs)
        return out

    def loop(self, s: ast.stmt, pre: State, is_for: bool) -> State | None:
        head: State = pre
        for _ in range(self.MAX_PASSES):
            frame: dict[str, list[State | None]] = {"break": [], "continue": []}
            self.loops.append(frame)
            if is_for:
                body_in: State | None = self.bind(s, head)
                done: State | None = head
            else:
                self.loads(s.test, head)  # type: ignore[attr-defined]
                body_in, done = self.split(s.test, head)  # type: ignore[attr-defined]
            end = self.block(s.body, body_in)  # type: ignore[attr-defined]
            self.loops.pop()
            new_head = _merge(pre, end, *frame["continue"])
            assert new_head is not None
            if new_head == head:
                break
            head = new_head
        else:
            raise _Unsupported
        after = self.block(s.orelse, done)  # type: ignore[attr-defined]
        return _merge(after, *frame["break"])

    def try_(self, s: ast.stmt, st: State) -> State | None:
        everything: dict[str, set[_Def]] = {}
        in_body: dict[str, set[_Def]] = {}
        self.collectors.append(everything)
        self.collectors.append(in_body)
        body_end = self.block(s.body, st)  # type: ignore[attr-defined]
        self.collectors.pop()
        # an exception can leave the body after any statement, in any nested block
        handler_in = self._anywhere(st, in_body)
        exits: list[State | None] = []
        for handler in s.handlers:  # type: ignore[attr-defined]
            self.loads(handler.type, handler_in)
            exits.append(self.block(handler.body, self.bind(handler, handler_in)))
        else_end = None
        if body_end is not None:
            else_end = self.block(s.orelse, body_end)  # type: ignore[attr-defined]
        self.collectors.pop()
        after = _merge(else_end, *exits)
        if s.finalbody:  # type: ignore[attr-defined]
            # it runs on the normal way out and on every exceptional one
            self.block(s.finalbody, self._anywhere(st, everything))  # type: ignore[attr-defined]
            if after is None:
                return None
            return self.block(s.finalbody, after)  # type: ignore[attr-defined]
        return after

    def match(self, s: ast.Match, st: State) -> State | None:
        self.loads(s.subject, st)
        exits: list[State | None] = [st]
        for case in s.cases:
            names = [
                n.name
                for n in ast.walk(case.pattern)
                if isinstance(n, (ast.MatchAs, ast.MatchStar)) and n.name
            ] + [
                n.rest for n in ast.walk(case.pattern) if isinstance(n, ast.MatchMapping) and n.rest
            ]
            inner = self.bind_opaque(("match", id(case)), names, st)
            self.loads(case.guard, inner)
            exits.append(self.block(case.body, inner))
        return _merge(*exits)


def find_unsafe_markup(
    source: str, module: str | None = None, is_package: bool = False
) -> list[Finding]:
    """Every sink in ``source`` that is handed a string built from a raw value.

    ``module`` is the dotted name the source is importable as (``djust.components.utils``). With it a
    relative import resolves to its origin (so ``from ..utils import safe_url`` is the project's
    escaper) and a ``def`` in the file has an origin of its own; without it neither does, which can
    only flag more, never less.
    """
    return _Module(source, module, is_package).findings()


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
