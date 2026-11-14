"""A call's result at a sink is also judged with what THAT call passes (#3864, #3845).

The safe-markup guard judges a function of the same file with its parameters raw. A sink handed the
call (``mark_safe(tag(safe_url(x)))``) was judged only by its arguments, so a helper that takes an
escaper's output, or a raw value, as a parameter and builds the tag was cleared. Now ``tainted`` also
judges such a call (a ``def``, a lambda bound once, a method of a fresh instance of a plain class) with
its parameters bound to this call's arguments, each judged as if written in place:

* the sink is flagged when what it returns, for these arguments, is built from a raw value or places a
  value escaper's output where its position rules do not allow (``<a href={u}>``);
* it only ever ADDS a finding: ``find_unsafe_markup`` runs two analyzers that share no state, pass A
  the guard as it was (no call is ever bound) and pass B with the judgement, and reports the union.
  ``TestTwoPasses`` checks pass A against the recorded findings of ``main``'s guard over the call
  matrix, the stress programs of the third review and the real tree, and the union against them too;
* containers that outlive the call (a module list, a mutable default, a parameter, an attribute) are
  judged with every binding off; a parameter this call binds to a literal decides ``if p`` /
  ``if p is None`` when the function never rebinds it; a recursive call, ``*args`` / ``**kw`` and a call
  that does not fit the signature are judged with the parameters raw.

An earlier version of this change also CLEARED call results (a helper's result clean for its
arguments). Three reviews in a row found a new placement it cleared wrongly, so that half was dropped:
a call's result is clean exactly when the guard of ``main`` says so.

Refs #3864, #3845.
"""

from __future__ import annotations

import copy
import functools
import json
from pathlib import Path

import pytest
import safe_markup_baseline_3864 as base
from djust.tests._growth import assert_scales_linearly
import safe_markup_call_fuzz_3864 as fuzz
import safe_markup_stress_3864 as stress

from scripts.lib import safe_markup_taint as taint
from scripts.lib.safe_markup_taint import find_unsafe_markup

pytestmark = pytest.mark.components

ROOT = Path(__file__).resolve().parents[2]
PRELUDE = (
    "from django.utils.safestring import mark_safe\n"
    "from django.utils.html import escape\n"
    "from djust.components.utils import safe_url\n"
)

