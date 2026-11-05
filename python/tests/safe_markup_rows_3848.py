"""Directed rows for the review of #3848: label -> (source, flagged?).

A ``True`` row is code that is unsafe or that the guard must not clear; a ``False`` row is its clean
twin. ``test_safe_markup_guard_3848.py`` checks every row, and ``test_safe_markup_guard_3820.py`` counts
the ``True`` rows among the directed rows each broken analysis must trip over.
"""

ROWS: dict[str, tuple[str, bool]] = {}


def row(label: str, source: str, flagged: bool) -> None:
    assert label not in ROWS, label
    ROWS[label] = (source, flagged)


# --- narrowing: equality is narrowing only against a LITERAL -----------------------------
row(
    "equality with another variable is no narrowing",
    """
def f(raw, other):
    if raw == other:
        return mark_safe(f"<b>{raw}</b>")
    return ""
""",
    True,
)
row(
    "equality with a call is no narrowing",
    """
def f(raw):
    if raw == build():
        return mark_safe(f"<b>{raw}</b>")
    return ""
""",
    True,
)
row(
    "equality with a literal narrows",
    """
def f(raw):
    if raw == "ok":
        return mark_safe(f"<b>{raw}</b>")
    return ""
""",
    False,
)

# --- an escaper import that is rebound is not that escaper -------------------------------
row(
    "an escaper rebound at module level",
    """
escape = lambda v: v
def f(raw):
    return mark_safe(f"<b>{escape(raw)}</b>")
""",
    True,
)
row(
    "an escaper rebound from another function through global",
    """
from django.utils.html import escape
def patch():
    global escape
    escape = lambda v: v
def f(raw):
    patch()
    return mark_safe(f"<b>{escape(raw)}</b>")
""",
    True,
)
row(
    "an escaper replaced through globals()",
    """
from django.utils.html import escape
def f(raw):
    globals()["escape"] = lambda v: v
    return mark_safe(f"<b>{escape(raw)}</b>")
""",
    True,
)
row(
    "an escaper that nothing rebinds",
    """
from django.utils.html import escape
def f(raw):
    return mark_safe(f"<b>{escape(raw)}</b>")
""",
    False,
)

# --- wrappers around a sink: data flow, not a shape -------------------------------------
row(
    "a wrapper that strips, aliases and then marks",
    """
def _ms(v):
    x = v.strip()
    y = x
    return mark_safe(y)
def f(raw):
    return _ms(f"<b>{raw}</b>")
""",
    True,
)
row(
    "a lambda wrapper",
    """
_ms = lambda v: mark_safe(v)
def f(raw):
    return _ms(f"<b>{raw}</b>")
""",
    True,
)
row(
    "a wrapper that stringifies",
    """
def _ms(v):
    return mark_safe(str(v))
def f(raw):
    return _ms(f"<b>{raw}</b>")
""",
    True,
)
row(
    "a wrapper over *args",
    """
def _ms(*parts):
    return mark_safe(parts[0])
def f(raw):
    return _ms(f"<b>{raw}</b>")
""",
    True,
)
row(
    "a wrapper that casts",
    """
from typing import cast
def _ms(v):
    return cast(str, mark_safe(v))
def f(raw):
    return _ms(f"<b>{raw}</b>")
""",
    True,
)
row(
    "a partial wrapper",
    """
from functools import partial
_ms = partial(mark_safe)
def f(raw):
    return _ms(f"<b>{raw}</b>")
""",
    True,
)
row(
    "a function that BUILDS what it marks is not a wrapper",
    """
def bold(v):
    return mark_safe(f"<b>{escape(v)}</b>")
def f(raw):
    return bold(f"{raw}")
""",
    False,
)
row(
    "a function with other returns is not a wrapper",
    """
def pick(v, k):
    if k:
        return mark_safe(v)
    return "no"
def f(raw):
    return pick(f"<b>{raw}</b>", 1)
""",
    False,
)

# --- depth: beyond what it can follow, flag ----------------------------------------------
_CHAIN = "\n".join(
    ["def f(raw):", "    a0 = f'<b>{raw}</b>'"]
    + [f"    a{i + 1} = a{i}" for i in range(60)]
    + ["    return mark_safe(a60)"]
)
row("a 60-hop alias chain", _CHAIN, True)
_NESTED = (
    "def f(raw, c):\n    return mark_safe("
    + "(" * 55
    + "f'<b>{raw}</b>'"
    + " if c else 'x')" * 55
    + ")"
)
row("a 55-deep conditional expression", _NESTED, True)
_CALLS = "def f(raw):\n    return mark_safe(" + "str(" * 55 + "f'<b>{raw}</b>'" + ")" * 55 + ")"
row("a 55-deep call chain", _CALLS, True)

