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
    "a class attribute read through self, a class in the file defines __getattribute__",
    """
class Spy:
    def __getattribute__(self, name):
        return object.__getattribute__(self, name)
class K:
    LABEL = "ok"
    def r(self):
        return mark_safe(f"<b>{self.LABEL}</b>")
""",
    True,
)
row(
    "a class attribute read through self, a class in the file defines __getattr__",
    # __getattr__ runs only for a name normal lookup did not find: it cannot replace LABEL
    """
class Proxy:
    def __init__(self, inner):
        self._inner = inner
    def __getattr__(self, name):
        return getattr(self._inner, name)
class K:
    LABEL = "ok"
    def r(self):
        return mark_safe(f"<b>{self.LABEL}</b>")
""",
    False,
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

# --- #3850 item 2: the two source rules no earlier row isolated --------------------------
# ``escaped_elements``: an ELEMENT of a table is handed on (``inner = T["a"]``), so something else can
# change the list inside it; the append goes through the alias, never through ``T``.
row(
    "an inner list of a table handed on and appended to",
    """
T = {"a": ["<i>"]}
def f(raw):
    inner = T["a"]
    inner.append(raw)
    return mark_safe("".join(T["a"]))
""",
    True,
)
row(
    "an inner list of a table only read in place",
    """
T = {"a": ["<i>"]}
def f(raw):
    return mark_safe("".join(T["a"]) + escape(raw))
""",
    False,
)
# ``setdefault`` in MUTATORS: ``T.setdefault(k)`` (one argument) adds the key ``k`` with ``None``; the
# two-argument form is also caught as a ``contents`` write, this one is not.
row(
    "setdefault with one argument adds a key to a membership table",
    """
ALLOWED = {"a": 1}
def add(k):
    ALLOWED.setdefault(k)
def f(raw):
    add(raw)
    if raw in ALLOWED:
        return mark_safe(f"<b>{raw}</b>")
    return ""
""",
    True,
)
row(
    "a membership table nothing adds a key to",
    """
ALLOWED = {"a": 1}
def add(k):
    return ALLOWED.get(k)
def f(raw):
    add(raw)
    if raw in ALLOWED:
        return mark_safe(f"<b>{raw}</b>")
    return ""
""",
    False,
)
row(
    "setdefault with one argument on a class table reached through self",
    """
class C:
    ALLOWED = {"a": 1}
    def add(self, k):
        self.ALLOWED.setdefault(k)
    def r(self, raw):
        self.add(raw)
        if raw in self.ALLOWED:
            return mark_safe(f"<b>{raw}</b>")
        return ""
""",
    True,
)

# --- #3850 item 3: a class attribute the class body may not define --------------------------
# ``__getattr__`` (or a base class) supplies the value when the body did not define the name, so only an
# unconditional, top-level, never-deleted assignment is the value.
row(
    "a conditionally defined class attribute and a __getattr__",
    """
class C:
    if FLAG:
        LABEL = "ok"
    def __getattr__(self, name):
        return DATA
    def r(self):
        return mark_safe(f"<b>{self.LABEL}</b>")
""",
    True,
)
row(
    "an unconditionally defined class attribute and a __getattr__",
    """
class C:
    LABEL = "ok"
    def __getattr__(self, name):
        return DATA
    def r(self):
        return mark_safe(f"<b>{self.LABEL}</b>")
""",
    False,
)
row(
    "a class attribute deleted from outside and a __getattr__",
    """
class C:
    LABEL = "ok"
    def __getattr__(self, name):
        return DATA
    def r(self):
        return mark_safe(f"<b>{self.LABEL}</b>")
del C.LABEL
""",
    True,
)
row(
    "a class attribute deleted in the class body and a __getattr__",
    """
class C:
    LABEL = "ok"
    del LABEL
    def __getattr__(self, name):
        return DATA
    def r(self):
        return mark_safe(f"<b>{self.LABEL}</b>")
""",
    True,
)
row(
    "a conditionally defined class attribute of a class with a base",
    """
class C(Base):
    if FLAG:
        LABEL = "ok"
    def r(self):
        return mark_safe(f"<b>{self.LABEL}</b>")
""",
    True,
)
row(
    "a conditionally defined class attribute of a class with no base and no fall-through",
    """
class C:
    if FLAG:
        LABEL = "ok"
    def r(self):
        return mark_safe(f"<b>{self.LABEL}</b>")
""",
    False,
)
row(
    "a conditionally defined method and a __getattr__",
    """
class C:
    if FLAG:
        def label(self):
            return "ok"
    def __getattr__(self, name):
        return lambda: DATA
    def r(self):
        return mark_safe(f"<b>{self.label()}</b>")
""",
    True,
)

# --- #3850 item 1: a context-sensitive escaper's output is not clean in every position -------
# ``safe_url`` / ``url_attr`` are clean in text or inside a quoted attribute value, never in an unquoted
# attribute or a tag name; ``escape_json_for_script`` only in script text. Every way of building the
# string is judged by the literal text in front of the value, not only the f-string.
_URL = "from djust.components.utils import safe_url\n"
_JSON = "import json\nfrom djust.security.json_script import escape_json_for_script\n"
for _label, _body, _bad in (
    ("concatenation", '"<a href=" + safe_url(x) + ">go</a>"', True),
    ("concatenation into a quoted value", '\'<a href="\' + safe_url(x) + \'">go</a>\'', False),
    ("concatenation into text", '"<p>" + safe_url(x) + "</p>"', False),
    ("a % format string", '"<a href=%s>go</a>" % safe_url(x)', True),
    ("a % format string into a quoted value", '\'<a href="%s">go</a>\' % safe_url(x)', False),
    ("str.format", '"<a href={}>go</a>".format(safe_url(x))', True),
    ("str.format into a quoted value", '\'<a href="{}">go</a>\'.format(safe_url(x))', False),
    ("str.format by name", '"<a href={u}>go</a>".format(u=safe_url(x))', True),
    ("join", '"<a href=" + ",".join(safe_url(y) for y in x) + ">"', True),
):
    row(
        f"safe_url in an unquoted attribute by {_label}" if _bad else f"safe_url kept by {_label}",
        f"{_URL}def f(x):\n    return mark_safe({_body})\n",
        _bad,
    )
row(
    "safe_url after a prefix held in a variable",
    f"{_URL}def f(x):\n    p = '<a href='\n    return mark_safe(p + safe_url(x))\n",
    True,
)
row(
    "safe_url after a prefix held in a variable, in an f-string",
    f'{_URL}def f(x):\n    p = "<a href="\n    return mark_safe(f"{{p}}{{safe_url(x)}}")\n',
    True,
)
row(
    "safe_url after a quoted prefix held in a variable",
    f"{_URL}def f(x):\n    p = '<a href=\"'\n    return mark_safe(p + safe_url(x) + '\">')\n",
    False,
)
row(
    "safe_url held in a variable and placed in an unquoted attribute",
    f'{_URL}def f(x):\n    u = safe_url(x)\n    return mark_safe("<a href=" + u + ">go</a>")\n',
    True,
)
row(
    "safe_url chosen by a conditional expression and placed in an unquoted attribute",
    f'{_URL}def f(x):\n    u = safe_url(x) if x else "#"\n    return mark_safe(f"<a href={{u}}>go</a>")\n',
    True,
)
row(
    "safe_url chosen by a conditional expression and placed in a quoted attribute",
    f'{_URL}def f(x):\n    u = safe_url(x) if x else "#"\n    return mark_safe(f\'<a href="{{u}}">go</a>\')\n',
    False,
)
row(
    "safe_url passed through an escaped earlier attribute into a quoted one",
    f"{_URL}def f(x, y):\n    return mark_safe('<a title=\"' + escape(y) + '\" href=\"' + safe_url(x) + '\">')\n",
    False,
)
row(
    "escape_json_for_script concatenated into an attribute",
    f"{_JSON}def f(x):\n    return mark_safe('<div data-x=\"' + escape_json_for_script(json.dumps(x)) + '\">')\n",
    True,
)
row(
    "escape_json_for_script concatenated into script text",
    f'{_JSON}def f(x):\n    return mark_safe("<script>" + escape_json_for_script(json.dumps(x)) + "</script>")\n',
    False,
)
row(
    "a conditionally defined class attribute of a class with a metaclass",
    """
class C(metaclass=Meta):
    if FLAG:
        LABEL = "ok"
    def r(self):
        return mark_safe(f"<b>{self.LABEL}</b>")
""",
    True,
)
row(
    "a conditionally defined class attribute and a __getattr__ assigned in the body",
    """
class C:
    if FLAG:
        LABEL = "ok"
    __getattr__ = handler
    def r(self):
        return mark_safe(f"<b>{self.LABEL}</b>")
""",
    True,
)
row(
    "a % format string whose conversions do not pair with its arguments",
    f"{_URL}def f(x):\n    return mark_safe('<a href=\"%s\" title=%s>' % (safe_url(x),))\n",
    True,
)
row(
    "a helper that returns safe_url, placed in an unquoted attribute",
    f'{_URL}def link(p):\n    return safe_url(p)\ndef f(x):\n    return mark_safe(f"<a href={{link(x)}}>go</a>")\n',
    True,
)
row(
    "safe_url chosen between a direct call and a helper that returns it (the position check stops at the call)",
    f'{_URL}def link(p):\n    return safe_url(p)\ndef f(x, c):\n    u = safe_url(x) if c else link(x)\n    return mark_safe(f\'<a href="{{u}}">go</a>\')\n',
    True,
)

# --- #3850 round 2: a string BUILT from an escaper's output is judged where it is placed ------
# (each run with x = "x onmouseover=alert(1)" is unsafe at runtime; the guard used to clear them)
_COMPOSITES = {
    "a helper that returns '' + safe_url(x), in an unquoted attribute": (
        "def g(x):\n    return '' + safe_url(x)\n"
        "def f(x):\n    return mark_safe(f'<a href={g(x)}>x</a>')\n"
    ),
    "'' + safe_url(x) held in a name, concatenated into an unquoted attribute": (
        "def f(x):\n    u = '' + safe_url(x)\n    return mark_safe('<a href=' + u + '>')\n"
    ),
    "'%s' % safe_url(x) held in a name": (
        "def f(x):\n    u = '%s' % safe_url(x)\n    return mark_safe('<a href=' + u + '>')\n"
    ),
    "f'{safe_url(x)}' held in a name": (
        "def f(x):\n    u = f'{safe_url(x)}'\n    return mark_safe('<a href=' + u + '>')\n"
    ),
    "'{}'.format(safe_url(x)) held in a name": (
        "def f(x):\n    u = '{}'.format(safe_url(x))\n    return mark_safe('<a href=' + u + '>')\n"
    ),
    "a % string as an f-string field": (
        "def f(x):\n    return mark_safe(f'<a href={\"%s\" % safe_url(x)}>')\n"
    ),
    "a % string as a + operand": (
        "def f(x):\n    return mark_safe('<a href=' + ('%s' % safe_url(x)))\n"
    ),
    "a + chain inside a % argument": (
        "def f(x):\n    return mark_safe('<a href=%s>' % ('' + safe_url(x)))\n"
    ),
    "a + chain inside a .format argument": (
        "def f(x):\n    return mark_safe('<a href={}>'.format('' + safe_url(x)))\n"
    ),
    "an f-string inside an f-string field": (
        "def f(x):\n    return mark_safe(f\"<a href={f'{safe_url(x)}'}>\")\n"
    ),
    "a join of the output, in an unquoted attribute": (
        "def f(x):\n    return mark_safe('<a href=' + ''.join([safe_url(x)]) + '>')\n"
    ),
}
for _label, _body in _COMPOSITES.items():
    row("composite: " + _label, _URL + _body, True)
row(
    "composite: a list of attribute fragments joined, placed between attributes",
    _URL
    + "def f(x):\n    attrs = [f'href=\"{safe_url(x)}\"']\n"
    "    return mark_safe(f'<a {\" \".join(attrs)}>go</a>')\n",
    False,
)
row(
    "composite: an attribute fragment built in place and placed between attributes",
    _URL
    + "def f(x, c):\n    style = f' style=\"background:{safe_url(x)}\"' if c else ''\n"
    "    return mark_safe(f'<div class=\"a\"{style}>go</div>')\n",
    False,
)
row(
    "composite: '' + safe_url(x) placed in text",
    _URL + "def f(x):\n    u = '' + safe_url(x)\n    return mark_safe('<p>' + u + '</p>')\n",
    False,
)
row(
    "composite: '' + safe_url(x) placed in a quoted attribute",
    _URL + "def f(x):\n    u = '' + safe_url(x)\n    return mark_safe('<a href=\"' + u + '\">')\n",
    False,
)
row(
    "composite: conditional_escape(safe_url(x)) placed in an unquoted attribute",
    _URL + "def f(x):\n    u = conditional_escape(safe_url(x))\n    return mark_safe(f'<a href={u}>')\n",
    True,
)
row(
    "composite: conditional_escape(safe_url(x)) placed in a quoted attribute",
    _URL + "def f(x):\n    u = conditional_escape(safe_url(x))\n    return mark_safe(f'<a href=\"{u}\">')\n",
    False,
)

# or / and / walrus seen through, in an unquoted attribute
row(
    "safe_url chosen with or, in an unquoted attribute",
    _URL + "def f(x):\n    u = safe_url(x) or '#'\n    return mark_safe('<a href=' + u + '>')\n",
    True,
)
row(
    "safe_url chosen with and, in an unquoted attribute",
    _URL + "def f(x, c):\n    u = c and safe_url(x)\n    return mark_safe('<a href=' + u + '>')\n",
    True,
)
row(
    "safe_url bound by a walrus, in an unquoted attribute",
    _URL + "def f(x):\n    return mark_safe(f'<a href={(u := safe_url(x))}>')\n",
    True,
)
row(
    "safe_url bound by a walrus, in a quoted attribute",
    _URL + "def f(x):\n    return mark_safe(f'<a href=\"{(u := safe_url(x))}\">')\n",
    False,
)
row(
    "safe_url chosen with or, in a quoted attribute",
    _URL + "def f(x):\n    u = safe_url(x) or '#'\n    return mark_safe('<a href=\"' + u + '\">')\n",
    False,
)
# str.format with *args: the arguments do not line up with the fields
row(
    "str.format with a starred argument",
    _URL + "def f(x):\n    return mark_safe('<a href=\"{}\">'.format(*[safe_url(x)]))\n",
    True,
)

# --- #3850 round 2 item 2: a prefix passed as a literal ARGUMENT --------------------------
row(
    "str.format: the prefix is an argument (positional)",
    _URL + "def f(x):\n    return mark_safe('{0}{1}>'.format('<a href=', safe_url(x)))\n",
    True,
)
row(
    "str.format: the prefix is an argument (automatic numbering)",
    _URL + "def f(x):\n    return mark_safe('{}{}>'.format('<a href=', safe_url(x)))\n",
    True,
)
row(
    "str.format: the prefix is an argument (by name)",
    _URL + "def f(x):\n    return mark_safe('{p}{u}>'.format(p='<a href=', u=safe_url(x)))\n",
    True,
)
row(
    "% format: the prefix is an argument",
    _URL + "def f(x):\n    return mark_safe('%s%s>' % ('<a href=', safe_url(x)))\n",
    True,
)
row(
    "% format: a quoted prefix is an argument",
    _URL + "def f(x):\n    return mark_safe('%s%s\">' % ('<a href=\"', safe_url(x)))\n",
    False,
)
row(
    "a prefix returned by a helper that always returns one literal",
    _URL
    + "def pre():\n    return '<a href='\ndef f(x):\n    return mark_safe(pre() + safe_url(x) + '>')\n",
    True,
)

# --- #3850 round 2 item 3: the tag name ----------------------------------------------------
row("a tag name in an f-string", _URL + "def f(x):\n    return mark_safe(f'<{safe_url(x)}>x')\n", True)
row("a tag name in a + chain", _URL + "def f(x):\n    return mark_safe('<' + safe_url(x) + '>')\n", True)
row("a closing tag name", _URL + "def f(x):\n    return mark_safe('</' + safe_url(x) + '>')\n", True)
row(
    "a less-than sign followed by a space is text",
    _URL + "def f(x):\n    return mark_safe('a < ' + safe_url(x))\n",
    False,
)

# --- #3850 round 2 item 4: __getattr__ from a subclass or assigned from outside ---------------
row(
    "a conditional attribute of a plain base, a subclass of this file defines __getattr__",
    """
class B:
    if FLAG:
        LABEL = "ok"
    def r(self):
        return mark_safe(f"<b>{self.LABEL}</b>")
class S(B):
    def __getattr__(self, name):
        return DATA
""",
    True,
)
row(
    "a conditional method of a plain base, a subclass of this file defines __getattr__",
    """
class B:
    if FLAG:
        def item(self, x):
            return escape(x)
    def m(self, raw):
        return mark_safe("<b>" + self.item(raw) + "</b>")
class S(B):
    def __getattr__(self, name):
        return lambda x: x
""",
    True,
)
row(
    "a conditional attribute and a __getattr__ assigned after class creation",
    """
class C:
    if FLAG:
        LABEL = "ok"
    def r(self):
        return mark_safe(f"<b>{self.LABEL}</b>")
C.__getattr__ = lambda self, name: DATA
""",
    True,
)

# --- #3850 round 2 item 5: a very long + chain is flagged, never a crash ----------------------
row(
    "a + chain of 2000 literals is too long to follow",
    "def f():\n    return mark_safe(" + " + ".join(["'a'"] * 2000) + ")\n",
    True,
)
row("a + chain of 6 literals", "def f():\n    return mark_safe(" + " + ".join(["'a'"] * 6) + ")\n", False)

# --- the remaining branches of the composite rule --------------------------------------------
_nested = "safe_url(x)"
for _ in range(40):
    _nested = f"('%s' % {_nested})"
row(
    "composite: nested 40 deep (past the bound: fail closed)",
    _URL + "def f(x):\n    return mark_safe('<a href=' + " + _nested + " + '>')\n",
    True,
)
row(
    "composite: an element of a list the output was appended to",
    _URL
    + "def f(x):\n    parts = []\n    parts.append(safe_url(x))\n    return mark_safe('<a href=' + parts[0] + '>')\n",
    True,
)
row(
    "composite: a method called on the output",
    _URL + "def f(x):\n    return mark_safe('<a href=' + safe_url(x).strip() + '>')\n",
    True,
)
row(
    "composite: a function given the output",
    _URL + "def f(x):\n    return mark_safe('<a href=' + fmt(safe_url(x)) + '>')\n",
    True,
)
row(
    "composite: the length of the output is digits",
    _URL + "def f(x):\n    return mark_safe('<td width=' + str(len(safe_url(x))) + '>')\n",
    False,
)
row(
    "composite: an attribute fragment built with % and placed between attributes",
    _URL
    + "def f(x):\n    frag = ' href=\"%s\"' % safe_url(x)\n    return mark_safe('<a class=\"a\"' + frag + '>go</a>')\n",
    False,
)
row(
    "composite: an attribute fragment built with + and placed between attributes",
    _URL
    + "def f(x):\n    frag = ' href=\"' + safe_url(x) + '\"'\n    return mark_safe('<a class=\"a\"' + frag + '>go</a>')\n",
    False,
)
row(
    "composite: an attribute fragment built with .format and placed between attributes",
    _URL
    + "def f(x):\n    frag = ' href=\"{}\"'.format(safe_url(x))\n    return mark_safe('<a class=\"a\"' + frag + '>go</a>')\n",
    False,
)
row(
    "composite: an attribute fragment from a helper that always returns one shape",
    _URL
    + "def attr(x):\n    return ' href=\"' + safe_url(x) + '\"'\n"
    "def f(x):\n    return mark_safe('<a class=\"a\"' + attr(x) + '>go</a>')\n",
    False,
)
row(
    "composite: an attribute fragment chosen between two shapes",
    _URL
    + "def f(x, c):\n    frag = ' href=\"' + safe_url(x) + '\"' if c else ''\n"
    "    return mark_safe('<a class=\"a\"' + frag + '>go</a>')\n",
    False,
)

# --- composite rule: the constructs it sees through (each is also cleared in place by the builder,
# so only the composite rule stops them being placed in an unquoted attribute) ------------------
row(
    "composite: a % string around an `or` of the output",
    _URL + "def f(x):\n    u = '%s' % (safe_url(x) or '#')\n    return mark_safe('<a href=' + u + '>')\n",
    True,
)
row(
    "composite: a % string around a conditional expression of the output",
    _URL
    + "def f(x, c):\n    u = '%s' % (safe_url(x) if c else '#')\n    return mark_safe('<a href=' + u + '>')\n",
    True,
)
row(
    "composite: a % string around a walrus of the output",
    _URL + "def f(x):\n    u = '%s' % (v := safe_url(x))\n    return mark_safe('<a href=' + u + '>')\n",
    True,
)
row(
    "composite: a composite appended to a list, read back by index",
    _URL
    + "def f(x):\n    parts = []\n    parts.append('' + safe_url(x))\n    return mark_safe('<a href=' + parts[0] + '>')\n",
    True,
)
row(
    "composite: a composite in a list display, read back by index",
    _URL + "def f(x):\n    return mark_safe('<a href=' + ['' + safe_url(x)][0] + '>')\n",
    True,
)

# --- round 3: an exempt fragment must be BALANCED, and a plain-text < is not markup ------------
row(
    "fragment: a plain-text less-than sign in front of the output, placed in an unquoted attribute",
    _URL
    + "def f(x):\n    frag = '1 < 2 ' + safe_url(x)\n    return mark_safe('<a href=' + frag + '>')\n",
    True,
)
row(
    "fragment: an UNCLOSED quote in front of the output, placed in an unquoted attribute",
    _URL
    + "def f(x):\n    frag = 'x=\"' + safe_url(x)\n    return mark_safe('<a href=' + frag + '>')\n",
    True,
)
row(
    "fragment: a balanced attribute fragment, placed between attributes",
    _URL
    + "def f(x):\n    frag = ' title=\"' + safe_url(x) + '\"'\n    return mark_safe('<a class=\"a\"' + frag + '>go</a>')\n",
    False,
)
row(
    "fragment: a balanced attribute fragment with an element after it, placed between attributes",
    _URL
    + "def f(x):\n    frag = '<i title=\"' + safe_url(x) + '\">x</i>'\n    return mark_safe('<p>' + frag + '</p>')\n",
    False,
)

# --- #3898: data_url_attr and attr_text are context-sensitive escapers too ------------------
# Both HTML-escape the value (a quoted attribute or text is safe) but keep spaces and the rest, so
# an unquoted attribute, an attribute NAME and a tag name are not.
for _helper in ("data_url_attr", "attr_text"):
    _imp = f"from djust.components.utils import {_helper}\n"
    for _label, _body, _bad in (
        ("an unquoted attribute", f'f"<a href={{{_helper}(x)}}>go</a>"', True),
        ("an attribute name", f'f"<a {{{_helper}(x)}}=1>go</a>"', True),
        ("a tag name", f"f'<{{{_helper}(x)}}>x'", True),
        ("a quoted attribute", f"f'<a href=\"{{{_helper}(x)}}\">go</a>'", False),
        ("text", f'f"<p>{{{_helper}(x)}}</p>"', False),
    ):
        row(
            f"{_helper} in {_label}" if _bad else f"{_helper} kept in {_label}",
            f"{_imp}def f(x):\n    return mark_safe({_body})\n",
            _bad,
        )