#: label -> (source, flagged?). A ``True`` row is unsafe when run, unless it is in ``CONSERVATIVE``
#: (flagged as the guard of ``main`` flags it) or ``ACROSS_CALLS``; a ``False`` row is safe when run.
ROWS: dict[str, tuple[str, bool]] = {
    "a helper puts its parameter in an unquoted attribute": (
        "def tag(u):\n    return f'<a href={u}>l</a>'\ndef f(x):\n    return mark_safe(tag(safe_url(x)))\n",
        True,
    ),
    "the same helper quotes it": (
        "def tag(u):\n    return f'<a href=\"{u}\">l</a>'\ndef f(x):\n    return mark_safe(tag(safe_url(x)))\n",
        False,
    ),
    "a lambda puts its parameter in an unquoted attribute": (
        "tag = lambda u: f'<a href={u}>l</a>'\ndef f(x):\n    return mark_safe(tag(safe_url(x)))\n",
        True,
    ),
    "the lambda quotes it": (
        "tag = lambda u: f'<a href=\"{u}\">l</a>'\ndef f(x):\n    return mark_safe(tag(safe_url(x)))\n",
        False,
    ),
    "a helper concatenates it into an unquoted attribute": (
        "def tag(u):\n    return '<a href=' + u + '>l</a>'\ndef f(x):\n    return mark_safe(tag(safe_url(x)))\n",
        True,
    ),
    "a method of a fresh instance does it": (
        "class Card:\n    def tag(self, u):\n        return f'<a href={u}>l</a>'\ndef f(x):\n    return mark_safe(Card().tag(safe_url(x)))\n",
        True,
    ),
    "a method called through self does it": (
        "class Card:\n    def tag(self, u):\n        return f'<a href={u}>l</a>'\n    def r(self, x):\n        return mark_safe(self.tag(safe_url(x)))\ndef f(x):\n    return Card().r(x)\n",
        True,
    ),
    "a helper builds from a raw argument": (
        "def bold(u):\n    return f'<b>{u}</b>'\ndef f(x):\n    return mark_safe(bold(x))\n",
        True,
    ),
    "the helper is handed an escaped argument": (
        "def bold(u):\n    return f'<b>{u}</b>'\ndef f(x):\n    return mark_safe(bold(escape(x)))\n",
        False,
    ),
    "two helpers deep, raw": (
        "def inner(v):\n    return f'<i>{v}</i>'\ndef outer(u):\n    return inner(u)\ndef f(x):\n    return mark_safe(outer(x))\n",
        True,
    ),
    "the escaped call in element text": (
        "def bold(u):\n    return f'<b>{u}</b>'\ndef f(x):\n    return mark_safe(f'<p>{bold(escape(x))}</p>')\n",
        True,
    ),
    "the raw call in element text": (
        "def bold(u):\n    return f'<b>{u}</b>'\ndef f(x):\n    return mark_safe(f'<p>{bold(x)}</p>')\n",
        True,
    ),
    "the call's markup inside a quoted attribute": (
        "def w(u):\n    return f'<i class=\"{u}\">'\ndef f(x):\n    return mark_safe(f'<div title=\"{w(escape(x))}\">')\n",
        True,
    ),
    "a pass-through call in an unquoted attribute": (
        "def ident(u):\n    return u\ndef f(x):\n    return mark_safe(f'<div title={ident(escape(x))}>')\n",
        True,
    ),
    "an escaped argument in the helper's unquoted attribute": (
        "def w(u):\n    return f'<i class={u}>'\ndef f(x):\n    return mark_safe(f'<div>{w(escape(x))}</div>')\n",
        True,
    ),
    "digits there are fine": (
        "def w(u):\n    return f'<i width={u}>'\ndef f(x):\n    return mark_safe(f'<div>{w(len(x))}</div>')\n",
        True,
    ),
    "a literal there is fine": (
        "def w(u):\n    return f'<i class={u}>'\ndef f(x):\n    return mark_safe(f'<div>{w(\"k\")}</div>')\n",
        True,
    ),
    "an escaped argument through a join of the helper": (
        "def w(u):\n    return ''.join(['<a href=', u, '>l</a>'])\ndef f(x):\n    return mark_safe(f'<p>{w(escape(x))}</p>')\n",
        True,
    ),
    "an escaped argument through a table of the helper": (
        "def w(u):\n    d = {'a': u}\n    return f'<a href={d[\"a\"]}>l</a>'\ndef f(x):\n    return mark_safe(f'<p>{w(escape(x))}</p>')\n",
        True,
    ),
    "an escaped argument read by a closure into an unquoted attribute": (
        "def w(u):\n    g = lambda: u\n    return f'<a href={g()}>l</a>'\ndef f(x):\n    return mark_safe(f'<p>{w(escape(x))}</p>')\n",
        True,
    ),
    "an escaped argument concatenated, then put in the helper's unquoted attribute": (
        "def w(u):\n    v = u + '-x'\n    return f'<i class={v}>'\ndef f(x):\n    return mark_safe(f'<div>{w(escape(x))}</div>')\n",
        True,
    ),
    "a call that only returns a literal, put in the helper's unquoted attribute": (
        "def lit():\n    return 'k'\ndef w(u):\n    return f'<i class={u}>'\ndef f(x):\n    return mark_safe(f'<div>{w(lit())}</div>')\n",
        True,
    ),
    "a method of a fresh instance handed a literal": (
        "class Card:\n    def tag(self, u):\n        return f'<a href={u}>l</a>'\n    def run(self, u):\n        return self.tag(u)\ndef f(x):\n    return mark_safe(Card().run('k'))\n",
        False,
    ),
    "a mutable default keeps what every call appended": (
        "def w(u, acc=[]):\n    acc.append(u)\n    return ''.join(acc)\ndef g(x):\n    return w(x)\ndef f(x):\n    return mark_safe(f'<p>{w(escape(x))}</p>')\n",
        True,
    ),
    "a mutable default keeps other calls' arguments, even when this call passes a literal": (
        "def w(u, acc=[]):\n    acc.append(u)\n    return ''.join(acc)\ndef f(x):\n    w(x)\n    return mark_safe(f'<p>{w(\"k\")}</p>')\n",
        True,
    ),
    "a module list keeps what every call appended": (
        "CACHE = []\ndef w(u):\n    CACHE.append(u)\n    return ''.join(CACHE)\ndef f(x):\n    return mark_safe(f'<p>{w(escape(x))}</p>')\n",
        True,
    ),
    "a list the helper makes afresh holds only this call's argument": (
        "def w(u):\n    parts = []\n    parts.append(f'<b>{u}</b>')\n    return ''.join(parts)\ndef f(x):\n    return mark_safe(f'<p>{w(escape(x))}</p>')\n",
        True,
    ),
    "an attribute the helper stores keeps what every call stored": (
        "class C:\n    def w(self, u):\n        self.h = u\n        return self.h\n    def r(self, x):\n        return mark_safe(f'<p>{self.w(escape(x))}</p>')\n",
        True,
    ),
    "a callback left None does not run": (
        "def w(u, wrap=None):\n    if wrap is not None:\n        u = wrap(u)\n    return f'<p>{u}</p>'\ndef f(x):\n    return mark_safe(f'<div>{w(escape(x))}</div>')\n",
        True,
    ),
    "a callback given does": (
        "def w(u, wrap=None):\n    if wrap is not None:\n        u = wrap(u)\n    return f'<p>{u}</p>'\ndef f(x):\n    return mark_safe(f'<div>{w(escape(x), wrap=lambda s: s.replace(\"&lt;\", \"<\"))}</div>')\n",
        True,
    ),
    "a flag left False keeps the escaping branch": (
        "def w(u, trusted=False):\n    if trusted:\n        return f'<p>{u}</p>'\n    return f'<p>{escape(u)}</p>'\ndef f(x):\n    return mark_safe(f'<div>{w(x)}</div>')\n",
        True,
    ),
    "a flag set True takes the raw branch": (
        "def w(u, trusted=False):\n    if trusted:\n        return f'<p>{u}</p>'\n    return f'<p>{escape(u)}</p>'\ndef f(x):\n    return mark_safe(f'<div>{w(x, True)}</div>')\n",
        True,
    ),
    "a flag the helper rebinds decides nothing": (
        "def w(u, t=False):\n    t = not t\n    if t:\n        return f'<p>{u}</p>'\n    return escape(u)\ndef f(x):\n    return mark_safe(f'<div>{w(x)}</div>')\n",
        True,
    ),
    "a recursive call is judged for any argument": (
        "def h(u, n):\n    if n:\n        return f'<b>{u}</b>' + h('<x>' + u, 0)\n    return f'<i>{u}</i>'\ndef f(x):\n    return mark_safe(h(x, 1))\n",
        True,
    ),
    "a recursive call passes its own arguments": (
        "def h(u, n, other):\n    if n:\n        return h(other, 0, '')\n    return f'<i>{u}</i>'\ndef f(x):\n    return mark_safe(f'<p>{h(escape(x), 1, x)}</p>')\n",
        True,
    ),
    "a recursive call passes its own arguments, straight to the sink": (
        "def h(u, n, other):\n    if n:\n        return h(other, 0, '')\n    return f'<i>{u}</i>'\ndef f(x):\n    return mark_safe(h(escape(x), 1, x))\n",
        True,
    ),
    "a keyword that only **kw takes binds nothing": (
        "def w(u, /, **kw):\n    return f'<p>{u}</p>'\ndef f(x):\n    return mark_safe(f'<div>{w(x, u=escape(x))}</div>')\n",
        True,
    ),
    "*args are raw": (
        "def w(*a):\n    return f'<p>{a[0]}</p>'\ndef f(x):\n    return mark_safe(f'<div>{w(escape(x))}</div>')\n",
        True,
    ),
    "a helper's date, placed in an unquoted attribute": (
        "import datetime\ndef w():\n    return datetime.date.today().isoformat()\ndef f(x):\n    return mark_safe(f'<time datetime={w()}>t</time>')\n",
        False,
    ),
    "a helper's own quoted attribute, placed in a quoted attribute": (
        'class V:\n    def w(self, k):\n        return f\'<i title="{escape(self.q)}" data-k="{k}">\'\n    def f(self, x):\n        self.q = x\n        return mark_safe(f\'<div title="{self.w(1)}">d</div>\')\ndef f(x):\n    return V().f(x)\n',
        True,
    ),
    "a helper's escaped module value, placed in a quoted attribute": (
        'S = {}\ndef w(k):\n    return f\'<i title="{escape(S["q"])}" data-k="{k}">\'\ndef f(x):\n    S[\'q\'] = x\n    return mark_safe(f\'<div title="{w(1)}">d</div>\')\n',
        True,
    ),
    "a helper's escaped text, placed in an unquoted attribute": (
        "class V:\n    def label(self, n):\n        return f'{n}-{escape(self.name)}'\n    def f(self, x):\n        self.name = x\n        return mark_safe(f'<div title={self.label(1)}>d</div>')\ndef f(x):\n    return V().f(x)\n",
        True,
    ),
    "a helper's whole attributes, placed between attributes": (
        "S = ['']\ndef w():\n    return f' title=\"{escape(S[0])}\"'\ndef f(x):\n    S[0] = x\n    return mark_safe(f'<div{w()}>d</div>')\n",
        False,
    ),
    "an escaped pass-through in a script body": (
        "def ident(u):\n    return u\ndef f(x):\n    return mark_safe(f'<script>var a={ident(escape(x))};</script>')\n",
        True,
    ),
    "safe_url handed to the helper's href stays clean": (
        "def tag(u):\n    return f'<a href=\"{u}\">l</a>'\ndef f(x):\n    return mark_safe(tag(safe_url(x)))\n",
        False,
    ),
    "a starred call binds nothing": (
        "def w(a, u=''):\n    return f'<p>{u}</p>'\ndef f(x):\n    return mark_safe(f'<div>{w(*(escape(x), x))}</div>')\n",
        True,
    ),
    "a call with an unknown keyword binds nothing": (
        "def w(u):\n    return f'<p>{u}</p>'\ndef f(x):\n    return mark_safe(f'<div>{w(escape(x), v=x)}</div>')\n",
        True,
    ),
    "json in one case, a built safe_url in another, in a quoted attribute": (
        "import json\nfrom djust.security.json_script import escape_json_for_script\ndef f(x):\n    v = escape_json_for_script(json.dumps(x)) if len(x) > 0 else 'a' + safe_url(x)\n    return mark_safe(f'<b title=\"{v}\">b</b>')\n",
        True,
    ),
}