# --- except*: the handlers run one after another -----------------------------------------
row(
    "a binding in an earlier except* handler is seen by a later one",
    """
def f(raw):
    w = 1
    try:
        g()
    except* ValueError:
        w = raw
    except* TypeError:
        return mark_safe(f"<b>{w}</b>")
    return ""
""",
    True,
)

# --- escapers whose output is only clean in a position -----------------------------------
row(
    "attr_name is a legal NAME, not a cleared one",
    """
from djust._html import attr_name
def f(key):
    return mark_safe(f"<input {attr_name(key)}=1>")
""",
    True,
)
row(
    "escape_json_for_script on a value that is not json.dumps output",
    """
from djust.security.json_script import escape_json_for_script
def f(raw):
    return mark_safe(f"<script>{escape_json_for_script(raw)}</script>")
""",
    True,
)
row(
    "escape_json_for_script on json.dumps output in script text",
    """
import json
from djust.security.json_script import escape_json_for_script
def f(raw):
    return mark_safe(f"<script>{escape_json_for_script(json.dumps(raw))}</script>")
""",
    False,
)
row(
    "escape_json_for_script on json.dumps output inside a quoted attribute",
    """
import json
from djust.security.json_script import escape_json_for_script
def f(raw):
    return mark_safe(f'<div data-x="{escape_json_for_script(json.dumps(raw))}">')
""",
    True,
)
row(
    "safe_url in an unquoted attribute",
    """
from djust.components.utils import safe_url
def f(raw):
    return mark_safe(f"<a href={safe_url(raw)}>x</a>")
""",
    True,
)
row(
    "safe_url held in a name, in an unquoted attribute",
    """
from djust.components.utils import safe_url
def f(raw):
    u = safe_url(raw)
    return mark_safe(f"<a href={u}>x</a>")
""",
    True,
)
row(
    "safe_url in a quoted attribute",
    """
from djust.components.utils import safe_url
def f(raw):
    return mark_safe(f'<a href="{safe_url(raw)}">x</a>')
""",
    False,
)
row(
    "safe_url in element text",
    """
from djust.components.utils import safe_url
def f(raw):
    return mark_safe(f"<p>{safe_url(raw)}</p>")
""",
    False,
)
row(
    "safe_url in a single-quoted attribute after other fields",
    """
from djust.components.utils import safe_url
def f(raw, cls):
    return mark_safe(f"<a class='{escape(cls)}' href='{safe_url(raw)}'>x</a>")
""",
    False,
)
row(
    "safe_css_color with its default hidden in *args",
    """
from djust.components.utils import safe_css_color
def f(args):
    return mark_safe(f'<i style="color:{safe_css_color(*args)}">')
""",
    True,
)
row(
    "safe_css_color with its default hidden in **kwargs",
    """
from djust.components.utils import safe_css_color
def f(raw, kw):
    return mark_safe(f'<i style="color:{safe_css_color(raw, **kw)}">')
""",
    True,
)

# --- containers changed behind a constant's back ---------------------------------------
row(
    "a module table changed in another function, then read",
    """
TABLE = {"a": "<i>"}
def put(k, v):
    TABLE[k] = v
def f(raw):
    put("z", raw)
    return mark_safe(f"<b>{TABLE['z']}</b>")
""",
    True,
)
row(
    "a module table nothing changes",
    """
TABLE = {"a": "<i>"}
def f(k):
    return mark_safe(f"<b>{TABLE.get(k, '')}</b>")
""",
    False,
)
row(
    "a module list appended to in another function",
    """
PARTS = []
def add(x):
    PARTS.append(f"<i>{x}</i>")
def out():
    return mark_safe("".join(PARTS))
""",
    True,
)
row(
    "a table handed to a function that may change it",
    """
TABLE = {"a": "<i>"}
def f(raw):
    grow(TABLE, raw)
    return mark_safe(f"<b>{TABLE['z']}</b>")
""",
    True,
)
row(
    "a table returned from a function",
    """
TABLE = {"a": "<i>"}
def table():
    return TABLE
def f(raw):
    table()["z"] = raw
    return mark_safe(f"<b>{TABLE['z']}</b>")
""",
    True,
)
row(
    "a narrowing set that is aliased",
    """
ALLOWED = {"a"}
def f(raw):
    s = ALLOWED
    s.add(raw)
    if raw in ALLOWED:
        return mark_safe(f"<b>{raw}</b>")
    return ""
""",
    True,
)
row(
    "a narrowing set that is only read",
    """
ALLOWED = {"a", "b"}
def f(raw):
    if raw in ALLOWED:
        return mark_safe(f"<b>{raw}</b>")
    return ""
""",
    False,
)
row(
    "a narrowing set unioned in place",
    """
ALLOWED = {"a"}
def add(x):
    global ALLOWED
    ALLOWED |= {x}
def f(raw):
    add(raw)
    if raw in ALLOWED:
        return mark_safe(f"<b>{raw}</b>")
    return ""
""",
    True,
)
row(
    "a local list of constants that is only joined",
    """
def f():
    parts = ["<i>", "<b>"]
    return mark_safe("".join(parts))
""",
    False,
)
row(
    "a local list handed to another function",
    """
def f(raw):
    parts = ["<i>"]
    fill(parts, raw)
    return mark_safe("".join(parts))
""",
    True,
)

