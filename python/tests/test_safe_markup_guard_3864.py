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
    # -- the review of db1add19b
    # review of db1add19b, finding 1: a helper's result is not free in every position
    "a helper's own quoted attribute, placed in a quoted attribute": (
        'class V:\n    def w(self, k):\n        return f\'<i title="{escape(self.q)}" data-k="{k}">\'\n'
        "    def f(self, x):\n        self.q = x\n        return mark_safe(f'<div title=\"{self.w(1)}\">d</div>')\n"
        "def f(x):\n    return V().f(x)\n",
        True,
    ),
    "a helper's escaped module value, placed in a quoted attribute": (
        'S = {}\ndef w(k):\n    return f\'<i title="{escape(S["q"])}" data-k="{k}">\'\n'
        "def f(x):\n    S['q'] = x\n    return mark_safe(f'<div title=\"{w(1)}\">d</div>')\n",
        True,
    ),
    "a helper's escaped text, placed in an unquoted attribute": (
        "class V:\n    def label(self, n):\n        return f'{n}-{escape(self.name)}'\n"
        "    def f(self, x):\n        self.name = x\n        return mark_safe(f'<div title={self.label(1)}>d</div>')\n"
        "def f(x):\n    return V().f(x)\n",
        True,
    ),
    "a zero-argument helper's markup, placed in a quoted attribute": (
        "S = ['']\ndef w():\n    return f'<i title=\"{escape(S[0])}\">'\n"
        "def f(x):\n    S[0] = x\n    return mark_safe(f'<div title=\"{w()}\">d</div>')\n",
        True,
    ),
    "a helper's whole attributes, placed between attributes": (
        "S = ['']\ndef w():\n    return f' title=\"{escape(S[0])}\"'\n"
        "def f(x):\n    S[0] = x\n    return mark_safe(f'<div{w()}>d</div>')\n",
        False,
    ),
    # finding 2: a bound argument that is only HTML-escaped, in a URL / script / style
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
    "an escaped pass-through in a script body": (
        "def ident(u):\n    return u\ndef f(x):\n    return mark_safe(f'<script>var a={ident(escape(x))};</script>')\n",
        True,
    ),
    "an escaped argument in the helper's style body": (
        "def css(u):\n    return f'<style>.c{{color:{u}}}</style>'\ndef f(x):\n    return mark_safe(css(escape(x)))\n",
        True,
    ),
    "safe_url handed to the helper's href stays clean": (
        "def tag(u):\n    return f'<a href=\"{u}\">l</a>'\ndef f(x):\n    return mark_safe(tag(safe_url(x)))\n",
        False,
    ),
    # finding 3: the refusals, pinned
    "a starred call binds nothing": (
        "def w(a, u=''):\n    return f'<p>{u}</p>'\ndef f(x):\n    return mark_safe(f'<div>{w(*(escape(x), x))}</div>')\n",
        True,
    ),
    "a call with an unknown keyword binds nothing": (
        "def w(u):\n    return f'<p>{u}</p>'\ndef f(x):\n    return mark_safe(f'<div>{w(escape(x), v=x)}</div>')\n",
        True,
    ),
}


ROWS["json in one case, a built safe_url in another, in a quoted attribute"] = (
    "import json\nfrom djust.security.json_script import escape_json_for_script\n"
    "def f(x):\n    v = escape_json_for_script(json.dumps(x)) if len(x) > 0 else 'a' + safe_url(x)\n"
    "    return mark_safe(f'<b title=\"{v}\">b</b>')\n",
    True,
)

#: Unsafe, and clean on main and here: an HTML escaper written IN PLACE where HTML escaping is not
#: enough (a URL, an event handler, a script body). Pinned so the day the guard learns one it moves.
KNOWN_GAPS = {
    "escape() written into an href": (
        "def f(x):\n    return mark_safe(f'<a href=\"{escape(x)}\">l</a>')\n",
    ),
    "escape() written into an event handler string": (
        "def f(x):\n    return mark_safe(f'<b onclick=\"go(\\'{escape(x)}\\')\">b</b>')\n",
    ),
    "escape() written into a script body": (
        "def f(x):\n    return mark_safe(f'<script>var a={escape(x)};</script>')\n",
    ),
}