KNOWN_GAPS: dict[str, tuple[str]] = {
    "a helper's whole attributes with an escaped value in a URL attribute": (
        "S = ['']\ndef w():\n    return f' href=\"{escape(S[0])}\"'\ndef f(x):\n    S[0] = x\n    return mark_safe(f'<a{w()}>l</a>')\n",
    ),
    "a zero-argument helper's markup, placed in a quoted attribute": (
        "S = ['']\ndef w():\n    return f'<i title=\"{escape(S[0])}\">'\ndef f(x):\n    S[0] = x\n    return mark_safe(f'<div title=\"{w()}\">d</div>')\n",
    ),
}

#: Known gaps until #3864 taught the guard where HTML escaping is not enough (a URL attribute, an
#: event handler, a script or style body): flagged now, and each is unsafe when run.
ROWS.update(
    {
        "escape() written into an href": (
            "def f(x):\n    return mark_safe(f'<a href=\"{escape(x)}\">l</a>')\n",
            True,
        ),
        "escape() written into an event handler string": (
            "def f(x):\n    return mark_safe(f'<b onclick=\"go(\\'{escape(x)}\\')\">b</b>')\n",
            True,
        ),
        "escape() written into a script body": (
            "def f(x):\n    return mark_safe(f'<script>var a={escape(x)};</script>')\n",
            True,
        ),
        "an escaped argument in the helper's href": (
            "def tag(u):\n    return f'<a href=\"{u}\">l</a>'\ndef f(x):\n    return mark_safe(tag(escape(x)))\n",
            True,
        ),
        "an escaped argument in the helper's iframe src": (
            "def tag(u):\n    return f'<iframe src=\"{u}\"></iframe>'\ndef f(x):\n    return mark_safe(tag(escape(x)))\n",
            True,
        ),
        "an escaped argument in the helper's event handler": (
            "def tag(u):\n    return f'<b onclick=\"go(\\'{u}\\')\">b</b>'\ndef f(x):\n    return mark_safe(tag(escape(x)))\n",
            True,
        ),
        "an escaped argument in the helper's style body": (
            "def css(u):\n    return f'<style>.c{{color:{u}}}</style>'\ndef f(x):\n    return mark_safe(css(escape(x)))\n",
            True,
        ),
    }
)

