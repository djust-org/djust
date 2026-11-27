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
* A SINK HANDED A CALL (#3864, #3845): ``mark_safe(helper(...))`` of a function of this file (also a
  lambda bound to a name once, and a method of a fresh instance of a plain class, ``Card().tag(x)``)
  is also judged by what that function returns with its parameters bound to THIS call's arguments,
  each judged as if written in place: ``mark_safe(tag(safe_url(x)))`` of ``def tag(u): return f"<a
  href={u}>"`` and ``mark_safe(helper(x))`` of a helper that interpolates its argument are flagged.
  It only ever ADDS a finding, by construction: ``find_unsafe_markup`` runs TWO analyzers that share
  no state, pass A the guard as it was (no call is ever bound: its binding table raises on a write)
  and pass B with this judgement, and reports the union of their findings (it costs one more
  analysis per file). Containers that outlive the call are judged
  with every binding off; a parameter the call binds to a literal decides ``if p`` / ``if p is None``
  when the function never rebinds it; a recursive call, ``*args`` / ``**kw`` and a call that does not
  fit the signature are judged with the parameters raw; past the depth bound the sink is flagged;
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
* Anything that crosses a module or a file's own helper that is not clean: a string built in one
  function and marked safe in another (``build_theme_head_context`` builds ``css_block``;
  ``ThemeMixin`` marks it safe; the inline theme switcher builds ``preset_options`` into a returned
  f-string that ``LazyThemeHTML`` marks safe, so the switcher is protected by its own tests, not by
  this guard), a value stored on
  ``self``. A helper of the SAME file that returns an interpolated string and is passed straight to
  the sink is seen since #3864 (above); one whose result is placed inside a larger string is judged
  as before, by whether it is clean for any argument.
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
  placed. The exception (pass A) is a BALANCED fragment: EVERY part the guard cannot read sits
  inside a quote the builder itself opens (``' style="' + safe_css_length(x)``; a second output after
  the quote closed, ``' title="' + safe_url(x) + '" z=' + safe_url(x)``, does not qualify since
  #3864) and the builder's whole literal text leaves no quote open; pass A places such a fragment
  unchecked, pass B where it lands (below). A bare output anywhere nobody checked (``join`` of bare
  outputs, an argument) is not clean. AN ESCAPER THAT ONLY HTML-ESCAPES (``escape`` /
  ``conditional_escape`` / ``html.escape`` / ``attr_text``) is clean as text and in an ordinary
  quoted value (``conditional_escape`` as text only: it leaves a marked-safe value as it is, so in
  ANY quoted value, and in a comment, it is flagged, #4009) and flagged where that is not enough: at
  the START of a URL attribute's value (``href="{escape(x)}"``: ``javascript:`` has nothing to
  escape; ``data-href`` counts, the client follows it; an escaper around a URL escaper's output does
  not), in an ``on*`` handler, a ``<script>`` or ``<style>`` body, ``srcdoc``, an unquoted value,
  between attributes and as an attribute or tag name (a space in the value starts an attribute). A
  URL escaper's output is flagged in the handler, script, style and ``srcdoc`` positions too. A
  ``<script>`` whose literal ``type`` is a JSON data block (``application/json`` / ``ld+json``) is not
  a script body (``_html_context`` reads the positions).
* THE POSITION WALK (pass B, #3864): every part of a string a sink is handed is placed by text the
  guard READ, or reported. ``_Module._misplaces`` reads the whole string (``_text_b``): literal text
  wherever it is spelled out (every spelling of a name, a branch, a constant table or ``dict.get``,
  a tuple, a narrowed name, ``str`` / ``strip`` / ``replace`` / a slice of a literal, a class
  attribute, a ``%`` / ``.format`` / f-string field, a ``join`` of a display, a function or lambda of
  this file with ITS call's arguments bound; a call read inside such a call is read with its
  parameters unknown, so a helper is read once per call site, not once per path) and, for a part
  it cannot spell out, a SYMBOL of what that part can do there (``SYM_*``): an escaped value keeps the position (text, a quoted value or a
  comment), digits and words fit almost anywhere, markup (``format_html``, ``render()``, a rendered
  block body, a string that starts and ends in element text) fits element text only, whole
  attributes fit between attributes, and a part it cannot class (``SYM_UNKNOWN``) may sit in element
  text only, where an arbitrary escaped string would be safe; everywhere else it is reported, and
  every positioned part after it is reported too, since its effect on the position is not known. A
  transformation of an escaper's output keeps the class only where it provably cannot add ``"'<>&``
  (padding and alignment with a plain fill, case changes, ``strip``, a slice: a character
  reference it cuts decodes to a character of the value, never to markup); ``!r`` / ``%r`` are
  spelled with their quotes; ``replace`` or a fill that brings in such a character, and ``%c`` /
  ``{:c}``, are read as a part whose escaping is undone (``SYM_RAW``), placed nowhere. ``.format`` / ``format_map`` / ``%`` with a
  mapping / ``string.Template`` are read field by field, a format spec included; a builder whose
  positions the guard cannot read (a format string built at run time) is unknown, and refused where
  any argument needs a position. ``format_html`` / ``format_html_join``: each ARGUMENT is placed where
  it lands in the format string, escaped (markup only when the guard knows it is). ``_walk`` then
  reads the text the way an HTML tokenizer does: tags, attribute names, quoted and unquoted values,
  comments (abrupt ``<!-->`` / ``<!--->`` included), character references a part could complete
  (``&#10`` + a digit), ``RCDATA`` (``textarea`` / ``title``) and ``RAWTEXT`` bodies (``script``,
  ``style``, ``xmp``, ``iframe``, ``noembed``, ``noframes``, ``noscript``) that end only at
  ``</name`` followed by a delimiter, foreign content (``svg`` / ``math``: no raw-text body there),
  JSON data blocks, a literal ``javascript:`` / ``vbscript:`` / ``data:`` at a URL's start (read after
  decoding character references); CDATA, the script ``<!--`` escape states and ``plaintext`` are not
  modelled, so nothing positioned may follow them. It refuses a part placed where its class may not
  sit, and the finding says which part, where, and the text in front of it. A value with up to
  ``_SPELLINGS`` spellings is read once per spelling, left to right, prefixes that leave the same
  state merged (at most ``_PREFIXES``); straight-line ``s += piece`` is read in source order, a piece
  under a branch as optional. Only pass B walks, so it only adds findings. Before it, an earlier part
  the guard could not read counted as an ordinary character (``pre() + safe_url(x)`` of a ``pre``
  that returns ``'<a href='`` from one of two branches was cleared), a balanced fragment was placed
  unchecked inside a quoted or unquoted value, and a helper's markup placed in a larger string was
  not looked into.
* A function of this file REPLACED without a ``def`` (#3864): ``sys.modules[__name__].helper = ...``,
  ``setattr`` on a module object (``sys.modules[...]``, ``importlib.import_module(...)``, a name that
  holds one), its ``__dict__``, or ``helper.__code__ = ...``: pass B does not follow a call of it (its
  result is an unknown call's). Replacing it from ANOTHER file is not seen: like a subclass there,
  that is the developer's own code.
* A ``style`` ATTRIBUTE is CSS (#4212): an HTML escaper's output there is reported (``;`` and
  ``url(...)`` pass HTML escaping); a value escaper's (``safe_css_*``, and ``safe_url``, which the
  guard reads as the same class) is not.
* What the walk does not model: attributes a browser does not treat as URLs (``data-url``: the
  client code that reads one decides); the result of an unknown call (``functools.partial(...)(x)``:
  ``partial(mark_safe, ...)`` is a sink already, a partial of anything else is an unknown call);
  and a developer's own ``__format__`` or ``int``
  subclass behind a numeric format spec or an ``isinstance`` check (like a subclass in another file,
  that is the developer's own code, not request data). A metaclass data descriptor of a class attribute's name is not missed: ``cls.X`` /
  ``C.X`` of a class with a metaclass (or any class keyword) is not trusted (``self.X`` is, since an
  instance lookup never reaches the metaclass).
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
import functools
import re
import string
import sys
from html import unescape as _unescape
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
#: ``style_attrs`` returns `` style="<declarations>"`` or `` data-dj-style`` plus
#: `` data-dj-style-<name>="<value>"`` pairs: each name is a key of the fixed ``STYLE_PROPERTIES``
#: table (any other raises), each value passed its property's check, and every attribute value
#: (the ``css`` template included) is ``django.utils.html.escape``d (#3979).
CLEAN_PROJECT_ORIGINS = frozenset(
    {
        "djust.components.utils.interpolate_color",
        "djust.components.utils.style_attrs",
    }
)
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
        # A whole ``data-component-id="..."`` attribute, its value through attr_text, or "" (#4134).
        "djust.components.utils.component_id_attr",
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
#: Escapers that only HTML-escape (``&``, ``<``, ``>`` and the quotes): right in element text and in an
#: ordinary quoted value, not enough where the text is read as something else afterwards (#3864).
HTML_ONLY_ORIGINS = frozenset(
    {
        "django.utils.html.escape",
        "django.utils.html.conditional_escape",
        "html.escape",
        "djust.components.utils.attr_text",
    }
)
#: HTML-only escapers that leave a marked-safe value (``mark_safe``, ``__html__``) as it is: right in
#: element text (the marker means "this is markup"), wrong in any attribute value, where the value
#: can close the attribute (#4009). ``attr_text`` / ``escape`` always escape.
MARK_HONOURING_ORIGINS = frozenset({"django.utils.html.conditional_escape"})
#: CSS value escapers: their output is what a ``style`` value takes, and no URL (#4212 item 7).
CSS_ESCAPER_ORIGINS = frozenset(
    {
        "djust.components.utils.safe_css_color",
        "djust.components.utils.safe_css_length",
        "djust.components.utils.safe_css_ratio",
        "djust.components.utils.safe_css_number",
    }
)
#: Escapers that also refuse a URL that is not a navigation: an HTML escaper wrapped around one of
#: these is still a checked URL (``conditional_escape(safe_url(x))``).
URL_ESCAPER_ORIGINS = frozenset(
    {
        "djust.components.utils.safe_url",
        "djust.components.utils.url_attr",
        "djust.components.utils.data_url_attr",
        "djust.components.utils.srcset_attr",
    }
)
#: Attributes whose value is a URL the browser may run or load (``javascript:`` in ``href``).
URL_ATTRIBUTES = frozenset(
    {
        "href",
        "src",
        "srcset",
        "action",
        "formaction",
        "xlink:href",
        "data",
        "poster",
        "background",
        "cite",
        "codebase",
        "longdesc",
        "manifest",
        "ping",
        "icon",
        "profile",
        "usemap",
        "classid",
        "archive",
    }
)
#: Elements whose body is not HTML text: entities are not decoded there, the text is code.
RAW_TEXT_ELEMENTS = frozenset({"script", "style"})
#: ``<script type=...>`` values that make the element a JSON data block: the browser never runs it,
#: and an HTML escaper leaves no ``<`` to close it with (the reader decodes the entities itself).
DATA_BLOCK_TYPES = frozenset({"application/json", "application/ld+json"})
#: A URL attribute's value up to the field is "the start of the URL" when it is only this: a browser
#: drops leading spaces and control characters before it reads the scheme.
_URL_LEAD = re.compile(r"[\x01-\x20]*")

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
#: Attributes of a function that replace what calling it runs or returns (#3864).
_CODE_ATTRS = frozenset({"__code__", "__defaults__", "__kwdefaults__", "__wrapped__", "__call__"})
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

# -- the text a part contributes, as pass B reads it (#3864) ---------------------------------------
#
# Pass B reads the text in front of a part as a string of literal characters and these symbols, one
# per part it cannot spell out. Each says what the part can do to the HTML position it sits in, so a
# position is either established from text the guard read or reported as unknown, never assumed:
#
#: digits or a fixed alphabet (``int(x)``, ``uuid4().hex``, a date's ``isoformat()``): no markup, no
#: space, no quote: it changes nothing, wherever it sits
SYM_DIGITS = "\x04"
#: literal text the developer wrote (one of several spellings) with no ``<``, ``>`` or quote, that
#: leaves a tag between attributes (`` hidden``, ``btn btn-primary``): anywhere but a tag's name or
#: an unquoted value
SYM_WORDS = "\x07"
#: a URL checker's output (``safe_url``, ``url_attr``): no ``<`` or quote, but spaces: text, a
#: quoted value (the start of a URL included) or a comment, not a tag (it would add attributes), an
#: unquoted value or a ``style`` value (``)`` and ``;`` pass a URL check and end a CSS ``url(...)``
#: or declaration, #4212 item 7)
SYM_VALUE = "\x00"
#: a CSS escaper's output (``safe_css_*``): as ``SYM_VALUE``, but it fits a ``style`` value and not
#: the start of a URL (it is no checked URL)
SYM_CSS = "\x0e"
#: an HTML-only escaper's output (``escape``, ``attr_text``): as ``SYM_VALUE``, and not where HTML
#: escaping is not enough (the start of a URL, a handler, a script or style body, ``srcdoc``)
SYM_ESCAPED = "\x05"
#: JSON escaped for a script (``escape_json_for_script``): element text or a script body
SYM_JSON = "\x06"
#: markup that starts and ends in element text (``format_html``, ``conditional_escape``, a template's
#: ``render()``, ``rich_html``, a string of this file whose every spelling does): element text only
SYM_MARKUP = "\x02"
#: whole attributes (a record builder's, ``component_id_attr``): between attributes, or in text
SYM_ATTRS = "\x03"
#: a part whose text the guard cannot read and cannot class: nothing after it has a known position
SYM_UNKNOWN = "\x01"
#: a value whose escaping was undone (``escape(x).replace("&quot;", '"')``, ``%c`` of a number,
#: ``!r``): it is placed nowhere
SYM_RAW = "\x08"
_SYMBOLS = frozenset(
    {
        SYM_DIGITS,
        SYM_WORDS,
        SYM_VALUE,
        SYM_CSS,
        SYM_ESCAPED,
        SYM_JSON,
        SYM_MARKUP,
        SYM_ATTRS,
        SYM_UNKNOWN,
        SYM_RAW,
    }
)
#: symbols that need a known position
_POSITIONED = frozenset(
    {SYM_WORDS, SYM_VALUE, SYM_CSS, SYM_ESCAPED, SYM_JSON, SYM_MARKUP, SYM_ATTRS}
)
#: a text :func:`_walk` always refuses
SYM_REFUSED = SYM_RAW
#: a value with a few known spellings is kept as all of them: ``\x10a\x11b\x12`` is ``a`` or ``b``,
#: and :func:`_walk` reads each (up to ``_SPELLINGS``) rather than the class they share
CHOICE_OPEN, CHOICE_SEP, CHOICE_CLOSE = "\x10", "\x11", "\x12"
_CONTROL = _SYMBOLS | {CHOICE_OPEN, CHOICE_SEP, CHOICE_CLOSE}
#: the classes whose every spelling starts and ends in element text (what a recursive call is
#: first read as, ``SYM_MARKUP``, promises)
_FITS_MARKUP = frozenset(
    {SYM_DIGITS, SYM_WORDS, SYM_VALUE, SYM_CSS, SYM_ESCAPED, SYM_MARKUP, SYM_ATTRS}
)
#: how deep ``_text_b`` follows a value (names, calls, containers) before it reads "unknown"
_TEXT_DEPTH = 30
_SPELLINGS = 32
#: how many distinct texts in front ``_walk`` reads a string with before it reads choices as classes
_PREFIXES = 256
#: the order the symbols join in: a set of spellings takes the weakest class every member fits
_SPECIAL_TEXT = frozenset("<>\"'`")
_NUMERIC_TEXT = frozenset("0123456789.-+e" + SYM_DIGITS)
_WS = " \t\n\r\f"


def _mapped(text: str) -> str:
    """``text`` as :meth:`_Module._html_context` reads it. A symbol that may stand for the empty
    string must not hide the start of a URL value: a value, escaped text, JSON and markup become
    nothing, words and whole attributes a space (they stay apart from a neighbour); digits are a
    number, never empty."""
    return (
        text.replace(SYM_DIGITS, "0")
        .replace(SYM_VALUE, "")
        .replace(SYM_CSS, "")
        .replace(SYM_ESCAPED, "")
        .replace(SYM_JSON, "")
        .replace(SYM_MARKUP, "")
        .replace(SYM_WORDS, " ")
        .replace(SYM_ATTRS, " ")
    )


@functools.lru_cache(maxsize=1 << 16)
def _spelling_count(text: str) -> int:
    """How many spellings ``text`` has (its choices taken each way), capped past ``_SPELLINGS``."""
    if CHOICE_OPEN not in text:
        return 1
    # per open choice: the count of the run before it, and the counts of its finished spellings
    levels: list[tuple[int, list[int]]] = []
    cur = 1
    for ch in text:
        if ch == CHOICE_OPEN:
            levels.append((cur, []))
            cur = 1
        elif ch == CHOICE_SEP:
            levels[-1][1].append(cur)
            cur = 1
        elif ch == CHOICE_CLOSE:
            outer, alts = levels.pop()
            alts.append(cur)
            cur = min(outer * sum(alts), _SPELLINGS + 1)
    return min(cur, _SPELLINGS + 1)


def _expand(text: str, limit: int = _SPELLINGS) -> list[str] | None:
    """Every spelling of ``text`` (its choices taken each way), or ``None`` past ``limit``."""
    start = text.find(CHOICE_OPEN)
    if start < 0:
        return [text]
    depth, i, parts, last = 0, start, [], start + 1
    while i < len(text):
        ch = text[i]
        if ch == CHOICE_OPEN:
            depth += 1
        elif ch == CHOICE_CLOSE:
            depth -= 1
            if depth == 0:
                parts.append(text[last:i])
                break
        elif ch == CHOICE_SEP and depth == 1:
            parts.append(text[last:i])
            last = i + 1
        i += 1
    else:
        return None  # not closed: never made by ``_summarize``
    head, tail = text[:start], text[i + 1 :]
    out: list[str] = []
    for part in parts:
        for spelled in _expand(part + tail, limit) or [None]:  # type: ignore[list-item]
            if spelled is None:
                return None
            out.append(head + spelled)
            if len(out) > limit:
                return None
    return out


@functools.lru_cache(maxsize=1 << 16)
def _walk(text: str) -> tuple[bool, str]:
    """Read ``text`` from element text (see :func:`_walk_one`); a text with choices is read once per
    spelling: every spelling must place its parts, and the state is theirs when they agree.

    The spellings are read left to right, a choice at a time; a prefix that ends in element text
    leaves nothing behind that changes what follows, so it is read as empty and equal prefixes
    merge. Past ``_SPELLINGS`` distinct prefixes a choice is read as the class its spellings
    share."""
    if CHOICE_OPEN not in text:
        return _walk_one(text)
    prefixes = {""}
    for segment in _segments(text):
        grown = {p + s for p in prefixes for s in segment}
        prefixes = set()
        for p in grown:
            ok, state, tail = _walk_tail(p)
            if not ok:
                return False, "unknown"
            # what follows depends on the text since the tokenizer was last in element text
            prefixes.add("" if state == "text" else p[tail:])
        if len(prefixes) > _PREFIXES:
            return _walk_one(_collapse(text))
    states = {_walk_one(p)[1] for p in prefixes}
    return True, (states.pop() if len(states) == 1 else "unknown")


_SYMBOL_NAMES = {
    SYM_DIGITS: "digits",
    SYM_WORDS: "literal words",
    SYM_VALUE: "a URL checker's output",
    SYM_CSS: "a CSS escaper's output",
    SYM_ESCAPED: "an HTML-escaped value",
    SYM_JSON: "script-escaped JSON",
    SYM_MARKUP: "markup",
    SYM_ATTRS: "whole attributes",
    SYM_UNKNOWN: "a value the guard cannot read",
    SYM_RAW: "a value whose escaping is undone (or is not text)",
}


def _spell(text: str) -> str:
    return "".join(f"{{{_SYMBOL_NAMES[c]}}}" if c in _SYMBOL_NAMES else c for c in text)


def refusal(text: str) -> str:
    """Why :func:`_walk` refuses ``text``: the part, where it lands, and the text in front."""
    spelling = None
    if CHOICE_OPEN not in text:
        spelling = text
    else:
        prefixes = {""}
        for segment in _segments(text):
            grown = {p + x for p in prefixes for x in segment}
            bad = [p for p in grown if not _walk_one(p)[0]]
            if bad:
                spelling = min(bad, key=len)
                break
            prefixes = {"" if _walk_one(p)[1] == "text" else p for p in grown}
    if spelling is None:
        return "a string the guard cannot read in full"
    for i in range(len(spelling)):
        if spelling[i] in _SYMBOLS and not _walk_one(spelling[: i + 1])[0]:
            front = spelling[:i]
            ok, state = _walk_one(front)
            where = (
                _Module._html_context(_mapped(front), css=True)  # noqa: SLF001
                if ok and state != "unknown"
                else None
            )
            if not ok or state == "unknown":
                place = "after a part whose effect on the position is not known"
            elif where == "url":
                place = "at the start of a URL attribute's value"
            elif where == "handler":
                place = "in an event-handler attribute"
            elif where in ("script", "style"):
                place = f"in a <{where}> body"
            elif where == "srcdoc":
                place = "in srcdoc"
            elif where == "css":
                place = "in a style attribute"
            elif where == "unquoted" or state == "unquoted":
                place = "in an unquoted attribute value"
            elif state == "tag":
                place = "inside a tag (a name or between attributes)"
            elif state == "quoted":
                place = "inside a quoted attribute value"
            elif state == "comment":
                place = "in a comment"
            elif state == "raw":
                place = "in a raw-text element's body"
            else:
                place = "in element text"
            return f"{_SYMBOL_NAMES[spelling[i]]} {place}, after {_spell(front[-50:])!r}"
    return "a string the guard cannot read in full"


def _segments(text: str) -> list[list[str]]:
    """``text`` as a sequence of its literal runs (one spelling each) and its outermost choices
    (each spelling of each choice, its own choices expanded; past the bound, their class)."""
    out: list[list[str]] = []
    i, n = 0, len(text)
    while i < n:
        j = text.find(CHOICE_OPEN, i)
        if j < 0:
            out.append([text[i:]])
            break
        if j > i:
            out.append([text[i:j]])
        depth, k, parts, last = 0, j, [], j + 1
        while k < n:
            ch = text[k]
            if ch == CHOICE_OPEN:
                depth += 1
            elif ch == CHOICE_CLOSE:
                depth -= 1
                if depth == 0:
                    parts.append(text[last:k])
                    break
            elif ch == CHOICE_SEP and depth == 1:
                parts.append(text[last:k])
                last = k + 1
            k += 1
        spelled: list[str] = []
        for part in parts:
            spelled.extend(_expand(part) or [_collapse(part)])
        out.append(sorted(set(spelled)))
        i = k + 1
    return out


def _collapse(text: str) -> str:
    """``text`` with each outermost choice replaced by the class its spellings share."""
    out, i = [], 0
    while i < len(text):
        if text[i] != CHOICE_OPEN:
            out.append(text[i])
            i += 1
            continue
        depth, j = 0, i
        while j < len(text):
            if text[j] == CHOICE_OPEN:
                depth += 1
            elif text[j] == CHOICE_CLOSE:
                depth -= 1
                if depth == 0:
                    break
            j += 1
        inner, parts, depth, last = text[i + 1 : j], [], 0, 0
        for k, ch in enumerate(inner):
            if ch == CHOICE_OPEN:
                depth += 1
            elif ch == CHOICE_CLOSE:
                depth -= 1
            elif ch == CHOICE_SEP and depth == 0:
                parts.append(inner[last:k])
                last = k + 1
        parts.append(inner[last:])
        out.append(_join({_text_class(p) for p in parts}))
        i = j + 1
    return "".join(out)


#: elements whose body the tokenizer reads as text up to their own end tag: RCDATA (entities are
#: decoded) and RAWTEXT (they are not). ``script`` and ``style`` bodies are code (``_html_context``).
_RCDATA = frozenset({"textarea", "title"})
_RAWTEXT = frozenset({"script", "style", "xmp", "iframe", "noembed", "noframes", "noscript"})
#: a character reference the next part could complete (``&#10`` + ``6;`` is ``j``)
_OPEN_CHARREF = re.compile(r"&[#0-9A-Za-z]*$")


def _ends_raw(text: str, i: int, name: str) -> int:
    """The length of ``</name`` at ``i`` when it ends element ``name`` (followed by white space,
    ``/`` or ``>``), ``-1`` when the text ends there (the next part decides), else 0."""
    end = i + 2 + len(name)
    if text[i : i + 2] != "</" or text[i + 2 : end].lower() != name:
        return 0
    if end >= len(text):
        return -1
    nxt = text[end]
    if nxt in _WS + "/>":
        return end - i
    return -1 if nxt in _SYMBOLS else 0


@functools.lru_cache(maxsize=1 << 16)
def _walk_one(text: str) -> tuple[bool, str]:
    ok, state, _tail = _walk_tail(text)
    return ok, state


@functools.lru_cache(maxsize=1 << 16)
def _walk_tail(text: str) -> tuple[bool, str, int]:
    """Read ``text`` (literal characters and the symbols above, no choice) from element text the
    way an HTML tokenizer does: whether every symbol sits where its part may sit, and the state it
    ends in: ``"text"``, ``"tag"`` (a tag's name, between attributes, an attribute name or after
    ``=``), ``"unquoted"`` (an unquoted value), ``"quoted"``, ``"comment"``, ``"raw"`` (the body of
    a ``RAWTEXT`` / ``RCDATA`` element) or ``"unknown"``. A part it cannot read
    (``SYM_UNKNOWN``) may sit in element text only; whatever is positioned after it is refused.
    Pass B only."""
    state, quote, raw = "text", "", ""
    foreign = 0  # inside ``<svg>`` / ``<math>``: no element there has a raw-text body
    closing, raw_closed = False, ""
    ts = ""  # in a tag: "name", "between", "attr", "after" (after a name), "value" (after ``=``)
    loose = False  # a word between attributes: the names that follow are not known
    ok = True
    ctx = 0  # where the tokenizer was last in element text: ``_html_context`` reads from there
    i, n = 0, len(text)

    def where_at(k: int) -> str | None:
        return _Module._html_context(_mapped(text[ctx:k]), css=True)  # noqa: SLF001

    while i < n:
        ch = text[i]
        if ch in _SYMBOLS:
            if ch == SYM_DIGITS:
                if state == "tag" and ts == "value":
                    state = "unquoted"
                i += 1
                continue
            if ch == SYM_RAW or (ch == SYM_UNKNOWN and state != "text"):
                return False, "unknown", 0
            if ch == SYM_UNKNOWN:  # in element text (``raw`` is empty there): hides what follows
                state = "unknown"
                i += 1
                continue
            if state == "unknown":
                return False, "unknown", 0
            if state in ("quoted", "unquoted") and _OPEN_CHARREF.search(text[ctx:i]):
                return False, "unknown", 0  # the part may complete ``&#10`` into another character
            if ch == SYM_WORDS and state == "tag" and ts in ("between", "after"):
                loose = True  # (digits cannot spell a name, and an empty one is mapped away)
            if loose and state in ("tag", "quoted", "unquoted") and ch != SYM_WORDS:
                return False, "unknown", 0
            where = None if state == "comment" else where_at(i)
            body = raw if state == "raw" else ""
            plain_body = body and body not in ("script", "style")  # escaped text cannot end it
            if ch == SYM_WORDS:
                allowed = (
                    state in ("text", "quoted", "comment")
                    or (state == "raw" and (plain_body or where is None))
                    or (state == "tag" and ts in ("between", "after") and where is None)
                )
            elif ch == SYM_VALUE:
                # ``raw`` with ``where`` None is a JSON data block's body: never run
                # (not a ``style`` value: a checked URL may hold ``)`` / ``;``, #4212 item 7)
                allowed = (state in ("text", "quoted", "comment") and where in (None, "url")) or (
                    state == "raw" and (plain_body or where is None)
                )
            elif ch == SYM_CSS:
                # a CSS escaper's output (``safe_css_*``) is what a ``style`` value takes
                allowed = (state in ("text", "quoted", "comment") and where in (None, "css")) or (
                    state == "raw" and (plain_body or where is None)
                )
            elif ch == SYM_ESCAPED:
                allowed = state in ("text", "quoted", "comment") and where is None
                allowed = allowed or (state == "raw" and (plain_body or where is None))
            elif ch == SYM_JSON:
                allowed = state == "text" or (state == "raw" and where in (None, "script"))
            elif ch == SYM_MARKUP:
                # element text, or a JSON data block's body (never run; ``where`` is None there)
                # (and an RCDATA body, ``<textarea>``: a marked-safe value is the developer's
                # markup there as in element text)
                allowed = (state == "text" and where is None) or (
                    state == "raw" and (body == "script" or body in _RCDATA) and where is None
                )
            else:  # SYM_ATTRS (whole attributes start with a space: after a tag's name too)
                allowed = (state == "text" and where is None) or (
                    state == "tag" and ts in ("name", "between", "attr", "after")
                )
            if not allowed:
                return False, "unknown", 0
            if state == "unquoted" or (state == "tag" and ts == "value"):
                state = "unquoted"
            i += 1
            continue
        if state == "text":
            if text.startswith("<!--", i):
                if text.startswith("<!-->", i) or text.startswith("<!--->", i):
                    i += 5 if text[i + 4] == ">" else 6  # an abrupt comment: back in text
                    ctx = i
                    continue
                state = "comment"
                i += 4
                continue
            if text.startswith("<![CDATA[", i):
                state = "unknown"  # foreign content: not modelled, nothing placed after it
                i += 9
                continue
            if ch == "<":
                nxt = text[i + 1] if i + 1 < n else ""
                if nxt == "/" and (i + 2 >= n or not text[i + 2].isalpha()):
                    if i + 2 < n and text[i + 2] in _SYMBOLS:
                        return False, "unknown", 0
                    state, ts = "bogus", ""  # ``</`` + not a letter: a bogus comment to ``>``
                    i += 2
                    continue
                if nxt in "!?":
                    state, ts = "bogus", ""
                    i += 2
                    continue
                if nxt == "" or nxt.isalpha() or nxt == "/" or nxt == SYM_DIGITS:
                    closing = nxt == "/"
                    j = i + (2 if closing else 1)
                    k = j
                    while k < n and text[k] not in _WS + "/>" and text[k] not in _SYMBOLS:
                        k += 1
                    name = text[j:k].lower()
                    state, ts = "tag", "name"
                    raw = "" if closing else name
                    raw_closed = name
                    i = k
                    continue
                if nxt in _SYMBOLS:
                    # ``'<' + escape(x)``: the value is the tag's name
                    if nxt in _POSITIONED - {SYM_WORDS} or nxt in (SYM_RAW, SYM_UNKNOWN):
                        return False, "unknown", 0
                    state = "unknown"
                    i += 2
                    continue
        elif state == "tag":
            if ch == ">":
                self_closing = text[i - 1 : i] == "/"
                if raw in ("svg", "math") and not self_closing:
                    foreign += 1
                elif closing and raw_closed in ("svg", "math") and foreign:
                    foreign -= 1
                if foreign and raw not in ("svg", "math"):
                    state, raw = "text", ""  # foreign content: a ``<title>`` there is markup
                elif raw in _RAWTEXT or raw in _RCDATA:
                    state = "raw"
                elif raw == "plaintext":
                    state = "unknown"
                else:
                    state, raw = "text", ""
                    ctx = i + 1
                ts, loose = "", False
            elif ts == "value":
                if ch in "\"'":
                    state, quote = "quoted", ch
                elif ch not in _WS:
                    state = "unquoted"
            elif ch == "=" and ts in ("attr", "after"):
                ts = "value"
            elif ch in _WS:
                if ts in ("name", "attr"):
                    ts = "after"
                loose = False  # the next name is written out
            elif ch == "/":
                ts, loose = "between", False
            elif ts in ("name",):
                pass
            else:
                ts = "attr"
        elif state == "unquoted":
            if ch in _WS:
                state, ts, loose = "tag", "between", False
            elif ch == ">":
                state, ts = "tag", ""
                continue  # the ``>`` closes the tag
        elif state == "quoted":
            if ch == quote:
                state, ts = "tag", "between"
        elif state == "comment":
            if text.startswith("-->", i):
                state, ctx = "text", i + 3
                i += 3
                continue
            if text.startswith("--!>", i):
                state, ctx = "text", i + 4
                i += 4
                continue
        elif state == "bogus":
            if ch == ">":
                state, ctx = "text", i + 1
        elif state == "raw":
            if raw == "script" and text.startswith("<!--", i):
                state = "unknown"  # the script data escape states: not modelled
                i += 4
                continue
            m = _ends_raw(text, i, raw) if text.startswith("</", i) else 0
            if m < 0:
                state = "unknown"
                i += 2
                continue
            if m:
                state, ts, raw = "tag", "name", ""
                i += m
                continue
        i += 1
    if state == "bogus":
        state = "comment"
    return ok, state, ctx


def _cut_class(cls: str) -> str:
    """What is left of a part of class ``cls`` once characters are cut out of it (a slice, a
    ``strip`` / ``replace`` whose arguments are not read): no more than was there, so the same
    class, except that a checked URL (``SYM_VALUE``) is only escaped text after a cut: cutting
    ``/`` off ``safe_url('/javascript:x')`` uncovers the scheme it judged as a path (#4212)."""
    if cls == SYM_VALUE:
        return SYM_ESCAPED
    return cls if cls in (SYM_CSS, SYM_ESCAPED, SYM_WORDS) else SYM_UNKNOWN


def _text_class(text: str) -> str:
    """The weakest symbol a string fits: its effect on the position, wherever it is read."""
    # a text with choices: what any spelling holds (``flat``) and how every spelling walks
    flat = text.replace(CHOICE_OPEN, "").replace(CHOICE_SEP, "").replace(CHOICE_CLOSE, "")
    if SYM_RAW in flat:
        return SYM_RAW
    if SYM_UNKNOWN in flat:
        return SYM_UNKNOWN
    special = any(ch in _SPECIAL_TEXT for ch in flat)
    syms = {ch for ch in flat if ch in _SYMBOLS}
    if (
        CHOICE_OPEN in text
        and set(flat) <= _NUMERIC_TEXT
        and all(t for t in (_expand(text) or [""]))
    ):
        return SYM_DIGITS
    if not text:
        return SYM_WORDS  # nothing: a word that may be empty
    if CHOICE_OPEN not in text and set(text) <= _NUMERIC_TEXT:
        return SYM_DIGITS  # a number: it cannot spell a name
    # ``SYM_WORDS`` after it: it must leave a tag between attributes (not in an unquoted value)
    if not special and not (syms - {SYM_DIGITS, SYM_WORDS}):
        # words: no character HTML reads; with an ``=`` it must not leave a value open
        if "=" not in flat or _walk("<x " + text + " " + SYM_WORDS) == (True, "tag"):
            return SYM_WORDS
    if not special and syms <= {SYM_DIGITS, SYM_VALUE}:
        return SYM_VALUE
    if not special and syms <= {SYM_DIGITS, SYM_CSS}:
        return SYM_CSS
    if not special and syms <= {SYM_DIGITS, SYM_VALUE, SYM_CSS, SYM_ESCAPED}:
        return SYM_ESCAPED
    if not special and syms <= {SYM_DIGITS, SYM_JSON}:
        return SYM_JSON
    if _walk(text) != (True, "text"):
        return SYM_UNKNOWN
    # whole attributes fit between attributes AND element text; markup fits element text only
    if _walk("<x " + text + " " + SYM_WORDS) == (True, "tag"):
        return SYM_ATTRS
    return SYM_MARKUP


def _join(classes: set[str]) -> str:
    """The class of a value that is any of ``classes`` (each a symbol)."""
    classes = set(classes) - {SYM_DIGITS} or {SYM_DIGITS}
    if len(classes) > 1:
        classes -= {SYM_WORDS}  # every other class admits fewer positions
    if SYM_RAW in classes:
        return SYM_RAW
    if len(classes) == 1:
        return next(iter(classes))
    if SYM_UNKNOWN in classes:
        return SYM_UNKNOWN
    if classes <= {SYM_VALUE, SYM_CSS, SYM_ESCAPED}:
        return SYM_ESCAPED
    if SYM_JSON in classes:
        return SYM_UNKNOWN if classes & {SYM_ATTRS} else SYM_MARKUP
    return SYM_MARKUP  # escaped values, markup, attributes: all fit element text and stay there


def _summarize(texts: list[str], repeated: bool = False) -> str:
    """One text for a value that can be spelled each of ``texts`` (or, ``repeated``, any sequence of
    them, a string built up in a loop): the text itself when there is one, else its class."""
    distinct = set(texts)
    if not distinct:
        return SYM_UNKNOWN
    if len(distinct) == 1 and not repeated:
        return next(iter(distinct))
    if not repeated and sum(_spelling_count(t) for t in distinct) <= _SPELLINGS:
        # each spelling is read where it lands; past the bound the class (a string rebuilt in
        # every branch would otherwise double its spellings with each branch, #4145)
        return CHOICE_OPEN + CHOICE_SEP.join(sorted(distinct)) + CHOICE_CLOSE
    joined = _join({_text_class(t) for t in distinct})
    if repeated and joined == SYM_UNKNOWN and any(ch in _POSITIONED for t in distinct for ch in t):
        # pieces in an order the guard does not know, one of them leaving a position open and
        # another needing one (``s = '<a href="'`` then ``s += escape(x)``): never established
        return SYM_REFUSED
    return joined


#: ``[[fill]align][sign][z][#][0][width][grouping][.precision][type]`` of ``format``
_FORMAT_SPEC = re.compile(
    r"(?:(?P<fill>.)?(?P<align>[<>=^]))?[+\- ]?z?#?0?(?P<width>[0-9]*)[,_]?"
    r"(?:\.(?P<precision>[0-9]+))?(?P<type>[bcdeEfFgGnosxX%])?"
)


def _spec_text(text: str, spec: str, numeric: bool) -> str:
    """``format(value, spec)`` of a value whose text is ``text`` (``numeric``: a number for sure).
    Padding keeps what the value is (escaped text stays escaped) unless the fill character is one
    HTML reads; a precision cuts it (digits may then be nothing); ``c`` writes any character; a
    number's type prints digits only for a number (another object's ``__format__`` prints
    anything)."""
    m = _FORMAT_SPEC.fullmatch(spec)
    if m is None:
        return SYM_UNKNOWN  # not a spec ``format`` takes (or one built at run time): raises
    kind = m.group("type") or ""
    if kind == "c":
        return SYM_RAW
    cls = _text_class(text)
    if kind and kind != "s":
        # a number's type prints digits (a ``str`` raises). An object of the developer's own
        # class whose ``__format__`` returns markup is the developer's code, not request data:
        # like a subclass in another file, it is not seen (the module docstring says so)
        return SYM_DIGITS
    if cls == SYM_RAW:
        return SYM_RAW
    fill = m.group("fill") or " "
    if (m.group("width") or m.group("align")) and fill in _SPECIAL_TEXT | {"&"} | _CONTROL:
        return SYM_RAW
    padded = bool(m.group("width"))
    cut = m.group("precision") is not None
    if not padded and not cut:
        return text
    if cls == SYM_DIGITS:
        return SYM_WORDS if (cut or padded) else SYM_DIGITS
    if cls in (SYM_WORDS, SYM_VALUE, SYM_CSS, SYM_ESCAPED):
        return cls  # spaces around (or a part of) escaped text are escaped text
    return SYM_UNKNOWN


def _repr_text(text: str) -> str:
    """``repr`` of a value whose text is ``text``: a number's digits; escaped text (it holds no
    quote, so ``repr`` quotes it with ``'``) between apostrophes; anything else is placed nowhere."""
    cls = _text_class(text)
    if cls == SYM_DIGITS:
        return SYM_DIGITS
    if cls in (SYM_WORDS, SYM_VALUE, SYM_CSS, SYM_ESCAPED):
        return "'" + cls + "'"
    return SYM_RAW


class _Raw:
    """A binding whose value is not known: a parameter, a loop variable, a ``with`` target."""


RAW = _Raw()


class _Param(_Raw):
    """A parameter's value on entry: raw, unless a call being judged binds it (``_actuals``). One
    object per parameter, so the entry binding is told apart from any other unknown binding."""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name


class _ElementOf:
    """A name bound by iterating ``iterable`` (a ``for`` target or a comprehension variable)."""

    def __init__(self, iterable: ast.expr) -> None:
        self.iterable = iterable
        #: the target unpacks each element (``for a, b in rows``), and which member this name is
        self.unpacked = False
        self.index: int | None = None


class _Foreign:
    """A value put into a container from ANOTHER scope: a closure's ``parts.append(s)`` where ``parts``
    is the enclosing function's (#3850). It is judged in the scope that wrote it."""

    __slots__ = ("expr", "scope")

    def __init__(self, expr: ast.expr, scope: _Scope) -> None:
        self.expr = expr
        self.scope = scope


class _Actual(_Foreign):
    """A parameter bound to the argument of the ONE call whose result is being judged at a sink
    (#3864): the argument, judged in the caller's scope as if it were written in place."""

    __slots__ = ()


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


def _actual_values(value: object) -> list[tuple[ast.expr, _Scope]]:
    """The argument a call-bound parameter stands for, with its scope; nothing for anything else."""
    if isinstance(value, _Actual):
        return [(value.expr, value.scope)]
    return []


def _return_values(fn: ast.AST) -> list[ast.expr | None]:
    """What a function returns: each ``return``'s value (``None`` for a bare one), or a lambda's body."""
    if isinstance(fn, ast.Lambda):
        return [fn.body]
    return [n.value for n in _own_nodes(fn) if isinstance(n, ast.Return)]


class _NoBindings(dict):
    """The binding table of pass A: no call is ever bound there; a write is a bug, so it raises."""

    def __setitem__(self, key: object, value: object) -> None:
        raise RuntimeError("pass A of the safe-markup guard never binds a call (#3864)")


_NO_BINDINGS: dict = _NoBindings()


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
        call_binding: bool = False,
    ) -> None:
        self.source = source
        #: pass B (``find_unsafe_markup``): also judge a sink handed a call with that call's
        #: arguments (#3864). Pass A (``False``) is the guard as it was: nothing ever binds a call.
        self.call_binding = call_binding
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
        #: functions of this module the file replaces where it does not say ``def`` (#3864):
        #: ``sys.modules[__name__].helper = ...``, ``setattr(<module>, "helper", ...)``,
        #: ``helper.__code__ = ...``; ``patched_all`` when the name is not a literal. Pass B does
        #: not follow a call into such a function.
        self.patched_names: set[str] = set()
        self.patched_all = False
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
        #: id(scope) -> parameter -> the argument (an ``_Actual``) of the call whose RESULT is being
        #: judged at a sink (#3864). Only ``tainted`` sets it, and only to flag MORE: a verdict that
        #: reads it is OR-ed with the one it would give without (``_returns_tainted``).
        self._actuals: dict[int, dict[str, object]] = {} if call_binding else _NO_BINDINGS
        self._facts: dict[int, dict[int, tuple[tuple[str, str, bool], ...]]] = {}
        self._memo_text: dict[tuple, str] = {}
        #: ids of the values the engine binds a block handler's ``content`` to (rendered markup)
        self._engine_values: set[int] = set()
        #: id(the value a narrowing test binds) -> (that value, the test, its scope)
        self._narrowed_values: dict[int, tuple] = {}
        #: pass B's text of a part (``_text_b``), remembered under (node, scope, bindings in force);
        #: only nodes of this file's tree are keys (a node made on the fly may reuse an id)
        self._memo_texts: dict[tuple, str] = {}
        self._texts_busy: set[tuple] = set()
        self._site_bindings: dict[tuple, dict | None] = {}
        #: why the last ``_misplaces`` refused (the finding's message)
        self._why_misplaced: str | None = None
        self._memo_checked: dict[tuple, bool] = {}
        self._memo_numeric: dict[tuple, bool] = {}
        self._parents_of: dict[int, dict[int, ast.AST]] = {}
        #: id(function) -> the ``_text_b`` key of the call being read into it
        self._fns_reading: dict[int, tuple] = {}
        self._texts_recursed: set[tuple] = set()
        self._texts_pessimistic: set[tuple] = set()
        self._tree_ids: frozenset[int] = (
            frozenset(id(n) for n in ast.walk(self.tree)) if call_binding else frozenset()
        )
        #: every call binding installed, kept alive for the run so an id in a memo key (the call
        #: bindings in force, ``_binding_state``) is never reused (#4145)
        self._installed_bindings: list[dict[str, object]] = []
        #: (id(callee expression), id(scope)) -> what ``_local_function`` found (it reads only
        #: what the file says); the entry holds the scope so its id cannot be reused meanwhile
        self._locals: dict[tuple[int, int], tuple] = {}
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

    def _scan_patches(self, nodes: list[ast.AST]) -> None:
        """Fill ``patched_names`` / ``patched_all`` (#3864): writes that replace a function of this
        module, or its code, without a ``def``. A module object is ``sys.modules[...]``,
        ``importlib.import_module(...)`` / ``__import__(...)``, or a name the file binds to one of
        those. A module replaced from ANOTHER file is not seen (the module docstring says so)."""
        module_names: set[str] = set()

        def module_like(e: ast.AST) -> bool:
            if isinstance(e, ast.Subscript):
                v = e.value
                return isinstance(v, ast.Attribute) and v.attr == "modules"
            if isinstance(e, ast.Call):
                f = e.func
                return (
                    (isinstance(f, ast.Name) and f.id in ("__import__", "import_module"))
                    or (isinstance(f, ast.Attribute) and f.attr == "import_module")
                    or (
                        isinstance(f, ast.Attribute)
                        and f.attr == "get"
                        and isinstance(f.value, ast.Attribute)
                        and f.value.attr == "modules"
                    )
                )
            return isinstance(e, ast.Name) and e.id in module_names

        for _ in range(2):  # a name bound to a name bound to a module
            for node in nodes:
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                    value = node.value
                    if value is not None and module_like(value):
                        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                        for t in targets:
                            module_names.update(_target_names(t))
        for node in nodes:
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
                if node.attr in _CODE_ATTRS:
                    if isinstance(node.value, ast.Name):
                        self.patched_names.add(node.value.id)
                    else:
                        self.patched_all = True
                elif module_like(node.value):
                    if node.attr in ("__class__", "__dict__", "__getattr__"):
                        self.patched_all = True
                    else:
                        self.patched_names.add(node.attr)
                elif not (isinstance(node.value, ast.Name) and node.value.id in ("self", "cls")):
                    # ``obj.helper = ...``: ``obj`` may be this module (handed in, say)
                    self.patched_names.add(node.attr)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ("setattr", "delattr")
                and len(node.args) >= 2
            ):
                target, name = node.args[0], node.args[1]
                if isinstance(target, ast.Name) and target.id in ("self", "cls"):
                    continue
                if isinstance(name, ast.Constant) and isinstance(name.value, str):
                    self.patched_names.add(name.value)
                    if name.value in _CODE_ATTRS:
                        if isinstance(target, ast.Name):
                            self.patched_names.add(target.id)
                        else:
                            self.patched_all = True
                elif module_like(target):
                    self.patched_all = True
            elif isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)):
                # ``sys.modules[__name__].__dict__["helper"] = ...``
                v = node.value
                if isinstance(v, ast.Attribute) and v.attr == "__dict__" and module_like(v.value):
                    self.patched_all = True
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in MUTATORS
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "__dict__"
                and module_like(node.func.value.value)
            ):
                self.patched_all = True

    def _patched(self, name: str) -> bool:
        """Pass B: the file replaces the function ``name`` without a ``def`` (#3864)."""
        return self.call_binding and (self.patched_all or name in self.patched_names)

    def _scan_hazards(self) -> None:
        parents: dict[int, ast.AST] = {}
        nodes = list(ast.walk(self.tree))
        for n in nodes:
            for c in ast.iter_child_nodes(n):
                parents[id(c)] = n
        self._scan_patches(nodes)
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
                if arg.arg in engine:
                    self._engine_values.add(id(value))  # rendered markup, for pass B's text
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
            flat = isinstance(target, (ast.Tuple, ast.List)) and all(
                isinstance(x, ast.Name) for x in target.elts
            )
            for n in _target_names(target):
                element = _ElementOf(iterable)
                # ``for a, b in rows``: each name is a MEMBER of an element (pass B's text, #3864)
                element.unpacked = isinstance(target, (ast.Tuple, ast.List))
                if flat:
                    element.index = [x.id for x in target.elts].index(n)  # type: ignore[attr-defined]
                self._bind(scope, key, n, element)
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
                    self._why_misplaced = None
                    if self.tainted(arg, scope, 0, frozenset()):
                        why = (ast.get_source_segment(self.source, arg) or ast.dump(arg))[:90]
                        if self._why_misplaced:
                            why += f" [position: {self._why_misplaced}]"
                        out.append(Finding(call.lineno, scope.name, name, why))
                        break
                else:
                    if name in self.format_sinks and self.call_binding:
                        why = self._format_args_misplaced(call, scope, name)
                        if why:
                            out.append(Finding(call.lineno, scope.name, name, why))
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
            if self._misplaces(e, sc):
                return False
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
            if isinstance(e.op, (ast.Add, ast.Mod)) and self._misplaces(e, sc):
                return False
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
            return self._call_clean(e, sc, d, seen - {_CTX_OK})
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
        if isinstance(value, _Actual):  # a bound argument: what the caller passed
            return self._hold_level(value.expr, value.scope, origins, depth + 1, visited)
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
                if key not in self._fn_hold:
                    if key in self._fn_hold_busy:
                        return 0  # recursion adds nothing new
                    self._fn_hold_busy.add(key)
                    try:
                        # the depth carries on into the callee (#3864: a chain of helpers each
                        # calling the next ran Python out of stack); past the bound it is ``2``
                        held = any(
                            self._hold_level(
                                r.value, fscope, origins, d if self.call_binding else 0
                            )
                            for r in _own_nodes(fn)
                            if isinstance(r, ast.Return) and r.value is not None
                        )
                    finally:
                        self._fn_hold_busy.discard(key)
                    if d > 30 and self.call_binding:
                        return 2 if held else 0  # cut short by the bound: not remembered
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

    def _binding_state(self) -> tuple:
        """The call bindings in force, as a key: what ``_bindings`` (and so every answer built on it)
        reads besides tables fixed at construction. A binding dict is never changed once installed,
        and ``_judge_returns`` keeps each one alive, so its id is not reused within the run (#4145)."""
        actuals = self._actuals
        if not actuals:
            return ()
        return tuple(sorted((k, id(v)) for k, v in actuals.items()))

    def _alternatives(
        self, value: ast.expr, sc: _Scope, depth: int = 0, visited: frozenset = frozenset()
    ) -> list[tuple[ast.expr, _Scope]]:
        """The expressions ``value`` can be: itself, or for a name / ``a if c else b`` / ``a or b`` /
        ``(n := a)`` each value it can hold."""
        if depth > 8:
            return [(value, sc)]
        d = depth + 1
        if isinstance(value, ast.IfExp):
            return [
                *self._alternatives(value.body, sc, d, visited),
                *self._alternatives(value.orelse, sc, d, visited),
            ]
        if isinstance(value, ast.BoolOp):
            return [a for v in value.values for a in self._alternatives(v, sc, d, visited)]
        if isinstance(value, ast.NamedExpr):
            return self._alternatives(value.value, sc, d, visited)
        if isinstance(value, ast.Name):
            owner = sc.find(value.id)
            if owner is None:
                return []
            key = ("alt", value.id, id(owner), self._ctx(owner))
            if key in visited:
                return []
            if owner.contents.get(value.id) or owner.pieces.get(value.id):
                return [(value, sc)]  # a container / a string built up: judged as a whole
            values, _ = self._bindings(value, value.id, owner, sc)
            out: list[tuple[ast.expr, _Scope]] = []
            for v in values:
                if isinstance(v, ast.expr):
                    out.extend(self._alternatives(v, owner, d, visited | {key}))
                for x, xsc in _actual_values(v):  # a bound argument: each one, in its scope
                    out.extend(self._alternatives(x, xsc, d, visited | {key}))
            return out
        return [(value, sc)]

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
        for alt, asc in self._alternatives(value, sc):
            for origins, kind in ((JSON_ESCAPERS, "json"), (CONTEXT_SENSITIVE_ORIGINS, "sens")):
                level = self._hold_level(alt, asc, origins)
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
                    # (pass B's ``_misplaces`` also walks the string it is placed in, #3864)
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
                    break
        # one order for both passes: pass B's own order changed no verdict, the position pass
        # judges each part where it lands (#4212)
        for kind in ("json*", "sens*", "json", "sens"):
            if kind in found:
                return kind
        return None

    def _leaf_text(self, e: ast.expr, sc: _Scope, depth: int = 0) -> str:
        """The literal text ``e`` contributes in front of a later part of the same string: its own for a
        string constant, an f-string (a field counts as ``\x00``), a name that only ever holds one
        such value, or a call of a function of this file whose every ``return`` is the same literal;
        ``\x00`` for anything the guard cannot read. (Pass B's walk reads with :meth:`_text_b`.)"""
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
            if len(values) == 1 and isinstance(values[0], ast.expr):
                return self._leaf_text(values[0], owner, d)
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
                # the same answer every time it is asked at this depth (it reads no call
                # binding, #4212): remembered while ``_returns_tainted`` judges a call (#3864)
                memo = ("leaf", id(fn), d) if self._actuals else None
                if memo is not None and memo in self._memo_text:
                    return self._memo_text[memo]
                returns = [
                    r.value
                    for r in _own_nodes(fn)
                    if isinstance(r, ast.Return) and r.value is not None
                ]
                texts = {self._leaf_text(r, fscope, d) for r in returns}
                text = texts.pop() if len(texts) == 1 else "\x00"
                if memo is not None:
                    self._memo_text[memo] = text
                return text
        return "\x00"

    # -- pass B: the text in front of a part, read or classed, never assumed (#3864) ---------------
    #
    # Pass A reads an earlier part it cannot spell out as ``\x00``, an ordinary character, so a part
    # behind it was positioned as if that part could not move the position: ``pre() + safe_url(x)``
    # was cleared where ``pre`` returns ``'<a href='`` from either of two branches. Pass B spells a
    # part out where its text is known (every spelling of a name, a branch, a constant table, a
    # function of this file) and otherwise puts the SYMBOL of what the part can do there (the
    # ``SYM_*`` constants). :func:`_walk` then reads the whole text: a part behind one it cannot
    # read sits at an unknown position and is reported.

    def _text_b(self, e: object, sc: _Scope, depth: int = 0) -> str:
        """The text ``e`` contributes (literal characters and ``SYM_*`` symbols). Remembered for the
        run under the node, its scope and the call bindings in force; a value that reaches itself
        (a recursion) is unknown."""
        if depth > _TEXT_DEPTH:
            return SYM_UNKNOWN  # past the bound: unknown, and remembered so (fail closed)
        d = depth + 1
        if isinstance(e, _Foreign):
            return self._text_b(e.expr, e.scope, d)
        if isinstance(e, _AnyOf):
            return _summarize([self._text_b(v, sc, d) for v in e.values])
        if isinstance(e, _ElementOf):
            it = e.iterable
            if (
                isinstance(it, ast.Call)
                and isinstance(it.func, ast.Name)
                and it.func.id == "range"
                and sc.find("range") is None
            ) or self._deep_digits(it, sc):
                return SYM_DIGITS
            if isinstance(it, ast.Call) and self.origin(it.func, sc) in CLEAN_RECORD_ORIGINS:
                return self._call_text(it, sc, d)  # ``root, zone, field = upload_attrs(...)``
            texts = self._element_texts(it, sc, d, iterate=True, unpack=e.unpacked, index=e.index)
            return SYM_UNKNOWN if texts is None else _summarize(texts)
        if not isinstance(e, ast.expr):
            return SYM_UNKNOWN  # a parameter, a loop variable of something unknown
        if id(e) in self._engine_values:
            return SYM_MARKUP  # the rendered body of a block tag (``ENGINE_RENDERED_PARAMETERS``)
        if id(e) in self._narrowed_values:
            return self._narrowed_text(*self._narrowed_values[id(e)][1:], d)
        key = (id(e), id(sc), self._binding_state()) if id(e) in self._tree_ids else None
        if key is None:
            return self._read_text(e, sc, d)
        hit = self._memo_texts.get(key)
        if hit is not None:
            return hit
        if key in self._texts_busy:
            # a recursion (a helper that renders its children through itself): read the inner
            # call as markup that starts and ends in element text, and check that assumption below
            self._texts_recursed.add(key)
            return SYM_UNKNOWN if key in self._texts_pessimistic else SYM_MARKUP
        start = len(self._memo_texts)
        self._texts_busy.add(key)
        try:
            text = self._read_text(e, sc, d)
            if key in self._texts_recursed and _text_class(text) not in _FITS_MARKUP:
                # the result does not start and end in element text (a base case that leaves a
                # quote open, under a step that adds the closing one): what was read under the
                # assumption is forgotten and the inner call read as unknown
                for stale in list(self._memo_texts)[start:]:
                    del self._memo_texts[stale]
                self._texts_pessimistic.add(key)
                text = self._read_text(e, sc, d)
        finally:
            self._texts_busy.discard(key)
            self._texts_recursed.discard(key)
        self._memo_texts[key] = text
        return text

    def _narrowed_text(self, test: ast.AST, sc: _Scope, d: int) -> str:
        """A name a test narrowed (``x in TABLE``, ``x == "a"``, ``isinstance(x, int)``): one of the
        members the test names."""
        if isinstance(test, ast.Compare) and len(test.ops) == 1:
            op, other = test.ops[0], test.comparators[0]
            if isinstance(op, (ast.In, ast.NotIn)):
                texts = self._element_texts(other, sc, d, iterate=True)
                return SYM_UNKNOWN if texts is None else _summarize(texts)
            if isinstance(op, (ast.Eq, ast.NotEq)):
                side = test.left if isinstance(test.left, ast.Constant) else other
                if isinstance(side, ast.Constant):
                    return self._literal_text(side.value)
            return SYM_UNKNOWN
        # ``isinstance(x, (int, float))``: a number. A subclass of the developer's own whose
        # ``__str__`` returns markup is the developer's code, not request data (not seen, like a
        # subclass in another file; the module docstring says so)
        return SYM_DIGITS

    def _str_fact_text(self, call: ast.Call, sc: _Scope, d: int) -> str:
        """``str(x)`` where ``str(x) in TABLE`` holds (#3846): a member of any such table the
        function tests ``str(x)`` against."""
        arg = call.args[0] if call.args else None
        if not isinstance(arg, ast.Name):
            return SYM_UNKNOWN
        texts: list[str] = []
        for node in ast.walk(sc.node):
            if (
                isinstance(node, ast.Compare)
                and len(node.ops) == 1
                and isinstance(node.ops[0], (ast.In, ast.NotIn))
                and isinstance(node.left, ast.Call)
                and isinstance(node.left.func, ast.Name)
                and node.left.func.id == "str"
                and len(node.left.args) == 1
                and isinstance(node.left.args[0], ast.Name)
                and node.left.args[0].id == arg.id
            ):
                inner = self._element_texts(node.comparators[0], sc, d, iterate=True)
                if inner is None:
                    return SYM_UNKNOWN
                texts.extend(inner)
        return _summarize(texts)

    @staticmethod
    def _literal_text(value: object) -> str:
        if isinstance(value, str):
            return SYM_UNKNOWN if any(ch in _CONTROL for ch in value) else value
        if value is None or isinstance(value, (bool, int, float)):
            return SYM_DIGITS
        return SYM_UNKNOWN

    def _field_text(self, v: ast.expr, sc: _Scope, d: int) -> str:
        """One piece of an f-string."""
        if isinstance(v, ast.Constant):
            return self._literal_text(v.value)
        if not isinstance(v, ast.FormattedValue):
            return SYM_UNKNOWN
        spec = v.format_spec
        text = self._text_b(v.value, sc, d)
        if v.conversion in (ord("r"), ord("a")):
            text = _repr_text(text)
        if spec is None:
            return text
        if not (
            isinstance(spec, ast.JoinedStr)
            and all(isinstance(p, ast.Constant) and isinstance(p.value, str) for p in spec.values)
        ):
            return SYM_UNKNOWN  # a spec built at run time
        return _spec_text(
            text,
            "".join(p.value for p in spec.values),  # type: ignore[attr-defined]
            self._numeric(v.value, sc),
        )

    def _percent_text(self, e: ast.BinOp, sc: _Scope, d: int) -> str:
        fmt = e.left
        if not (isinstance(fmt, ast.Constant) and isinstance(fmt.value, str)):
            return self._unplaced([e.right], sc, d, containers=True)
        text = self._literal_text(fmt.value)
        if text == SYM_UNKNOWN:
            return text
        right = e.right
        args = list(right.elts) if isinstance(right, ast.Tuple) else [right]
        named = (
            {
                k.value: v
                for k, v in zip(right.keys, right.values)
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
            if isinstance(right, ast.Dict) and all(k is not None for k in right.keys)
            else None
        )
        specs = [m for m in _PERCENT_SPEC.finditer(text) if m.group(2) != "%"]
        out, last, i = [], 0, 0
        for m in _PERCENT_SPEC.finditer(text):
            out.append(text[last : m.start()])
            last = m.end()
            if m.group(2) == "%":
                out.append("%")
                continue
            arg: ast.expr | None = None
            if m.group(1) is not None:
                arg = named.get(m.group(1)[1:-1]) if named is not None else None
            elif named is None and len(specs) == len(args):
                arg = args[i]
            i += 1
            if arg is None:
                # ``'%(u)s' % d`` of a dict held by a name: one of its values, unread
                out.append(self._unplaced([right], sc, d, containers=True))
            elif m.group(2) in "diouxXeEfFgG":
                out.append(SYM_DIGITS)  # ``%d`` and kin: ``int()`` / ``float()`` of it, or raises
            elif m.group(2) == "c":
                out.append(SYM_RAW)  # the character with that code: a quote, ``<``
            elif m.group(2) in "ra":
                out.append(_repr_text(self._text_b(arg, sc, d)))
            else:
                # ``%-5s`` pads, ``%.3s`` cuts: as the same spec of ``format``
                flags = m.group(0)[1:-1].split(")")[-1]
                out.append(_spec_text(self._text_b(arg, sc, d), flags.replace("-", "<"), False))
        out.append(text[last:])
        return "".join(out)

    def _escaped_text(self, text: str, plain: bool) -> str:
        """What ``conditional_escape`` makes of a value whose text is ``text`` (``plain``: never a
        marked-safe value): escaped text, or, for a value that may be marked safe, the markup."""
        cls = _text_class(text)
        if cls in (SYM_DIGITS, SYM_WORDS, SYM_VALUE, SYM_CSS, SYM_ESCAPED):
            return cls
        if plain:
            return SYM_ESCAPED
        return cls if cls in (SYM_MARKUP, SYM_ATTRS, SYM_JSON) else SYM_MARKUP

    def _format_args_misplaced(self, call: ast.Call, sc: _Scope, name: str) -> str:
        """``format_html(fmt, *args)`` / ``format_html_join(sep, fmt, rows)``: each argument
        (``conditional_escape``-d by Django) read where it lands in the format string.
        A reason when one lands where its text may not sit (#3864), else ``""``.

        A format string held in a name, chosen by a branch or passed in is read like any other
        text (#4212): each spelling is judged; one the guard cannot spell out, with an argument
        to place, is reported."""
        position = FORMAT_SINK_ARG.get(name, 0)
        fmt = call.args[position] if len(call.args) > position else None
        if fmt is None or isinstance(fmt, ast.Starred):
            return ""
        if isinstance(fmt, ast.Constant) and isinstance(fmt.value, str):
            return self._format_spelling_misplaced(fmt.value, call, sc, name)
        to_place = len(call.args) > position + 1 or bool(call.keywords)
        spellings = _expand(self._text_b(fmt, sc))
        if spellings is None or any(set(t) & _CONTROL for t in spellings):
            if to_place:
                return (
                    "format string the guard cannot read [position: its arguments are not placed]"
                )
            return ""
        for spelling in spellings:
            reason = self._format_spelling_misplaced(spelling, call, sc, name)
            if reason:
                return reason
        return ""

    def _format_spelling_misplaced(
        self, fmt_value: str, call: ast.Call, sc: _Scope, name: str
    ) -> str:
        """:meth:`_format_args_misplaced` for one spelling of the format string. An argument
        that cannot be located (after a ``*seq``, or a name a ``**map`` may hold) is read as
        escaped text, which is what ``format_html`` makes of it (#4212)."""
        position = FORMAT_SINK_ARG.get(name, 0)
        try:
            fields = list(string.Formatter().parse(fmt_value))
        except ValueError:
            return ""
        keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg}
        double_star = any(kw.arg is None for kw in call.keywords)
        rows = (
            call.args[position + 1] if name == "format_html_join" and len(call.args) > 2 else None
        )
        positional = call.args[position + 1 :]
        first_star = next(
            (i for i, a in enumerate(positional) if isinstance(a, ast.Starred)), len(positional)
        )
        out, auto = [], 0
        for literal, field, spec, conv in fields:
            out.append(literal)
            if field is None:
                continue
            index: int | None = None
            key = None
            if field == "":
                index, auto = auto, auto + 1
            elif field.isdigit():
                index = int(field)
            else:
                key = field
            if rows is not None:
                texts = (
                    self._element_texts(rows, sc, 0, unpack=True, index=index)
                    if index is not None and not isinstance(rows, ast.Starred)
                    else None
                )
                value = (
                    SYM_ESCAPED
                    if texts is None
                    else _summarize(
                        [
                            self._escaped_text(
                                t, _text_class(t) not in (SYM_MARKUP, SYM_ATTRS, SYM_JSON)
                            )
                            for t in texts
                        ]
                    )
                )
            else:
                located = True
                if key is not None:
                    arg = keywords.get(key)
                    located = arg is not None or not double_star
                elif index is not None and index >= first_star:
                    arg, located = None, first_star == len(positional)
                else:
                    arg = positional[index] if index is not None else None
                if not located:
                    value = SYM_ESCAPED  # a ``*seq`` / ``**map`` element, escaped by format_html
                elif arg is None:
                    value = SYM_UNKNOWN
                elif self._checked_url(arg, sc):
                    value = SYM_VALUE
                else:
                    # ``format_html`` escapes each argument; a marked-safe one passes as it is,
                    # which is the developer's markup by Django's contract: read as markup only
                    # when the guard knows it is (``format_html(...)``, ``mark_safe(...)``)
                    text = self._text_b(arg, sc)
                    known_markup = _text_class(text) in (SYM_MARKUP, SYM_ATTRS, SYM_JSON)
                    value = self._escaped_text(text, not known_markup)
            if conv in ("r", "a"):
                value = _repr_text(value)
            if spec:
                value = _spec_text(value, spec, False) if "{" not in spec else SYM_UNKNOWN
            out.append(value)
        text = "".join(out)
        if _walk(text)[0]:
            return ""
        return f"format string {fmt_value[:60]!r} [position: {refusal(text)}]"

    def _ordered_pieces(self, use: ast.Name, owner: _Scope, values: list, d: int) -> str | None:
        """``s = '<a'`` then ``if c: s += f' title="{v}"'`` then ``s += '>'`` in one function, no
        loop: the pieces in source order, each one under an ``if`` / ``try`` / ``match`` either
        there or not. ``None`` when that order is not the order they run in (a piece in a loop,
        more than one binding, a piece after the use, a closure)."""
        fn = owner.node
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or len(values) != 1:
            return None
        if use.id in owner.params or use.id in self.rebound_names:
            return None
        parents = self._fn_parents(fn)
        bind = [
            n
            for n in _own_nodes(fn)
            if isinstance(n, (ast.Assign, ast.AnnAssign))
            and use.id
            in [
                t
                for x in (n.targets if isinstance(n, ast.Assign) else [n.target])
                for t in _target_names(x)
            ]
        ]
        augs = [
            n
            for n in _own_nodes(fn)
            if isinstance(n, ast.AugAssign)
            and isinstance(n.target, ast.Name)
            and n.target.id == use.id
        ]
        if len(bind) != 1 or not augs or any(not isinstance(a.op, ast.Add) for a in augs):
            return None
        loops = (ast.For, ast.AsyncFor, ast.While)

        def path(node: ast.AST) -> list[ast.AST]:
            out, cur = [], parents.get(id(node))
            while cur is not None and cur is not fn:
                out.append(cur)
                cur = parents.get(id(cur))
            return out

        start = bind[0]
        if any(isinstance(x, loops) for x in path(start)) or any(
            isinstance(x, loops) for x in path(use)
        ):
            return None
        order = sorted(augs, key=lambda n: (n.lineno, n.col_offset))
        if order[0].lineno <= start.lineno or order[-1].lineno >= use.lineno:
            return None
        out = [self._text_b(values[0], owner, d)]
        for aug in order:
            above = path(aug)
            if any(isinstance(x, loops) for x in above):
                return None
            text = self._text_b(aug.value, owner, d)
            optional = any(isinstance(x, (ast.If, ast.Try, ast.Match, ast.With)) for x in above)
            out.append(_summarize([text, ""]) if optional else text)
        return "".join(out)

    def _fn_parents(self, fn: ast.AST) -> dict[int, ast.AST]:
        hit = self._parents_of.get(id(fn))
        if hit is None:
            hit = self._parents_of[id(fn)] = {
                id(c): n for n in ast.walk(fn) for c in ast.iter_child_nodes(n)
            }
        return hit

    def _unplaced(self, args: list, sc: _Scope, d: int, containers: bool = False) -> str:
        """A string built from ``args`` at positions the guard does not read: ``SYM_RAW`` when one
        of them (or, ``containers``, an element of one) needs a position, else unknown."""
        texts: list[str] = []
        for a in args:
            elements = self._element_texts(a, sc, d) if containers else None
            texts.extend(elements if elements is not None else [self._text_b(a, sc, d)])
        needs = _POSITIONED - {SYM_WORDS} | {SYM_UNKNOWN, SYM_RAW}
        if any(ch in needs for t in texts for ch in t):
            return SYM_RAW
        return SYM_UNKNOWN

    def _named_arguments(self, e: ast.Call) -> dict[str, ast.expr] | None:
        """The names a call hands over: its keywords, or the one dict display it is given
        (``format_map({...})``, ``substitute({...})``); ``None`` when they are not written out."""
        if any(kw.arg is None for kw in e.keywords) or len(e.args) > 1:
            return None
        named = {kw.arg: kw.value for kw in e.keywords if kw.arg}
        if e.args:
            table = e.args[0]
            if not (
                isinstance(table, ast.Dict)
                and all(
                    isinstance(k, ast.Constant) and isinstance(k.value, str) for k in table.keys
                )
            ):
                return None
            named.update({k.value: v for k, v in zip(table.keys, table.values)})  # type: ignore[union-attr]
        return named

    def _template_text(self, e: ast.Call, template: str, sc: _Scope, d: int) -> str | None:
        """``Template("<a title='$x'>").substitute(x=...)``: the literal text, each placeholder the
        text of its value; ``None`` when the values are not written out."""
        named = self._named_arguments(e)
        if named is None:
            return None
        out, last = [], 0
        for m in re.finditer(
            r"\$(?:(\$)|([_a-zA-Z][_a-zA-Z0-9]*)|\{([_a-zA-Z][_a-zA-Z0-9]*)\})", template
        ):
            out.append(template[last : m.start()])
            last = m.end()
            if m.group(1):
                out.append("$")
                continue
            value = named.get(m.group(2) or m.group(3))
            out.append(SYM_UNKNOWN if value is None else self._text_b(value, sc, d))
        out.append(template[last:])
        return "".join(out)

    def _format_text(
        self,
        e: ast.Call,
        recv: ast.Constant,
        sc: _Scope,
        d: int,
        keywords: dict[str, ast.expr] | None = None,
    ) -> str:
        """``"<a href={}>".format(...)`` (or ``format_map`` of a dict display, ``keywords``): the
        literal text, each field the text of its argument."""
        text = self._literal_text(recv.value)
        if text == SYM_UNKNOWN or self._has_star(e):
            return SYM_UNKNOWN
        positional = e.args if keywords is None else []
        if keywords is None:
            keywords = {kw.arg: kw.value for kw in e.keywords if kw.arg}
        try:
            fields = list(string.Formatter().parse(text))
        except ValueError:
            return SYM_UNKNOWN
        out, auto = [], 0
        for literal, field, spec, conv in fields:
            out.append(literal)
            if field is None:
                continue
            arg: ast.expr | None = None
            if field == "":
                arg = positional[auto] if auto < len(positional) else None
                auto += 1
            elif field.isdigit():
                arg = positional[int(field)] if int(field) < len(positional) else None
            elif field.isidentifier():
                arg = keywords.get(field)
            if arg is None:
                out.append(SYM_UNKNOWN)
                continue
            value = self._text_b(arg, sc, d)
            if conv in ("r", "a"):
                value = _repr_text(value)
            if spec:
                value = (
                    SYM_UNKNOWN if "{" in spec else _spec_text(value, spec, self._numeric(arg, sc))
                )
            out.append(value)
        return "".join(out)

    def _element_texts(
        self,
        e: object,
        sc: _Scope,
        d: int,
        iterate: bool = False,
        unpack: bool = False,
        index: int | None = None,
    ) -> list[str] | None:
        """The texts of the elements of a container (its VALUES for a dict, or its keys when
        ``iterate``; with ``unpack``, the members of each element, for ``for a, b in rows``); ``None``
        when they are not known."""
        if unpack and isinstance(e, ast.Call) and not e.keywords:
            # ``for k, v in d.items()`` / ``for a, b in zip(xs, ys)``: each name is one of these
            pools: list[tuple[ast.expr, bool]] | None = None
            if isinstance(e.func, ast.Attribute) and e.func.attr == "items" and not e.args:
                pools = [(e.func.value, True), (e.func.value, False)]
            elif isinstance(e.func, ast.Name) and e.func.id == "zip" and sc.find("zip") is None:
                pools = [(a, False) for a in e.args]
            if pools is not None:
                if index is not None and index < len(pools):
                    pools = [pools[index]]
                out: list[str] = []
                for pool, keys in pools:
                    inner = self._element_texts(pool, sc, d, iterate=keys)
                    if inner is None:
                        return None
                    out.extend(inner)
                return out
        exprs = self._element_exprs(e, sc, d, iterate)
        if exprs is None:
            return None
        if not unpack:
            return [self._text_b(x, xsc, d) for x, xsc in exprs]
        out = []
        for x, xsc in exprs:
            if isinstance(x, (ast.Tuple, ast.List)) and not any(
                isinstance(m, ast.Starred) for m in x.elts
            ):
                if index is not None:
                    if index >= len(x.elts):
                        return None
                    out.append(self._text_b(x.elts[index], xsc, d))
                else:
                    out.extend(self._text_b(m, xsc, d) for m in x.elts)
            else:
                return None  # an element whose members are not written out
        return out

    def _element_exprs(
        self, e: object, sc: _Scope, d: int, iterate: bool = False
    ) -> list[tuple[object, _Scope]] | None:
        """The element expressions of a container, each with its scope (see ``_element_texts``)."""
        if d > _TEXT_DEPTH:
            return None
        if isinstance(e, _Foreign):
            return self._element_exprs(e.expr, e.scope, d + 1, iterate)
        if not isinstance(e, ast.expr):
            return None
        if isinstance(e, ast.Dict):
            if any(k is None for k in e.keys):
                return None  # ``{**other}``
            items = e.keys if iterate else e.values
            return [(x, sc) for x in items if x is not None]
        if isinstance(e, (ast.List, ast.Tuple, ast.Set)):
            if any(isinstance(x, ast.Starred) for x in e.elts):
                return None
            return [(x, sc) for x in e.elts]
        if isinstance(e, (ast.ListComp, ast.GeneratorExp, ast.SetComp)):
            return [(e.elt, self._comprehension_scope(e, sc))]
        if isinstance(e, ast.DictComp):
            return [(e.key if iterate else e.value, self._comprehension_scope(e, sc))]
        if isinstance(e, (ast.IfExp, ast.BoolOp)):
            out: list[tuple[object, _Scope]] = []
            for part in [e.body, e.orelse] if isinstance(e, ast.IfExp) else e.values:
                inner = self._element_exprs(part, sc, d + 1, iterate)
                if inner is None:
                    return None
                out.extend(inner)
            return out
        if isinstance(e, ast.Subscript) and not isinstance(e.slice, ast.Slice):
            # ``TABLE[k]`` of a table of tables: the elements of every element
            outer = self._element_exprs(e.value, sc, d + 1)
            if outer is None:
                return None
            out = []
            for x, xsc in outer:
                inner = self._element_exprs(x, xsc, d + 1, iterate)
                if inner is None:
                    return None
                out.extend(inner)
            return out
        if isinstance(e, ast.Call) and isinstance(e.func, ast.Attribute) and not e.args:
            recv = e.func.value
            if e.func.attr == "values":
                return self._element_exprs(recv, sc, d + 1)
            if e.func.attr == "keys":
                return self._element_exprs(recv, sc, d + 1, iterate=True)
        if (
            isinstance(e, ast.Call)
            and isinstance(e.func, ast.Name)
            and e.func.id in ELEMENT_CALLS
            and sc.find(e.func.id) is None
            and len(e.args) == 1
            and not e.keywords
        ):
            return self._element_exprs(e.args[0], sc, d + 1, iterate)
        if isinstance(e, ast.Attribute):
            found = self.class_attribute(e, sc)
            if found is None:
                return None
            out = []
            for v in found[1]:
                inner = self._element_exprs(v, found[0], d + 1, iterate)
                if inner is None:
                    return None
                out.extend(inner)
            return out
        if isinstance(e, ast.Name):
            owner = sc.find(e.id)
            if owner is None or owner.pieces.get(e.id):
                return None
            if self._in(self.escaped, self.escaped_in, e.id, owner):
                return None  # handed on: something else can fill it
            values, _ = self._bindings(e, e.id, owner, sc)
            if not values:
                return None
            out = []
            for v in values:
                inner = self._element_exprs(v, owner, d + 1, iterate)
                if inner is None:
                    return None
                out.extend(inner)
            dict_like = owner.keys.get(e.id) or any(
                isinstance(v, (ast.Dict, ast.DictComp))
                or (
                    isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == "dict"
                )
                for v in values
            )
            put = (
                owner.keys.get(e.id, []) if iterate and dict_like else owner.contents.get(e.id, [])
            )
            for c in put:
                out.append(_unwrap(c, owner))
            return out
        return None

    def _attribute_everywhere(self, e: ast.Attribute, sc: _Scope) -> list[tuple] | None:
        """``self.X`` of a class that a subclass of this file overrides: every value the class and
        those subclasses write (``KIND = "select"`` on each filter). ``None`` where
        :meth:`class_attribute` refuses for any other reason."""
        if not (isinstance(e.value, ast.Name) and e.value.id == "self"):
            return None
        cls: _Scope | None = sc
        while cls is not None and not isinstance(cls.node, ast.ClassDef):
            cls = cls.parent
        if cls is None or not self._overridden_in_file(cls, e.attr):
            return None
        out: list[tuple] = []
        for c in [cls, *self._subclasses(cls)]:
            if c.node.decorator_list or e.attr in c.annotated or self._class_dynamic(c):  # type: ignore[attr-defined]
                return None
            if e.attr in c.def_nodes:
                return None
            values = c.assigns.get(e.attr, [])
            if not self._stable(e.attr, values, c):
                return None
            out.extend((v, c) for v in values)
        if self._mutated_or_rebound(e.attr) or e.attr in self.assigned_attrs:
            return None
        if self._falls_through(cls) and not self._defined_unconditionally(cls, e.attr):
            return None
        return out or None

    def _numeric(self, e: object, sc: _Scope, depth: int = 0) -> bool:
        """``e`` is a number: a numeric literal, ``len`` / ``int`` / ``float`` / ``round`` / ``abs`` /
        a ``math`` function, arithmetic no string supports (``-``, ``/``, ``//``, ``**``), or ``+`` /
        ``*`` / ``min`` / ``max`` of numbers, or a name that only ever holds one."""
        if depth > 8 or not isinstance(e, ast.expr):
            return False
        if isinstance(e, ast.Constant):
            return isinstance(e.value, (int, float)) and not isinstance(e.value, bool)
        key = (id(e), id(sc), self._binding_state()) if id(e) in self._tree_ids else None
        if key is not None:
            hit = self._memo_numeric.get(key)
            if hit is None:
                self._memo_numeric[key] = False  # a name that reaches itself: not shown a number
                hit = self._memo_numeric[key] = self._numeric_uncached(e, sc, depth)
            return hit
        return self._numeric_uncached(e, sc, depth)

    def _numeric_uncached(self, e: ast.expr, sc: _Scope, depth: int) -> bool:
        d = depth + 1
        if isinstance(e, ast.UnaryOp):
            return isinstance(e.op, (ast.USub, ast.UAdd)) and self._numeric(e.operand, sc, d)
        if isinstance(e, ast.BinOp):
            if isinstance(e.op, (ast.Add, ast.Mult, ast.Mod)):
                # ``"<b>" * 3`` and ``"%s" % x`` are strings: only numbers on both sides
                return self._numeric(e.left, sc, d) and self._numeric(e.right, sc, d)
            return isinstance(e.op, (ast.Sub, ast.Div, ast.FloorDiv, ast.Pow))
        if isinstance(e, ast.Call):
            origin = self.origin(e.func, sc) or ""
            if origin in ("len", "int", "float", "round", "abs") or origin.startswith("math."):
                return True
            if origin in ("min", "max", "sum"):
                if len(e.args) == 1 and isinstance(
                    e.args[0], (ast.GeneratorExp, ast.ListComp, ast.SetComp)
                ):
                    return self._numeric_elements(e.args[0], sc, d)
                return bool(e.args) and all(self._numeric(a, sc, d) for a in e.args)
            return False
        if isinstance(e, ast.IfExp):
            return self._numeric(e.body, sc, d) and self._numeric(e.orelse, sc, d)
        if isinstance(e, ast.Name):
            owner = sc.find(e.id)
            if owner is None or owner.contents.get(e.id):
                return False
            values, _ = self._bindings(e, e.id, owner, sc)
            return (
                bool(values)
                and all(isinstance(v, ast.expr) and self._numeric(v, owner, d) for v in values)
                and all(self._numeric(p, owner, d) for p in owner.pieces.get(e.id, []))
            )
        return False

    def _numeric_elements(
        self, comp: ast.GeneratorExp | ast.ListComp | ast.SetComp, sc: _Scope, d: int
    ) -> bool:
        """Every element of ``comp`` is a number (``max(len(r) for r in rows)``). A loop variable is
        not one, whatever the same name holds outside the loop: the scope records it as bound by
        the loop."""
        return self._numeric(comp.elt, sc, d)

    def _read_text(self, e: ast.expr, sc: _Scope, d: int) -> str:
        T = self._text_b
        if isinstance(e, ast.Constant):
            return self._literal_text(e.value)
        if isinstance(e, (ast.BinOp, ast.UnaryOp, ast.Call, ast.Name, ast.IfExp)) and self._numeric(
            e, sc
        ):
            return SYM_DIGITS
        if isinstance(e, ast.JoinedStr):
            return "".join(self._field_text(v, sc, d) for v in e.values)
        if isinstance(e, ast.BinOp):
            if isinstance(e.op, ast.Add):
                return T(e.left, sc, d) + T(e.right, sc, d)
            if isinstance(e.op, ast.Mod):
                return self._percent_text(e, sc, d)
            if isinstance(e.op, ast.Mult):
                # ``"<li>" * n``: the text, any number of times
                texts = [T(x, sc, d) for x in (e.left, e.right)]
                strings = [t for t in texts if _text_class(t) != SYM_DIGITS]
                if not strings:
                    return SYM_DIGITS  # a number times a number
                return _summarize(strings, repeated=True) if len(strings) == 1 else SYM_UNKNOWN
            # ``-``, ``/``, ``//``, ``**``, the bit operators: no string supports them
            return SYM_DIGITS
        if isinstance(e, ast.UnaryOp):
            return SYM_DIGITS
        if isinstance(e, ast.Compare):
            return SYM_DIGITS  # ``True`` / ``False``
        if isinstance(e, ast.IfExp):
            return _summarize([T(e.body, sc, d), T(e.orelse, sc, d)])
        if isinstance(e, ast.BoolOp):
            return _summarize([T(v, sc, d) for v in e.values])
        if isinstance(e, (ast.NamedExpr, ast.Await)):
            return T(e.value, sc, d)
        if isinstance(e, ast.Name):
            owner = sc.find(e.id)
            if owner is None:
                origin = self._origin_of_import(e, sc)
                return SYM_MARKUP if origin in CLEAN_NAME_ORIGINS else SYM_UNKNOWN
            if owner.contents.get(e.id):
                return SYM_UNKNOWN  # a container, printed
            values, _ = self._bindings(e, e.id, owner, sc)
            texts = [T(v, owner, d) for v in values]
            pieces = owner.pieces.get(e.id, [])
            if pieces:
                ordered = self._ordered_pieces(e, owner, values, d)
                if ordered is not None:
                    return ordered
                # ``s += part`` in a loop: any sequence of the pieces, after any first value
                return self._raw_all(
                    owner,
                    e.id,
                    lambda: _summarize(texts + [T(p, owner, d) for p in pieces], repeated=True),
                )
            return _summarize(texts)
        if isinstance(e, ast.Attribute):
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
                return SYM_DIGITS
            if (
                e.attr == "hex"
                and isinstance(e.value, ast.Call)
                and self.origin(e.value.func, sc) in SAFE_ALPHABET_ORIGINS
            ):
                return SYM_DIGITS
            if self._is_record(e.value, sc, d, frozenset()):
                return SYM_ATTRS
            found = self.class_attribute(e, sc)
            if found is not None:
                return _summarize([T(v, found[0], d) for v in found[1]])
            every = self._attribute_everywhere(e, sc)
            if every is None:
                return SYM_UNKNOWN
            return _summarize([T(v, vsc, d) for v, vsc in every])
        if isinstance(e, ast.Subscript):
            if isinstance(e.slice, ast.Slice):
                base = T(e.value, sc, d)
                bounds = [e.slice.lower, e.slice.upper, e.slice.step]
                if not any(ch in _CONTROL for ch in base) and all(
                    b is None or (isinstance(b, ast.Constant) and type(b.value) is int)
                    for b in bounds
                ):
                    lo, hi, st = (None if b is None else b.value for b in bounds)  # type: ignore[union-attr]
                    try:
                        return base[lo:hi:st]
                    except ValueError:
                        return SYM_UNKNOWN
                cls = _text_class(base)
                if cls == SYM_DIGITS:
                    return SYM_WORDS  # a slice of digits may be nothing at all
                return _cut_class(cls)
            if isinstance(e.slice, ast.Constant) and type(e.slice.value) is int:
                base = T(e.value, sc, d)
                if base and not any(ch in _CONTROL for ch in base):
                    try:
                        return base[e.slice.value]
                    except IndexError:
                        return SYM_UNKNOWN
            texts = self._element_texts(e.value, sc, d)
            return SYM_UNKNOWN if texts is None else _summarize(texts)
        if isinstance(e, ast.Call):
            return self._call_text(e, sc, d)
        return SYM_UNKNOWN

    def _checked_url(self, e: ast.expr, sc: _Scope) -> bool:
        """``e`` is a URL escaper's output (``_hold_level`` 1), remembered like ``_text_b``."""
        key = (id(e), id(sc), self._binding_state())
        hit = self._memo_checked.get(key)
        if hit is None:
            hit = self._memo_checked[key] = self._hold_level(e, sc, URL_ESCAPER_ORIGINS) == 1
        return hit

    def _plain_str(self, e: ast.expr, sc: _Scope, depth: int = 0) -> bool:
        """``e`` is never a marked-safe value: a literal, an f-string, ``%`` / ``.format`` /
        ``.join`` / ``.decode()`` / a ``str`` method's result, or a number."""
        if isinstance(e, (ast.Constant, ast.JoinedStr)):
            return True
        if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Mod):
            return isinstance(e.left, (ast.Constant, ast.JoinedStr))
        if isinstance(e, ast.Call):
            if isinstance(e.func, ast.Attribute) and (
                e.func.attr in ("decode", "format", "join", "hexdigest", "hex")
                or e.func.attr in STR_METHODS
            ):
                return True
            if isinstance(e.func, ast.Name) and e.func.id in ("len", "int", "float"):
                return True
            local = self._local_callee(e.func, sc) if depth < 6 else None
            if local is not None:
                fn, fscope = local
                values = _return_values(fn)
                return bool(values) and all(
                    v is not None and self._plain_str(v, fscope, depth + 1) for v in values
                )
            return False
        if isinstance(e, ast.Name) and depth < 6:
            owner = sc.find(e.id)
            if owner is None or owner.pieces.get(e.id) or owner.contents.get(e.id):
                return False
            values, _ = self._bindings(e, e.id, owner, sc)
            return bool(values) and all(
                isinstance(v, ast.expr) and self._plain_str(v, owner, depth + 1) for v in values
            )
        return False

    def _call_text(self, e: ast.Call, sc: _Scope, d: int) -> str:
        T = self._text_b
        origin = self.origin(e.func, sc)
        if origin in HTML_ONLY_ORIGINS:
            if origin in QUOTE_ESCAPERS and not self._quotes_escaped(e, QUOTE_ESCAPERS[origin]):
                return SYM_UNKNOWN
            if e.args and self._checked_url(e.args[0], sc):
                return SYM_VALUE  # ``conditional_escape(safe_url(x))``: a checked URL
            inner = _text_class(self._text_b(e.args[0], sc, d)) if e.args else SYM_UNKNOWN
            if inner in (SYM_DIGITS, SYM_WORDS):
                return inner  # nothing there to escape
            if origin in MARK_HONOURING_ORIGINS and not (e.args and self._plain_str(e.args[0], sc)):
                return SYM_MARKUP  # a marked-safe value comes out as it went in
            return SYM_ESCAPED
        if origin in CSS_ESCAPER_ORIGINS:
            return SYM_CSS
        if origin in CONTEXT_SENSITIVE_ORIGINS:
            return SYM_VALUE
        if origin in JSON_ESCAPERS:
            return SYM_JSON
        if origin in (
            "djust.components.utils.component_id_attr",
            "djust.components.utils.style_attrs",
        ):
            return SYM_ATTRS
        if origin == "djust.components.utils.interpolate_color" or origin in SAFE_ALPHABET_ORIGINS:
            return SYM_DIGITS
        if origin in CLEAN_RECORD_ORIGINS:
            if origin in ("djust.components.utils.rich_html", "djust.markdown.render_markdown"):
                return SYM_MARKUP
            if origin.endswith((".clamp_columns", ".clamp_cells")):
                return SYM_DIGITS
            return SYM_ATTRS
        if origin == "django.utils.html.format_html":
            fmt = e.args[0] if e.args else None
            if isinstance(fmt, ast.Constant) and isinstance(fmt.value, str):
                text = self._literal_text(fmt.value)
                try:
                    fields = list(string.Formatter().parse(text))
                except ValueError:
                    return SYM_UNKNOWN
                spelled = "".join(
                    lit + ("" if field is None else SYM_DIGITS) for lit, field, _s, _c in fields
                )
                # markup that ends in element text is placed as markup; one that leaves a tag or
                # a value open is spelled out, each argument an escaped value that may be empty
                # (``format_html('<a href={}', ...)``: what follows is in that value)
                if _walk(spelled)[1] == "text":
                    cls = _text_class(spelled)  # whole attributes (`` disabled{}``) stay so
                    return cls if cls in (SYM_ATTRS, SYM_WORDS, SYM_DIGITS) else SYM_MARKUP
                return spelled.replace(SYM_DIGITS, SYM_VALUE)
            return SYM_MARKUP
        if origin == "django.utils.html.format_html_join":
            return SYM_MARKUP
        if (
            isinstance(e.func, ast.Name)
            and self._sink_name(e.func, sc) in ("mark_safe", "SafeString", "SafeText")
            and len(e.args) == 1
            and not e.keywords
        ):
            return T(e.args[0], sc, d)  # marking a string safe does not change its text
        if isinstance(e.func, ast.Name) and sc.find(e.func.id) is None:
            if e.func.id in NUMERIC_TYPES or e.func.id in ("len", "round", "abs", "sum"):
                return SYM_DIGITS
            if e.func.id in ("min", "max"):
                return _summarize([T(a, sc, d) for a in e.args]) if e.args else SYM_UNKNOWN
            if e.func.id == "str" and len(e.args) == 1 and not e.keywords:
                if id(e) in self.narrowed_exprs:
                    return self._str_fact_text(e, sc, d)
                return T(e.args[0], sc, d)
            if e.func.id == "str" and not e.args:
                return ""
            if e.func.id in RENDER_CALLS:
                return SYM_MARKUP
        if (
            isinstance(e.func, ast.Attribute)
            and e.func.attr in DATE_DIGIT_METHODS
            and not e.args
            and not e.keywords
            and self._is_date(e.func.value, sc)
        ):
            return SYM_DIGITS
        if isinstance(e.func, ast.Attribute):
            recv = e.func.value
            attr = e.func.attr
            if attr in RENDER_CALLS:
                return SYM_MARKUP  # by method name, as ``clean`` trusts it
            if (
                attr == "hex"
                and not e.args
                and isinstance(recv, ast.Call)
                and isinstance(recv.func, ast.Attribute)
                and recv.func.attr == "encode"
            ):
                return SYM_DIGITS  # ``s.encode().hex()``: hex digits
            if attr == "join" and len(e.args) == 1 and not e.keywords:
                sep = T(recv, sc, d)
                arg = e.args[0]
                if isinstance(arg, (ast.List, ast.Tuple)) and not any(
                    isinstance(x, ast.Starred) for x in arg.elts
                ):
                    return sep.join(T(x, sc, d) for x in arg.elts)
                texts = self._element_texts(arg, sc, d)
                if texts is None:
                    return SYM_UNKNOWN
                return _summarize([*texts, sep], repeated=True)
            if attr == "format" and isinstance(recv, ast.Constant) and isinstance(recv.value, str):
                return self._format_text(e, recv, sc, d)
            if (
                attr == "format_map"
                and isinstance(recv, ast.Constant)
                and isinstance(recv.value, str)
                and not e.keywords
            ):
                named = self._named_arguments(e)
                if named is not None:
                    return self._format_text(e, recv, sc, d, keywords=named)
            if (
                attr in ("substitute", "safe_substitute")
                and isinstance(recv, ast.Call)
                and self.origin(recv.func, sc) == "string.Template"
                and len(recv.args) == 1
                and isinstance(recv.args[0], ast.Constant)
                and isinstance(recv.args[0].value, str)
            ):
                spelled = self._template_text(e, recv.args[0].value, sc, d)
                if spelled is not None:
                    return spelled
            if attr in FORMAT_METHODS:
                # ``format_map``, ``Template.substitute``, a format string held elsewhere: where
                # each argument lands is not read, so one that needs a position is placed nowhere
                return self._unplaced(
                    [*e.args, *(k.value for k in e.keywords)], sc, d, containers=True
                )
            if attr == "get" and 1 <= len(e.args) <= 2 and not e.keywords:
                texts = self._element_texts(recv, sc, d)
                if texts is None:
                    return SYM_UNKNOWN
                default = T(e.args[1], sc, d) if len(e.args) == 2 else SYM_DIGITS
                return _summarize([*texts, default])
            if attr in STR_METHODS and not e.keywords:
                base = T(recv, sc, d)
                # what a call can put INTO the string: ``replace``'s new text, the fill of
                # ``ljust`` / ``rjust`` / ``center``. One that may hold a character HTML reads
                # undoes the escaping (``escape(x).replace("&quot;", '"')``): placed nowhere
                put = e.args[1:2] if attr in ("replace", "ljust", "rjust", "center") else []
                for a in put:
                    t = T(a, sc, d)
                    if any(ch in _SPECIAL_TEXT | {"&"} for ch in t) or _text_class(t) not in (
                        SYM_DIGITS,
                        SYM_WORDS,
                        SYM_VALUE,
                        SYM_CSS,
                        SYM_ESCAPED,
                    ):
                        return SYM_RAW
                args = [a.value if isinstance(a, ast.Constant) else a for a in e.args]
                if any(not isinstance(a, (str, int)) for a in args) or any(
                    isinstance(a, str) and (set(a) & _CONTROL) for a in args
                ):
                    # what it removes or matches is not known: what it leaves is no more than
                    # what was there (the new text was checked above)
                    return _cut_class(_text_class(base))
                if attr in PURE_TRANSFORMS and args:
                    # ``strip(chars)`` removes characters, adds none
                    return _cut_class(_text_class(base))
                spellings = _expand(base)
                if spellings is None:
                    return SYM_UNKNOWN
                try:
                    return _summarize([getattr(str, attr)(t, *args) for t in spellings])
                except (TypeError, ValueError):
                    return SYM_UNKNOWN
        local = self._local_callee(e.func, sc)
        if local is not None:
            fn, fscope = local
            if self._actuals:
                # read inside a bound call: with NO binding in force (its parameters, and any it
                # closes over, unknown), so what a function's text is read as is remembered once
                # per call site, not once per path to it (#4145)
                saved, self._actuals = self._actuals, {}
                try:
                    return self._text_b(e, sc, d)
                finally:
                    self._actuals = saved
            # a function read again while it is being read: a recursion, handled like a value
            # that reaches itself in ``_text_b`` (its call sites alternate bound and unbound
            # reads, so the same key need not come round before the depth bound)
            outer = self._fns_reading.get(id(fn))
            if outer is not None:
                self._texts_recursed.add(outer)
                return SYM_UNKNOWN if outer in self._texts_pessimistic else SYM_MARKUP
            self._fns_reading[id(fn)] = (id(e), id(sc), self._binding_state())
            try:
                return self._local_text(local, e, sc, d)
            finally:
                self._fns_reading.pop(id(fn), None)
        return SYM_UNKNOWN

    def _local_text(self, local: tuple, e: ast.Call, sc: _Scope, d: int) -> str:
        """What a call of a function of this file returns, read with no binding in force: its
        parameters bound to THIS call's arguments (``_judge_returns``), or unknown for a call it
        cannot bind."""
        fn, fscope = local

        def read() -> str:
            values = [fn.body] if isinstance(fn, ast.Lambda) else self._live_returns(fn, fscope)
            return _summarize(
                [SYM_DIGITS if v is None else self._text_b(v, fscope, d) for v in values]
                or [SYM_DIGITS]
            )

        site = (id(e), id(sc))
        binding = self._site_bindings.get(site, False)
        if binding is False:
            binding = self._binding(local, e, sc)
            self._site_bindings[site] = binding
            if binding is not None:
                self._installed_bindings.append(binding)  # its id is part of memo keys
        if binding is None:
            return read()
        self._actuals[id(fscope)] = binding
        try:
            return read()
        finally:
            self._actuals.pop(id(fscope), None)

    def _misplaces(self, e: ast.expr, sc: _Scope) -> bool:
        """Pass B: the string ``e`` builds, read from element text, puts a part where it may not sit
        or behind a part whose effect it cannot read (:func:`_walk`, #3864). The first refusal is
        kept for the finding's message."""
        if not self.call_binding:
            return False
        text = self._text_b(e, sc)
        if _walk(text)[0]:
            return False
        if self._why_misplaced is None:
            self._why_misplaced = refusal(text)
        return True

    def _position_ok(self, kind: str, prefix: str) -> bool:
        """A ``kind`` of escaper output (or a string built from it, ``kind`` + ``"*"``) may sit after
        ``prefix``: text for ``"json"``; text or a quoted value for ``"sens"``; never an unquoted
        attribute or a tag name."""
        where = self._position(prefix)
        return where == "text" if kind.startswith("json") else where != "tag"

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

    @staticmethod
    def _html_context(prefix: str, quoted: bool = False, css: bool = False) -> str | None:
        """Where HTML escaping is NOT enough for the next field after ``prefix`` (#3864), or ``None``:
        ``"script"`` / ``"style"`` (the body of that element: entities are not decoded, the text is
        code), ``"handler"`` (the quoted value of an ``on*`` attribute: the HTML parser decodes
        ``&#x27;`` back to a quote before the script runs), ``"srcdoc"`` (decoded once, then parsed as a
        document) and ``"url"`` (the START of the quoted value of a URL attribute, or anywhere after a
        literal ``javascript:``: an escaped ``javascript:alert(1)`` is still that URL).

        It reads the literal text only (``\x00`` is a part it cannot read, taken as an ordinary
        character, so a URL attribute whose value starts with an unknown part is not "the start").
        Names are matched in lower case; an attribute name ends at white space, ``/``, ``=`` or ``>``,
        an unquoted value at white space or ``>``. ``"unquoted"`` is an unquoted attribute value
        (``title={escape(x)}``: a space in ``x`` starts a new attribute). A ``<script>`` whose literal
        ``type`` is a JSON data block is not a script body. With ``css`` (the position walk asks),
        the quoted value of a ``style`` attribute is ``"css"``: a CSS declaration list, where HTML
        escaping does not stop ``;`` or ``url(...)`` (#4212).
        """
        ws = " \t\n\r\f"
        i, n = 0, len(prefix)
        raw = ""  # the raw-text element whose body we are in
        inert = False  # ... and it is a JSON data block: never run, and ``<`` is escaped
        while i < n:
            if raw:
                # the element ends at ``</raw`` followed by white space, ``/`` or ``>`` only
                found = re.compile("</" + raw + r"[\t\n\f\r /]|</" + raw + ">", re.I).search(
                    prefix, i
                )
                if found is None:
                    return None if inert else raw
                i, raw = found.start(), ""
            while i < n and not (
                prefix[i] == "<"
                and (i + 1 == n or prefix[i + 1].isalpha() or prefix[i + 1] in "/!?")
            ):
                i += 1
            if i >= n:
                return None  # element text
            closing = prefix[i + 1 : i + 2] == "/"
            i += 2 if closing else 1
            start = i
            while i < n and prefix[i] not in ws + "/>":
                i += 1
            tag = prefix[start:i].lower()
            kind = None  # the ``type`` attribute's value, when the literal text shows it
            while True:  # the attributes of this tag
                while i < n and prefix[i] in ws + "/":
                    i += 1
                if i >= n:
                    return None  # inside the tag, between attributes
                if prefix[i] == ">":
                    i += 1
                    if not closing and tag in RAW_TEXT_ELEMENTS:
                        raw = tag
                        inert = tag == "script" and kind in DATA_BLOCK_TYPES
                    break
                start = i
                i += 1  # a name is at least one character (``=`` first is part of the name)
                while i < n and prefix[i] not in ws + "/>=":
                    i += 1
                attr = prefix[start:i].lower()
                while i < n and prefix[i] in ws:
                    i += 1
                if i >= n or prefix[i] != "=":
                    continue  # an attribute without a value
                i += 1
                while i < n and prefix[i] in ws:
                    i += 1
                if i >= n:
                    return "unquoted"  # the field starts an unquoted value
                if prefix[i] not in "\"'":
                    start = i
                    while i < n and prefix[i] not in ws + ">":
                        i += 1
                    if i >= n:
                        return "unquoted"  # the field continues one
                    if attr == "type" and kind is None:
                        kind = prefix[start:i].lower()
                    continue
                close = prefix.find(prefix[i], i + 1)
                if close >= 0:
                    if attr == "type" and kind is None:  # the first of a repeated name counts
                        kind = prefix[i + 1 : close].strip(ws).lower()
                    i = close + 1
                    continue
                value = prefix[i + 1 :]  # the field is inside this quoted value
                if len(attr) > 2 and attr.startswith("on"):
                    return "handler"
                if attr == "srcdoc":
                    return "srcdoc"
                # ``data-href`` too: the client follows it (a table row's link), as ``xlink:href``.
                # The value is read as the browser reads it: character references decoded
                # (``&#9;`` is a leading tab), white space and controls dropped from the scheme
                decoded = _unescape(value)
                scheme = re.sub(r"[\x01-\x20]", "", decoded).lower()
                if attr in URL_ATTRIBUTES or attr.endswith(("-href", ":href")):
                    if scheme.startswith(("javascript:", "vbscript:", "data:")):
                        # after a literal script or data scheme the rest of the value is code
                        # or a document: no escaper's output is safe there, a checked URL neither
                        return "url-code"
                    if _URL_LEAD.fullmatch(decoded) is not None:
                        return "url"
                if css and attr == "style":
                    return "css"
                return "value" if quoted else None
        return (None if inert else raw) or None

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

    def _call_clean(self, e: ast.Call, sc: _Scope, d: int, seen: frozenset) -> bool:
        if self._trusted_call(e, sc):
            return True
        name = _callee(e.func)
        if (
            isinstance(e.func, ast.Attribute)
            and (name == "join" or name in FORMAT_METHODS)
            and self._misplaces(e, sc)
        ):
            return False
        args = [*e.args, *(kw.value for kw in e.keywords)]
        origin = self.origin(e.func, sc)
        if origin in CLEAN_IF_ARGS_CLEAN:
            return all(self.clean(a, sc, d, seen) for a in args)
        if origin in JSON_ESCAPERS:
            return len(e.args) == 1 and self._json_dumped(e.args[0], sc, d, seen)
        local = self._local_function(e.func, sc)
        if local is not None:
            return self._returns_clean(local, d, seen)
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
            returns = [n for n in _own_nodes(fn) if isinstance(n, ast.Return)]
            return bool(returns) and all(
                r.value is not None and self._json_dumped(r.value, fscope, d + 1, seen | {key})
                for r in returns
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
        """:meth:`_resolve_local`, remembered."""
        key = (id(func), id(sc))
        hit = self._locals.get(key)
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
            if self._patched(func.id):
                return None  # ``sys.modules[__name__].helper = ...`` and kin (#3864)
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

    # -- a call's RESULT at a sink, judged with THIS call's arguments (#3864, #3845) ---------------
    #
    # Only ever adds a finding: ``tainted`` OR-s it with what it says without it, and ``clean`` never
    # reads a binding. So everything ``main``'s guard flags stays flagged.

    def _local_callee(self, func: ast.expr, sc: _Scope) -> tuple | None:
        """The function a call reaches for ``_returns_tainted``: ``_local_function``, or a lambda bound
        to a name once (``tag = lambda u: ...``), or a method of a fresh instance of a plain class of
        this file (``Card().tag(x)``: no base, metaclass or decorator, no ``__new__`` /
        ``__getattribute__``)."""
        local = self._local_function(func, sc)
        if local is not None:
            return local
        if isinstance(func, ast.Name):
            owner = sc.find(func.id)
            if (
                owner is None
                or func.id in owner.opaque
                or func.id in self.rebound_names
                or self.dynamic_globals
                or self._patched(func.id)
                or owner.def_nodes.get(func.id)
                or owner.contents.get(func.id)
                or owner.pieces.get(func.id)
            ):
                return None
            bound = owner.assigns.get(func.id, [])
            if len(bound) == 1 and isinstance(bound[0], ast.Lambda):
                fscope = self.scope_of.get(id(bound[0]))
                return (bound[0], fscope) if fscope is not None else None
            return None
        if not (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Call)
            and isinstance(func.value.func, ast.Name)
            and not self._mutated_or_rebound(func.attr)
        ):
            return None
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
        cls = self.scope_of.get(id(cnode))
        if (
            cls is None
            or cnode.bases
            or cnode.keywords
            or cnode.decorator_list
            or self._class_dynamic(cls)
            or any(n in cls.assigns or n in cls.def_nodes for n in ("__new__", "__getattribute__"))
            or func.attr in self.assigned_attrs
            or not self._defined_unconditionally(cls, func.attr)
        ):
            return None
        defs = cls.def_nodes.get(func.attr, [])
        if (
            len(defs) != 1
            or len(cls.assigns.get(func.attr, [])) != 1
            or not isinstance(defs[0], (ast.FunctionDef, ast.AsyncFunctionDef))
            or any(
                not (isinstance(d, ast.Name) and d.id in ("staticmethod", "classmethod"))
                for d in defs[0].decorator_list
            )
            or any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in _own_nodes(defs[0]))
        ):
            return None
        return defs[0], self.scope_of[id(defs[0])]

    def _returns_tainted(
        self, local: tuple, call: ast.Call, sc: _Scope, d: int, seen: frozenset
    ) -> bool:
        """What THIS call returns is a string built from a raw value, or places an escaper's output
        where it does not belong, judged with its parameters bound to its arguments; a call it cannot
        bind (``*args`` / ``**kw``, an argument that does not fit, a recursive call) is judged with
        them raw."""
        fn, fscope = local
        key = ("returns-tainted", id(fn), id(call))
        if key in seen:
            return False  # a recursive call adds nothing new
        if d > _DEPTH:
            return True  # beyond what it can follow, flag
        return self._judge_returns(local, call, sc, d, seen | {key})

    def _judge_returns(
        self, local: tuple, call: ast.Call, sc: _Scope, d: int, inner: frozenset
    ) -> bool:
        fn, fscope = local

        def judge() -> bool:
            return any(
                v is not None and self.tainted(v, fscope, d + 1, inner)
                for v in self._live_returns(fn, fscope)
            )

        binding = self._binding(local, call, sc)
        if binding is None:
            return self._unbound(fscope, judge)
        self._installed_bindings.append(binding)  # its id is part of memo keys (#4145)
        self._actuals[id(fscope)] = binding
        try:
            return judge()
        finally:
            self._actuals.pop(id(fscope), None)

    def _binding(self, local: tuple, call: ast.Call, sc: _Scope) -> dict[str, object] | None:
        """Each parameter of the function -> the argument (or default) THIS call gives it, as an
        ``_Actual``; ``None`` for a call that cannot be bound or a function already bound by an outer
        call being judged (recursion). ``self`` / ``cls``, ``*args`` and ``**kw`` stay raw."""
        fn, fscope = local
        if id(fscope) in self._actuals or self._has_star(call):
            return None
        a = fn.args
        positional = [*a.posonlyargs, *a.args]
        implicit = self._implicit_arguments(fn, call)
        names = [p.arg for p in positional][implicit:]
        if len(call.args) > len(names) and a.vararg is None:
            return None
        binding: dict[str, object] = {}
        for param, value in zip(names, call.args):
            binding[param] = _Actual(value, sc)
        by_keyword = {p.arg for p in [*a.args, *a.kwonlyargs]} - {
            p.arg for p in positional[:implicit]
        }
        for kw in call.keywords:
            if kw.arg in by_keyword and kw.arg not in binding:
                binding[kw.arg] = _Actual(kw.value, sc)
            elif kw.arg in by_keyword or a.kwarg is None:
                return None  # given twice, or a keyword it does not take: the call raises
        holder = fscope.parent or fscope  # where the ``def`` (and its defaults) is evaluated
        every = [p.arg for p in positional]
        defaults = dict(zip(every[len(every) - len(a.defaults) :], a.defaults))
        for p, default in zip(a.kwonlyargs, a.kw_defaults):
            if default is not None:
                defaults[p.arg] = default
        for param in [*names, *(p.arg for p in a.kwonlyargs)]:
            if param not in binding:
                if param not in defaults:
                    return None  # a required argument is missing: the call raises
                binding[param] = _Actual(defaults[param], holder)
        return {k: v for k, v in binding.items() if isinstance(fscope.params.get(k), _Param)}

    @staticmethod
    def _implicit_arguments(fn: ast.AST, call: ast.Call) -> int:
        """How many leading parameters the call fills itself: ``self`` of ``self.m(..)`` / ``C().m(..)``,
        ``cls`` of a ``classmethod``; none for a function, a ``staticmethod`` or ``C.m(obj, ..)``."""
        if not isinstance(call.func, ast.Attribute):
            return 0
        decorators = {d.id for d in getattr(fn, "decorator_list", []) if isinstance(d, ast.Name)}
        if "staticmethod" in decorators:
            return 0
        if "classmethod" in decorators:
            return 1
        receiver = call.func.value
        if isinstance(receiver, ast.Call):
            return 1
        return 1 if isinstance(receiver, ast.Name) and receiver.id == "self" else 0

    def _unbound(self, fscope: _Scope, judge):
        """``judge()`` with ``fscope``'s parameters raw (a recursive call must not borrow the binding
        of the outer call)."""
        saved = self._actuals.pop(id(fscope), None)
        try:
            return judge()
        finally:
            if saved is not None:
                self._actuals[id(fscope)] = saved

    def _feasible(self, owner: _Scope, d: _Def) -> bool:
        """Whether the binding ``d`` can happen under the call binding in force: not when an ``if``
        around it tests a parameter that THIS call binds to a literal the test rules out (``if wrap is
        not None:`` of a call that leaves ``wrap=None``). Only a parameter nothing rebinds, and only
        ``p``, ``not p``, ``p is None``, ``p is not None``."""
        bound = self._actuals.get(id(owner))
        if not bound or d.site is None:
            return True
        return self._site_feasible(owner, d.site, bound)

    def _site_feasible(self, owner: _Scope, site: int, bound: dict[str, object]) -> bool:
        for param, kind, holds in self._branch_facts(owner).get(site, ()):
            value = bound.get(param)
            if not (isinstance(value, _Actual) and isinstance(value.expr, ast.Constant)):
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
        """id(node) -> the parameter tests of the ``if`` statements around it in ``fscope``'s own body:
        ``(param, kind, holds)``; ``holds`` is ``False`` in an ``else``."""
        cached = self._facts.get(id(fscope))
        if cached is not None:
            return cached
        out: dict[int, tuple[tuple[str, str, bool], ...]] = {}
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

        if isinstance(fscope.node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for stmt in fscope.node.body:
                mark(stmt, ())
        self._facts[id(fscope)] = out
        return out

    def _live_returns(self, fn: ast.AST, fscope: _Scope) -> list[ast.expr | None]:
        """What the function can return under the call binding in force (``_feasible``)."""
        if isinstance(fn, ast.Lambda):
            return [fn.body]
        bound = self._actuals.get(id(fscope))
        return [
            n.value
            for n in _own_nodes(fn)
            if isinstance(n, ast.Return)
            and (not bound or self._site_feasible(fscope, id(n), bound))
        ]

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
                live = [x for x in hit[1] if self._feasible(owner, x)]
                return self._unvouched(
                    name, self._actual(name, owner, [x.value for x in live]), owner, sc
                ), tuple(sorted(id(x) for x in live))
        values = self._actual(name, owner, list(owner.assigns.get(name, [])))
        if name in owner.opaque:
            values.append(RAW)
        return self._unvouched(name, values, owner, sc), None

    def _actual(self, name: str, owner: _Scope, values: list[object]) -> list[object]:
        """``values`` with a parameter's entry binding replaced by the argument of the call being
        judged (``_specialized``)."""
        bound = self._actuals.get(id(owner))
        if not bound or name not in bound:
            return values
        entry = owner.params.get(name)
        if not isinstance(entry, _Param):
            return values
        return [bound[name] if v is entry else v for v in values]

    def _ctx(self, owner: _Scope) -> int:
        """Tells apart judgements of one name under different call bindings (cycle keys)."""
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
        if owner.pieces.get(name) and self._misplaces(node, sc):
            return False
        return all(self.clean(v, owner, d, seen) for v in values) and self._raw_all(
            owner,
            name,
            lambda: (
                all(self.clean(p, owner, d, seen) for p in owner.pieces.get(name, []))
                and all(self.clean(c, owner, d, seen) for c in owner.contents.get(name, []))
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
            if self._misplaces(e, sc):
                return True
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
            local = self._local_callee(e.func, sc) if self.call_binding else None
            if local is not None and self._returns_tainted(local, e, sc, d, seen):
                return (
                    True  # a function of this file builds it out of what this call passes (#3864)
                )
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
                    if self._misplaces(e, sc):
                        return True
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
        if owner.pieces.get(name) and self._misplaces(node, sc):
            return True
        return any(self.tainted(v, owner, d, seen) for v in values) or self._raw_all(
            owner,
            name,
            lambda: (
                any(not self.clean(p, owner, d, seen) for p in owner.pieces.get(name, []))
                or any(self.tainted(c, owner, d, seen) for c in owner.contents.get(name, []))
            ),
        )

    def _raw_all(self, owner: _Scope, name: str, judge):
        """``judge()`` with every call binding off, unless ``name`` is a container its function makes
        afresh on every call: a module list, a mutable default, a parameter or a closure's list keeps
        what EVERY call put there, so it is never judged as what one call passes."""
        if not self._actuals or self._fresh(owner, name):
            return judge()
        saved = self._actuals
        self._actuals = {}
        try:
            return judge()
        finally:
            self._actuals = saved

    def _fresh(self, owner: _Scope, name: str) -> bool:
        """``name`` is a local of a function, never a parameter, bound only to new displays or
        literals: each call starts its own (``parts = []``, ``s = ""``)."""
        if not isinstance(owner.node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return False
        if name in owner.params or name in owner.opaque or name in self.rebound_names:
            return False
        values = owner.assigns.get(name, [])
        return bool(values) and all(
            isinstance(v, (ast.List, ast.Dict, ast.Set, ast.Constant, ast.ListComp, ast.DictComp))
            for v in values
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
        value = ast.Constant("")
        # pass B reads its text as the members of the collection the test names (#3864)
        self.mod._narrowed_values[id(value)] = (value, key, self.scope)
        new[name.id] = frozenset(
            {self._def(("narrow", id(key), name.id), name.id, value, counted=False)}
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
    # Two analyzers that share NOTHING (#3864): pass A is the guard as it was, in which no call is
    # ever bound; pass B adds the judgement of a sink handed a call, with its own copy of every
    # table and cache. A finding of either is a finding, so whatever pass B does, the result holds
    # every finding pass A makes.
    first = _Module(source, module, is_package, dynamic_bases).findings()
    second = _Module(source, module, is_package, dynamic_bases, call_binding=True).findings()
    # one finding per sink: pass B's says where a part lands when that is why it flags
    merged = {(f.lineno, f.function, f.sink): f for f in first}
    for f in second:
        key = (f.lineno, f.function, f.sink)
        if key not in merged or "[position:" in f.why:
            merged[key] = f
    return sorted(merged.values(), key=lambda f: (f.lineno, f.function, f.sink, f.why))


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
