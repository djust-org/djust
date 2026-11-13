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
  verdict does not depend on the arguments. The exception (#3845) is a function NESTED in another
  whose name is only ever the callee of a plain call in that function (not handed to ``map``,
  returned or stored): every call site is in view, so its parameters hold what those calls pass (and
  a default, where a call omits one). A method is not bound that way: a subclass anywhere can call it;
* the RESULT of such a call (also of a lambda bound to a name once, ``tag = lambda u: ...``, and of a
  method of a fresh instance of a plain class of this file, ``Card().render(x)``) judged with its
  parameters bound to what THAT call passes (#3845, #3864; ``_specialized``). A sink handed the call
  is flagged when what it returns for these arguments is built from a raw value (``mark_safe(
  helper(x))``) or puts an escaper's output where it does not belong (``tag(safe_url(x))`` of ``def
  tag(u): return f"<a href={u}>"``). The result of ANY call of a function of the file (a zero-argument
  one too) is placed by what it can return (``_value_kind``): literals and digits anywhere; escaped
  text in element text or a plain quoted value; whole attributes (`` aria-x="..."``) between
  attributes; any other markup, or text the guard cannot read, in element text only. "Clean for these
  arguments" is never "free in any position". A bound argument that is only HTML-escaped is clean in
  text or a plain quoted value: not in an unquoted attribute (escaped text keeps its spaces), a URL
  attribute (``javascript:``), an event handler or ``style`` (the browser decodes the attribute before
  it runs it), nor a ``<script>`` / ``<style>`` body, which is not element text; a URL escaper's
  output handed to a helper may go into its URL attribute. What ``append``
  / ``+=`` put into a container that outlives the call (a module list, a mutable default, a
  parameter, an attribute) is judged with every binding off, and a parameter this call binds to a
  literal decides ``if p:`` / ``if p is None:`` (when the function never rebinds it). A recursive call,
  ``*args`` / ``**kw`` and a sink inside the function's OWN body are judged with the parameters raw;
* type facts of the standard library and the project (#3847): a ``date`` / ``datetime`` value's
  ``isoformat()`` / ``weekday()`` / ``.year`` / ``(a - b).days`` (a ``date`` accepts ints only), the
  elements (at any depth) of ``calendar.Calendar(...).monthdayscalendar(...)``, and
  ``djust.components.utils.interpolate_color`` (pinned by a hostile-payload test), also when a class
  aliases it as ``_f = staticmethod(interpolate_color)`` and calls ``self._f(...)``;

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
an early ``raise`` / ``return`` carry it to the right edge. ``str(x) in COLLECTION`` narrows the
EXPRESSION ``str(x)`` (not ``x``, which may be an object whose ``str()`` is in the table) for as long
as ``x`` keeps its bindings, and only where every path into the use carries it (#3846). ``assert`` narrows nothing (``python -O``
removes it), and a non-literal member, a collection that is mutated or reassigned anywhere in the
file, or ``self.X = ...`` shadowing the class attribute turns it off.

Anything else, including a function parameter, an attribute (``obj.id``) and the result of an
unknown call, is raw.

What it does NOT see (probed, and pinned in ``KNOWN_MISSES`` of its test)
--------------------------------------------------------------------------
* Anything that crosses a module, or a value stored on ``self`` and marked safe in another method: a
  string built in one function and marked safe in another (``build_theme_head_context`` builds
  ``css_block``; ``ThemeMixin`` marks it safe; the inline theme switcher builds ``preset_options`` into a
  returned f-string that ``LazyThemeHTML`` marks safe, so the switcher is protected by its own tests,
  not by this guard). A helper of the SAME file that returns an interpolated string and is passed
  straight to the sink is seen since #3864, and so is one that takes an escaper's output as a parameter
  and builds the tag, as a ``def``, a lambda or a method of a fresh instance (not of a class with a
  base, a metaclass or a decorator: its ``__new__`` may return anything).
* ``mark_safe(x)`` where ``x`` is a bare parameter or an unknown call's result: that is how trusted
  markup is passed in, and no syntax says whether ``x`` was escaped.
* A sink reached through ``functools.reduce``, ``exec`` (code held in a string), and any builder not
  listed above. A sink reached WITHOUT its name in the call IS seen since the follow-up of #3850:
  ``partial(mark_safe, f"..")``, ``map(mark_safe, [..])``, ``getattr(safestring, "mark_safe")(..)``,
  ``self._ms`` of ``self._ms = mark_safe``, ``FUNCS["safe"]`` of a container that holds one,
  ``mark_safe if c else str``, ``str.__new__(SafeString, ..)``, an instance of a class whose
  ``__call__`` marks its argument, a class whose ``__html__`` returns itself, and the format string of
  ``format_html_join``. The value returned by a ``__html__`` method and by ``__str__`` of an
  ``@html_safe`` class is judged as a sink argument (Django takes it as safe markup).
* Known false negatives the review of #3848 probed, and what became of them (#3850): a dict KEY
  built from raw data and then iterated, a closure that appends to (or stores into) the enclosing
  function's container, a nested container (``rows[0].append(x)``), a ``for`` / ``with`` target that
  is a subscript, an ``await`` or ``*args`` as the sink argument, ``str.translate``, ``(s % x) * n``,
  ``html.escape(x, quote=False)`` and a ``from x import *`` (which makes every free name's origin
  unknown) are caught. Still not: a ``str`` subclass without ``__html__`` (Django escapes it), a
  sink inside ``exec``'s string, ``mark_safe(param)`` handed to ``conditional_escape``, and a sink in a
  default argument, a decorator, a class body or a module-level loop whose payload is a LITERAL (literal
  markup is the developer's by design; the same positions with any other value are flagged). Python
  3.14 t-strings are parsed and then skipped.
* Whether a name it trusts is the real thing: ``x.render()`` and ``.strip()`` are trusted by method
  name, whatever ``x`` is.
* WHERE a clean fragment is placed. ``format_html`` output, ``rich_html`` and the record builders are
  markup: right in an element, wrong inside an attribute value. The guard asks whether a value is raw,
  not which HTML context it lands in. The exception is the context-sensitive escapers
  (``safe_url`` / ``url_attr`` / ``safe_css_*``: text or a quoted value; ``escape_json_for_script``:
  script text): their output is checked against the literal text in front of it in an f-string, a
  ``+`` chain, a ``%`` format string and ``str.format`` (a prefix held in a name that only holds one
  literal, a call of a function of this file that always returns one, or passed as a literal argument
  counts; a trailing ``<`` is a tag name). A string BUILT from such an output (``'' + safe_url(x)``,
  ``'%s' % ..``, a helper that returns one, a ``join`` or container of it) is judged again where it is
  placed. The exception is a BALANCED fragment: EVERY part the guard cannot read sits inside a quote
  the builder itself opens (the literal text before each such part leaves a quote open, as in
  ``' style="' + safe_css_length(x)``; a second output after the quote closed, ``' title="' +
  safe_url(x) + '" z=' + safe_url(x)``, does not qualify since #3864) and the builder's whole literal
  text leaves no quote open. A plain ``<`` or an unclosed quote does not qualify. Such a fragment is placed UNCHECKED: between
  attributes, which is its purpose, but also inside a quoted value or as an unquoted value, where it
  would be wrong and the guard does not see it (#3864). A bare output anywhere nobody checked
  (``join`` of bare outputs, an argument) is not clean. What that check still cannot see (#3850,
  #3864): a prefix the guard cannot read (any unknown earlier part is assumed position-neutral;
  treating markup-carrying parts as unknown flagged over a hundred correct sites in the tree, so it
  was not done), ``format_map`` (flagged, not cleared), and a module attribute replaced from outside
  the file (``sys.modules[__name__].helper = ...``, ``setattr`` on an imported module). A function or
  lambda that takes the output as a PARAMETER and builds the tag is seen since #3864 (above). An
  escaper WRITTEN IN PLACE where its escaping is not enough is cleared, as it always was: ``escape(x)``
  in an unquoted attribute, a URL attribute, an event handler, a ``style`` attribute or body, and a
  value escaper in an event handler or a ``style`` body (pinned in ``TestKnownGaps`` of
  ``test_safe_markup_guard_3864.py``; making the plain escapers position-checked flagged about seventy
  correct sites in the tree). Through a parameter of a helper each of them is caught.
  A metaclass data descriptor of a class attribute's name is no longer missed: ``cls.X`` / ``C.X`` of
  a class with a metaclass (or any class keyword) is not trusted (``self.X`` is, since an instance
  lookup never reaches the metaclass).
* Class attributes of a class whose base writes attributes from keywords: the package's own
  (``Component``, found by :func:`dynamic_class_names`) and Django's class-based views and forms
  (``EXTERNAL_DYNAMIC_BASES``, matched by name: ``View.__init__`` ``setattr``s every ``as_view()``
  keyword) are defaults, not constants. The command line passes the classes it finds in the files it
  is given (#3864).
* A subclass in ANOTHER file that overrides a method or class attribute the guard resolved with
  something computed: that is the developer's own code, not request data. (One in the same file is
  seen: the name is no longer trusted.)

Each of those needs reading, which is what an audited allowlist entry records.

Refs #3766, #3775, #3820, #1859, #1468.
"""

from __future__ import annotations

import ast
import copy
import re
import string
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

SINKS = frozenset({"mark_safe", "SafeString", "SafeText", "_safe", "safe_html"})
#: ``_safe`` (``components/rust_handlers.py``) and ``djust._html.safe_html`` are ``cast(str,
#: mark_safe(html))`` (#3820). They mark their argument safe exactly as ``mark_safe`` does, so an
#: interpolated string handed to one is the same hole; before #3820 they were not sinks and about 190
#: call sites were invisible to the guard, the audited baseline and the payload sweep.
WRAPPER_SINKS = frozenset({"_safe", "safe_html"})
#: Sinks with an argument that is a markup template, and its position: an interpolated one is the
#: same hole. ``format_html_join(sep, format_string, args)`` takes it second (#3850).
FORMAT_SINK_ARG = {"format_html": 0, "format_html_join": 1}
FORMAT_SINKS = frozenset(FORMAT_SINK_ARG)
#: Constructors of ``datetime.date`` / ``datetime.datetime`` values (#3847). ``date(...)`` accepts ints
#: only (it raises on anything else), so what such a value prints through ``isoformat()`` and the
#: numbers it yields (``weekday()``, ``.year``, ``(a - b).days``) are digits, ``-``, ``:``, ``T``,
#: ``.`` and ``+``: never markup.
DATE_ORIGINS = frozenset(
    {
        "datetime.date",
        "datetime.date.today",
        "datetime.date.fromordinal",
        "datetime.datetime",
        "datetime.datetime.now",
        "datetime.datetime.utcnow",
        "datetime.datetime.fromordinal",
    }
)
DELTA_ORIGINS = frozenset({"datetime.timedelta"})
#: Methods of a date value that return digits (``isoformat``) or an int.
DATE_DIGIT_METHODS = frozenset({"isoformat", "weekday", "isoweekday", "toordinal"})
#: Attributes of a date / timedelta value that are ints.
DATE_DIGIT_ATTRS = frozenset({"year", "month", "day", "days", "seconds", "microseconds"})
#: ``calendar.Calendar`` methods whose result is (nested) ints: every element, at any depth, is
#: an int (#3847). Called on an instance of these classes.
CALENDAR_CLASSES = frozenset(
    {"calendar.Calendar", "calendar.TextCalendar", "calendar.HTMLCalendar"}
)
CALENDAR_DIGIT_METHODS = frozenset(
    {"monthdayscalendar", "itermonthdays", "monthdays2calendar", "yeardayscalendar"}
)
#: Origins (in the project) whose result is clean for ANY argument; each is pinned by a
#: hostile-payload test in ``python/tests/test_safe_markup_guard_3820.py`` (#3847).
#: ``interpolate_color`` returns ``f"#{r:02x}{g:02x}{b:02x}"`` of ``int()``s, or raises.
CLEAN_PROJECT_ORIGINS = frozenset({"djust.components.utils.interpolate_color"})
#: Escapers with a ``quote`` switch (and its position): only clean when it is left on.
QUOTE_ESCAPERS = {"html.escape": 1}
#: Calls that apply their FIRST argument to the rest: ``map(mark_safe, items)``,
#: ``partial(mark_safe, x)``. With a sink first, the rest is what it marks (#3850).
APPLY_CALLS = frozenset({"map", "partial"})

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
        "djust.components.utils.data_url_attr",  # url_attr that never honours the safe marker (#3898)
        "djust.components.utils.attr_text",  # django.utils.html.escape of the exact str (#3898)
    }
)
#: NOT in ``CLEAN_ORIGINS`` on purpose (review of #3848):
#:
#: * ``djust._html.attr_name`` returns a LEGAL attribute name, and ``onmouseover`` / ``srcdoc`` /
#:   ``style`` are legal names: a cleared name is not a cleared event-handler name. A writer that
#:   needs one also needs ``djust._attr_policy.passthrough_attrs`` (which refuses ``on*``), and
#:   that is something to read, not to trust.
#: * ``escape_json_for_script`` escapes ``< > &`` and U+2028/9 and leaves ``"`` alone, so it is only
#:   clean for ``json.dumps`` output (whose quotes are already ``\"``): see ``JSON_ESCAPERS``.
#:
#: Origins whose result is clean ONLY when their first argument is ``json.dumps(...)`` output (or a
#: method that returns it) AND the field sits in ``<script>`` TEXT: the output keeps ``"``, so inside a
#: quoted attribute the JSON's own quotes end the value.
JSON_ESCAPERS = frozenset({"djust.security.json_script.escape_json_for_script"})
JSON_PRODUCERS = frozenset({"json.dumps"})
#: Escapers whose contract is about a VALUE: their output is HTML-escaped but keeps spaces and other
#: characters, so it is clean inside a QUOTED attribute value or element text, and not in an
#: unquoted ``href={...}`` (``x onmouseover=alert(1)`` becomes an attribute there).
CONTEXT_SENSITIVE_ORIGINS = frozenset(
    {
        "djust.components.utils.safe_url",
        "djust.components.utils.url_attr",
        "djust.components.utils.srcset_attr",
        "djust.components.utils.data_url_attr",
        "djust.components.utils.attr_text",
        "djust.components.utils.safe_css_color",
        "djust.components.utils.safe_css_length",
        "djust.components.utils.safe_css_ratio",
        "djust.components.utils.safe_css_number",
    }
)
#: A call to any of these (or a class body that defines one) means attributes can be written under a
#: name the source does not show: ``setattr(self, key, value)`` over constructor kwargs (what
#: ``Component`` does), ``self.__dict__.update``, ``object.__setattr__``, ``vars(self)[...]``. No class
#: attribute or method of such a file is trusted. ``__getattr__`` is NOT one of them: Python calls it
#: only for a name normal lookup did not find, so it cannot replace an attribute a class body defines
#: unconditionally (``__getattribute__`` intercepts every lookup, and is). It DOES supply the value of
#: one the body defines only inside an ``if`` / ``try`` / loop, or deletes: for a class with a
#: ``__getattr__``, a base other than ``object`` or a class keyword, only an unconditional top-level
#: assignment is the value (:meth:`_Module._falls_through`, #3850).
DYNAMIC_ATTR_NAMES = frozenset({"__setattr__", "__delattr__", "__getattribute__", "__dict__"})
DYNAMIC_ATTR_CALLS = frozenset({"setattr", "delattr", "vars"})
DYNAMIC_GLOBAL_CALLS = frozenset({"globals", "exec", "eval", "vars"})
#: Methods that change a container in place.
MUTATORS = frozenset(
    {
        "append",
        "extend",
        "insert",
        "add",
        "update",
        "setdefault",
        "pop",
        "popitem",
        "remove",
        "discard",
        "clear",
        "sort",
        "reverse",
        "appendleft",
        "extendleft",
        "rotate",
        "intersection_update",
        "difference_update",
        "symmetric_difference_update",
        "__setitem__",
        "__delitem__",
        "__iadd__",
        "__ior__",
        "__iand__",
        "__ixor__",
        "__isub__",
        "__imul__",
    }
)
#: Reading an element out of a container: the result can still be (a reference to) a container.
READERS = frozenset({"get", "keys", "values", "items", "copy", "index", "count"})
#: Builtins that read a container without keeping a reference to it.
SAFE_READ_CALLS = frozenset(
    {
        "len",
        "sorted",
        "list",
        "tuple",
        "set",
        "frozenset",
        "dict",
        "min",
        "max",
        "sum",
        "any",
        "all",
        "enumerate",
        "reversed",
        "iter",
        "str",
        "repr",
        "bool",
        "zip",
        "isinstance",
        "print",
    }
)
SAFE_READ_METHODS = frozenset({"join", "format", "startswith", "endswith"})
#: Methods that only transform text; a raw receiver stays a bare passthrough (see the module docstring).
PURE_TRANSFORMS = frozenset(
    {"strip", "lstrip", "rstrip", "lower", "upper", "title", "capitalize", "casefold"}
)
#: ``a.__add__(b)`` is ``a + b``.
DUNDER_BUILDERS = frozenset({"__add__", "__radd__", "__mod__", "__rmod__", "__mul__", "__rmul__"})
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
#: ``clean`` carries this in ``seen`` when the caller has checked WHERE a context-sensitive escaper's
#: output (``safe_url``, ``escape_json_for_script``) lands; the call consumes it, any other call strips it
_CTX_OK = ("position-checked",)
#: one conversion of a ``%`` format string: ``%s``, ``%d``, ``%-5.2f``, ``%(name)s`` (``%%`` is group 2)
_PERCENT_SPEC = re.compile(
    r"%(\([^)]*\))?[-#0 +]*(?:\d+|\*)?(?:\.(?:\d+|\*))?[hlL]?([diouxXeEfFgGcrsa%])"
)


class _Raw:
    """A binding whose value is not known: a parameter, a loop variable, a ``with`` target."""


RAW = _Raw()


class _Param(_Raw):
    """A parameter's value on entry: raw, unless a call site in view binds it (:meth:`_Module._actuals`).
    One object per parameter, so the entry binding is told apart from any other unknown binding."""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name


class _ElementOf:
    """A name bound by iterating ``iterable`` (a ``for`` target or a comprehension variable)."""

    def __init__(self, iterable: ast.expr) -> None:
        self.iterable = iterable


class _Foreign:
    """A value put into a container from ANOTHER scope: a closure's ``parts.append(s)`` where ``parts``
    is the enclosing function's (#3850). It is judged in the scope that wrote it."""

    __slots__ = ("expr", "scope")

    def __init__(self, expr: ast.expr, scope: _Scope) -> None:
        self.expr = expr
        self.scope = scope


class _Actual(_Foreign):
    """A parameter bound to the argument of the ONE call whose result is being judged (#3845, #3864).
    The argument is judged in the caller's scope, and only where its POSITION was checked: it may be
    an escaped value, and escaped text is clean in element text or a quoted value, never in an
    unquoted attribute (``x onmouseover=1`` stays two attributes there)."""

    __slots__ = ()


#: Pseudo-origins for :meth:`_Module._hold_level`: a value bound from a call site (:class:`_Actual`)
#: and a call of a function of this file that is clean only for the arguments it is given.
_BOUND = frozenset({"<a parameter bound from its call>"})
_SPEC = frozenset({"<a call of a function of this file>"})


#: Characters that make literal text MARKUP: it ends or opens a tag or a quoted value
_MARKUP_CHARS = frozenset("<>\"'`")
#: Escapers whose output is text with no markup character (quotes included), but spaces kept
_TEXT_ESCAPERS = frozenset(
    {"django.utils.html.escape", "django.utils.html.conditional_escape", "html.escape"}
)
#: Attributes whose value a browser reads as a URL, as script or as CSS: HTML escaping does not make
#: a value safe there (``javascript:``; ``&#x27;`` is a quote again once the attribute is decoded)
_URL_ATTRIBUTES = frozenset(
    {
        "href",
        "src",
        "action",
        "formaction",
        "srcset",
        "poster",
        "background",
        "cite",
        "data",
        "codebase",
        "ping",
        "manifest",
        "xlink:href",
        "dynsrc",
        "lowsrc",
        "longdesc",
        "usemap",
    }
)


def _code_attribute(name: str) -> bool:
    """A browser runs the value of this attribute as script or CSS (an event handler, ``style``)."""
    name = name.lower()
    return name.startswith("on") or name == "style"


#: Where a value of each placement kind may land (see ``_Module._place``): ``None`` anywhere;
#: ``"bound"`` escaped text: element text or a quoted value that is not a URL / script / style;
#: ``"attrs"`` whole attributes (`` aria-x="..."``): element text or between two attributes;
#: ``"call"`` markup or text the guard cannot read: element text only.
_PLACES: dict = {
    None: frozenset({"text", "quoted", "url", "code", "between", "unquoted", "rawtext"}),
    "bound": frozenset({"text", "quoted"}),
    "attrs": frozenset({"text", "between"}),
    "call": frozenset({"text"}),
    # a URL escaper's output handed to a helper: a URL attribute is what it is for, script is not
    "bsens": frozenset({"text", "quoted", "url"}),
    # the value escapers keep the rules they have when written in place (``_position_ok``)
    "sens": frozenset({"text", "quoted", "url", "code", "rawtext"}),
    "json": frozenset({"text", "rawtext"}),
}


def _strictest(kinds: list) -> str | None:
    """The kind of a value that may be any of ``kinds``: where ALL of them may land."""
    allowed = _PLACES[None]
    for k in kinds:
        allowed = allowed & _PLACES[k]
    for kind, places in _PLACES.items():
        if places == allowed:
            return kind
    return "call"


class _AnyOf:
    """A parameter of a nested function bound from its call sites: it holds one of ``values``, each
    judged in the scope of its call (or, for a default, of the ``def``) (#3845)."""

    __slots__ = ("values",)

    def __init__(self, values: list[_Foreign]) -> None:
        self.values = values


def _unwrap(value: object, owner: _Scope) -> tuple[object, _Scope]:
    if isinstance(value, _Foreign):
        return value.expr, value.scope
    return value, owner


def _foreign_values(value: object, owner: _Scope) -> list[tuple[ast.expr, _Scope]]:
    """The expressions a binding stands for, each with the scope it is judged in: itself, or for a
    parameter bound from call sites (``_Foreign`` / ``_AnyOf``) each argument. Nothing for a value
    that is not an expression (RAW, an element of an iterable)."""
    if isinstance(value, _Foreign):
        return [(value.expr, value.scope)]
    if isinstance(value, _AnyOf):
        return [(v.expr, v.scope) for v in value.values]
    if isinstance(value, ast.expr):
        return [(value, owner)]
    return []


def _return_values(fn: ast.AST) -> list[ast.expr | None]:
    """What a function returns: each ``return``'s value (``None`` for a bare one), or a lambda's body."""
    if isinstance(fn, ast.Lambda):
        return [fn.body]
    return [n.value for n in _own_nodes(fn) if isinstance(n, ast.Return)]


class _Def:
    """One binding of a name: what reaches a use is a SET of these (see :class:`_Flow`)."""

    __slots__ = ("name", "value", "site")

    def __init__(self, name: str, value: object, site: int | None = None) -> None:
        self.name = name
        self.value = value
        #: id of the statement that binds it, for a binding the source makes (else ``None``)
        self.site = site


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
        #: name -> every KEY stored into it (``d[k] = v``): what iterating a dict yields (#3850).
        #: Kept apart from ``contents``: a key is an element when the container is ITERATED, not a
        #: piece of a string a call builds out of the container.
        self.keys: dict[str, list[object]] = {}
        #: name -> every value ``+=``-ed onto it as a string piece
        self.pieces: dict[str, list[ast.expr]] = {}
        self.sinks: list[ast.Call] = []
        #: ``return`` statements whose value Django takes as already-safe markup: those of a
        #: ``__html__`` method, and of ``__str__`` in an ``@html_safe`` class (#3850)
        self.html_returns: list[ast.Return] = []
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
            # a class body is not visible from the functions (or classes) inside it: a bare
            # ``helper(x)`` in a method is the MODULE's ``helper``, not the method of that name
            while scope is not None and isinstance(scope.node, ast.ClassDef):
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


def _subscript_targets(target: ast.expr) -> list[ast.Subscript]:
    """The ``c[k]`` stores a ``for`` / ``with`` target makes (``for box[0], n in ...``)."""
    if isinstance(target, ast.Subscript):
        return [target]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [x for t in target.elts for x in _subscript_targets(t)]
    if isinstance(target, ast.Starred):
        return _subscript_targets(target.value)
    return []


def _target_names(target: ast.expr) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [n for t in target.elts for n in _target_names(t)]
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    return []


class _Module:
    def __init__(
        self,
        source: str,
        module: str | None = None,
        is_package: bool = False,
        dynamic_bases: frozenset[str] = frozenset(),
    ) -> None:
        self.source = source
        #: names of classes (anywhere in the package) that write attributes under names the source
        #: does not show (``Component``: ``setattr(self, key, value)`` for every kwarg), and the
        #: classes that inherit from them: see :func:`dynamic_class_names`
        self.dynamic_bases = dynamic_bases | EXTERNAL_DYNAMIC_BASES
        #: The dotted name this source is importable as (``djust.components.utils``), when known.
        #: It resolves a relative import to its ORIGIN and names the functions this file defines.
        self.module = module
        self.is_package = is_package
        self.tree = ast.parse(source)
        #: names declared ``global`` / ``nonlocal`` anywhere, and attributes assigned to anywhere
        #: (``self.X = ...``): one walk, because the class-attribute rules ask per use
        self.rebound_names: set[str] = set()
        self.assigned_attrs: set[str] = set()
        #: file-wide: attributes / module names can be written under a name the source does not show
        self.dynamic_attrs = False
        #: ``C.__getattr__ = ...`` somewhere: any class may fall through to it (#3850)
        self.getattr_assigned = False
        #: (function, origins) -> whether what it returns is built from an escaper's output; memoised
        self._fn_hold: dict[tuple[int, int], int] = {}
        self._fn_hold_busy: set[tuple[int, int]] = set()
        self.dynamic_globals = False
        #: keys (a name, or an attribute name) of containers that are changed in place, handed on
        #: (aliased, passed, returned), or whose ELEMENTS are handed on, anywhere in the file
        self.mutated: set[str] = set()
        self.escaped: set[str] = set()
        self.escaped_elements: set[str] = set()
        #: the same, per enclosing function / class / module: ``(id(node), key)`` for every node whose
        #: extent holds the change. A LOCAL list is judged by what happens inside its own function, a
        #: module or class table by the whole file.
        self.mutated_in: set[tuple[int, str]] = set()
        self.escaped_in: set[tuple[int, str]] = set()
        self.escaped_elements_in: set[tuple[int, str]] = set()
        self._scan_hazards()
        self.sinks = set(SINKS)
        self.format_sinks = set(FORMAT_SINKS)
        #: name of a container display that holds a sink -> that sink (``FUNCS["safe"](...)``)
        self.sink_containers: dict[str, str] = {}
        #: local name -> dotted origin, or ``None`` when the file imports it as two different things
        self.imports: dict[str, str | None] = {}
        #: the file has a ``from x import *``: no free name has a known origin
        self.star_import = False
        self._collect_imports()
        self._collect_aliases()
        self._collect_wrappers()
        self.scopes: list[_Scope] = []
        #: id(``ast.Name``) -> (scope, the definitions that can reach it); see :class:`_Flow`
        self.reach: dict[int, tuple[_Scope, tuple[_Def, ...]]] = {}
        #: id(function node) -> the scope it opens (to judge a call to it by what it returns)
        self.scope_of: dict[int, _Scope] = {}
        #: ids of ``str(x)`` calls that only run where ``str(x) in CONSTANT`` held (#3846)
        self.narrowed_exprs: set[int] = set()
        #: id(scope) -> parameter name -> the argument ONE call site passes (a ``_Foreign`` judged in
        #: the caller's scope), while that call's result is judged (#3845, #3864). A function's
        #: parameters are raw everywhere else.
        self._actuals: dict[int, dict[str, object]] = {}
        self._facts: dict[int, dict[int, tuple[tuple[str, str, bool], ...]]] = {}
        #: id(function) -> whether it returns a clean value for any argument; memoised
        self._raw_clean: dict[int, bool] = {}
        self._raw_clean_busy: set[int] = set()
        #: (id(callee expression), id(scope)) -> the function a call reaches (``_local_function``)
        self._locals: dict[tuple[int, int], tuple] = {}
        self._kind_busy: set[tuple] = set()
        self._kinds: dict[tuple, str | None] = {}
        self._build(self.tree, "<module>", None)
        self._closure_writes()
        self._bind_nested_parameters()
        for scope in self.scopes:
            if isinstance(scope.node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _Flow(self, scope).run()

    # -- hazards: what can change a value the source does not show -------------------------
    @staticmethod
    def _ref_key(e: ast.AST) -> str | None:
        if isinstance(e, ast.Name):
            return e.id
        if isinstance(e, ast.Attribute):
            return e.attr
        return None

    def _root_key(self, e: ast.AST) -> str | None:
        """The container ``e`` reaches into: ``D`` for ``D[k]``, ``D.get(k)``, ``self.D[a][b]``."""
        while True:
            if isinstance(e, ast.Subscript):
                e = e.value
            elif (
                isinstance(e, ast.Call)
                and isinstance(e.func, ast.Attribute)
                and (e.func.attr in READERS or e.func.attr in ("setdefault", "pop"))
            ):
                e = e.func.value
            else:
                return self._ref_key(e)

    def _scan_hazards(self) -> None:
        parents: dict[int, ast.AST] = {}
        nodes = list(ast.walk(self.tree))
        for n in nodes:
            for c in ast.iter_child_nodes(n):
                parents[id(c)] = n
        for node in nodes:
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                self.rebound_names.update(node.names)
            elif isinstance(node, ast.Attribute):
                # ``del C.LABEL`` removes what the class body wrote (#3850): a lookup then falls
                # through to ``__getattr__`` or a base, so a delete is as much a rebind as a store
                if isinstance(node.ctx, (ast.Store, ast.Del)):
                    self.assigned_attrs.add(node.attr)
                    if node.attr == "__getattr__":
                        self.getattr_assigned = True
                if node.attr in DYNAMIC_ATTR_NAMES:
                    self.dynamic_attrs = True
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Del):
                # ``del LABEL`` in a class or module body (a local of a function is the flow's)
                scope_node = parents.get(id(node))
                while scope_node is not None and not isinstance(
                    scope_node,
                    (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda),
                ):
                    scope_node = parents.get(id(scope_node))
                if isinstance(scope_node, (ast.Module, ast.ClassDef)):
                    self.rebound_names.add(node.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in DYNAMIC_ATTR_NAMES:
                    self.dynamic_attrs = True
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in DYNAMIC_ATTR_CALLS:
                    self.dynamic_attrs = True
                if node.func.id in DYNAMIC_GLOBAL_CALLS:
                    self.dynamic_globals = True
            # -- changes in place
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in MUTATORS
            ):
                key = self._root_key(node.func.value)
                if key:
                    self._note(self.mutated, self.mutated_in, key, node, parents)
            elif isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)):
                key = self._root_key(node.value)
                if key:
                    self._note(self.mutated, self.mutated_in, key, node, parents)
            elif isinstance(node, ast.AugAssign):
                target = node.target
                key = self._root_key(target.value if isinstance(target, ast.Subscript) else target)
                if key:
                    self._note(self.mutated, self.mutated_in, key, node, parents)
        for node in nodes:
            if isinstance(node, (ast.Name, ast.Attribute)) and isinstance(node.ctx, ast.Load):
                key = self._ref_key(node)
                if key is None:
                    continue
                verdict = self._read_verdict(node, parents)
                if verdict == "escapes":
                    self._note(self.escaped, self.escaped_in, key, node, parents)
                elif verdict == "element":
                    self._note(self.escaped_elements, self.escaped_elements_in, key, node, parents)

    @staticmethod
    def _note(
        whole: set[str],
        scoped: set[tuple[int, str]],
        key: str,
        node: ast.AST,
        parents: dict[int, ast.AST],
    ) -> None:
        whole.add(key)
        cur: ast.AST | None = node
        while cur is not None:
            if isinstance(
                cur, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
            ):
                scoped.add((id(cur), key))
            cur = parents.get(id(cur))

    def _in(
        self, whole: set[str], scoped: set[tuple[int, str]], key: str, owner: _Scope | None
    ) -> bool:
        """``key`` is in the set: for a local of a function, within that function; otherwise anywhere."""
        if owner is not None and isinstance(
            owner.node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
        ):
            return (id(owner.node), key) in scoped
        return key in whole

    def _read_verdict(self, node: ast.AST, parents: dict[int, ast.AST]) -> str:
        """What happens to the value read at ``node``: ``"safe"`` (compared, printed, iterated,
        measured, joined), ``"element"`` (an ELEMENT of it is handed on) or ``"escapes"`` (the
        container itself is aliased, passed, returned, stored)."""
        cur = node
        element = False
        while True:
            parent = parents.get(id(cur))
            if parent is None:
                return "safe"
            if isinstance(parent, ast.Subscript) and parent.value is cur:
                if not isinstance(parent.ctx, ast.Load):
                    return "safe"  # a store / delete: the mutation scan has it
                cur, element = parent, True
                continue
            if isinstance(parent, ast.Attribute) and parent.value is cur:
                if parent.attr in READERS:
                    grand = parents.get(id(parent))
                    if isinstance(grand, ast.Call) and grand.func is parent:
                        cur, element = grand, True
                        continue
                    return "safe"
                return "safe"  # ``.upper()``, ``.startswith(...)``, a mutator: derived or handled
            break
        bad = "element" if element else "escapes"
        if isinstance(
            parent,
            (ast.Compare, ast.FormattedValue, ast.BinOp, ast.Expr, ast.JoinedStr),
        ):
            return "safe"
        if isinstance(parent, ast.UnaryOp) and isinstance(parent.op, ast.Not):
            return "safe"
        if isinstance(parent, (ast.If, ast.While, ast.IfExp)) and parent.test is cur:
            return "safe"
        if isinstance(parent, (ast.For, ast.AsyncFor, ast.comprehension)) and parent.iter is cur:
            return "safe"
        if isinstance(parent, ast.Call):
            if parent.func is cur:
                return "safe"
            func = parent.func
            if cur in parent.args and (
                (isinstance(func, ast.Name) and func.id in SAFE_READ_CALLS)
                or (isinstance(func, ast.Attribute) and func.attr in SAFE_READ_METHODS)
            ):
                return "safe"
        return bad

    @staticmethod
    def _mutable_display(v: object) -> bool:
        """A list / dict / set written out (or built by the builtin of that name): aliasing it lets
        something else change it. Any other call is not assumed to return one."""
        if isinstance(v, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)):
            return True
        return (
            isinstance(v, ast.Call)
            and isinstance(v.func, ast.Name)
            and v.func.id in ("list", "dict", "set", "defaultdict", "deque", "OrderedDict")
        )

    def _mutable_elements(self, v: object) -> bool:
        if isinstance(v, ast.Dict):
            kids: list = [x for x in v.values if x is not None]
        elif isinstance(v, (ast.List, ast.Set, ast.Tuple)):
            kids = list(v.elts)
        else:
            return False
        return any(self._mutable_display(k) or self._mutable_elements(k) for k in kids)

    def _class_dynamic(self, cls: _Scope) -> bool:
        """Attributes of instances / the class can be written under names the source does not show."""
        if self.dynamic_attrs:
            return True
        if not self.dynamic_bases:
            return False
        names = {cls.node.name}  # type: ignore[attr-defined]
        pending = [x for x in self.scopes if isinstance(x.node, ast.ClassDef)]
        bases = {
            b.id if isinstance(b, ast.Name) else b.attr
            for b in cls.node.bases  # type: ignore[attr-defined]
            if isinstance(b, (ast.Name, ast.Attribute))
        }
        grew = True
        while grew:
            grew = False
            for scope in list(pending):
                if scope.node.name in bases:  # type: ignore[attr-defined]
                    pending.remove(scope)
                    bases |= {
                        b.id if isinstance(b, ast.Name) else b.attr
                        for b in scope.node.bases  # type: ignore[attr-defined]
                        if isinstance(b, (ast.Name, ast.Attribute))
                    }
                    grew = True
        return bool((bases | names) & self.dynamic_bases)

    def _foreign(self, owner: _Scope, sc: _Scope) -> bool:
        """``owner`` binds the name but the use is somewhere else: a module or class constant read from
        a function, or a variable read by a nested function."""
        if isinstance(owner.node, (ast.Module, ast.ClassDef)):
            return True
        cur: _Scope | None = sc
        while cur is not None and isinstance(
            cur.node, (ast.ListComp, ast.GeneratorExp, ast.SetComp, ast.DictComp)
        ):
            cur = cur.parent
        return cur is not owner

    # -- same-file wrappers around a sink ---------------------------------------------------
    def _collect_wrappers(self) -> None:
        for name in wrapper_names(self.tree, self.sinks):
            self.sinks.add(name)
        # ``class Safe: def __call__(self, v): return mark_safe(v)``; ``_wrap = Safe()``: the
        # instance is a sink (#3850). So is the class's name called directly on a value? No: that
        # constructs one. Only a name bound to an instance is.
        callable_classes = set()
        for cls in (n for n in ast.walk(self.tree) if isinstance(n, ast.ClassDef)):
            for stmt in cls.body:
                # ``class H(str): def __html__(self): return str(self)``: constructing one marks
                # the value it is built from safe, so the class itself is a sink (#3850)
                if (
                    isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and stmt.name == "__html__"
                    and stmt.args.args
                ):
                    me = {stmt.args.args[0].arg}
                    returns = [r.value for r in _own_nodes(stmt) if isinstance(r, ast.Return)]
                    if returns and all(r is not None and _transparent(r, me) for r in returns):
                        self.sinks.add(cls.name)
                if (
                    isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and stmt.name == "__call__"
                ):
                    probe = copy.copy(stmt)
                    probe.name = "_djust_probe_call"
                    module = ast.Module(body=[probe], type_ignores=[])
                    if "_djust_probe_call" in wrapper_names(module, self.sinks):
                        callable_classes.add(cls.name)
        if callable_classes:
            for dst, value in self._alias_pairs():
                if isinstance(value, ast.Call) and _callee(value.func) in callable_classes:
                    self.sinks.add(dst)

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
                    if alias.name == "*":
                        # ``from re import *`` can rebind ANY name, ``escape`` included (#3850)
                        self.star_import = True
                        continue
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
            if e.id in self.rebound_names or self.dynamic_globals:
                # ``global escape; escape = ...`` somewhere, or ``globals()[...] = ...``
                return None
            owner = sc.find(e.id)
            if owner is None:
                if self.star_import:
                    return None  # a ``from x import *`` may have rebound it: nothing is trusted
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
            # ``self._f`` of a class body ``_f = staticmethod(f)`` (#3847): the alias resolves to ``f``
            if isinstance(e.value, ast.Name) and e.value.id in ("self", "cls"):
                found = self.class_attribute(e, sc)
                if found is not None and len(found[1]) == 1:
                    value = found[1][0]
                    if (
                        isinstance(value, ast.Call)
                        and isinstance(value.func, ast.Name)
                        and value.func.id == "staticmethod"
                        and found[0].find("staticmethod") is None
                        and len(value.args) == 1
                        and not value.keywords
                        and isinstance(value.args[0], (ast.Name, ast.Attribute))
                    ):
                        return self.origin(value.args[0], found[0], depth + 1)
                return None
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
        while (
            changed
        ):  # ``ms = mark_safe``; ``m2 = ms``; ``a = b = mark_safe``; ``(ms := mark_safe)``
            changed = False
            for dst, value in self._alias_pairs():
                # ``mark_safe if c else str`` / ``x or mark_safe``: either may be what is bound (#3850)
                for src in self._alias_sources(value):
                    for group in (self.sinks, self.format_sinks):
                        if src in group and dst not in group:
                            group.add(dst)
                            changed = True
                # ``FUNCS = {"safe": mark_safe}`` / ``[mark_safe]``: an element of it is a sink
                if isinstance(value, (ast.Dict, ast.List, ast.Tuple, ast.Set)):
                    elts = value.values if isinstance(value, ast.Dict) else value.elts
                    for elt in elts:
                        for src in self._alias_sources(elt) if elt is not None else ():
                            if src in self.sinks and dst not in self.sink_containers:
                                self.sink_containers[dst] = src
                                changed = True

    @staticmethod
    def _alias_sources(value: ast.AST) -> list[str]:
        if isinstance(value, (ast.Name, ast.Attribute)):
            return [_callee(value)]  # type: ignore[list-item]
        if isinstance(value, ast.IfExp):
            return [*_Module._alias_sources(value.body), *_Module._alias_sources(value.orelse)]
        if isinstance(value, ast.BoolOp):
            return [n for v in value.values for n in _Module._alias_sources(v)]
        return []

    def _alias_pairs(self):
        """``(name, value)`` for every way a name can be bound to a plain name / attribute: an
        assignment (several targets, tuple unpacking), an annotated one, ``:=``, a ``for`` over a
        literal, and a ``with`` item that is handed the value."""
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    yield from self._pairs(target, node.value)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                yield from self._pairs(node.target, node.value)
            elif isinstance(node, ast.NamedExpr):
                yield from self._pairs(node.target, node.value)
            elif isinstance(node, (ast.For, ast.AsyncFor)) and isinstance(
                node.iter, (ast.Tuple, ast.List, ast.Set)
            ):
                for elt in node.iter.elts:
                    yield from self._pairs(node.target, elt)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if isinstance(item.context_expr, ast.Call) and item.optional_vars is not None:
                        for arg in item.context_expr.args:
                            yield from self._pairs(item.optional_vars, arg)

    def _pairs(self, target: ast.AST, value: ast.AST):
        if isinstance(target, ast.Name):
            yield target.id, value
        elif isinstance(target, ast.Attribute):
            # ``self._ms = mark_safe``: ``self._ms(...)`` is matched by the attribute name (#3850)
            yield target.attr, value
        elif (
            isinstance(target, (ast.Tuple, ast.List))
            and isinstance(value, (ast.Tuple, ast.List))
            and len(target.elts) == len(value.elts)
        ):
            for t, v in zip(target.elts, value.elts):
                yield from self._pairs(t, v)

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
                value: object = ast.Constant("") if arg.arg in engine else _Param(arg.arg)
                scope.assigns.setdefault(arg.arg, []).append(value)
                scope.params[arg.arg] = value
        if self._returns_markup(node, parent):
            scope.html_returns.extend(n for n in _own_nodes(node) if isinstance(n, ast.Return))
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

    def _closure_writes(self) -> None:
        """A nested function that fills a container of an ENCLOSING scope (``parts.append(s)``,
        ``state["v"] = raw``, ``box[0] = raw``) without binding the name itself changes the enclosing
        scope's container (#3850): its writes become the owner's contents, judged where they were
        written."""
        for scope in self.scopes:
            if not isinstance(scope.node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            for store in ("contents", "keys"):
                for name, values in list(getattr(scope, store).items()):
                    if name in scope.assigns or name in scope.opaque:
                        continue  # its own local
                    outer = scope.parent
                    while outer is not None and isinstance(outer.node, ast.ClassDef):
                        outer = outer.parent
                    owner = outer.find(name) if outer is not None else None
                    if owner is None:
                        continue
                    getattr(owner, store).setdefault(name, []).extend(
                        _Foreign(v, scope) if not isinstance(v, _Foreign) else v for v in values
                    )

    def _returns_markup(self, node: ast.AST, parent: _Scope | None) -> bool:
        """``node`` is a method whose return value Django treats as safe markup: ``__html__`` (what
        ``conditional_escape`` / ``format_html`` call), or ``__str__`` of a class decorated with
        ``django.utils.html.html_safe`` (which makes ``__html__`` return it)."""
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or parent is None:
            return False
        if not isinstance(parent.node, ast.ClassDef):
            return False
        if node.name == "__html__":
            return True
        return node.name == "__str__" and any(
            self.origin(d, parent) == "django.utils.html.html_safe" or _callee(d) == "html_safe"
            for d in parent.node.decorator_list
        )

    def _bind_nested_parameters(self) -> None:
        """A function NESTED in another, whose name is only ever the callee of a plain call in that
        function, has every call site in view: its parameters hold what those calls pass (#3845).

        Not for a method (a subclass anywhere can call it with anything), a name used any other way
        (``map(_btn, xs)``, returned, stored, passed), a decorated function, a call with ``*args`` /
        ``**kw``, or a ``*args`` / ``**kw`` parameter. A parameter a call omits holds its default.
        """
        for fscope in self.scopes:
            fn = fscope.node
            holder = fscope.parent
            if (
                not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                or fn.decorator_list
                or holder is None
                or not isinstance(holder.node, (ast.FunctionDef, ast.AsyncFunctionDef))
                or len(holder.def_nodes.get(fn.name, [])) != 1
                or len(holder.assigns.get(fn.name, [])) != 1
                or fn.name in holder.opaque
                or fn.name in self.rebound_names
                or self.dynamic_globals
            ):
                continue
            calls = self._only_called(fn.name, holder)
            if not calls:
                continue
            positional = [*fn.args.posonlyargs, *fn.args.args]
            names = [a.arg for a in positional]
            defaults: dict[str, ast.expr] = dict(
                zip(names[len(names) - len(fn.args.defaults) :], fn.args.defaults)
            )
            for arg, default in zip(fn.args.kwonlyargs, fn.args.kw_defaults):
                if default is not None:
                    defaults[arg.arg] = default
            bound: dict[str, list[_Foreign]] = {
                a.arg: [] for a in [*positional, *fn.args.kwonlyargs]
            }
            ok = True
            for call, call_scope in calls:
                given: dict[str, ast.expr] = {}
                if len(call.args) > len(positional):
                    ok = False
                    break
                for param, value in zip(names, call.args):
                    given[param] = value
                for kw in call.keywords:
                    if (
                        kw.arg not in bound
                        or kw.arg in given
                        or kw.arg in names[: len(fn.args.posonlyargs)]
                    ):
                        ok = False
                        break
                    given[kw.arg] = kw.value
                if not ok:
                    break
                for param in bound:
                    if param in given:
                        bound[param].append(_Foreign(given[param], call_scope))
                    elif param in defaults:
                        bound[param].append(_Foreign(defaults[param], holder))
                    else:
                        ok = False  # a call that would raise: nothing to learn from it
                        break
                if not ok:
                    break
            if not ok:
                continue
            for param, values in bound.items():
                entry = fscope.params.get(param)
                if not isinstance(entry, _Param):
                    continue  # the engine vouched for it already
                value = _AnyOf(values)
                fscope.params[param] = value
                fscope.assigns[param] = [value if v is entry else v for v in fscope.assigns[param]]

    def _only_called(self, name: str, holder: _Scope) -> list[tuple[ast.Call, _Scope]] | None:
        """Every use of ``name`` inside ``holder`` (nested scopes included), when each one is the
        callee of a call without ``*args`` / ``**kw`` that resolves to ``holder``'s binding; else
        ``None``."""
        parents = {id(c): n for n in ast.walk(holder.node) for c in ast.iter_child_nodes(n)}
        calls: list[tuple[ast.Call, _Scope]] = []
        for node in ast.walk(holder.node):
            if not (isinstance(node, ast.Name) and node.id == name):
                continue
            if not isinstance(node.ctx, ast.Load):
                if node is not None and isinstance(node.ctx, (ast.Store, ast.Del)):
                    return None  # rebound or deleted somewhere
                continue
            call = parents.get(id(node))
            if not (isinstance(call, ast.Call) and call.func is node) or self._has_star(call):
                return None
            # the scope the call is evaluated in, and it must see ``holder``'s ``name``
            owner = parents.get(id(call))
            while owner is not None and id(owner) not in self.scope_of:
                owner = parents.get(id(owner))
            call_scope = self.scope_of.get(id(owner)) if owner is not None else holder
            if call_scope is None or call_scope.find(name) is not holder:
                return None
            calls.append((call, call_scope))
        return calls or None

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
                elif isinstance(target, ast.Subscript):
                    self._store_into(scope, target, node.value)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if isinstance(node.target, ast.Name):
                self._bind(scope, node, node.target.id, node.value)
                scope.annotated.add(node.target.id)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            n = node.target.id
            if isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
                scope.contents.setdefault(n, []).extend(node.value.elts)
            elif isinstance(node.op, (ast.Add, ast.Mod, ast.BitOr)):
                scope.pieces.setdefault(n, []).append(node.value)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Subscript):
            self._store_into(scope, node.target, node.value)  # ``d[k] += raw``
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            self._bind_iteration(scope, node, node.target, node.iter)
            for leaf in _subscript_targets(node.target):  # ``for box[0] in items`` (#3850)
                self._store_into(
                    scope,
                    leaf,
                    ast.Subscript(value=node.iter, slice=ast.Constant(0), ctx=ast.Load()),
                )
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
                    for leaf in _subscript_targets(item.optional_vars):  # ``as box[0]`` (#3850)
                        self._store_into(scope, leaf, item.context_expr)
        elif isinstance(node, ast.NamedExpr):
            self._bind(scope, node, node.target.id, node.value)
        elif isinstance(node, (ast.ExceptHandler,)) and node.name:
            self._bind(scope, node, node.name, RAW)
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            root = (
                self._container_root(call.func.value)
                if isinstance(call.func, ast.Attribute)
                else None
            )
            if root is not None:
                # ``rows[0].append(x)`` / ``d["a"].append(x)`` / ``d.setdefault("a", []).append(x)``:
                # an element of an element of the root container, judged as one of its elements
                n, method = root, call.func.attr  # type: ignore[union-attr]
                if method in ("append", "add") and call.args:
                    scope.contents.setdefault(n, []).append(call.args[0])
                elif method == "insert" and len(call.args) >= 2:
                    scope.contents.setdefault(n, []).append(call.args[1])
                elif method == "setdefault" and len(call.args) >= 2:
                    scope.contents.setdefault(n, []).append(call.args[1])
                elif method == "update" and call.keywords:
                    for kw in call.keywords:  # ``d.update(k=raw)`` / ``d.update(**raw_dict)``
                        value = kw.value
                        if kw.arg is None:
                            value = ast.Subscript(
                                value=value, slice=ast.Constant(0), ctx=ast.Load()
                            )
                        scope.contents.setdefault(n, []).append(value)
                    if call.args:
                        scope.contents.setdefault(n, []).append(
                            ast.Subscript(value=call.args[0], slice=ast.Constant(0), ctx=ast.Load())
                        )
                if method in ("extend", "update") and call.args and not call.keywords:
                    arg = call.args[0]
                    if isinstance(arg, (ast.List, ast.Tuple, ast.Set)):
                        scope.contents.setdefault(n, []).extend(arg.elts)
                    else:  # an iterable of unknown elements: judge it as the elements
                        scope.contents.setdefault(n, []).append(
                            ast.Subscript(value=arg, slice=ast.Constant(0), ctx=ast.Load())
                        )
        # a sink call is recorded wherever it appears in this scope's own nodes
        if isinstance(node, ast.Call):
            name = self._sink_name(node.func, scope)
            if name in self.sinks or name in self.format_sinks:
                scope.sinks.append(node)
            elif (
                isinstance(node.func, ast.Name)
                and node.func.id in APPLY_CALLS
                and node.args
                and self._sink_name(node.args[0], scope) in self.sinks
            ):
                scope.sinks.append(node)  # ``map(mark_safe, items)``, ``partial(mark_safe, x)``
            elif (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "__new__"
                and node.args
                and self._sink_name(node.args[0], scope) in self.sinks
            ):
                scope.sinks.append(node)  # ``str.__new__(SafeString, x)``: a SafeString of ``x``

    def _sink_name(self, func: ast.expr, scope: _Scope | None = None) -> str | None:
        """The sink a callee spells, by NAME (#3850): ``mark_safe``, ``ss.mark_safe``, ``self._ms`` of
        ``self._ms = mark_safe``, ``FUNCS["safe"]`` of a container that holds a sink,
        ``getattr(ss, "mark_safe")``, ``(mark_safe if c else str)``, ``str.__new__`` handed a
        ``SafeString`` is not a callee (it is in :meth:`findings`)."""
        name = _callee(func)
        if name is not None:
            return name
        if isinstance(func, ast.Subscript):
            root = func.value
            while isinstance(root, ast.Subscript):
                root = root.value
            key = _callee(root)
            return self.sink_containers.get(key) if key else None
        if (
            isinstance(func, ast.Call)
            and isinstance(func.func, ast.Name)
            and func.func.id == "getattr"
            and len(func.args) >= 2
            and isinstance(func.args[1], ast.Constant)
            and isinstance(func.args[1].value, str)
        ):
            return func.args[1].value
        if isinstance(func, ast.IfExp):
            for branch in (func.body, func.orelse):
                name = self._sink_name(branch, scope)
                if name in self.sinks or name in self.format_sinks:
                    return name
        if isinstance(func, ast.BoolOp):
            for value in func.values:
                name = self._sink_name(value, scope)
                if name in self.sinks or name in self.format_sinks:
                    return name
        return None

    @staticmethod
    def _container_root(e: ast.expr) -> str | None:
        """The local container ``e`` reaches into: ``rows`` of ``rows``, ``rows[0]``, ``d["a"][1]``,
        ``d.get("a")``, ``d.setdefault("a", [])``; ``None`` for anything else."""
        while True:
            if isinstance(e, ast.Name):
                return e.id
            if isinstance(e, ast.Subscript):
                e = e.value
            elif (
                isinstance(e, ast.Call)
                and isinstance(e.func, ast.Attribute)
                and e.func.attr in ("get", "setdefault", "pop")
            ):
                e = e.func.value
            else:
                return None

    def _store_into(self, scope: _Scope, target: ast.Subscript, value: ast.expr) -> None:
        """``c[k] = value`` (or ``c[i][j] = value``): ``value`` and the KEY ``k`` become elements of
        the root container ``c`` (a dict's keys are what iterating it yields, #3850)."""
        root = self._container_root(target.value)
        if root is None:
            return
        scope.contents.setdefault(root, []).append(value)
        key = target.slice
        if not isinstance(key, ast.Slice):
            scope.keys.setdefault(root, []).append(key)

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
        return name in self.rebound_names or name in self.mutated

    def _stable(self, key: str, values: list[object], owner: _Scope | None = None) -> bool:
        """Nothing in the file changes what ``key`` holds behind the back of its definition.

        Not stable when it is changed in place anywhere (``.append`` / ``.update`` / ``.setdefault`` /
        ``D[k] = v`` / ``|=`` / ``.__setitem__``, through ``self.D`` or an alias), declared ``global`` /
        ``nonlocal``, or a list / dict / set that is handed on (``a = D``, ``f(D)``, ``return D``) so
        that something else can change it; or, when its ELEMENTS are containers, when one of those is.
        """
        if self._in(self.mutated, self.mutated_in, key, owner) or key in self.rebound_names:
            return False
        if self._in(self.escaped, self.escaped_in, key, owner) and any(
            self._mutable_display(v) for v in values
        ):
            return False
        if self._in(self.escaped_elements, self.escaped_elements_in, key, owner) and any(
            self._mutable_elements(v) for v in values
        ):
            return False
        return True

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
            if not self._stable(e.id, values, owner):
                return False
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
        if e.value.id != "self" and cls.node.keywords:  # type: ignore[attr-defined]
            # ``cls.X`` / ``C.X`` with a metaclass: a data descriptor ``X`` on the metaclass wins over
            # the class body's ``X`` (#3864). An instance lookup (``self.X``) never sees it.
            return None
        if self._class_dynamic(cls):
            return None  # ``setattr(self, key, value)`` and kin: any attribute may be replaced
        if self._mutated_or_rebound(e.attr) or e.attr in self.assigned_attrs:
            return None
        if self._falls_through(cls) and not self._defined_unconditionally(cls, e.attr):
            return None
        values = cls.assigns.get(e.attr, [])
        if not self._stable(e.attr, values, cls):
            return None
        # a ``def`` / ``class`` of that name is a method or a nested class, not a constant
        if not values or e.attr in cls.def_nodes or self._overridden_in_file(cls, e.attr):
            return None
        return cls, values

    def _falls_through(self, cls: _Scope) -> bool:
        """A lookup the class body does not satisfy can reach other code: ``__getattr__`` (on the class,
        on a class of this file that inherits from it, or assigned from outside), a base class other
        than ``object`` (it may define the name), a metaclass or other class keyword (#3850)."""
        node = cls.node
        if self.getattr_assigned or node.keywords:  # type: ignore[attr-defined]
            return True
        if any(
            not (isinstance(b, ast.Name) and b.id == "object")
            for b in node.bases  # type: ignore[attr-defined]
        ):
            return True
        return any(
            "__getattr__" in x.def_nodes or "__getattr__" in x.assigns
            for x in [cls, *self._subclasses(cls)]
        )

    @staticmethod
    def _defined_unconditionally(cls: _Scope, name: str) -> bool:
        """The class body binds ``name`` by a top-level statement (not inside ``if`` / ``try`` /
        ``for`` / ``with``), so the name is there whatever runs at class-creation time."""
        for stmt in cls.node.body:  # type: ignore[attr-defined]
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if stmt.name == name:
                    return True
            elif isinstance(stmt, ast.Assign):
                if any(name in _target_names(t) for t in stmt.targets):
                    return True
            elif isinstance(stmt, ast.AnnAssign):
                if stmt.value is not None and name in _target_names(stmt.target):
                    return True
        return False

    def _overridden_in_file(self, cls: _Scope, attr: str) -> bool:
        """A class of THIS file that inherits from ``cls`` (directly or through others of this file)
        and binds ``attr`` itself: ``self.attr`` may be its value, not ``cls``'s. A subclass in
        another file cannot be seen (the module docstring says so)."""
        return any(attr in x.assigns or attr in x.def_nodes for x in self._subclasses(cls))

    def _subclasses(self, cls: _Scope) -> list[_Scope]:
        """The classes of this file that inherit from ``cls``, directly or through each other."""
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
        return subclasses

    # -- judging ------------------------------------------------------------------------
    def findings(self) -> list[Finding]:
        out: list[Finding] = []
        for scope in self.scopes:
            for call in scope.sinks:
                name = self._sink_name(call.func, scope) or "?"
                if (isinstance(call.func, ast.Name) and call.func.id in APPLY_CALLS) or (
                    isinstance(call.func, ast.Attribute) and call.func.attr == "__new__"
                ):
                    args = [*call.args[1:], *(kw.value for kw in call.keywords)]
                elif name in self.format_sinks and name not in self.sinks:
                    position = FORMAT_SINK_ARG.get(name, 0)
                    args = call.args[position : position + 1]
                    if not args:  # by keyword (``format_string=``), or hidden in ``**kw``
                        named = [kw.value for kw in call.keywords if kw.arg == "format_string"]
                        args = named or [kw.value for kw in call.keywords][:1]
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
            judged = {id(c) for c in scope.sinks}
            for ret in scope.html_returns:
                value = ret.value
                while (  # ``str(mark_safe(..))`` / ``cast(str, mark_safe(..))`` is that sink's value
                    isinstance(value, ast.Call)
                    and id(value) not in judged
                    and _callee(value.func) in ("str", "cast")
                    and value.args
                ):
                    value = value.args[-1]
                if value is None or id(value) in judged:
                    continue  # ``return mark_safe(..)``: that sink is judged (and counted) already
                if self.tainted(ret.value, scope, 0, frozenset()):
                    out.append(
                        Finding(
                            ret.lineno,
                            scope.name,
                            "__html__",
                            (ast.get_source_segment(self.source, ret.value) or "")[:90],
                        )
                    )
        return sorted(set(out), key=lambda f: (f.lineno, f.function))

    # clean: no raw data anywhere in this expression
    def clean(self, e: object, sc: _Scope, depth: int, seen: frozenset) -> bool:
        if depth > _DEPTH or e is RAW:
            return False
        if isinstance(e, _Actual) and _CTX_OK not in seen:
            # an argument bound from the call (#3864): clean only where its position was checked
            return self._position_free(e.expr, e.scope)
        if isinstance(e, _Foreign):
            return self.clean(e.expr, e.scope, depth + 1, seen)
        if isinstance(e, _AnyOf):
            return bool(e.values) and all(self.clean(v, sc, depth + 1, seen) for v in e.values)
        if isinstance(e, _ElementOf):
            if self._deep_digits(e.iterable, sc):
                return True  # ``for day in week`` of ``cal.monthdayscalendar(...)``: an int
            return self.elements_clean(e.iterable, sc, depth + 1, seen)
        if not isinstance(e, ast.expr):
            return False
        d = depth + 1
        if isinstance(e, ast.Constant):
            return True
        if isinstance(e, ast.JoinedStr):
            return all(
                self._field_clean(v, sc, d, seen, e, i)
                for i, v in enumerate(e.values)
                if isinstance(v, ast.FormattedValue)
            )
        if isinstance(e, (ast.NamedExpr, ast.Await)):
            return self.clean(e.value, sc, d, seen)
        if isinstance(e, ast.Starred):  # ``*items``: what it spreads
            return self.elements_clean(e.value, sc, d, seen)
        if isinstance(e, ast.BinOp):
            if isinstance(e.op, ast.Add):
                return self._add_clean(e, sc, d, seen)
            if isinstance(e.op, ast.Mod):
                return self._percent_clean(e, sc, d, seen)
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
            # ``(end - start).days`` / ``current.year``: an int (#3847)
            if e.attr in DATE_DIGIT_ATTRS and (
                self._is_date(e.value, sc)
                or self._is_delta(e.value, sc)
                or (
                    isinstance(e.value, ast.BinOp)
                    and isinstance(e.value.op, ast.Sub)
                    and self._is_date(e.value.left, sc)
                    and self._is_date(e.value.right, sc)
                )
            ):
                return True
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
            if id(e) in self.narrowed_exprs:
                return True  # ``str(x)`` where ``str(x) in CONSTANT`` holds: a value from the table
            if _CTX_OK not in seen and (
                self.origin(e.func, sc) in CONTEXT_SENSITIVE_ORIGINS
                or self.origin(e.func, sc) in JSON_ESCAPERS
            ):
                return False  # a context-sensitive escaper's output whose position nobody checked
            return self._call_clean(e, sc, d, seen - {_CTX_OK}, placed=_CTX_OK in seen)
        return False

    def _origin_of_import(self, e: ast.Name, sc: _Scope) -> str | None:
        """The origin of ``e`` when it is a bare imported name nothing in the code rebinds."""
        if sc.find(e.id) is not None or self.star_import:
            return None
        return self.imports.get(e.id)

    def _hold_level(
        self,
        value: object,
        sc: _Scope,
        origins: frozenset,
        depth: int = 0,
        visited: frozenset = frozenset(),
    ) -> int:
        """Whether ``value`` carries the output of an escaper of ``origins``: ``0`` no; ``1`` it IS such a
        call (through a name, ``a if c else b``, ``a or b``, ``(n := ...)``); ``2`` it is built from one
        (a ``+`` / ``%`` / ``.format`` / f-string / ``join`` of it, a container or comprehension of it,
        another call given it, a local helper that returns it): the position it was checked at is no
        longer the position it lands at (#3850), so it is judged again where it is placed. Past the
        depth bound the answer is ``2`` (fail closed)."""
        if isinstance(value, _Actual) and origins is _BOUND:
            return 0 if self._position_free(value.expr, value.scope) else 1
        if isinstance(value, _Foreign):  # a parameter bound from a call site: the argument (#3864)
            return self._hold_level(value.expr, value.scope, origins, depth + 1, visited)
        if isinstance(value, _AnyOf):
            return max(
                (self._hold_level(v, sc, origins, depth + 1, visited) for v in value.values),
                default=0,
            )
        if not isinstance(value, ast.expr):
            return 0
        if depth > 30:
            return 2
        d = depth + 1

        def many(parts) -> int:
            return max((self._hold_level(x, sc, origins, d, visited) for x in parts), default=0)

        def built(parts) -> int:
            return 2 if many(parts) else 0

        if isinstance(value, ast.Call):
            if self.origin(value.func, sc) in origins:
                return 1
            if origins is _SPEC or origins is _BOUND:
                return self._call_carries(value, sc, origins, many)
            if (
                isinstance(value.func, ast.Name)
                and value.func.id in ("len", "int", "float", "bool")
                and sc.find(value.func.id) is None
            ):
                return 0  # digits
            parts = [*value.args, *(k.value for k in value.keywords)]
            if isinstance(value.func, ast.Attribute):
                parts.append(value.func.value)
            fn_origin = self.origin(value.func, sc)
            if fn_origin in CLEAN_ORIGINS or fn_origin in SAFE_ALPHABET_ORIGINS:
                # ``conditional_escape(safe_css_length(x))``: still the escaper's output, same position rules
                return 1 if many(parts) else 0
            if many(parts):
                return 2
            local = self._local_function(value.func, sc)
            if local is not None:
                fn, fscope = local
                key = (id(fn), id(origins))
                if self._actuals:  # a call-site binding is in force: what it returns may differ
                    if key in self._fn_hold_busy:
                        return 0  # recursion adds nothing new
                    self._fn_hold_busy.add(key)
                    try:
                        held = self._unbound(
                            fscope, lambda: self._returns_hold(fn, fscope, origins)
                        )
                    finally:
                        self._fn_hold_busy.discard(key)
                    return 2 if held else 0
                if key not in self._fn_hold:
                    if key in self._fn_hold_busy:
                        return 0  # recursion adds nothing new
                    self._fn_hold_busy.add(key)
                    held = self._returns_hold(fn, fscope, origins)
                    self._fn_hold_busy.discard(key)
                    self._fn_hold[key] = 2 if held else 0
                return self._fn_hold[key]
            return 0
        if isinstance(value, ast.IfExp):
            return many([value.body, value.orelse])
        if isinstance(value, ast.BoolOp):
            return many(value.values)
        if isinstance(value, ast.NamedExpr):
            return self._hold_level(value.value, sc, origins, d, visited)
        if isinstance(value, ast.Name):
            owner = sc.find(value.id)
            if owner is None:
                return 0
            key = ("name", value.id, id(owner), self._ctx(owner))
            if key in visited:
                return 0
            values, _ = self._bindings(value, value.id, owner, sc)
            inner = visited | {key}
            level = max((self._hold_level(v, owner, origins, d, inner) for v in values), default=0)
            extra = [*owner.contents.get(value.id, []), *owner.pieces.get(value.id, [])]
            if any(
                self._hold_level(*_unwrap(v, owner), origins, d, inner)  # type: ignore[arg-type]
                for v in extra
            ):
                level = 2
            return level
        if isinstance(value, ast.BinOp):
            return built([value.left, value.right])
        if isinstance(value, ast.JoinedStr):
            return built([v.value for v in value.values if isinstance(v, ast.FormattedValue)])
        if isinstance(value, ast.FormattedValue):
            return built([value.value])
        if isinstance(value, ast.Starred):
            return built([value.value])
        if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            return built(value.elts)
        if isinstance(value, ast.Dict):
            return built([v for v in value.values if v is not None])
        if isinstance(value, ast.Subscript):
            return built([value.value])
        if isinstance(value, (ast.ListComp, ast.GeneratorExp, ast.SetComp, ast.DictComp)):
            inner_scope = self._comprehension_scope(value, sc)
            elt = value.value if isinstance(value, ast.DictComp) else value.elt
            return 2 if self._hold_level(elt, inner_scope, origins, d, visited) else 0
        return 0

    def _call_carries(self, value: ast.Call, sc: _Scope, origins: frozenset, many) -> int:
        """:meth:`_hold_level` of a call for a bound argument / a call clean only for its arguments.

        Only a call whose RESULT holds its arguments' text counts: a function of this file that needs
        them, ``str`` / ``min`` / ... of them, ``x.strip()`` / ``.join`` / ``.format`` of them. Any
        other call (an unknown one, ``d.get(k)``, ``escape(..)``) gives a value that is raw or clean
        on its own terms, so nothing to place: what it may still need is judged by ``clean``."""
        origin = self.origin(value.func, sc)
        if (
            origin in CLEAN_ORIGINS
            or origin in CONTEXT_SENSITIVE_ORIGINS
            or origin in JSON_ESCAPERS
            or self._trusted_call(value, sc)
            or self._position_free(value, sc)
        ):
            return 0  # an escaper trusted by its origin (placed by its own rules), or digits
        args = [*value.args, *(k.value for k in value.keywords)]
        if self._needs_arguments(value, sc):
            return 1 if origins is _SPEC else (2 if many(args) else 0)
        if self._local_function(value.func, sc) is not None:
            # clean whatever it is given: its result carries none of them, but it is placed by what
            # it returns (markup or escaped text is not clean in every position)
            return 1 if origins is _SPEC else 0
        name = _callee(value.func)
        if origin in CLEAN_IF_ARGS_CLEAN:
            return 2 if many(args) else 0
        if isinstance(value.func, ast.Attribute) and (
            name in STR_METHODS or name == "join" or name in FORMAT_METHODS
        ):
            return 2 if many([*args, value.func.value]) else 0
        return 0

    def _return_kind(self, call: ast.Call, sc: _Scope, depth: int = 0) -> str | None:
        """Where the result of a call of a function of this file may be placed, judged by what it
        returns (any argument): ``None`` anywhere (literals, digits), ``"bound"`` in text or a quoted
        value that is not a URL / event handler / style (escaped text keeps its spaces), ``"call"`` in
        element text only (markup, or text the guard cannot read)."""
        local = self._local_function(call.func, sc)
        if local is None:
            return "call"
        fn, fscope = local
        key = ("return-kind", id(fn))
        if not self._actuals and key in self._kinds:
            return self._kinds[key]
        if depth > 6 or key in self._kind_busy:
            return "call"
        self._kind_busy.add(key)
        try:
            kinds = self._unbound(
                fscope,
                lambda: [
                    self._value_kind(v, fscope, depth + 1)
                    for v in self._live_returns(fn, fscope)
                    if v is not None
                ],
            )
        finally:
            self._kind_busy.discard(key)
        kind = _strictest(kinds)
        if not self._actuals and not self._kind_busy:  # not cut short by a recursion
            self._kinds[key] = kind
        return kind

    def _value_kind(self, e: ast.expr, sc: _Scope, depth: int) -> str | None:
        """:meth:`_return_kind` of one returned expression."""
        if depth > 8:
            return "call"
        if self._position_free(e, sc):
            return None
        d = depth + 1
        if isinstance(e, (ast.JoinedStr, ast.BinOp, ast.Constant)):
            segments = self._segments(e)
            if segments is not None:
                return self._segments_kind(segments, sc, d)
        if isinstance(e, ast.IfExp):
            return _strictest([self._value_kind(e.body, sc, d), self._value_kind(e.orelse, sc, d)])
        if isinstance(e, ast.BoolOp):
            return _strictest([self._value_kind(v, sc, d) for v in e.values])
        if isinstance(e, ast.Name):
            owner = sc.find(e.id)
            if owner is None or owner.contents.get(e.id):
                return "call"
            values, _ = self._bindings(e, e.id, owner, sc)
            found = [x for v in values for x in _foreign_values(v, owner)]
            if not values or len(found) != len(values):
                return "call"
            # ``s += piece``: each piece is placed after the others, so each must be a whole
            # fragment on its own (whole attributes stay whole attributes when concatenated)
            pieces = [(p, owner) for p in owner.pieces.get(e.id, [])]
            return _strictest([self._value_kind(x, xsc, d) for x, xsc in [*found, *pieces]])
        if isinstance(e, (ast.Attribute, ast.Subscript)):
            values = self._table_values(e, sc)
            if values is not None:
                return _strictest([self._value_kind(v, vsc, d) for v, vsc in values])
        if isinstance(e, ast.Call):
            origin = self.origin(e.func, sc)
            if origin in _TEXT_ESCAPERS:
                return "bound"  # escaped text: no quote, no tag, but its spaces and its scheme
            if origin in CONTEXT_SENSITIVE_ORIGINS:
                return "sens"  # a value escaper: text or a quoted value (``_PLACES``)
            if origin in JSON_ESCAPERS:
                return "json"
            if self._local_function(e.func, sc) is not None:
                return self._return_kind(e, sc, d)
            name = _callee(e.func)
            args = [*e.args, *(k.value for k in e.keywords)]
            if origin in CLEAN_IF_ARGS_CLEAN and not self._has_star(e):
                return _strictest([self._value_kind(a, sc, d) for a in args])  # ``str(x)``
            if isinstance(e.func, ast.Attribute):
                recv = e.func.value
                if name == "join" and len(e.args) == 1 and not e.keywords:
                    elements = self._elements_of(e.args[0], sc)
                    if elements is not None:
                        return _strictest(
                            [self._value_kind(recv, sc, d)]
                            + [self._value_kind(x, xsc, d) for x, xsc in elements]
                        )
                if name in PURE_TRANSFORMS and not args:  # ``x.strip()``: the text of ``x``
                    return self._value_kind(recv, sc, d)
            if (
                self._trusted_call(e, sc)
                and origin not in CLEAN_ORIGINS
                and origin not in CLEAN_RECORD_ORIGINS
                and _callee(e.func) not in RENDER_CALLS
            ):
                return None  # a value of a fixed alphabet: a colour, a date, a token
        return "call"

    def _table_values(self, e: ast.expr, sc: _Scope) -> list[tuple[ast.expr, _Scope]] | None:
        """What ``self.TABLE`` (a class attribute the class writes out) or ``TABLE[k]`` /
        ``self.TABLE[k]`` of a display can be; ``None`` when that is not known."""
        if isinstance(e, ast.Attribute):
            found = self.class_attribute(e, sc)
            if found is None or not all(isinstance(v, ast.expr) for v in found[1]):
                return None
            return [(v, found[0]) for v in found[1]]  # type: ignore[misc]
        if isinstance(e, ast.Subscript) and not isinstance(e.slice, ast.Slice):
            holders = (
                self._table_values(e.value, sc)
                if isinstance(e.value, ast.Attribute)
                else [(e.value, sc)]
            )
            if holders is None:
                return None
            out: list[tuple[ast.expr, _Scope]] = []
            for holder, hsc in holders:
                if isinstance(holder, ast.Dict):
                    if any(v is None for v in holder.values):
                        return None
                    out.extend((v, hsc) for v in holder.values)  # type: ignore[misc]
                    continue
                elements = self._elements_of(holder, hsc)
                if elements is None:
                    return None
                out.extend(elements)
            return out
        return None

    @staticmethod
    def _segments(e: ast.expr) -> list | None:
        """``e`` as literal text and the parts between: an f-string, a ``+`` chain, a literal."""
        if isinstance(e, ast.Constant):
            return [e.value] if isinstance(e.value, str) else None
        if isinstance(e, ast.JoinedStr):
            out: list = []
            for v in e.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    out.append(v.value)
                elif isinstance(v, ast.FormattedValue):
                    out.append(v.value)
                else:
                    return None
            return out
        if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Add):
            left = _Module._segments(e.left)
            right = _Module._segments(e.right)
            return [
                *(left if left is not None else [e.left]),
                *(right if right is not None else [e.right]),
            ]
        return None

    def _segments_kind(self, segments: list, sc: _Scope, d: int) -> str | None:
        """Literal text with parts in it. Text with no markup character: the strictest part. Whole
        attributes (every part inside a quote the text opens and fit for that attribute, every quote
        closed, no ``<`` / ``>``, a leading space): ``"attrs"``. Any other markup: ``"call"``."""
        literal = "".join(x for x in segments if isinstance(x, str))
        parts = [x for x in segments if not isinstance(x, str)]
        if not _MARKUP_CHARS & set(literal):
            return _strictest([self._value_kind(p, sc, d) for p in parts])
        if set("<>`") & set(literal):
            return "call"
        text = ""
        for x in segments:
            if isinstance(x, str):
                text += x
                continue
            kind = self._value_kind(x, sc, d)
            if kind is not None and self._place("<x " + text) not in _PLACES[kind]:
                return "call"
            text += "0"
        state, _ = self._context("<x " + text)
        return "attrs" if state == "tag" and text[:1].isspace() else "call"

    def _returns_hold(self, fn: ast.AST, fscope: _Scope, origins: frozenset) -> bool:
        return any(
            self._hold_level(v, fscope, origins) for v in _return_values(fn) if v is not None
        )

    def _alternatives(
        self,
        value: ast.expr,
        sc: _Scope,
        depth: int = 0,
        visited: frozenset = frozenset(),
        via: bool = False,
    ) -> list[tuple[ast.expr, _Scope, bool]]:
        """The expressions ``value`` can be: itself, or for a name / ``a if c else b`` / ``a or b`` /
        ``(n := a)`` / ``await a`` each value it can hold. The flag says it was reached through a
        parameter bound from the call whose result is judged (:class:`_Actual`)."""
        if depth > 8:
            return [(value, sc, via)]
        d = depth + 1
        if isinstance(value, ast.IfExp):
            return [
                *self._alternatives(value.body, sc, d, visited, via),
                *self._alternatives(value.orelse, sc, d, visited, via),
            ]
        if isinstance(value, ast.BoolOp):
            return [a for v in value.values for a in self._alternatives(v, sc, d, visited, via)]
        if isinstance(value, (ast.NamedExpr, ast.Await)):
            return self._alternatives(value.value, sc, d, visited, via)
        if isinstance(value, ast.Name):
            owner = sc.find(value.id)
            if owner is None:
                return []
            key = ("alt", value.id, id(owner), self._ctx(owner))
            if key in visited:
                return []
            if owner.contents.get(value.id) or owner.pieces.get(value.id):
                return [(value, sc, via)]  # a container / a string built up: judged as a whole
            values, _ = self._bindings(value, value.id, owner, sc)
            out: list[tuple[ast.expr, _Scope, bool]] = []
            for v in values:
                # a parameter bound from its call sites: each argument, in its caller's scope
                bound = via or isinstance(v, _Actual)
                for x, xsc in _foreign_values(v, owner):
                    out.extend(self._alternatives(x, xsc, d, visited | {key}, bound))
            return out
        return [(value, sc, via)]

    def _elements_of(
        self, e: ast.expr, sc: _Scope, depth: int = 0
    ) -> list[tuple[ast.expr, _Scope]] | None:
        """The element expressions of a list / tuple / set display, a comprehension or generator, or a
        name that holds one (and every ``append`` / ``+=`` to it); ``None`` when that is not known."""
        if depth > 6:
            return None
        if isinstance(e, (ast.List, ast.Tuple, ast.Set)):
            return [(x, sc) for x in e.elts]
        if isinstance(e, (ast.ListComp, ast.GeneratorExp, ast.SetComp)):
            return [(e.elt, self._comprehension_scope(e, sc))]
        if isinstance(e, ast.Name):
            owner = sc.find(e.id)
            if owner is None:
                return None
            values, _ = self._bindings(e, e.id, owner, sc)
            out: list[tuple[ast.expr, _Scope]] = [
                _unwrap(c, owner)  # type: ignore[misc]
                for c in owner.contents.get(e.id, [])
            ]
            for v in values:
                inner = self._elements_of(v, owner, depth + 1) if isinstance(v, ast.expr) else None
                if inner is None:
                    return None
                out.extend(inner)
            for piece in owner.pieces.get(e.id, []):
                inner = self._elements_of(piece, owner, depth + 1)
                if inner is None:
                    return None
                out.extend(inner)
            return out
        return None

    def _joined_elements_kind(self, alt: ast.expr, sc: _Scope, kind: str, found: list[str]) -> bool:
        """``"".join(parts)``: judged by what each element is. An element that opens markup of its own
        (``f'src="{u}"'``) is placed wherever the join is; a bare output (``safe_url(y)``) or one that
        is position-dependent makes the join position-dependent. ``False`` when the elements are not
        known (the caller then treats the join as built from the output)."""
        if not (
            isinstance(alt, ast.Call)
            and isinstance(alt.func, ast.Attribute)
            and alt.func.attr == "join"
            and len(alt.args) == 1
        ):
            return False
        elements = self._elements_of(alt.args[0], sc)
        if elements is None:
            return False
        for element, esc in elements:
            sub_kind = self._sens_kind(element, esc)
            if sub_kind is not None and sub_kind.startswith(kind):
                found.append(sub_kind if sub_kind.endswith("*") else sub_kind + "*")
        return True

    def _sens_kind(self, value: ast.expr, sc: _Scope) -> str | None:
        """``"json"`` (clean in text only), ``"sens"`` (clean in text or a quoted value) or ``None``;
        a trailing ``*`` marks a value BUILT from such an output whose position is the outer one
        (clean in text only). The strictest of the values it can hold."""
        found: list[str] = []
        for alt, asc, via in self._alternatives(value, sc):
            escaper = False  # an escaper's output was found: the other escaper is not looked for
            # a call of a function of this file, or an argument bound from the call being judged:
            # placed by what it holds (``_value_kind``: escaped text, whole attributes, markup)
            if (
                (via and not self._position_free(alt, asc))
                or self._hold_level(alt, asc, _SPEC)
                or self._hold_level(alt, asc, _BOUND)
            ):
                kind = self._value_kind(alt, asc, 0)
                if kind in ("bound", "attrs", "call"):
                    found.append(kind)
            for origins, kind in ((JSON_ESCAPERS, "json"), (CONTEXT_SENSITIVE_ORIGINS, "sens")):
                if escaper:
                    continue
                level = self._hold_level(alt, asc, origins)
                if level and via and kind == "sens":
                    found.append("bsens")  # handed to a helper: not into its script or CSS
                if level == 1:
                    found.append(kind)
                elif level == 2 and self._joined_elements_kind(alt, asc, kind, found):
                    pass
                elif level == 2:
                    # built from the output. It is judged again where it is placed, unless it is a
                    # BALANCED fragment of its own: the output sits inside a quote the builder opens
                    # ("the head": the literal text before the first part the guard cannot read, in an
                    # attribute: ``' style="' + safe_css_length(x)``) and the builder's whole literal
                    # text leaves no quote open. Such a fragment is then placed unchecked: between
                    # attributes (the case that matters), but also inside a quoted value or an
                    # unquoted one, which the guard does not see (#3864).
                    text = self._leaf_text(alt, asc)
                    whole = text.replace("\x00", "0")
                    # EVERY part the guard cannot read sits inside a quote the builder opened, not
                    # only the first (#3864: ``' title="' + safe_url(x) + '" z=' + safe_url(x)``)
                    unread = [i for i, ch in enumerate(text) if ch == "\x00"]
                    balanced = (
                        bool(unread)
                        and all(
                            self._position("<x " + text[:i].replace("\x00", "0")) == "quoted"
                            for i in unread
                        )
                        and self._position("<x " + whole) != "quoted"
                    )
                    if not balanced:
                        found.append(kind + "*")
                if level:
                    escaper = True  # placed by the rules of the first escaper found
        # the strictest first: element text only, then anything but an unquoted attribute
        bases = {k.rstrip("*") for k in found}
        if bases & {"call", "attrs", "bound", "bsens"}:
            # a call or a bound argument among them: where EVERY one of them may land
            return _strictest(sorted(bases)) or "call"
        for kind in ("json*", "json", "sens*", "sens"):
            if kind in found:
                return kind
        return None

    def _leaf_text(self, e: ast.expr, sc: _Scope, depth: int = 0) -> str:
        """The literal text ``e`` contributes in front of a later part of the same string: its own for a
        string constant, an f-string (a field counts as ``\x00``), a name that only ever holds one
        such value, or a call of a function of this file whose every ``return`` is the same literal;
        ``\x00`` for anything the guard cannot read."""
        if depth > 6:
            return "\x00"
        d = depth + 1
        if isinstance(e, ast.Constant):
            return e.value if isinstance(e.value, str) else "\x00"
        if isinstance(e, ast.JoinedStr):
            return "".join(
                v.value
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
                else (
                    self._leaf_text(v.value, sc, d)
                    if isinstance(v, ast.FormattedValue)
                    and v.conversion == -1
                    and v.format_spec is None
                    else "\x00"
                )
                for v in e.values
            )
        if isinstance(e, ast.BinOp):
            if isinstance(e.op, ast.Add):
                return self._leaf_text(e.left, sc, d) + self._leaf_text(e.right, sc, d)
            if (
                isinstance(e.op, ast.Mod)
                and isinstance(e.left, ast.Constant)
                and isinstance(e.left.value, str)
            ):
                return _PERCENT_SPEC.sub(
                    lambda m: "%" if m.group(2) == "%" else "\x00", e.left.value
                )
            return "\x00"
        if isinstance(e, ast.Name):
            owner = sc.find(e.id)
            if owner is None:
                return "\x00"
            values, _ = self._bindings(e, e.id, owner, sc)
            found = [x for v in values for x in _foreign_values(v, owner)]
            if len(values) == 1 and len(found) == 1:
                return self._leaf_text(found[0][0], found[0][1], d)
        if (
            isinstance(e, ast.Call)
            and isinstance(e.func, ast.Attribute)
            and e.func.attr == "format"
            and isinstance(e.func.value, ast.Constant)
            and isinstance(e.func.value.value, str)
        ):
            try:
                fields = list(string.Formatter().parse(e.func.value.value))
            except ValueError:
                return "\x00"
            return "".join(lit + ("" if field is None else "\x00") for lit, field, _s, _c in fields)
        if isinstance(e, ast.Call):
            local = self._local_function(e.func, sc)
            if local is not None:
                fn, fscope = local
                returns = [v for v in _return_values(fn) if v is not None]
                texts = self._unbound(
                    fscope, lambda: {self._leaf_text(r, fscope, d) for r in returns}
                )
                if len(texts) == 1:
                    return texts.pop()
        return "\x00"

    def _position_ok(self, kind: str, prefix: str) -> bool:
        """A ``kind`` of escaper output (or a string built from it, ``kind`` + ``"*"``) may sit after
        ``prefix``: text for ``"json"``; text or a quoted value for ``"sens"``; never an unquoted
        attribute or a tag name."""
        if kind.startswith("json"):
            return self._position(prefix) == "text"
        if kind in ("call", "bound", "attrs", "bsens"):
            # a bound argument is escaped text at best: a quoted value, but not one a browser reads
            # as a URL, as script or as CSS, where HTML escaping is not enough; a call's markup is
            # placed by what it returns; element text is never a <script> / <style> body
            return self._place(prefix) in _PLACES[kind]
        return self._position(prefix) != "tag"

    def _place(self, prefix: str) -> str:
        """``_context`` folded to the places of ``_PLACES``."""
        state, attr = self._context(prefix)
        if state == "quoted":
            if attr in _URL_ATTRIBUTES:
                return "url"
            return "code" if _code_attribute(attr) else "quoted"
        if state == "tag":
            return "unquoted" if prefix.rstrip().endswith("=") else "between"
        return state

    def _add_clean(self, e: ast.BinOp, sc: _Scope, d: int, seen: frozenset) -> bool:
        """``a + b + c``: each part clean, and a context-sensitive part (``safe_url(x)``) only where the
        literal text before it puts it in text or a quoted value (#3850: not only in an f-string)."""
        leaves: list[ast.expr] = []
        stack: list[ast.expr] = [e]
        while stack:
            x = stack.pop()
            if isinstance(x, ast.BinOp) and isinstance(x.op, ast.Add):
                if len(leaves) + len(stack) >= _DEPTH - d:
                    return False  # too long to follow: flag it, never recurse past the bound
                stack.append(x.right)
                stack.append(x.left)
            else:
                leaves.append(x)
        before = ""
        for leaf in leaves:
            kind = self._sens_kind(leaf, sc)
            if kind:
                if not self._position_ok(kind, before):
                    return False
                if not self.clean(leaf, sc, d, seen | {_CTX_OK}):
                    return False
            elif not self.clean(leaf, sc, d, seen):
                return False
            before += self._leaf_text(leaf, sc)
        return True

    def _percent_clean(self, e: ast.BinOp, sc: _Scope, d: int, seen: frozenset) -> bool:
        """``"<a href=%s>" % safe_url(x)``: the format string is a literal, its conversions pair with the
        arguments one to one, and a context-sensitive argument sits where its prefix allows."""
        args = list(e.right.elts) if isinstance(e.right, ast.Tuple) else [e.right]
        fmt = e.left
        prefixes: list[str] | None = None
        if isinstance(fmt, ast.Constant) and isinstance(fmt.value, str):
            specs = [m for m in _PERCENT_SPEC.finditer(fmt.value) if m.group(2) != "%"]
            if len(specs) == len(args):
                prefixes, before, last = [], "", 0
                for m, arg in zip(specs, args):
                    before += fmt.value[last : m.start()]
                    prefixes.append(before)
                    before += self._leaf_text(arg, sc)  # a literal argument is text too
                    last = m.end()
        if not self.clean(fmt, sc, d, seen):
            return False
        for i, arg in enumerate(args):
            kind = self._sens_kind(arg, sc)
            if kind:
                if prefixes is None or not self._position_ok(kind, prefixes[i]):
                    return False
                if not self.clean(arg, sc, d, seen | {_CTX_OK}):
                    return False
            elif not self.clean(arg, sc, d, seen):
                return False
        return True

    def _format_clean(
        self, call: ast.Call, recv: ast.expr, sc: _Scope, d: int, seen: frozenset
    ) -> bool:
        """``"<a href={}>".format(safe_url(x))``: as :meth:`_percent_clean`, for ``str.format``."""
        if not self.clean(recv, sc, d, seen):
            return False
        marks: dict[int, list[str]] = {}
        func = call.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "format"
            and isinstance(recv, ast.Constant)
            and isinstance(recv.value, str)
            # ``*args`` / ``**kw`` shift which value lands in which field: no position is known
            and not self._has_star(call)
        ):
            keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg}
            try:
                fields = list(string.Formatter().parse(recv.value))
            except ValueError:
                fields = []
            before, auto = "", 0
            for literal, field, _spec, _conv in fields:
                before += literal
                if field is None:
                    continue
                arg: ast.expr | None = None
                if field == "":
                    arg = call.args[auto] if auto < len(call.args) else None
                    auto += 1
                elif field.isdigit():
                    arg = call.args[int(field)] if int(field) < len(call.args) else None
                elif field.isidentifier():
                    arg = keywords.get(field)
                if arg is not None:
                    marks.setdefault(id(arg), []).append(before)
                before += self._leaf_text(arg, sc) if arg is not None else "\x00"
        for arg in [*call.args, *(kw.value for kw in call.keywords)]:
            kind = self._sens_kind(arg, sc)
            if kind:
                prefixes = marks.get(id(arg))
                if not prefixes or not all(self._position_ok(kind, p) for p in prefixes):
                    return False
                if not self.clean(arg, sc, d, seen | {_CTX_OK}):
                    return False
            elif not self.clean(arg, sc, d, seen):
                return False
        return True

    @staticmethod
    def _context(prefix: str) -> tuple[str, str]:
        """``(state, attribute)`` after ``prefix``: ``"text"``, ``"rawtext"`` (the body of a
        ``<script>`` / ``<style>`` element), ``"quoted"`` or ``"tag"``, and the name of the attribute a
        quoted value belongs to (``""`` elsewhere)."""
        state, quote, attr, tag, raw = "text", "", "", "", ""
        low = prefix.lower()
        start = 0  # where the open tag began
        i = 0
        while i < len(low):
            ch = low[i]
            if state == "rawtext":
                if low.startswith("</" + raw, i):
                    state = "text"
            elif state == "text":
                if ch == "<" and (i + 1 == len(low) or low[i + 1].isalpha() or low[i + 1] in "/!?"):
                    state, start = "tag", i
                    tag = re.match(r"<([a-z0-9-]*)", low[i:]).group(1)  # type: ignore[union-attr]
            elif state == "tag":
                if ch == ">":
                    state, raw = ("rawtext", tag) if tag in ("script", "style") else ("text", "")
                elif ch in "\"'":
                    found = re.search(r"([^\s=\"'<>/]+)\s*=\s*$", low[start:i])
                    state, quote, attr = "quoted", ch, found.group(1) if found else ""
            elif ch == quote:
                state, attr = "tag", ""
            i += 1
        return state, attr

    @staticmethod
    def _position(prefix: str) -> str:
        """Where the next field sits after ``prefix`` (the literal text before it, ``\x00`` for an
        earlier field): ``"text"`` (element text), ``"quoted"`` (inside a quoted attribute value) or
        ``"tag"`` (an unquoted attribute, or a tag name)."""
        state, quote = "text", ""
        for i, ch in enumerate(prefix):
            if state == "text":
                if ch == "<" and (
                    i + 1 == len(prefix)  # ``'<' + value``: the value is the tag name
                    or prefix[i + 1].isalpha()
                    or prefix[i + 1] in "/!?"
                ):
                    state = "tag"
            elif state == "tag":
                if ch == ">":
                    state = "text"
                elif ch in "\"'":
                    state, quote = "quoted", ch
            elif ch == quote:
                state = "tag"
        return state

    def _field_clean(
        self,
        field: ast.FormattedValue,
        sc: _Scope,
        d: int,
        seen: frozenset,
        joined: ast.JoinedStr | None = None,
        index: int = 0,
    ) -> bool:
        """One ``{value!c:spec}`` of an f-string: clean if the value is, or the spec prints digits.

        A value-escaper's output (``safe_url``, ``safe_css_*``) is clean in a quoted attribute or in
        text, not in an unquoted attribute (``<a href={safe_url(x)}>``): the literal text before the
        field decides.
        """
        spec = field.format_spec
        if (
            field.conversion == -1
            and isinstance(spec, ast.JoinedStr)
            and spec.values
            and all(isinstance(v, ast.Constant) and isinstance(v.value, str) for v in spec.values)
            and _NUMERIC_SPEC.fullmatch("".join(v.value for v in spec.values))  # type: ignore[attr-defined]
        ):
            return True
        kind = self._sens_kind(field.value, sc)
        if kind:
            if joined is None:
                return False
            prefix = self._leaf_text(ast.JoinedStr(values=list(joined.values[:index])), sc)
            if not self._position_ok(kind, prefix):
                return False
            return self.clean(field.value, sc, d, seen | {_CTX_OK})
        return self.clean(field.value, sc, d, seen)

    def _trusted_call(self, e: ast.Call, sc: _Scope) -> bool:
        """A call whose result is clean whatever its arguments (an escaper, a number, an engine)."""
        origin = self.origin(e.func, sc)
        if origin in QUOTE_ESCAPERS and not self._quotes_escaped(e, QUOTE_ESCAPERS[origin]):
            return False  # ``html.escape(x, quote=False)`` leaves ``"`` / ``'`` as they are (#3850)
        if origin in CLEAN_ORIGINS or origin in SAFE_ALPHABET_ORIGINS:
            return True
        if origin in CLEAN_PROJECT_ORIGINS:
            return True
        if (
            isinstance(e.func, ast.Attribute)
            and e.func.attr in DATE_DIGIT_METHODS
            and not e.args
            and not e.keywords  # ``d.isoformat(sep=raw)``: the separator is the caller's (#4068)
            and self._is_date(e.func.value, sc)
        ):
            return True  # ``current.isoformat()`` / ``start.weekday()`` of a date value
        if origin in CLEAN_RECORD_ORIGINS:
            return True
        name = _callee(e.func)
        if isinstance(e.func, ast.Name):
            # ``render_to_string(...)``: trusted unless this code defines something of that name
            return name in RENDER_CALLS and sc.find(name) is None  # type: ignore[arg-type]
        return (
            name in RENDER_CALLS
        )  # ``tmpl.render(...)``: by method name, see the module docstring

    def _is_date(
        self, e: object, sc: _Scope, depth: int = 0, seen: frozenset = frozenset()
    ) -> bool:
        """``e`` is a ``date`` / ``datetime`` value: a constructor of ``DATE_ORIGINS``, a date plus or
        minus a ``timedelta``, or a name that only ever holds one (``+= timedelta(...)`` included)."""
        if depth > 8 or not isinstance(e, ast.expr):
            return False
        d = depth + 1
        if isinstance(e, ast.Call):
            return self.origin(e.func, sc) in DATE_ORIGINS
        if isinstance(e, ast.BinOp) and isinstance(e.op, (ast.Add, ast.Sub)):
            return (self._is_date(e.left, sc, d, seen) and self._is_delta(e.right, sc)) or (
                isinstance(e.op, ast.Add)
                and self._is_delta(e.left, sc)
                and self._is_date(e.right, sc, d, seen)
            )
        if isinstance(e, ast.Name):
            owner = sc.find(e.id)
            if owner is None or owner.contents.get(e.id):
                return False
            values, defs = self._bindings(e, e.id, owner, sc)
            key = ("date", e.id, id(owner), defs)
            if key in seen:
                return True
            seen = seen | {key}
            return (
                bool(values)
                and all(self._is_date(v, owner, d, seen) for v in values)
                and all(self._is_delta(p, owner) for p in owner.pieces.get(e.id, []))
            )
        return False

    def _is_delta(self, e: object, sc: _Scope) -> bool:
        return isinstance(e, ast.Call) and self.origin(e.func, sc) in DELTA_ORIGINS

    def _calendar_digits(self, e: object, sc: _Scope) -> bool:
        """``cal.monthdayscalendar(...)`` of a ``calendar.Calendar``: (nested) ints only (#3847)."""
        if not (
            isinstance(e, ast.Call)
            and isinstance(e.func, ast.Attribute)
            and e.func.attr in CALENDAR_DIGIT_METHODS
        ):
            return False
        recv = e.func.value
        if isinstance(recv, ast.Call):
            return self.origin(recv.func, sc) in CALENDAR_CLASSES
        if isinstance(recv, ast.Name):
            owner = sc.find(recv.id)
            if owner is None or owner.contents.get(recv.id) or owner.pieces.get(recv.id):
                return False
            values, _ = self._bindings(recv, recv.id, owner, sc)
            return bool(values) and all(
                isinstance(v, ast.Call) and self.origin(v.func, owner) in CALENDAR_CLASSES
                for v in values
            )
        return False

    def _deep_digits(self, e: object, sc: _Scope, depth: int = 0) -> bool:
        """Every element of ``e``, at any depth, is an int: a calendar digit method's result, or an
        element of one (``for week in weeks: for day in week``)."""
        if depth > 8:
            return False
        if isinstance(e, _ElementOf):
            return self._deep_digits(e.iterable, sc, depth + 1)
        if self._calendar_digits(e, sc):
            return True
        if isinstance(e, ast.Name):
            owner = sc.find(e.id)
            if owner is None or owner.contents.get(e.id) or owner.pieces.get(e.id):
                return False
            values, _ = self._bindings(e, e.id, owner, sc)
            return bool(values) and all(
                self._deep_digits(v, owner, depth + 1)
                or (isinstance(v, ast.List) and not v.elts)  # ``weeks = []`` in an ``except``
                for v in values
            )
        return False

    @staticmethod
    def _quotes_escaped(call: ast.Call, position: int) -> bool:
        """The ``quote`` argument is absent or the literal ``True`` (and nothing hides it in ``*``)."""
        if any(isinstance(a, ast.Starred) for a in call.args) or any(
            kw.arg is None for kw in call.keywords
        ):
            return False
        values = [kw.value for kw in call.keywords if kw.arg == "quote"]
        if len(call.args) > position:
            values.append(call.args[position])
        return all(isinstance(v, ast.Constant) and v.value is True for v in values)

    def _call_clean(
        self, e: ast.Call, sc: _Scope, d: int, seen: frozenset, placed: bool = False
    ) -> bool:
        if self._trusted_call(e, sc):
            return True
        name = _callee(e.func)
        args = [*e.args, *(kw.value for kw in e.keywords)]
        origin = self.origin(e.func, sc)
        if origin in CLEAN_IF_ARGS_CLEAN:
            return all(self.clean(a, sc, d, seen) for a in args)
        if origin in JSON_ESCAPERS:
            return len(e.args) == 1 and self._json_dumped(e.args[0], sc, d, seen)
        local = self._local_function(e.func, sc)
        if local is not None:
            if self._unbound(local[1], lambda: self._returns_clean(local, d, seen)):
                return True  # clean whatever it is given
            if not placed:
                # clean for THESE arguments, so only where its position was checked (element text,
                # ``_sens_kind``), unless they are literals or digits
                return self._position_free(e, sc)
            # what it returns lands where the call does: in element text
            return bool(
                self._specialized(
                    local, e, sc, lambda: self._returns_clean(local, d, seen | {_CTX_OK})
                )
            )
        if origin in CLEAN_IF_DEFAULT_CLEAN:
            if self._has_star(e):
                return False  # a ``default`` hidden in ``*args`` / ``**kw`` is never examined
            default = self._default_arg(e, CLEAN_IF_DEFAULT_CLEAN[origin])
            return default is None or self.clean(default, sc, d, seen)
        if isinstance(e.func, ast.Attribute):
            recv = e.func.value
            if name == "join":
                return self.clean(recv, sc, d, seen) and all(
                    self.elements_clean(a, sc, d, seen) for a in args
                )
            if name in FORMAT_METHODS:
                return self._format_clean(e, recv, sc, d, seen)
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
    def _has_star(call: ast.Call) -> bool:
        return any(isinstance(a, ast.Starred) for a in call.args) or any(
            kw.arg is None for kw in call.keywords
        )

    def _json_dumped(self, e: ast.expr, sc: _Scope, d: int, seen: frozenset) -> bool:
        """``e`` is ``json.dumps(...)`` output, or a name that only holds some."""
        if d > _DEPTH:
            return False
        if isinstance(e, ast.Call):
            if self.origin(e.func, sc) in JSON_PRODUCERS:
                return True
            local = self._local_function(e.func, sc)  # ``self.to_json()``: a method that returns it
            if local is None:
                return False
            fn, fscope = local
            key = ("json-fn", id(fn))
            if key in seen:
                return True
            returns = _return_values(fn)
            return bool(returns) and self._unbound(
                fscope,
                lambda: all(
                    v is not None and self._json_dumped(v, fscope, d + 1, seen | {key})
                    for v in returns
                ),
            )
        if isinstance(e, ast.Name):
            owner = sc.find(e.id)
            if owner is None:
                return False
            values, defs = self._bindings(e, e.id, owner, sc)
            key = ("json", e.id, id(owner), defs, self._ctx(owner))
            if key in seen:
                return True
            seen = seen | {key}
            return (
                bool(values)
                and not owner.pieces.get(e.id)
                and all(
                    isinstance(v, ast.expr) and self._json_dumped(v, owner, d + 1, seen)
                    for v in values
                )
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
        """:meth:`_resolve_local`, memoised: it reads only what the file says, never a binding."""
        key = (id(func), id(sc))
        hit = self._locals.get(key)
        # the entry holds the scope itself, so a comprehension's short-lived scope cannot hand its id
        # to another one while the entry is alive
        if hit is None or hit[0] is not sc or hit[1] is not func:
            hit = self._locals[key] = (sc, func, self._resolve_local(func, sc))
        return hit[2]

    def _resolve_local(
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
            if func.id in self.rebound_names or self.dynamic_globals:
                # ``global helper; helper = ...`` somewhere, or ``globals()[...] = ...``
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
            if cls.node.decorator_list or self._class_dynamic(cls):  # type: ignore[attr-defined]
                return None
            if func.value.id != "self" and cls.node.keywords:  # type: ignore[attr-defined]
                return (
                    None  # a metaclass data descriptor of that name wins for ``cls.helper`` (#3864)
                )
            if func.attr in self.assigned_attrs or self._overridden_in_file(cls, func.attr):
                return None
            if self._falls_through(cls) and not self._defined_unconditionally(cls, func.attr):
                return None
            name, holder = func.attr, cls
        elif (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Call)
            and isinstance(func.value.func, ast.Name)
            and not self._mutated_or_rebound(func.attr)
        ):
            # ``Card().render(x)``: a method of a fresh instance of a class this file defines once
            # (#3864). Only a plain class: no base (``__new__`` of a base may return anything else),
            # no metaclass or decorator, no ``__new__`` / ``__getattribute__`` of its own.
            cname = func.value.func.id
            owner = sc.find(cname)
            if (
                owner is None
                or cname in owner.opaque
                or cname in self.rebound_names
                or self.dynamic_globals
                or len(owner.assigns.get(cname, [])) != 1
            ):
                return None
            cdefs = owner.def_nodes.get(cname, [])
            if len(cdefs) != 1 or not isinstance(cdefs[0], ast.ClassDef):
                return None
            cnode = cdefs[0]
            target = self.scope_of.get(id(cnode))
            if (
                target is None
                or cnode.bases
                or cnode.keywords
                or cnode.decorator_list
                or self._class_dynamic(target)
                or any(
                    n in target.assigns or n in target.def_nodes
                    for n in ("__new__", "__getattribute__")
                )
                or func.attr in self.assigned_attrs
                or not self._defined_unconditionally(target, func.attr)
            ):
                return None
            name, holder = func.attr, target
        else:
            return None
        defs = holder.def_nodes.get(name, [])
        bound = holder.assigns.get(name, [])
        if (
            not defs
            and len(bound) == 1
            and isinstance(bound[0], ast.Lambda)
            and name not in holder.opaque
            and not holder.contents.get(name)
            and not holder.pieces.get(name)
            and id(bound[0]) in self.scope_of
        ):
            # ``tag = lambda u: f"<a href={u}>"``: a function too, bound once (#3864)
            return bound[0], self.scope_of[id(bound[0])]  # type: ignore[return-value]
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
        key = ("returns", id(fn), self._ctx(fscope))
        if key in seen or d > _DEPTH:
            return key in seen  # a recursive call adds nothing new
        seen = seen | {key}
        return all(
            v is None or self.clean(v, fscope, d + 1, seen) for v in self._live_returns(fn, fscope)
        )

    def _specialized(self, local: tuple, call: ast.Call, sc: _Scope, judge):
        """``judge()`` with the function's parameters bound to what THIS call passes (#3845, #3864).

        A function of this file is judged with its parameters raw, so the verdict holds for any call.
        A call's RESULT is what this call makes of it: ``tag(safe_url(x))`` of ``def tag(u): return
        f"<a href={u}>"`` puts the escaper's output in an unquoted attribute, and ``bold(escape(x))`` of
        ``def bold(u): return f"<b>{u}</b>"`` is clean. Each parameter holds its argument (judged in the
        caller's scope) or its default; ``self`` / ``cls`` and ``*args`` / ``**kw`` stay raw. Returns
        ``None`` (nothing to bind) for a call that would raise, a ``*args`` / ``**kw`` call, or a
        function already being judged for an outer call (recursion)."""
        fn, fscope = local
        if id(fscope) in self._actuals or self._has_star(call):
            return None
        a = fn.args  # type: ignore[attr-defined]
        positional = [*a.posonlyargs, *a.args]
        implicit = self._implicit_arguments(fn, call, fscope)
        names = [p.arg for p in positional][implicit:]
        if len(call.args) > len(names) and a.vararg is None:
            return None
        binding: dict[str, object] = {}
        for param, value in zip(names, call.args):
            binding[param] = _Actual(value, sc)
        by_keyword = {p.arg for p in [*a.args, *a.kwonlyargs]} - set(
            p.arg for p in positional[:implicit]
        )
        for kw in call.keywords:
            if kw.arg in by_keyword and kw.arg not in binding:
                binding[kw.arg] = _Actual(kw.value, sc)
            elif kw.arg in by_keyword or a.kwarg is None:
                return None  # given twice, or an unknown keyword: the call raises
        holder = fscope.parent or fscope  # where the ``def`` (and its defaults) is evaluated
        all_names = [p.arg for p in positional]
        defaults = dict(zip(all_names[len(all_names) - len(a.defaults) :], a.defaults))
        for p, default in zip(a.kwonlyargs, a.kw_defaults):
            if default is not None:
                defaults[p.arg] = default
        for param in [*names, *(p.arg for p in a.kwonlyargs)]:
            if param not in binding:
                if param not in defaults:
                    return None  # a required argument is missing: the call raises
                binding[param] = _Actual(defaults[param], holder)
        binding = {k: v for k, v in binding.items() if isinstance(fscope.params.get(k), _Param)}
        outer = self._actuals.get(id(fscope))
        self._actuals[id(fscope)] = binding
        try:
            return judge()
        finally:
            if outer is None:
                self._actuals.pop(id(fscope), None)
            else:
                self._actuals[id(fscope)] = outer

    def _position_free(self, e: ast.expr, sc: _Scope, depth: int = 0) -> bool:
        """A value that may sit in ANY position, as it may when written in place: a literal the
        developer wrote, digits (``int`` / ``len`` / ``str`` of them, a fixed alphabet), or a call of
        a function of this file given only such values (what it returns is then the developer's
        markup and values its own body made clean)."""
        if depth > 6:
            return False
        if isinstance(e, ast.Constant):
            return True
        if isinstance(e, ast.JoinedStr):  # an f-string of literal text and such values
            return all(
                self._position_free(v.value, sc, depth + 1)
                for v in e.values
                if isinstance(v, ast.FormattedValue)
            )
        if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Add):
            return self._position_free(e.left, sc, depth + 1) and self._position_free(
                e.right, sc, depth + 1
            )
        if isinstance(e, ast.Name):  # a name that only ever holds such values
            owner = sc.find(e.id)
            if owner is None or owner.pieces.get(e.id) or owner.contents.get(e.id):
                return False
            values, _ = self._bindings(e, e.id, owner, sc)
            found = [x for v in values for x in _foreign_values(v, owner)]
            return (
                bool(values)
                and len(found) == len(values)
                and all(self._position_free(x, xsc, depth + 1) for x, xsc in found)
            )
        if not isinstance(e, ast.Call):
            return False
        args = [*e.args, *(k.value for k in e.keywords)]
        if isinstance(e.func, ast.Name) and sc.find(e.func.id) is None:
            if e.func.id in ("int", "float", "len", "bool"):
                return True
            if e.func.id == "str":
                return all(self._position_free(a, sc, depth + 1) for a in args)
        if self.origin(e.func, sc) in SAFE_ALPHABET_ORIGINS:
            return True
        local = self._local_function(e.func, sc)
        if local is None or self._has_star(e):
            return False
        if not all(self._position_free(a, sc, depth + 1) for a in args):
            return False
        fn, fscope = local
        # every value it can return, for THESE arguments, is itself free: being clean is not enough
        # (``def w(k): return f'<i title="{escape(S)}" data-k="{k}">'`` is clean for ``w(1)``, and its
        # own quote ends an attribute it is placed in)

        def judge() -> bool:
            returns = self._live_returns(fn, fscope)
            return bool(returns) and all(
                v is not None and self._position_free(v, fscope, depth + 1) for v in returns
            )

        verdict = self._specialized(local, e, sc, judge)
        return bool(verdict) if verdict is not None else False

    def _needs_arguments(self, e: ast.Call, sc: _Scope) -> bool:
        """``e`` calls a function of this file whose result is NOT clean for any argument: whether it
        is clean depends on what this call passes (its result is then clean in element text only)."""
        local = self._local_function(e.func, sc)
        if local is None:
            return False
        key = id(local[0])
        if key in self._raw_clean:
            return not self._raw_clean[key]
        if key in self._raw_clean_busy:
            return True  # a recursive helper: the stricter answer
        self._raw_clean_busy.add(key)
        try:
            clean = self._unbound(local[1], lambda: self._returns_clean(local, 0, frozenset()))
        finally:
            self._raw_clean_busy.discard(key)
        if not self._actuals:  # what it returns for ANY argument may still read an outer binding
            self._raw_clean[key] = clean
        return not clean

    def _raw_all(self, owner: _Scope, name: str, judge):
        """``judge()`` with every call-site binding off, unless ``name`` is a container its function
        makes afresh on every call. What ``+=`` / ``append`` / ``d[k] = v`` put into a container is
        flow-insensitive: a module list, a mutable default, a parameter or a closure's list keeps what
        EVERY call put there, so it is never judged as what one call passes."""
        if not self._actuals or self._fresh(owner, name):
            return judge()
        saved = self._actuals
        self._actuals = {}
        try:
            return judge()
        finally:
            self._actuals = saved

    def _fresh(self, owner: _Scope, name: str) -> bool:
        """``name`` is a local of a function, never a parameter, and every binding of it is a new
        display or a literal: each call starts its own (``parts = []``, ``s = ""``)."""
        if not isinstance(owner.node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return False
        if name in owner.params or name in owner.opaque or name in self.rebound_names:
            return False
        values = owner.assigns.get(name, [])
        return bool(values) and all(
            isinstance(v, (ast.List, ast.Dict, ast.Set, ast.Constant, ast.ListComp, ast.DictComp))
            for v in values
        )

    def _unbound(self, fscope: _Scope, judge):
        """``judge()`` with ``fscope``'s parameters raw: a call's result judged for ANY arguments must
        not borrow the binding of an outer call of the same function (a recursive call)."""
        saved = self._actuals.pop(id(fscope), None)
        try:
            return judge()
        finally:
            if saved is not None:
                self._actuals[id(fscope)] = saved

    @staticmethod
    def _implicit_arguments(fn: ast.AST, call: ast.Call, fscope: _Scope) -> int:
        """How many leading parameters the call fills by itself: ``self`` of ``self.m(..)``, ``cls`` of
        a ``classmethod``; none for a plain function, a ``staticmethod`` or ``C.m(obj, ..)``."""
        if not isinstance(call.func, ast.Attribute):
            return 0
        decorators = {d.id for d in getattr(fn, "decorator_list", []) if isinstance(d, ast.Name)}
        if "staticmethod" in decorators:
            return 0
        if "classmethod" in decorators:
            return 1
        receiver = call.func.value
        if isinstance(receiver, ast.Call):
            return 1  # ``C().m(..)``: the fresh instance is ``self``
        return 1 if isinstance(receiver, ast.Name) and receiver.id == "self" else 0

    def _returns_tainted(
        self, local: tuple, call: ast.Call, sc: _Scope, d: int, seen: frozenset
    ) -> bool:
        """What THIS call returns is a string built from a raw value (#3864: ``mark_safe(tag(x))``)."""
        fn, fscope = local

        def judge() -> bool:
            key = ("returns-tainted", id(fn), self._ctx(fscope))
            if key in seen:
                return False  # a recursive call adds nothing new
            inner = seen | {key}
            return any(
                v is not None and self.tainted(v, fscope, d + 1, inner)
                for v in self._live_returns(fn, fscope)
            )

        verdict = self._specialized(local, call, sc, judge)
        return self._unbound(fscope, judge) if verdict is None else verdict

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
        self, node: ast.Name | None, name: str, owner: _Scope, sc: _Scope | None = None
    ) -> tuple[list[object], tuple[int, ...] | None]:
        """The values ``name`` can hold at this use, and a key that tells those apart.

        With a ``node`` the flow analysis answered for (:class:`_Flow`) it is only the bindings that
        REACH that use; otherwise (a closure, a lambda, the module body, a name the analysis could not
        vouch for) it is every binding the name has anywhere in its scope, as before.
        """
        if node is not None:
            hit = self.reach.get(id(node))
            if hit is not None and hit[0] is owner and hit[1]:
                live = [x for x in hit[1] if self._feasible(owner, x.site)]
                return self._unvouched(
                    name, self._actual(name, owner, [x.value for x in live]), owner, sc
                ), tuple(sorted(id(x) for x in live))
        values = self._actual(name, owner, list(owner.assigns.get(name, [])))
        if name in owner.opaque:
            values.append(RAW)
        return self._unvouched(name, values, owner, sc), None

    def _actual(self, name: str, owner: _Scope, values: list[object]) -> list[object]:
        """``values`` with a parameter's entry binding replaced by the argument of the call whose result
        is being judged, when there is one (:meth:`_specialized`)."""
        bound = self._actuals.get(id(owner))
        if bound is None or name not in bound:
            return values
        entry = owner.params.get(name)
        if not isinstance(entry, _Param):
            return values
        return [bound[name] if v is entry else v for v in values]

    def _feasible(self, owner: _Scope, site: int | None) -> bool:
        """Whether the statement ``site`` can run under the call-site binding in force: not when an
        ``if`` around it tests a parameter that THIS call binds to a constant the test rules out
        (``if event_attrs is not None:`` of a call that leaves ``event_attrs=None``). Only a parameter
        nothing rebinds counts, and only ``P is None``, ``P is not None``, ``P`` and ``not P``."""
        bound = self._actuals.get(id(owner))
        if not bound or site is None:
            return True
        for param, kind, holds in self._branch_facts(owner).get(site, ()):
            value = bound.get(param)
            if not (isinstance(value, _Foreign) and isinstance(value.expr, ast.Constant)):
                continue
            c = value.expr.value
            truth = {
                "none": c is None,
                "notnone": c is not None,
                "truthy": bool(c),
                "falsy": not c,
            }[kind]
            if truth is not holds:
                return False
        return True

    def _branch_facts(self, fscope: _Scope) -> dict[int, tuple[tuple[str, str, bool], ...]]:
        """id(statement or expression) -> the parameter tests of the ``if`` statements around it, in
        ``fscope``'s own body: ``(param, kind, holds)``; ``holds`` is ``False`` in an ``else``."""
        cached = self._facts.get(id(fscope))
        if cached is not None:
            return cached
        out: dict[int, tuple[tuple[str, str, bool], ...]] = {}
        fn = fscope.node
        stable = {
            p
            for p, entry in fscope.params.items()
            if isinstance(entry, _Param)
            and len(fscope.assigns.get(p, [])) == 1
            and p not in fscope.opaque
            and not fscope.pieces.get(p)
            and not fscope.contents.get(p)
            and p not in self.rebound_names
        }

        def fact(test: ast.expr) -> tuple[str, str] | None:
            if isinstance(test, ast.Name) and test.id in stable:
                return test.id, "truthy"
            if (
                isinstance(test, ast.UnaryOp)
                and isinstance(test.op, ast.Not)
                and isinstance(test.operand, ast.Name)
                and test.operand.id in stable
            ):
                return test.operand.id, "falsy"
            if (
                isinstance(test, ast.Compare)
                and len(test.ops) == 1
                and isinstance(test.left, ast.Name)
                and test.left.id in stable
                and isinstance(test.comparators[0], ast.Constant)
                and test.comparators[0].value is None
                and isinstance(test.ops[0], (ast.Is, ast.IsNot))
            ):
                return test.left.id, "none" if isinstance(test.ops[0], ast.Is) else "notnone"
            return None

        def mark(node: ast.AST, facts: tuple) -> None:
            out[id(node)] = facts
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                return
            if isinstance(node, ast.If):
                found = fact(node.test)
                mark(node.test, facts)
                for child in node.body:
                    mark(child, facts + ((*found, True),) if found else facts)
                for child in node.orelse:
                    mark(child, facts + ((*found, False),) if found else facts)
                return
            for child in ast.iter_child_nodes(node):
                mark(child, facts)

        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for stmt in fn.body:
                mark(stmt, ())
        self._facts[id(fscope)] = out
        return out

    def _live_returns(self, fn: ast.AST, fscope: _Scope) -> list[ast.expr | None]:
        """What the function can return under the call-site binding in force (:meth:`_feasible`)."""
        if isinstance(fn, ast.Lambda):
            return [fn.body]
        return [
            n.value
            for n in _own_nodes(fn)
            if isinstance(n, ast.Return) and self._feasible(fscope, id(n))
        ]

    def _ctx(self, owner: _Scope) -> int:
        """Tells apart two judgements of one name made under different call-site bindings: a cycle
        key must not let one stand in for the other."""
        return id(self._actuals.get(id(owner)))

    def _unvouched(
        self, name: str, values: list[object], owner: _Scope, sc: _Scope | None
    ) -> list[object]:
        """``values`` plus a RAW binding when something the flow cannot see can bind the name: a
        ``global`` / ``nonlocal`` write from another scope, a list or dict that is aliased or handed on
        (so another name can fill it), or, for a use from another scope than the owner's (a module
        constant, a closure variable), any in-place change anywhere in the file."""
        if name in self.rebound_names:
            return [*values, RAW]
        if self._in(self.escaped, self.escaped_in, name, owner) and any(
            self._mutable_display(v) for v in values
        ):
            return [*values, RAW]
        if sc is not None and self._foreign(owner, sc):
            if not self._stable(name, values, owner) or (
                self.dynamic_globals and isinstance(owner.node, ast.Module)
            ):
                return [*values, RAW]
        return values

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
            values, defs = self._bindings(e, e.id, owner, sc)
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
        values, defs = self._bindings(node, name, owner, sc)
        key = ("clean", name, id(owner), defs, self._ctx(owner))
        if key in seen:
            return True  # a cycle adds nothing new
        seen = seen | {key}
        if not values and not owner.pieces.get(name) and not owner.contents.get(name):
            return False
        parts = seen - {_CTX_OK}  # what ``+=`` / ``append`` added is a part, not the whole
        return all(self.clean(v, owner, d, seen) for v in values) and self._raw_all(
            owner,
            name,
            lambda: (
                all(self.clean(p, owner, d, parts) for p in owner.pieces.get(name, []))
                and all(self.clean(c, owner, d, parts) for c in owner.contents.get(name, []))
            ),
        )

    # elements_clean: every element / value pulled out of this container is clean
    def elements_clean(self, e: object, sc: _Scope, depth: int, seen: frozenset) -> bool:
        if isinstance(e, _Foreign):
            return self.elements_clean(e.expr, e.scope, depth + 1, seen)
        if isinstance(e, _AnyOf):
            return bool(e.values) and all(
                self.elements_clean(v, sc, depth + 1, seen) for v in e.values
            )
        if depth > _DEPTH or e is RAW or isinstance(e, _ElementOf) or not isinstance(e, ast.expr):
            return False
        d = depth + 1
        if isinstance(e, ast.Constant):
            return True
        if isinstance(e, (ast.List, ast.Tuple, ast.Set)):
            return all(self.clean(x, sc, d, seen) for x in e.elts)
        if isinstance(e, ast.Dict):
            # iterating a dict yields its KEYS (``for k in d``, ``sorted(d)``, ``d.items()``): an
            # element pulled out of it is a key or a value, so both must be clean (#3850)
            return all(self.clean(v, sc, d, seen) for v in e.values if v is not None) and all(
                self.clean(k, sc, d, seen) for k in e.keys if k is not None
            )
        if isinstance(e, (ast.ListComp, ast.GeneratorExp, ast.SetComp, ast.DictComp)):
            inner = self._comprehension_scope(e, sc)
            if isinstance(e, ast.DictComp):
                return self.clean(e.value, inner, d, seen) and self.clean(e.key, inner, d, seen)
            return self.clean(e.elt, inner, d, seen)
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
            values, defs = self._bindings(e, e.id, owner, sc)
            key = ("elements", e.id, id(owner), defs, self._ctx(owner))
            if key in seen:
                return True
            seen = seen | {key}
            if not values and not owner.contents.get(e.id):
                return False
            return all(self.elements_clean(v, owner, d, seen) for v in values) and self._raw_all(
                owner,
                e.id,
                lambda: (
                    all(self.clean(c, owner, d, seen) for c in owner.contents.get(e.id, []))
                    and all(self.clean(k, owner, d, seen) for k in owner.keys.get(e.id, []))
                    # ``items += more``: the elements of ``more`` join the container
                    and all(
                        self.elements_clean(p, owner, d, seen) for p in owner.pieces.get(e.id, [])
                    )
                ),
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
        if isinstance(e, _Foreign):
            return self.tainted(e.expr, e.scope, depth + 1, seen)
        if isinstance(e, _AnyOf):
            return any(self.tainted(v, sc, depth + 1, seen) for v in e.values)
        if not isinstance(e, ast.expr):
            return False
        if depth > _DEPTH:
            return True  # beyond what it can follow, flag: a long chain must not hide a sink
        d = depth + 1
        if isinstance(e, ast.JoinedStr):
            return any(
                not self._field_clean(v, sc, d, seen, e, i)
                for i, v in enumerate(e.values)
                if isinstance(v, ast.FormattedValue)
            )
        if isinstance(e, (ast.NamedExpr, ast.Await)):  # ``await coro(f"..")``: what it returns
            return self.tainted(e.value, sc, d, seen)
        if isinstance(e, ast.Starred):  # ``mark_safe(*[f".."])``: each value it spreads (#3850)
            return self.tainted(e.value, sc, d, seen)
        if isinstance(e, (ast.ListComp, ast.GeneratorExp, ast.SetComp, ast.DictComp)):
            inner = self._comprehension_scope(e, sc)
            elt = e.value if isinstance(e, ast.DictComp) else e.elt
            return self.tainted(elt, inner, d, seen)
        if isinstance(e, ast.BinOp):
            if isinstance(e.op, (ast.Add, ast.Mod)):
                return not self.clean(e, sc, d, seen)
            if isinstance(e.op, ast.Mult):
                # ``"<b>" * n`` builds a string out of ``n``; ``raw * 3`` is still a bare passthrough
                text = any(
                    isinstance(x, ast.JoinedStr)
                    or (isinstance(x, ast.Constant) and isinstance(x.value, str))
                    for x in (e.left, e.right)
                )
                # ``("<li>%s</li>" % raw) * n``: a string built from raw, repeated (#3850)
                return (text and not self.clean(e, sc, d, seen)) or any(
                    self.tainted(x, sc, d, seen) for x in (e.left, e.right)
                )
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
                if self._has_star(e):
                    return True
                default = self._default_arg(e, CLEAN_IF_DEFAULT_CLEAN[origin])
                return default is not None and self.tainted(default, sc, d, seen)
            if origin in JSON_ESCAPERS:
                return not (len(e.args) == 1 and self._json_dumped(e.args[0], sc, d, seen))
            if origin in ("re.sub", "re.subn"):
                return not all(
                    self.clean(a, sc, d, seen)
                    for a in [*e.args[1:], *(k.value for k in e.keywords)]
                )
            local = self._local_function(e.func, sc)
            if local is not None and self._returns_tainted(local, e, sc, d, seen):
                return True  # a function of this file that builds the string (#3864)
            if isinstance(e.func, ast.Attribute):
                recv = e.func.value
                if name in DUNDER_BUILDERS:
                    return not (
                        self.clean(recv, sc, d, seen)
                        and all(self.clean(a, sc, d, seen) for a in e.args)
                    )
                if name in ("sub", "subn") and len(e.args) >= 2:
                    return not all(self.clean(a, sc, d, seen) for a in e.args[:2])
                if name == "translate" and e.args:
                    # ``"<b>x</b>".translate({ord("x"): raw})``: the table's VALUES go in (#3850)
                    table = e.args[0]
                    if isinstance(table, ast.Dict):
                        return not all(
                            self.clean(v, sc, d, seen) for v in table.values if v is not None
                        ) or any(v is None for v in table.values)
                    return not self.elements_clean(table, sc, d, seen)
                if name in STR_METHODS and name not in PURE_TRANSFORMS:
                    # ``TEMPLATE.replace("@@", raw)`` puts the argument into the string
                    if not all(self.clean(a, sc, d, seen) for a in e.args):
                        return True
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
        values, defs = self._bindings(node, name, owner, sc)
        key = ("tainted", name, id(owner), defs, self._ctx(owner))
        if key in seen:
            return False
        seen = seen | {key}
        return any(self.tainted(v, owner, d, seen) for v in values) or self._raw_all(
            owner,
            name,
            lambda: (
                any(not self.clean(p, owner, d, seen) for p in owner.pieces.get(name, []))
                or any(self.tainted(c, owner, d, seen) for c in owner.contents.get(name, []))
            ),
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
    # a narrowing FACT about an expression (``("str", x)``, #3846) holds after the join only if it
    # holds, unchanged, on every path that reaches it
    for key in [k for k in out if isinstance(k, tuple)]:
        if not all(state.get(key) == out[key] for state in live):
            del out[key]
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
        #: id(``str(x)`` call) -> ``x``, for each call evaluated where ``str(x) in CONST`` held
        #: on every path (#3846)
        self.narrowed_calls: dict[int, set[str]] = {}
        #: whether a ``str(x)`` call was evaluated, somewhere, where the fact did NOT hold
        self.unnarrowed_calls: set[int] = set()
        #: id(``str(x)`` call) -> the narrowing tests (their marker defs) it relied on
        self.call_markers: dict[int, set[_Def]] = {}
        #: marker defs of narrowing tests whose ``x`` was USED, while the fact held, other than as
        #: ``str(x)`` (``x.append(raw)``, ``x.name = raw``, ``f(x)``): ``str(x)`` may differ now
        self.spoiled: set[_Def] = set()
        #: names a nested function or lambda reads: it may change ``x`` behind the flow's back
        self.closure_names: set[str] = {
            n.id
            for sub in ast.walk(self.fn)
            if sub is not self.fn
            and isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef))
            for n in ast.walk(sub)
            if isinstance(n, ast.Name)
        }

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
        for call_id, names in self.narrowed_calls.items():
            if (
                call_id not in self.unnarrowed_calls
                and not names & bad
                and not self.call_markers.get(call_id, set()) & self.spoiled
            ):
                self.mod.narrowed_exprs.add(call_id)

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
            site = key[0] if isinstance(key[0], int) else None
            found = self.defs[key] = _Def(name, value, site)
            if counted:
                self.created.setdefault(name, []).append(found)
        return found

    def _note(self, state: State, name: str, d: _Def) -> None:
        state[name] = frozenset({d})
        state.pop(("str", name), None)  # a rebound ``x`` ends what ``str(x) in T`` said
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

    def _str_of_name(self, e: ast.AST) -> ast.Name | None:
        """``x`` of ``str(x)`` (the builtin, one positional argument that is a name), else ``None``."""
        if (
            isinstance(e, ast.Call)
            and isinstance(e.func, ast.Name)
            and self.mod.origin(e.func, self.scope) == "str"
            and len(e.args) == 1
            and not e.keywords
            and isinstance(e.args[0], ast.Name)
        ):
            return e.args[0]
        return None

    def loads(self, e: ast.AST | None, state: State | None) -> None:
        if e is None or state is None:
            return
        arg = self._str_of_name(e)
        if arg is not None:
            fact = state.get(("str", arg.id))
            markers = {d for d in fact or () if d.name is None}
            if fact is not None and (fact - markers) == state.get(arg.id):
                self.narrowed_calls.setdefault(id(e), set()).add(arg.id)
                self.call_markers.setdefault(id(e), set()).update(markers)
            else:
                self.unnarrowed_calls.add(id(e))
            self._load(arg, state)  # ``str(x)`` reads ``x`` without spoiling the fact
            self.loads(e.func, state)  # type: ignore[attr-defined]
            return
        if isinstance(e, ast.Name):
            if isinstance(e.ctx, ast.Load):
                self._load(e, state)
                fact = state.get(("str", e.id))
                if fact:  # any other use of ``x`` while the fact holds may change ``str(x)``
                    self.spoiled.update(d for d in fact if d.name is None)
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
            arg = self._str_of_name(left)
            if (
                isinstance(op, (ast.In, ast.NotIn))
                and arg is not None
                and arg.id in state
                and arg.id not in self.disabled
                and arg.id not in self.closure_names
                and self.mod.constant_set(right, self.scope)
            ):
                # ``str(x) in TABLE``: the EXPRESSION ``str(x)`` is a value the developer wrote on
                # this branch, as long as ``x`` keeps the bindings it has now (#3846). ``x`` itself
                # is not narrowed: it may be an object whose ``str()`` is in the table.
                held = dict(state)
                # the fact is x's bindings now plus a marker naming THIS test (``name=None``), so a
                # later use of ``x`` that may change ``str(x)`` can withdraw it (review of #4068)
                marker = self._def(("str-fact", id(test)), None, None, counted=False)  # type: ignore[arg-type]
                held[("str", arg.id)] = state[arg.id] | {marker}
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
        star = type(s) is not ast.Try  # ``except*``: every matching handler runs, in order
        entry: State | None = handler_in
        for handler in s.handlers:  # type: ignore[attr-defined]
            self.loads(handler.type, entry)
            out = self.block(handler.body, self.bind(handler, entry))  # type: ignore[arg-type]
            exits.append(out)
            if star:
                entry = _merge(entry, out)
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