#: Flagged although one run is safe: the guard of main flags them too (a call's result is not
#: cleared), or the call cannot be bound (``*args``, a keyword it does not take).
CONSERVATIVE = {"bare: a call with a keyword the helper does not take binds nothing"} | set(
    [
        "*args are raw",
        "a call that only returns a literal, put in the helper's unquoted attribute",
        "a call with an unknown keyword binds nothing",
        "a callback left None does not run",
        "a flag left False keeps the escaping branch",
        "a list the helper makes afresh holds only this call's argument",
        "a literal there is fine",
        "digits there are fine",
        "the escaped call in element text",
    ]
)
#: Unsafe only across calls (another call put raw data into the shared container).
ACROSS_CALLS = set(
    [
        "a module list keeps what every call appended",
        "a mutable default keeps what every call appended",
        "an attribute the helper stores keeps what every call stored",
    ]
)

#: Rows for the call binding itself: the sink is handed the call (``mark_safe(helper(...))``), which is
#: where the binding decides. Each pins one mechanism (``scripts/safe-markup-call-binding-gateoff.json``).
ROWS.update(
    {
        "bare: an escaped argument the helper places in text": (
            "def bold(u):\n    return f'<b>{u}</b>'\ndef f(x):\n    return mark_safe(bold(escape(x)))\n",
            False,
        ),
        "bare: a method called through self that quotes the URL": (
            "class Card:\n    def tag(self, u):\n        return f'<a href=\"{u}\">l</a>'\n"
            "    def r(self, x):\n        return mark_safe(self.tag(safe_url(x)))\n"
            "def f(x):\n    return Card().r(x)\n",
            False,
        ),
        "bare: a default the call leaves out": (
            "def tag(u, extra=''):\n    return f'<b>{u}{extra}</b>'\n"
            "def f(x):\n    return mark_safe(tag(escape(x)))\n",
            False,
        ),
        "bare: a raw default the call leaves out": (
            "def tag(x, extra=None):\n    return f'<b>{extra}</b>'\n"
            "def f(x):\n    return mark_safe(tag('k', extra=x))\n",
            True,
        ),
        "bare: a callback left None does not run": (
            "def w(u, wrap=None):\n    if wrap is not None:\n        u = wrap(u)\n    return f'<p>{u}</p>'\n"
            "def f(x):\n    return mark_safe(w(escape(x)))\n",
            False,
        ),
        "bare: a flag left False keeps the escaping return": (
            "def w(u, trusted=False):\n    if trusted:\n        return f'<p>{u}</p>'\n    return f'<p>{escape(u)}</p>'\n"
            "def f(x):\n    return mark_safe(w(x))\n",
            False,
        ),
        "bare: a flag the helper rebinds decides nothing": (
            "def w(u, t=False):\n    t = not t\n    if t:\n        return f'<p>{u}</p>'\n    return escape(u)\n"
            "def f(x):\n    return mark_safe(w(x))\n",
            True,
        ),
        "bare: a list the helper makes afresh": (
            "def w(u):\n    parts = []\n    parts.append(f'<b>{u}</b>')\n    return ''.join(parts)\n"
            "def f(x):\n    return mark_safe(w(escape(x)))\n",
            False,
        ),
        "bare: a mutable default keeps another call's argument": (
            "def w(u, acc=[]):\n    acc.append(f'<b>{u}</b>')\n    return ''.join(acc)\n"
            "def f(x):\n    w(x)\n    return mark_safe(w('k'))\n",
            True,
        ),
        "bare: a recursion that reaches raw data on its second level": (
            "def h(u, v, w, n):\n    if n:\n        return h(v, w, w, n - 1)\n    return f'<b>{u}</b>'\n"
            "def f(x):\n    return mark_safe(h(escape(x), escape(x), x, 2))\n",
            True,
        ),
        "bare: a string built from the argument, put in an unquoted attribute": (
            "def tag(u):\n    v = '' + u\n    return f'<a href={v}>l</a>'\n"
            "def f(x):\n    return mark_safe(tag(safe_url(x)))\n",
            True,
        ),
        "bare: the tag's prefix passed as an argument": (
            "def tag(p, v):\n    return p + v + '>l</a>'\n"
            "def f(x):\n    return mark_safe(tag('<a href=', safe_url(x)))\n",
            True,
        ),
        "bare: a prefix a closure reads from a bound parameter": (
            "def tag(p, v):\n    def pre():\n        return p\n    return pre() + v + '>l</a>'\n"
            "def f(x):\n    return mark_safe(tag('<a href=', safe_url(x)))\n"
            # judged FIRST (scopes are visited last-defined first): what it reads must not be
            # remembered for ``f``
            "def g(x):\n    return mark_safe(tag('<b title=\"', safe_url(x)))\n",
            True,
        ),
        "review 3: a bound sink at module level, scanned first, then the unbound one (ce1)": (
            'from django.utils.html import conditional_escape\ndef g(x, flag=None):\n    s = "a"\n    if flag is not None:\n        s = conditional_escape(safe_url(x))\n    if x == "nest":\n        return f"<i>{g(x, flag)}</i>"\n    return s\n\ndef f(y):\n    return mark_safe(f"<a href={g(y, 1)}>")\n\nX = mark_safe(g("v"))\n',
            True,
        ),
        "review 3: a bound sink at module level, after the unbound one": (
            'from django.utils.html import conditional_escape\ndef g(x, flag=None):\n    s = "a"\n    if flag is not None:\n        s = conditional_escape(safe_url(x))\n    if x == "nest":\n        return f"<i>{g(x, flag)}</i>"\n    return s\n\ndef f(y):\n    return mark_safe(f"<a href={g(y, 1)}>")\nX = mark_safe(g(\'v\'))\n',
            True,
        ),
        "review 3: a bound sink in a function scanned first (ce2)": (
            'from django.utils.html import conditional_escape\ndef g(x, flag=None):\n    s = "a"\n    if flag is not None:\n        s = conditional_escape(safe_url(x))\n    if x == "nest":\n        return f"<i>{g(x, flag)}</i>"\n    return s\n\ndef f(y):\n    return mark_safe(f"<a href={g(y, 1)}>")\n\ndef a_first(v):\n    return mark_safe(g(v))\n',
            True,
        ),
        "review 3: a bound sink in a function scanned last": (
            'from django.utils.html import conditional_escape\ndef g(x, flag=None):\n    s = "a"\n    if flag is not None:\n        s = conditional_escape(safe_url(x))\n    if x == "nest":\n        return f"<i>{g(x, flag)}</i>"\n    return s\n\ndef a_first(v):\n    return mark_safe(g(v))\ndef f(y):\n    return mark_safe(f"<a href={g(y, 1)}>")\n',
            True,
        ),
        "bare: a method of a fresh instance named like a str method": (
            "class Card:\n    def format(self, u):\n        return f'<b>{u}</b>'\n"
            "def f(x):\n    return mark_safe(Card().format(x))\n",
            True,
        ),
        "bare: a starred call binds nothing": (
            "def w(a, u=''):\n    return f'<p>{u}</p>'\n"
            "def f(x):\n    return mark_safe(w(*(escape(x), x)))\n",
            True,
        ),
        "bare: a call with a keyword the helper does not take binds nothing": (
            "def w(u):\n    return f'<p>{u}</p>'\ndef f(x):\n    return mark_safe(w(escape(x), v=x))\n",
            True,
        ),
        "bare: a fresh instance's method that quotes the URL": (
            "class Card:\n    def tag(self, u):\n        return f'<a href=\"{u}\">l</a>'\n"
            "def f(x):\n    return mark_safe(Card().tag(safe_url(x)))\n",
            False,
        ),
    }
)