#: Unsafe only across calls (another caller put a raw value into the shared container): the row pins
#: the guard's rule, one run of ``f`` cannot show it.
ACROSS_CALLS = {
    "a mutable default keeps what every call appended",
    "a module list keeps what every call appended",
    "an attribute the helper stores keeps what every call stored",
}
#: Flagged although this run is safe: the guard does not bind ``*args``; a call with a keyword the
#: function does not take raises (``TypeError``) and binds nothing.
CONSERVATIVE = {"*args are raw", "a call with an unknown keyword binds nothing"}


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


class TestKnownGaps:
    """Unsafe and cleared, here and on main: an HTML escaper written in place where HTML escaping is
    not enough. Through a helper's parameter the same values are flagged (``ROWS``)."""

    @pytest.mark.parametrize("label", sorted(KNOWN_GAPS))
    def test_the_gap_is_real(self, label):
        assert fuzz.injected(run_case(KNOWN_GAPS[label][0])), label

    @pytest.mark.parametrize("label", sorted(KNOWN_GAPS))
    def test_the_guard_still_clears_it(self, label):
        assert not flagged(KNOWN_GAPS[label][0]), f"the guard learned {label}: move it to ROWS"


class TestTheCallFuzz:
    """``safe_markup_call_fuzz_3864``: (source) x (helper chain) x (placement), RUN against hostile
    payloads. The misses left are the guard's documented limit, the same on main: an escaper written
    IN PLACE (no helper in between) where its escaping is not enough: ``escape(x)`` in an unquoted
    attribute, a URL, an event handler or a style body; ``safe_url(x)`` in an event handler or a style
    body."""

    GAPS = {
        ("escape", "unquoted"),
        ("escape", "url"),
        ("escape", "event"),
        ("escape", "style"),
        ("url", "event"),
        ("url", "style"),
    }

    @classmethod
    def known(cls, case: tuple) -> bool:
        source, helpers, placement = case
        return set(helpers) == {"none"} and (source, placement) in cls.GAPS

    @pytest.mark.parametrize("placement", sorted(fuzz.PLACEMENTS))
    def test_every_single_helper(self, placement):
        """The whole product of one helper, per placement: no miss but the known ones."""
        result = fuzz.survey(samples=0, seed=0, placement=placement)
        assert [c for c in result["false_negatives"] if not self.known(c)] == []
        assert result["cases"] == len(fuzz.SOURCES) * len(fuzz.HELPERS)

    def test_the_product_is_not_vacuous(self):
        """Enough cases let a payload through, and enough are cleared, for a miss to show."""
        unsafe = cleared = 0
        for placement in ("text", "url"):
            result = fuzz.survey(samples=0, seed=0, placement=placement)
            unsafe += result["unsafe"]
            cleared += result["cases"] - result["flagged"]
        assert unsafe >= 40 and cleared >= 40, (unsafe, cleared)

    @pytest.mark.parametrize("seed", [3864, 1])
    def test_random_two_helper_chains(self, seed):
        result = fuzz.survey(samples=200, seed=seed, exhaustive=False)
        assert [c for c in result["false_negatives"] if not self.known(c)] == []
        assert result["unsafe"] >= 30 and result["cases"] - result["flagged"] >= 30


class TestTheGateOffListApplies:
    """``scripts/safe-markup-call-binding-gateoff.json`` turns each mechanism off in turn (run it with
    ``scripts/gate_off.py``); this keeps the list from rotting as the guard changes."""

    def test_every_mutation_text_still_applies(self):
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        spec = json.loads((root / "scripts/safe-markup-call-binding-gateoff.json").read_text())
        assert len(spec["mutations"]) >= 20
        for entry in spec["mutations"]:
            source = (root / entry["file"]).read_text()
            found = source.count(entry["old"])
            assert found == entry.get("expect", 1), f"{entry['label']!r}: found {found} time(s)"
            assert entry["old"] != entry["new"], entry["label"]
