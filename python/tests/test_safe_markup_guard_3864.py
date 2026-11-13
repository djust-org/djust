"""A call's result is judged with what THAT call passes (#3845, #3864).

The safe-markup guard judges a function of the same file with its parameters raw, so the verdict holds
for any call. That left two holes the issues name: a helper (or lambda) that takes an escaper's output as
a PARAMETER and builds the tag (``def tag(u): return f"<a href={u}>"`` then ``mark_safe(tag(safe_url(x)))``
was cleared), and a helper that returns an interpolated string passed straight to the sink
(``mark_safe(helper(x))``). It also flagged correct code: ``f"<p>{bold(escape(x))}</p>"``.

Now a call of such a function is also judged with its parameters BOUND to this call's arguments:

* a sink handed the call is flagged when what it returns, for these arguments, is built from a raw value
  (``mark_safe(tag(x))``), or puts an escaper's output where it does not belong (``tag(safe_url(x))``);
* the call's result is clean for these arguments only where its POSITION was checked, and only in element
  text: it may be markup with quotes, wrong inside an attribute value;
* a bound argument is clean in text or a quoted value, never in an unquoted attribute (escaped text keeps
  its spaces: ``x onmouseover=1`` stays two attributes), unless it is a literal or digits;
* what ``append`` / ``+=`` put into a container that outlives the call (a module list, a mutable default,
  a parameter) is judged with every binding off: other calls put things there too;
* a test of a parameter this call binds to a literal (``if wrap is not None:``) rules out the branch.

Refs #3845, #3864.
"""

from __future__ import annotations


import pytest
import safe_markup_call_fuzz_3864 as fuzz

from scripts.lib.safe_markup_taint import find_unsafe_markup

pytestmark = pytest.mark.components

PRELUDE = (
    "from django.utils.safestring import mark_safe\n"
    "from django.utils.html import escape\n"
    "from djust.components.utils import safe_url\n"
)