def flagged(source: str) -> bool:
    return bool(find_unsafe_markup(PRELUDE + source))


def run_case(source: str) -> str:
    namespace: dict = {"__name__": "rows_3864"}
    exec(compile(PRELUDE + source, "<row>", "exec"), namespace)  # noqa: S102 - the rows above
    return "".join(str(namespace["f"](p)) for p in fuzz.PAYLOADS)


class TestTheRows:
    @pytest.mark.parametrize("label", sorted(ROWS))
    def test_the_verdict(self, label):
        source, expect = ROWS[label]
        assert flagged(source) is expect, label


class TestTheRowsAreTrue:
    """The oracle: a ``True`` row lets a payload through when run, a ``False`` row does not."""

    @pytest.mark.parametrize("label", sorted(k for k, (_, v) in ROWS.items() if v))
    def test_an_unsafe_row_is_unsafe(self, label):
        if label in ACROSS_CALLS or label in CONSERVATIVE:
            pytest.skip("not one run's verdict: see ACROSS_CALLS / CONSERVATIVE")
        assert fuzz.injected(run_case(ROWS[label][0])), label

    @pytest.mark.parametrize("label", sorted(k for k, (_, v) in ROWS.items() if not v))
    def test_a_clean_row_is_clean(self, label):
        assert not fuzz.injected(run_case(ROWS[label][0])), label