# --- helpers: rebinding, dynamic dispatch, class scope -----------------------------------
row(
    "a helper rebound from another function",
    """
from django.utils.html import escape
def helper(x):
    return escape(x)
def patch():
    global helper
    helper = lambda v: v
def f(raw):
    patch()
    return mark_safe("<b>" + helper(raw) + "</b>")
""",
    True,
)
row(
    "a bare name in a method is the module's, not the class's",
    """
def helper(x):
    return x
class K:
    def helper(self, x):
        return escape(x)
    def m(self, raw):
        return mark_safe("<b>" + helper(raw) + "</b>")
""",
    True,
)
row(
    "a method the class defines and nothing replaces",
    """
class K:
    def helper(self, x):
        return escape(x)
    def m(self, raw):
        return mark_safe("<b>" + self.helper(raw) + "</b>")
""",
    False,
)
row(
    "a class attribute read through self, with setattr in the file",
    """
class K:
    LABEL = "ok"
    def r(self):
        return mark_safe(f"<b>{self.LABEL}</b>")
def tweak(o, v):
    setattr(o, "LABEL", v)
""",
    True,
)
row(
    "a class attribute read through self, nothing writes it",
    """
class K:
    LABEL = "ok"
    def r(self):
        return mark_safe(f"<b>{self.LABEL}</b>")
""",
    False,
)
row(
    "a class table changed through self",
    """
class K:
    ICONS = {"a": "<svg/>"}
    def register(self, name, svg):
        self.ICONS[name] = svg
    def r(self, name):
        return mark_safe(f"<i>{self.ICONS[name]}</i>")
""",
    True,
)
row(
    "a class table changed through cls",
    """
class K:
    ICONS = {"a": "<svg/>"}
    @classmethod
    def register(cls, name, svg):
        cls.ICONS.update({name: svg})
    def r(self, name):
        return mark_safe(f"<i>{self.ICONS[name]}</i>")
""",
    True,
)
row(
    "a class table only read",
    """
class K:
    ICONS = {"a": "<svg/>"}
    def r(self, name):
        return mark_safe(f"<i>{self.ICONS.get(name, '')}</i>")
""",
    False,
)

# --- cheap builders that carry the argument into the string ------------------------------
row(
    "a template with a placeholder filled by replace()",
    """
TEMPLATE = "<b>@@</b>"
def f(raw):
    return mark_safe(TEMPLATE.replace("@@", raw))
""",
    True,
)
row(
    "a template with a placeholder filled by an escaped value",
    """
TEMPLATE = "<b>@@</b>"
def f(raw):
    return mark_safe(TEMPLATE.replace("@@", escape(raw)))
""",
    False,
)
row(
    "re.sub with a raw replacement",
    """
import re
def f(raw):
    return mark_safe(re.sub("@@", raw, "<b>@@</b>"))
""",
    True,
)
row(
    "a string repeated a raw number of times is still a number",
    """
def f(n):
    return mark_safe("<i></i>" * int(n))
""",
    False,
)
row(
    "str.__add__",
    """
def f(raw):
    return mark_safe("<b>".__add__(raw))
""",
    True,
)
row(
    "dict.update with keyword arguments",
    """
def f(raw):
    d = {"a": "<i>"}
    d.update(k=raw)
    return mark_safe(f"<b>{d['k']}</b>")
""",
    True,
)
row(
    "dict.setdefault with a raw default",
    """
def f(raw):
    d = {"a": "<i>"}
    d.setdefault("k", raw)
    return mark_safe(f"<b>{d['k']}</b>")
""",
    True,
)
row(
    "|= on a dict",
    """
def f(raw):
    d = {"a": "<i>"}
    d |= {"k": raw}
    return mark_safe(f"<b>{d['k']}</b>")
""",
    True,
)
row(
    "+= of another list",
    """
def f(raw):
    items = ["<i>"]
    more = [raw]
    items += more
    return mark_safe("".join(items))
""",
    True,
)
row(
    "an alias made by tuple assignment",
    """
a, b = mark_safe, str
def f(raw):
    return a(f"<b>{raw}</b>")
""",
    True,
)