#: label -> (source, flagged?). A ``True`` row is unsafe when run (``TestTheRowsAreTrue``); a ``False``
#: row is its correct twin. Each mechanism of the change has a row that fails without it.
ROWS: dict[str, tuple[str, bool]] = {
    # -- the issue's cases: the escaper's output handed to a helper that builds the tag
    "a helper puts its parameter in an unquoted attribute": (
        "def tag(u):\n    return f'<a href={u}>l</a>'\n"
        "def f(x):\n    return mark_safe(tag(safe_url(x)))\n",
        True,
    ),
    "the same helper quotes it": (
        "def tag(u):\n    return f'<a href=\"{u}\">l</a>'\n"
        "def f(x):\n    return mark_safe(tag(safe_url(x)))\n",
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
        "def tag(u):\n    return '<a href=' + u + '>l</a>'\n"
        "def f(x):\n    return mark_safe(tag(safe_url(x)))\n",
        True,
    ),
    "a method of a fresh instance does it": (
        "class Card:\n    def tag(self, u):\n        return f'<a href={u}>l</a>'\n"
        "def f(x):\n    return mark_safe(Card().tag(safe_url(x)))\n",
        True,
    ),
    "a method called through self does it": (
        "class Card:\n    def tag(self, u):\n        return f'<a href={u}>l</a>'\n"
        "    def r(self, x):\n        return mark_safe(self.tag(safe_url(x)))\n"
        "def f(x):\n    return Card().r(x)\n",
        True,
    ),
    # -- the helper returns a string built from its parameter, straight to the sink
    "a helper builds from a raw argument": (
        "def bold(u):\n    return f'<b>{u}</b>'\ndef f(x):\n    return mark_safe(bold(x))\n",
        True,
    ),
    "the helper is handed an escaped argument": (
        "def bold(u):\n    return f'<b>{u}</b>'\ndef f(x):\n    return mark_safe(bold(escape(x)))\n",
        False,
    ),
    "two helpers deep, raw": (
        "def inner(v):\n    return f'<i>{v}</i>'\ndef outer(u):\n    return inner(u)\n"
        "def f(x):\n    return mark_safe(outer(x))\n",
        True,
    ),
    # -- the call's result, placed: element text only
    "the escaped call in element text": (
        "def bold(u):\n    return f'<b>{u}</b>'\n"
        "def f(x):\n    return mark_safe(f'<p>{bold(escape(x))}</p>')\n",
        False,
    ),
    "the raw call in element text": (
        "def bold(u):\n    return f'<b>{u}</b>'\ndef f(x):\n    return mark_safe(f'<p>{bold(x)}</p>')\n",
        True,
    ),
    "the call's markup inside a quoted attribute": (
        "def w(u):\n    return f'<i class=\"{u}\">'\n"
        "def f(x):\n    return mark_safe(f'<div title=\"{w(escape(x))}\">')\n",
        True,
    ),
    "a pass-through call in an unquoted attribute": (
        "def ident(u):\n    return u\ndef f(x):\n    return mark_safe(f'<div title={ident(escape(x))}>')\n",
        True,
    ),
    # -- a bound argument inside the helper: not in an unquoted attribute
    "an escaped argument in the helper's unquoted attribute": (
        "def w(u):\n    return f'<i class={u}>'\ndef f(x):\n    return mark_safe(f'<div>{w(escape(x))}</div>')\n",
        True,
    ),
    "digits there are fine": (
        "def w(u):\n    return f'<i width={u}>'\ndef f(x):\n    return mark_safe(f'<div>{w(len(x))}</div>')\n",
        False,
    ),
    "a literal there is fine": (
        "def w(u):\n    return f'<i class={u}>'\ndef f(x):\n    return mark_safe(f'<div>{w(\"k\")}</div>')\n",
        False,
    ),
    "an escaped argument through a join of the helper": (
        "def w(u):\n    return ''.join(['<a href=', u, '>l</a>'])\n"
        "def f(x):\n    return mark_safe(f'<p>{w(escape(x))}</p>')\n",
        True,
    ),
    "an escaped argument through a table of the helper": (
        "def w(u):\n    d = {'a': u}\n    return f'<a href={d[\"a\"]}>l</a>'\n"
        "def f(x):\n    return mark_safe(f'<p>{w(escape(x))}</p>')\n",
        True,
    ),
    "an escaped argument read by a closure into an unquoted attribute": (
        "def w(u):\n    g = lambda: u\n    return f'<a href={g()}>l</a>'\n"
        "def f(x):\n    return mark_safe(f'<p>{w(escape(x))}</p>')\n",
        True,
    ),
    "an escaped argument concatenated, then put in the helper's unquoted attribute": (
        "def w(u):\n    v = u + '-x'\n    return f'<i class={v}>'\n"
        "def f(x):\n    return mark_safe(f'<div>{w(escape(x))}</div>')\n",
        True,
    ),
    "a call that only returns a literal, put in the helper's unquoted attribute": (
        "def lit():\n    return 'k'\ndef w(u):\n    return f'<i class={u}>'\n"
        "def f(x):\n    return mark_safe(f'<div>{w(lit())}</div>')\n",
        False,
    ),
    "a method of a fresh instance handed a literal": (
        "class Card:\n    def tag(self, u):\n        return f'<a href={u}>l</a>'\n"
        "    def run(self, u):\n        return self.tag(u)\n"
        "def f(x):\n    return mark_safe(Card().run('k'))\n",
        False,
    ),
    # -- containers that outlive the call
    "a mutable default keeps what every call appended": (
        "def w(u, acc=[]):\n    acc.append(u)\n    return ''.join(acc)\n"
        "def g(x):\n    return w(x)\n"
        "def f(x):\n    return mark_safe(f'<p>{w(escape(x))}</p>')\n",
        True,
    ),
    "a mutable default keeps other calls' arguments, even when this call passes a literal": (
        "def w(u, acc=[]):\n    acc.append(u)\n    return ''.join(acc)\n"
        "def f(x):\n    w(x)\n    return mark_safe(f'<p>{w(\"k\")}</p>')\n",
        True,
    ),
    "a module list keeps what every call appended": (
        "CACHE = []\ndef w(u):\n    CACHE.append(u)\n    return ''.join(CACHE)\n"
        "def f(x):\n    return mark_safe(f'<p>{w(escape(x))}</p>')\n",
        True,
    ),
    "a list the helper makes afresh holds only this call's argument": (
        "def w(u):\n    parts = []\n    parts.append(f'<b>{u}</b>')\n    return ''.join(parts)\n"
        "def f(x):\n    return mark_safe(f'<p>{w(escape(x))}</p>')\n",
        False,
    ),
    "an attribute the helper stores keeps what every call stored": (
        "class C:\n    def w(self, u):\n        self.h = u\n        return self.h\n"
        "    def r(self, x):\n        return mark_safe(f'<p>{self.w(escape(x))}</p>')\n",
        True,
    ),
    # -- a parameter the call binds to a literal decides a branch
    "a callback left None does not run": (
        "def w(u, wrap=None):\n    if wrap is not None:\n        u = wrap(u)\n    return f'<p>{u}</p>'\n"
        "def f(x):\n    return mark_safe(f'<div>{w(escape(x))}</div>')\n",
        False,
    ),
    "a callback given does": (
        "def w(u, wrap=None):\n    if wrap is not None:\n        u = wrap(u)\n    return f'<p>{u}</p>'\n"
        'def f(x):\n    return mark_safe(f\'<div>{w(escape(x), wrap=lambda s: s.replace("&lt;", "<"))}</div>\')\n',
        True,
    ),
    "a flag left False keeps the escaping branch": (
        "def w(u, trusted=False):\n    if trusted:\n        return f'<p>{u}</p>'\n    return f'<p>{escape(u)}</p>'\n"
        "def f(x):\n    return mark_safe(f'<div>{w(x)}</div>')\n",
        False,
    ),
    "a flag set True takes the raw branch": (
        "def w(u, trusted=False):\n    if trusted:\n        return f'<p>{u}</p>'\n    return f'<p>{escape(u)}</p>'\n"
        "def f(x):\n    return mark_safe(f'<div>{w(x, True)}</div>')\n",
        True,
    ),
    "a flag the helper rebinds decides nothing": (
        "def w(u, t=False):\n    t = not t\n    if t:\n        return f'<p>{u}</p>'\n    return escape(u)\n"
        "def f(x):\n    return mark_safe(f'<div>{w(x)}</div>')\n",
        True,
    ),
    # -- what is not bound
    "a recursive call is judged for any argument": (
        "def h(u, n):\n    if n:\n        return f'<b>{u}</b>' + h('<x>' + u, 0)\n    return f'<i>{u}</i>'\n"
        "def f(x):\n    return mark_safe(h(x, 1))\n",
        True,
    ),
    "a recursive call passes its own arguments": (
        "def h(u, n, other):\n    if n:\n        return h(other, 0, '')\n    return f'<i>{u}</i>'\n"
        "def f(x):\n    return mark_safe(f'<p>{h(escape(x), 1, x)}</p>')\n",
        True,
    ),
    "a recursive call passes its own arguments, straight to the sink": (
        "def h(u, n, other):\n    if n:\n        return h(other, 0, '')\n    return f'<i>{u}</i>'\n"
        "def f(x):\n    return mark_safe(h(escape(x), 1, x))\n",
        True,
    ),
    "a keyword that only **kw takes binds nothing": (
        "def w(u, /, **kw):\n    return f'<p>{u}</p>'\n"
        "def f(x):\n    return mark_safe(f'<div>{w(x, u=escape(x))}</div>')\n",
        True,
    ),
    "*args are raw": (
        "def w(*a):\n    return f'<p>{a[0]}</p>'\ndef f(x):\n    return mark_safe(f'<div>{w(escape(x))}</div>')\n",
        True,
    ),
}