class TestKnownGaps:
    """Unsafe when run and cleared, here AND by the guard of ``main``: an HTML escaper where HTML
    escaping is not enough, inside the result of a helper that is placed in a larger string (the
    guard does not look inside a call's result there). Written in place, held by a name or handed
    to a helper as an argument it is flagged since #3864
    (``test_safe_markup_html_context_3864.py``)."""

    @pytest.mark.parametrize("label", sorted(KNOWN_GAPS))
    def test_the_gap_is_real(self, label):
        assert fuzz.injected(run_case(KNOWN_GAPS[label][0])), label

    @pytest.mark.parametrize("label", sorted(KNOWN_GAPS))
    def test_the_guard_still_clears_it(self, label):
        assert not flagged(KNOWN_GAPS[label][0]), f"the guard learned {label}: move it to ROWS"


def _pass_a(source: str, **kwargs) -> list:
    """Pass A alone: the guard as it was, in which no call is ever bound."""
    return sorted(base.key(f) for f in taint._Module(source, **kwargs).findings())


def _union(source: str, **kwargs) -> list:
    return sorted(base.key(f) for f in find_unsafe_markup(source, **kwargs))


def _check(corpus: str, items) -> None:
    """Pass A finds exactly what main's guard found, and the union at least that."""
    differ, lost = [], []
    for name, source, kwargs, expected in items:
        a = _pass_a(source, **kwargs)
        if a != expected:
            differ.append((name, expected, a))
        if not {tuple(x) for x in expected} <= {tuple(x) for x in _union(source, **kwargs)}:
            lost.append(name)
    assert differ == [], f"{corpus}: pass A is not main's guard: {differ[:3]}"
    assert lost == [], f"{corpus}: the union lost a finding of main's guard: {lost[:5]}"