def _transparent(e: ast.AST, names: set[str]) -> bool:
    """``e`` is, or is derived by an operation that keeps the text (``str()``, ``.strip()``, ``cast``,
    ``args[0]``, ``*args``, a conditional), from one of ``names``."""
    if isinstance(e, ast.Name):
        return e.id in names
    if isinstance(e, (ast.Starred, ast.Subscript, ast.Await, ast.NamedExpr)):
        return _transparent(e.value, names)
    if isinstance(e, ast.IfExp):
        return _transparent(e.body, names) or _transparent(e.orelse, names)
    if isinstance(e, ast.BoolOp):
        return any(_transparent(v, names) for v in e.values)
    if isinstance(e, ast.Call):
        func = e.func
        if isinstance(func, ast.Name) and func.id in ("str", "repr") and e.args:
            return _transparent(e.args[0], names)
        if _callee(func) == "cast" and len(e.args) == 2:
            return _transparent(e.args[1], names)
        if isinstance(func, ast.Attribute) and func.attr in STR_METHODS:
            return _transparent(func.value, names)
    return False


def wrapper_names(
    tree: ast.AST, sinks: set[str] | frozenset[str], top_level_only: bool = True
) -> set[str]:
    """Functions (and lambdas, and ``partial`` aliases) that mark a PARAMETER safe: the value a sink
    receives derives from a parameter by a text-preserving operation (#3848).

    ``def _ms(v): x = v.strip(); y = x; return mark_safe(y)``, ``_ms = lambda v: mark_safe(str(v))``,
    ``def _ms(*a): return mark_safe(a[0])``, ``_ms = partial(mark_safe)`` are all ``mark_safe``. A
    function that BUILDS the string it marks (``mark_safe(f"<b>{v}</b>")``) is not a wrapper: the
    guard judges what it builds. Methods named like template renderers and dunders are skipped.
    With ``top_level_only`` only module-level definitions count (what the guard can match by name).
    """
    known = set(sinks)
    found: set[str] = set()
    parents = {id(c): n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            name: str | None = None
            args: ast.arguments | None = None
            body: ast.AST | None = None
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name, args, body = node.name, node.args, node
            elif (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                target = node.targets[0].id
                value = node.value
                if isinstance(value, ast.Lambda):
                    name, args, body = target, value.args, value.body
                elif (
                    isinstance(value, ast.Call)
                    and _callee(value.func) == "partial"
                    and value.args
                    and _callee(value.args[0]) in known
                    and target not in known
                ):
                    found.add(target)
                    known.add(target)
                    changed = True
            if name is None or args is None or body is None:
                continue
            if (
                name in known
                or name in RENDER_CALLS
                or (name.startswith("__") and name.endswith("__"))
            ):
                continue
            names = {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]}
            if args.vararg:
                names.add(args.vararg.arg)
            if args.kwarg:
                names.add(args.kwarg.arg)
            grew = True
            while grew:
                grew = False
                for sub in ast.walk(body):
                    pairs: list[tuple[ast.AST, ast.AST]] = []
                    if isinstance(sub, ast.Assign):
                        pairs = [(t, sub.value) for t in sub.targets]
                    elif isinstance(sub, (ast.AnnAssign, ast.NamedExpr)) and sub.value is not None:
                        pairs = [(sub.target, sub.value)]
                    for target_node, value_node in pairs:
                        if isinstance(target_node, ast.Name) and target_node.id not in names:
                            if _transparent(value_node, names):
                                names.add(target_node.id)
                                grew = True

            def marks(value: ast.AST | None) -> bool:
                while (  # ``cast(str, mark_safe(v))`` and ``str(mark_safe(v))`` still mark ``v``
                    isinstance(value, ast.Call)
                    and (
                        (_callee(value.func) == "cast" and len(value.args) == 2)
                        or (
                            isinstance(value.func, ast.Name)
                            and value.func.id == "str"
                            and value.args
                        )
                    )
                ):
                    value = value.args[-1]
                return (
                    isinstance(value, ast.Call)
                    and _callee(value.func) in known
                    and any(
                        _transparent(a, names)
                        for a in [*value.args, *(k.value for k in value.keywords)]
                    )
                )

            if isinstance(body, ast.expr):  # a lambda: its one expression
                results: list[ast.AST | None] = [body]
            else:  # every value the function returns (not those of functions nested in it)
                results = [r.value for r in _own_nodes(body) if isinstance(r, ast.Return)]
            hit = bool(results) and all(marks(r) for r in results)
            if hit:
                outer = parents.get(id(node))
                nested = False
                while outer is not None:
                    if isinstance(
                        outer, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
                    ):
                        nested = True
                        break
                    outer = parents.get(id(outer))
                if top_level_only and nested:
                    continue
                found.add(name)
                known.add(name)
                changed = True
    return found


#: Classes OUTSIDE the scanned sources that write attributes under names the source does not show:
#: Django's ``View.__init__`` does ``setattr(self, key, value)`` for every ``as_view()`` keyword, so
#: a class attribute of a class-based view is a default the URLconf can replace; a ``Form``'s
#: metaclass moves declared fields off the class (#3864). Matched by base-class NAME.
EXTERNAL_DYNAMIC_BASES = frozenset(
    {
        "View",
        "TemplateView",
        "RedirectView",
        "FormView",
        "CreateView",
        "UpdateView",
        "DeleteView",
        "ListView",
        "DetailView",
        "ArchiveIndexView",
        "YearArchiveView",
        "MonthArchiveView",
        "WeekArchiveView",
        "DayArchiveView",
        "TodayArchiveView",
        "DateDetailView",
        "BaseForm",
        "Form",
        "ModelForm",
        "BaseModelForm",
    }
)


def dynamic_class_names(sources: Iterable[str]) -> frozenset[str]:
    """Names of the classes, across ``sources``, that write attributes under a name the source does
    not show (``setattr(self, key, value)``, ``self.__dict__.update``, ``object.__setattr__``,
    ``__setattr__`` / ``__getattribute__``), and of every class that inherits from one of them.
    ``Component`` does this for every constructor and ``update()`` keyword, so a class attribute of a
    ``Component`` subclass is a default the caller replaces, not a constant (#3848)."""
    classes: dict[str, set[str]] = {}
    hazardous: set[str] = set(EXTERNAL_DYNAMIC_BASES)
    for source in sources:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            classes.setdefault(cls.name, set()).update(
                b.id if isinstance(b, ast.Name) else b.attr
                for b in cls.bases
                if isinstance(b, (ast.Name, ast.Attribute))
            )
            for node in ast.walk(cls):
                if (
                    (isinstance(node, ast.Attribute) and node.attr in DYNAMIC_ATTR_NAMES)
                    or (
                        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and node.name in DYNAMIC_ATTR_NAMES
                    )
                    or (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id in DYNAMIC_ATTR_CALLS
                    )
                ):
                    hazardous.add(cls.name)
                    break
    grew = True
    while grew:
        grew = False
        for name, bases in classes.items():
            if name not in hazardous and bases & hazardous:
                hazardous.add(name)
                grew = True
    return frozenset(hazardous)


def find_unsafe_markup(
    source: str,
    module: str | None = None,
    is_package: bool = False,
    dynamic_bases: frozenset[str] = frozenset(),
) -> list[Finding]:
    """Every sink in ``source`` that is handed a string built from a raw value.

    ``module`` is the dotted name the source is importable as (``djust.components.utils``). With it a
    relative import resolves to its origin (so ``from ..utils import safe_url`` is the project's
    escaper) and a ``def`` in the file has an origin of its own; without it neither does, which can
    only flag more, never less. ``dynamic_bases`` is :func:`dynamic_class_names` over the package.
    """
    return _Module(source, module, is_package, dynamic_bases).findings()


def _main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv]
    files = [f for p in paths for f in (sorted(p.rglob("*.py")) if p.is_dir() else [p])]
    # the classes that write attributes from keywords, over every file given (#3864): without it a
    # class attribute of a ``Component`` / ``View`` subclass is trusted as a constant
    dynamic = dynamic_class_names(f.read_text() for f in files)
    total = 0
    for path in files:
        for finding in find_unsafe_markup(path.read_text(), dynamic_bases=dynamic):
            print(f"{path}:{finding}")
            total += 1
    print(f"{total} finding(s) in {len(files)} file(s)", file=sys.stderr)
    return 1 if total else 0


if __name__ == "__main__":  # pragma: no cover - a CLI over the same function
    raise SystemExit(_main(sys.argv[1:]))