#: Unsafe only across calls (another caller put a raw value into the shared container): the row pins
#: the guard's rule, one run of ``f`` cannot show it.
ACROSS_CALLS = {
    "a mutable default keeps what every call appended",
    "a module list keeps what every call appended",
    "an attribute the helper stores keeps what every call stored",
}
#: Flagged although this run is safe: the guard does not bind ``*args``.
CONSERVATIVE = {"*args are raw"}


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
    """The oracle for the rows: run, a ``True`` row lets a payload become markup (some of the
    ``False`` rows are correct but beyond the guard and stay flagged elsewhere, so only the ``True``
    direction, and the ``False`` rows the guard clears, are run)."""

    @pytest.mark.parametrize("label", sorted(k for k, (_, v) in ROWS.items() if v))
    def test_an_unsafe_row_is_unsafe(self, label):
        if label in ACROSS_CALLS or label in CONSERVATIVE:
            pytest.skip("not one run's verdict: see ACROSS_CALLS / CONSERVATIVE")
        assert fuzz.injected(run_case(ROWS[label][0])), label

    @pytest.mark.parametrize("label", sorted(k for k, (_, v) in ROWS.items() if not v))
    def test_a_clean_row_is_clean(self, label):
        assert not fuzz.injected(run_case(ROWS[label][0])), label


class TestTheCallFuzz:
    """``safe_markup_call_fuzz_3864``: (source) x (helper chain) x (placement), RUN against hostile
    payloads. The one miss left is the guard's documented limit: ``escape()`` output written straight
    into an unquoted attribute (``<div title={escape(x)}>``) is trusted anywhere, with or without this
    change."""

    @staticmethod
    def known(case: tuple) -> bool:
        """``<div title={escape(x)}>`` written in place (no helper in between)."""
        source, helpers, placement = case
        return source == "escape" and placement == "unquoted" and set(helpers) == {"none"}

    def test_every_single_helper_in_every_placement(self):
        """The whole product of one helper: no miss but the known one, and not vacuous."""
        result = fuzz.survey(samples=0, seed=0)
        assert [c for c in result["false_negatives"] if not self.known(c)] == []
        assert result["cases"] == len(fuzz.SOURCES) * len(fuzz.HELPERS) * len(fuzz.PLACEMENTS)
        assert result["unsafe"] >= 80  # cases that really let a payload through
        assert result["cases"] - result["flagged"] >= 60  # cases the guard cleared

    @pytest.mark.parametrize("seed", [3864, 1])
    def test_random_two_helper_chains(self, seed):
        result = fuzz.survey(samples=200, seed=seed, exhaustive=False)
        assert [c for c in result["false_negatives"] if not self.known(c)] == []
        assert result["unsafe"] >= 30 and result["cases"] - result["flagged"] >= 30