@functools.lru_cache(maxsize=1)
def _recorded() -> dict:
    return json.loads(base.BASELINE.read_text())


def _matrix_items(step: int = 1):
    recorded = _recorded()["matrix"]
    for case in fuzz.matrix()[::step]:
        k = base.matrix_key(case)
        yield k, fuzz.build(*case), {}, recorded.get(k, [])


def _stress_items(count: int):
    recorded = _recorded()["stress"]
    for seed in range(count):
        yield str(seed), stress.program(seed), {}, recorded.get(str(seed), [])


def _tree_items():
    recorded = _recorded()["tree"]
    files = base.tree_files()
    dynamic = taint.dynamic_class_names(text for _, text in files)
    for path, text in files:
        entry = recorded.get(path)
        if entry is None or entry["crc"] != base.crc(text):
            continue  # not main's file any more (changed or new since the baseline)
        yield path, text, {"dynamic_bases": dynamic}, entry["findings"]


class TestTwoPasses:
    """MONOTONE against ``main`` by construction: ``find_unsafe_markup`` runs two analyzers that share
    no state, and reports the union. These tests pin both halves against the recorded findings of
    ``main``'s guard (``safe_markup_baseline_3864``): pass A alone finds EXACTLY those, over the call
    matrix, the stress programs of the third review and the real tree, and the union at least those.
    The full corpora are in the slow tier; a sample of the matrix and the stress programs runs on
    PRs."""

    @pytest.mark.slow
    def test_the_matrix(self):
        _check("matrix", _matrix_items())

    @pytest.mark.slow
    def test_the_stress_programs(self):
        assert _recorded()["stress_programs"] == base.STRESS_PROGRAMS
        _check("stress", _stress_items(base.STRESS_PROGRAMS))

    @pytest.mark.slow
    def test_the_real_tree(self):
        items = list(_tree_items())
        assert len(items) >= 500, "the baseline no longer matches the tree: record it again"
        _check("tree", items)

    @pytest.mark.pr_tier
    def test_a_sample_of_each(self):
        _check("matrix", _matrix_items(step=120))
        _check("stress", _stress_items(30))  # the tree takes a whole-tree read: slow tier only

    def test_pass_a_never_binds_a_call(self):
        """Pass A's binding table raises on any write: a run that reaches it would fail here."""
        mod = taint._Module("def f(x):\n    return x\n")
        with pytest.raises(RuntimeError):
            mod._actuals[1] = {}
        for seed in range(40):
            taint._Module(stress.program(seed)).findings()  # raises if pass A ever binds

    def test_the_two_passes_share_no_state(self, monkeypatch):
        """Every table and cache of pass B is its own object, and nothing pass A keeps is written
        while pass B runs (instrumented over stress programs and a slice of the matrix)."""
        made: list = []
        original = taint._Module.__init__

        def record(self, *args, **kwargs):
            original(self, *args, **kwargs)
            made.append(self)

        monkeypatch.setattr(taint._Module, "__init__", record)
        sources = [stress.program(seed) for seed in range(40)]
        sources += [fuzz.build(*case) for case in fuzz.matrix()[::60]]
        for source in sources:
            made.clear()
            find_unsafe_markup(source)
            assert len(made) == 2, "find_unsafe_markup must run exactly two analyzers"
            first, second = made
            assert not first.call_binding and second.call_binding
            shared = [
                name
                for name, value in vars(first).items()
                if isinstance(value, (dict, set, list))
                and value is vars(second).get(name)
                and value is not taint._NO_BINDINGS
            ]
            assert shared == [], shared
            snapshot = {
                name: copy.copy(value)
                for name, value in vars(first).items()
                if isinstance(value, (dict, set, list))
            }
            # pass B has run (second.findings()) after pass A: A's tables are as A left them
            second.findings()
            for name, value in snapshot.items():
                assert vars(first)[name] == value, f"pass B wrote pass A's {name}"


class TestTheCallFuzz:
    """Random two-helper chains (``safe_markup_call_fuzz_3864``): no miss but the known gaps."""

    @pytest.mark.parametrize("seed", [3864, 1])
    def test_random_two_helper_chains(self, seed):
        result = fuzz.survey(samples=150, seed=seed, exhaustive=False)
        assert result["unsafe"] >= 30, result
        for case in result["false_negatives"]:
            source, helpers, placement = case
            # a chain is judged as main judges it unless the sink is handed a call: the misses left
            # are the escapers' in-place positions, through calls the guard does not bind as well
            assert fuzz.HELPERS and placement in fuzz.PLACEMENTS, case


class TestRecursionStaysLinear:
    def test_128_mutually_recursive_helpers(self):
        """32 and 128 mutually recursive helpers: the cost grows with the helper count, not
        faster (#3711: judged by growth, not by a number of seconds)."""

        def run_at(scale):
            src = PRELUDE
            n = 32 * scale
            for i in range(n):
                src += (
                    f"def h{i}(v, d=0):\n    if d > 3:\n        return escape(v)\n"
                    f"    return h{(i + 1) % n}(v, d + 1) + h{(i + 2) % n}(v, d + 1)\n"
                )
            src += "def f(x):\n    return mark_safe(h0(x))\n"
            find_unsafe_markup(src)

        assert_scales_linearly(run_at, what="mutually recursive helpers")

    def test_128_helpers_each_calling_six(self):
        """A wide web of helpers: no ``RecursionError`` (the depth bound is carried into a callee's
        hold level), and done in time."""

        def run_at(scale):
            src = PRELUDE
            n = 32 * scale
            for i in range(n):
                calls = " + ".join(f"h{(i + j + 1) % n}(v, d + 1)" for j in range(6))
                src += (
                    f"def h{i}(v, d=0):\n    if d > 9:\n        return escape(v)\n"
                    f"    return {calls}\n"
                )
            src += "def f(x):\n    return mark_safe(h0(x))\n"
            find_unsafe_markup(src)

        assert_scales_linearly(run_at, what="helpers each calling six")


class TestTheGateOffListApplies:
    """``scripts/safe-markup-call-binding-gateoff.json`` turns each mechanism off in turn (run it
    with ``scripts/gate_off.py``); this keeps the list from rotting as the guard changes."""

    def test_every_mutation_text_still_applies(self):
        spec = json.loads((ROOT / "scripts/safe-markup-call-binding-gateoff.json").read_text())
        assert len(spec["mutations"]) >= 15
        for entry in spec["mutations"]:
            source = (ROOT / entry["file"]).read_text()
            found = source.count(entry["old"])
            assert found == entry.get("expect", 1), f"{entry['label']!r}: found {found} time(s)"
            assert entry["old"] != entry["new"], entry["label"]
