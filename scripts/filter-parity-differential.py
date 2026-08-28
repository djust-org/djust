#!/usr/bin/env python3
"""Two-build Django-vs-djust filter differential, across the CHAIN axis.

Why this is a script and not a test
-----------------------------------
It answers two questions a pytest cannot, because both need **two builds of
djust in the same comparison**:

1. *Non-regression as a set.* Not "how many cells agree" but "which cells
   agreed before and disagree now" — a change can fix 4,000 cells and break 40,
   and only a set comparison against a rebuilt baseline shows the 40. A
   single-build test sees one number and cannot tell those apart.
2. *More-permissive-than-Django as its own check.* For every cell it asks
   whether djust emits a live fragment of the hostile INPUT that Django's
   output does not. That is a different question from "do they agree", and
   deriving it from the agreement count is how a permissiveness regression
   hides inside a parity improvement.

The single-build half of this — the registry-wide sweep asserting djust grants
no capability Django does not — IS a test, and lives in
`python/tests/test_escape_chain_and_sequence_filters_2281_2283.py::
TestEscapeIsEager::test_no_chain_containing_escape_is_more_permissive_than_django`.
That is the load-bearing half and runs in CI. This script is the two-build
half, run by hand when a change touches the escaping model.

It found two shipped XSSes: #2281 (`{{ p|escape|safe }}`) and #2291
(`{{ p|linenumbers|safe }}`), neither by inspection.

How to run it
-------------
Build the BASELINE first, then the branch, and compare::

    git checkout origin/main && make dev-build
    PYTHONPATH=$PWD/python python scripts/filter-parity-differential.py base.json

    git checkout my-branch && make dev-build
    PYTHONPATH=$PWD/python python scripts/filter-parity-differential.py after.json

    python scripts/filter-parity-differential.py --compare base.json after.json

`--compare` prints three things and exits non-zero on either failure mode:

* the agreement counts before and after. **They must differ** — identical
  counts mean both files were produced by the same build and the baseline is
  not real, which is a silent way to "prove" zero regressions;
* every cell that agreed before and disagrees after (must be empty). Since
  #2325 this is split: a TAG-operand cell whose own `{{ p|f }}` twin diverges
  on BOTH builds agreed only by COINCIDENCE — the operand bug made djust
  render nothing, and Django rendered nothing for its own reason — so it is
  reported as `coincidental` and does not gate. See `unmasked`. Widening the
  corpus is what surfaced this: 445 of #2325's 445 reported regressions were
  of exactly that shape, and calling them regressions would have taught the
  next reader to ignore the number;
* every cell that newly emits a live payload fragment Django does not (must be
  empty). Cells where Django itself RAISES are counted and reported separately
  rather than dropped, because there is no Django output to be more permissive
  than. `live()` substring-matches, so a fragment such as `onerror=` also
  matches inside FULLY ESCAPED text; the flagged set is split by whether
  djust's output carries an unescaped tag opener at all, both halves are
  printed, and only the live half gates the exit.

The corpus
----------
Every filter in Django's LIVE `defaultfilters` registry (57 on Django 5.2),
read from the registry rather than transcribed, so a Django release that adds
or drops one is picked up instead of diverging silently. The `INPUTS` shapes
cover the axes filters actually branch on — string, list, tuple, dict, int,
float, `None`, empty, and (since #2293) a string carrying line breaks, tabs and
runs of spaces — with hostile payloads in each. The count is deliberately NOT
written here: it read "Sixteen" over a dict of 21, and #2327 and #2293 both
found that independently within a day of each other, which is the argument for
`len(INPUTS)` being the only place it is stated. Chains of length 2 and 3
over a hot subset are NOT optional: a candidate fix can be clean on 1-chains
and regress a thousand cells at length 2 (#2250 measured exactly that), and the
`escape`/`safe` interaction #2281 is about is invisible without them.

Since #2290 the corpus also carries a **custom-filter** axis: four
`@register.filter` probes registered on both engines and composed with the hot
built-ins. A custom filter dispatches through
`filter_registry::apply_custom_filter` — a Python call across the PyO3 boundary
— which no built-in cell reaches, so the whole of what a project's own filters
see was previously unmeasured by this tool.

Since #2325 it carries a **tag-operand** axis: the same filters and hot
2-chains on `{% for x in p|… %}`, `{% with q=p|… %}` and `{% if p|… %}`. Every
other cell is a `{{ p|… }}` chain, and a tag operand is a different resolution
path — djust had one filter-aware resolver and four tags that open-coded a bare
variable lookup, so the filter chain was dropped and the tag proceeded on the
miss, rendering an empty loop or echoing the expression's own source text into
the page. None of that was visible here, because the tool constructed no tag
cell at all. Same failure mode as the #2281 XSS it once reported clean over:
this tool is only ever as good as the shapes it builds, and a corpus gap is
silent by construction. Tag cells carry a third `\\t`-separated field in their
id so `{{ }}` ids stay byte-identical and an older baseline file remains
comparable.
"""

from __future__ import annotations

import itertools
import json
import re
import sys
from decimal import Decimal

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=False,
        USE_I18N=False,
        USE_TZ=False,
        TIME_ZONE="UTC",
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {"context_processors": []},
            }
        ],
        INSTALLED_APPS=[],
    )
    django.setup()

from django import template as _django_template  # noqa: E402
from django.template import Context, Engine, Template  # noqa: E402
from django.template.defaultfilters import register  # noqa: E402
from django.utils.html import conditional_escape  # noqa: E402
from django.utils.safestring import SafeData, mark_safe  # noqa: E402

from djust import _rust  # noqa: E402
from djust.mixins.rust_bridge import _collect_safe_keys  # noqa: E402


def _safe_keys_for(value) -> list[str]:
    """The dotted paths ``_sync_state_to_rust`` would hand to Rust for *value*.

    The production collector verbatim (``rust_bridge._collect_safe_keys``), not
    a transcription: a sweep that computed its own answer would be measuring
    itself rather than the channel a real render uses.
    """
    return _collect_safe_keys(value, "p")


# ---------------------------------------------------------------------------
# The CUSTOM-filter corpus (#2290)
# ---------------------------------------------------------------------------
#
# A custom filter is a different mechanism from a built-in: it crosses the PyO3
# boundary into a real Python callable, and until #2290 the value arrived there
# as a bare `str` with every Django `SafeData` marker stripped. The built-in
# sweep above cannot see that at all — it never dispatches through
# `filter_registry::apply_custom_filter` — so a fix or a regression on that path
# was invisible to this tool.
#
# These four are registered on BOTH sides: with the Rust registry via
# `register_custom_filter`, and as a builtin `Library` on Django's default
# `Engine`, so the same `{{ p|... }}` source runs through both engines with no
# `{% load %}`.
_CUSTOM_LIBRARY = _django_template.Library()


@_CUSTOM_LIBRARY.filter(name="cf_ident")
def _cf_ident(value):
    """Returns its input untouched — the sharpest probe there is.

    Django escapes the result iff the FINAL value lacks `__html__`, and the
    final value here IS the one the filter was handed.
    """
    return value


@_CUSTOM_LIBRARY.filter(name="cf_canon", needs_autoescape=True)
def _cf_canon(value, autoescape=True):
    """Django's canonical `needs_autoescape` body, verbatim."""
    from django.utils.html import escape

    autoescape = autoescape and not isinstance(value, SafeData)
    return mark_safe("[" + (escape(value) if autoescape else str(value)) + "]")


@_CUSTOM_LIBRARY.filter(name="cf_cond")
def _cf_cond(value):
    """`conditional_escape` — the widened-scope sink #2290 names first."""
    return conditional_escape(value)


@_CUSTOM_LIBRARY.filter(name="cf_join", needs_autoescape=True)
def _cf_join(value, autoescape=True):
    """Per-ITEM `conditional_escape`, which is the only way to observe the
    `items` granularity (`safeseq`/`escapeseq` mark elements, never the list).
    """
    if not isinstance(value, (list, tuple)):
        return conditional_escape(value)
    return mark_safe("".join(conditional_escape(v) for v in value))


CUSTOM = ["cf_ident", "cf_canon", "cf_cond", "cf_join"]

Engine.get_default().template_builtins.append(_CUSTOM_LIBRARY)
for _name, _fn in _CUSTOM_LIBRARY.filters.items():
    _rust.register_custom_filter(
        _name,
        _fn,
        bool(getattr(_fn, "is_safe", False)),
        bool(getattr(_fn, "needs_autoescape", False)),
    )

#: One benign argument per filter that requires one. Chosen so the filter runs
#: rather than raising — the point is to compare escaping, not argument parsing.
FILTER_ARGS = {
    "add": '"1"',
    "center": '"20"',
    "cut": '"b"',
    "date": '"Y-m-d"',
    "default": '"D"',
    "default_if_none": '"D"',
    "dictsort": '"k"',
    "dictsortreversed": '"k"',
    "divisibleby": '"2"',
    "floatformat": '"2"',
    "get_digit": '"1"',
    # Contains HTML on purpose. With a plain `", "` the sweep cannot see the
    # separator axis at all, which is how `join`'s separator divergence (Django
    # `mark_safe`s a quoted filter argument via `Variable.literal`; djust
    # escapes it) went unmeasured.
    "join": '"<br>"',
    "ljust": '"20"',
    "pluralize": '"s"',
    "rjust": '"20"',
    "slice": '":3"',
    "stringformat": '"s"',
    "time": '"H:i"',
    "truncatechars": '"5"',
    "truncatechars_html": '"5"',
    "truncatewords": '"2"',
    "truncatewords_html": '"2"',
    "urlizetrunc": '"15"',
    "wordwrap": '"5"',
    "yesno": '"y,n,m"',
}

INPUTS = {
    "s-img": "<img src=x onerror=alert(1)>",
    "s-script": "</script><script>alert(1)</script>",
    "s-lt": "a < b",
    "s-quote": '" onmouseover="x',
    "s-plain": "abc",
    "s-empty": "",
    # A string with STRUCTURE, which no other entry has (#2293). Every other
    # `s-` input is one line of ASCII-ish text with single spaces, so the whole
    # corpus could not construct a cell where `wordwrap` — a re-joiner that
    # flattened line breaks, collapsed runs of spaces, dropped indentation and
    # measured in bytes — differed from Django at all: the tool reported
    # `agree BEFORE == agree AFTER` over a fix that moved four behaviours.
    # Carries, in order: leading indentation, a run of spaces, a `\n`, a tab, a
    # `U+2028` (a line break to `splitlines` and NOT to `split("\n")`), a
    # `\xa0` (whitespace to `str.strip` and NOT a `textwrap` chunk boundary),
    # multi-byte words, and a live payload so the permissiveness half of the
    # tool reads it too. Spelled with escapes: the invisible ones do not
    # survive an editor as literals.
    "s-lines": (
        # leading indentation (ONE space: the run of spaces lives in the next
        # piece, so a mutation can remove either without removing the other),
        # a `\n`, a tab, and a live payload
        " <img src=x\n\tonerror=alert(1)>"
        # multi-byte words, and the two `str.isspace()` members that neither
        # the splitlines set nor the textwrap set contains
        "\u2028\u5b57\u65e5\xa0\xe9  x\x1f"
        # the remaining `py_is_line_break` boundaries, so no line boundary the
        # engine branches on is unreachable from the corpus
        "\r\x0b\x0c\x1c\x1d\x1e\x85\u2029 y"
    ),
    "s-unicode": "héllo→",
    "s-digits": "123",
    "l-plain": ["<b>", "x"],
    "l-nested": ["a", ["b", "c"]],
    "t-plain": ("<b>", "x"),
    # A tuple at the NESTING position (#2317). `t-plain` puts a tuple at the
    # top and `l-nested` puts a LIST in the sublist slot, so between them the
    # corpus could not construct the one cell `unordered_list`'s sublist test
    # reads — Django treats a tuple as a sublist and djust matched
    # `Value::List` alone, so this rendered the escaped tuple repr in its own
    # `<li>` where Django nests a `<ul>`. Every axis of a surface, not the one
    # you happened to notice.
    "t-nested": ["<b>", ("c", ("d",))],
    # ITEMS that are not strings (#2324). Every other `l-`/`t-` entry holds
    # strings or a nested sequence, so the corpus could not construct the cell
    # `safeseq`'s per-item stringify moves for a NUMBER — and that is the half
    # of the fix with a second spelling to get right: `mark_safe(1e20)` is
    # `'1e+20'`, CPython's `repr`, where `{{ f }}` renders the expanded
    # `100000000000000000000`, because `Display` is `numberformat.format()`.
    # Django really does spell one float two ways depending on the path, and
    # before this row the tool measured neither. The `Decimal` is the same split
    # (`str` is `1E-9`, the render is `0.000000001`); `None`/`True` are here
    # because `str(None)` is the text `None` and a mistake there puts it on the
    # page. Carries a live payload so the permissiveness half reads the row too.
    "l-scalars": [1e20, Decimal("1E-9"), 42, None, True, "<img src=x onerror=alert(1)>"],
    # A MAP at an ITEM position, which no other entry has — `d-plain` puts the
    # dict at the top, where no per-item rule can see it.
    "l-dict": [{"k": "<v>"}, "x"],
    "d-plain": {"k": "<v>", "j": 2},
    # The map a serialized Django MODEL arrives as (#2322). No other entry
    # carries the `"__str__"` key `Value::object_str` branches on, so until this
    # row the corpus could not construct a single cell that reaches the model
    # arm of `{{ p }}` or `{{ p|length }}` — the shape every djust page holding
    # a model renders through, and the one whose key set #2322 made uniform.
    # Its `__str__` carries a live payload because a model's `str()` is app
    # data and can hold anything a user typed; `__model__` cannot, being
    # `obj.__class__.__name__`.
    "d-model": {
        "id": 7,
        "pk": 7,
        "__str__": "<img src=x onerror=alert(1)>",
        "__model__": "Doc",
    },
    "i-int": 42,
    "f-float": 1.5,
    "n-none": None,
    "l-empty": [],
    # Context ITEM safety (#2287). `mark_safe` on the ELEMENTS and never on the
    # list, which is what `safeseq` produces and what a view returning a list of
    # sanitized fragments produces. Django's `join` / `unordered_list`
    # `conditional_escape` per element, so these come through LIVE in Django and
    # the sweep's bar for them is Django's own output, not "nothing is live".
    #
    # Until this axis existed the tool could not construct a single cell in
    # which the CONTEXT had marked anything safe — `render_template` takes no
    # `safe_keys` — so every cell #2287 touches read as "unchanged". A sweep is
    # only as good as its axes, the same lesson `HOT2` carries above.
    "l-marked": [mark_safe("<b>x</b>"), mark_safe("<i>y</i>")],
    "l-marked-img": [mark_safe("<img src=x onerror=alert(1)>")],
    "l-mixed": [mark_safe("<b>ok</b>"), "<img src=x onerror=alert(1)>"],
    "s-marked": mark_safe("<img src=x onerror=alert(1)>"),
    # A marked TUPLE, which is a genuinely different cell from `l-marked` and
    # not a shape-coverage nicety (#2305). `render_both` hands the context to
    # `render_template_with_dirs` UN-normalized — the same thing a direct API
    # caller does — so a Python tuple survives as `Value::Tuple` and comes back
    # out of `IntoPyObject` as a real `PyTuple` at the custom-filter boundary.
    # `filter_registry::mark_input_safety` handles `PyList` only, so every
    # `cf_*` cell on this key measured djust handing the filter a tuple of
    # plain `str` where Django hands it a tuple of `SafeString`. With only
    # `l-marked` on the axis the tool could not construct that cell at all.
    "t-marked": (mark_safe("<b>x</b>"), mark_safe("<i>y</i>")),
}

#: Inputs whose SAFETY the context declares. Rendered through
#: `render_template_with_dirs`, the only Python entry point that takes
#: `safe_keys`; everything else goes through `render_template` unchanged.
CONTEXT_SAFE_KEYS = {
    key: _safe_keys_for(value) for key, value in INPUTS.items() if _safe_keys_for(value)
}

#: The fragments of each hostile input a browser executes if they survive raw.
#: Deliberately a little broad (`onerror=` matches inside escaped text too):
#: over-reporting a leak is the safe direction for this check.
LIVE_FRAGMENTS = {
    "s-img": ["<img", "onerror="],
    "s-script": ["<script", "</script"],
    "s-lt": ["a < b"],
    "s-quote": ['" onmouseover="'],
    "s-lines": ["<img", "onerror="],
    "l-plain": ["<b>"],
    "t-plain": ["<b>"],
    "t-nested": ["<b>"],
    "l-scalars": ["<img", "onerror="],
    "l-dict": ["<v>"],
    "d-plain": ["<v>"],
    # `l-marked` / `s-marked` carry markup Django ITSELF emits live, so a
    # fragment entry for them would report every correct cell as a leak. The
    # permissiveness check is always djust-vs-DJANGO (`_leaks` subtracts
    # Django's own live fragments), but leaving these out keeps the report
    # readable. `l-mixed`'s UNMARKED element is the one that must never appear.
    #
    # `d-model` is out for a THIRD reason, measured rather than assumed (#2322):
    # a fragment entry on it reports 65 cells, identically on both builds, and
    # none of them is a leak this tool can judge. The two engines disagree about
    # what that value IS — djust reads a `"__str__"`-carrying map as an OBJECT
    # and renders the `__str__`, Django reads a plain `dict` and renders its
    # repr — which is the divergence already pinned as known-wrong in
    # `test_measuring_filter_parity_2294.py::
    # test_a_dict_carrying___str___is_WRONGLY_read_as_an_object`. Most of the 65
    # are the deliberately-broad `onerror=` matching inside ESCAPED text; the
    # rest are `|safe` chains where djust emits the `__str__` live and Django
    # emits a truncated dict repr. Neither is reachable from a real page, where
    # Django is handed the MODEL and renders `str(model)` too — the tool can
    # only hand both engines a dict, so the comparison is structurally unfair
    # on this key. 65 permanent false leaks would drown a real one.
    "l-mixed": ["<img", "onerror="],
}

#: Names worth composing: the safety- and shape-relevant ones. Keeps the chain
#: axis tractable while still covering every filter that can mark output safe.
#:
#: **Every name in any of `renderer.rs`'s safety sets MUST be here.** That is not
#: style — it is the lesson of the `dictsort` XSS this tool failed to report.
#: `dictsort` was added to `ITEM_SAFETY_PRESERVING_FILTERS` and NOT to these
#: lists, so the sweep never composed it, and the compare printed
#: `REGRESSIONS: 0 / INTRODUCED: 0` over a live XSS. With `dictsort` present the
#: same tool reports 18 regressions and 12 introduced leaks. A sweep is only as
#: good as its axes, and the filter you just granted safety to is precisely the
#: one that must be on them.
#:
#: `python/tests/test_escape_chain_and_sequence_filters_2281_2283.py::
#: test_every_safety_set_member_is_in_the_differential_hot_sets` enforces this
#: mechanically, so the coupling cannot rot back.
HOT2 = [
    "safe",
    "escape",
    "force_escape",
    "safeseq",
    "escapeseq",
    "join",
    "unordered_list",
    "upper",
    "lower",
    "striptags",
    "linebreaks",
    "linebreaksbr",
    "urlize",
    "urlizetrunc",
    "json_script",
    "first",
    "last",
    "slice",
    "make_list",
    "truncatechars_html",
    "title",
    "cut",
    "add",
    "default",
    "default_if_none",
    "pprint",
    "length",
    "linenumbers",
    "dictsort",
    "dictsortreversed",
    # Not in any safety set, so the enforcing test does not require it. It is
    # here because it reads the input's TYPE rather than its value, which is the
    # axis `|safe`'s stringify moves (#2303): this list missed the regression
    # `{{ n|safe|divisibleby:"2" }}` and a wider sweep found it.
    "divisibleby",
    # Also not in any safety set. It is here because it is the one built-in that
    # INSERTS structure — newlines — into a string, and half the hot list reads
    # whitespace (`linebreaks`, `linebreaksbr`, `striptags`, `urlize`,
    # `truncatechars_html`, `pprint`). #2293 changed what it emits for every
    # input carrying a line break, a run of spaces or a tab, and at length 1 the
    # sweep can only ask whether the output matches; at length 2 it can ask what
    # the NEXT filter does with it.
    "wordwrap",
]
HOT3 = [
    "safe",
    "escape",
    "force_escape",
    "safeseq",
    "escapeseq",
    "join",
    "unordered_list",
    "upper",
    "striptags",
    "first",
    "slice",
    "make_list",
    "pprint",
    "linebreaks",
    "dictsort",
    "dictsortreversed",
]
#: `l-marked` / `l-mixed` are on the CHAIN axes deliberately: #2287's risk is
#: not the single filter but what a SECOND filter does with a grant the first
#: preserved (`slice`) or minted (`join`), which is only visible at length 2+.
INPUTS_2 = [
    "s-img",
    "s-lt",
    "l-plain",
    "d-plain",
    "i-int",
    "n-none",
    "l-marked",
    "l-mixed",
    # #2305 / #2299 both turn on what a SECOND filter does with a grant carried
    # on a TUPLE, and the tuple-vs-list distinction survives the whole chain
    # (`slice` of a tuple is a tuple, `first` of one is its element).
    "t-marked",
    # The only input with STRUCTURE (#2293). Without it no chain the tool builds
    # contains a newline, a tab, a run of spaces or a `U+2028`, so every filter
    # that reads whitespace is composed only over inputs that have none.
    "s-lines",
    # The ITEM-TYPE axis (#2324). Every other 2-chain input is a scalar, a
    # string, or a sequence of strings, so no chain the tool built could ask
    # what a SECOND filter does with an item that is a number, a `Decimal`, a
    # map or a nested sequence — which is the whole of what `safeseq`'s
    # per-item `str()` moves, and the axis on which a wrong spelling
    # (`Display` rather than `py_str`) is visible.
    #
    # `l-nested` and `t-nested` were deliberately held off this list until now,
    # and the reason was #2324 itself: chaining them reported
    # `safeseq|unordered_list` disagreeing identically for both containers,
    # which was a pre-existing defect rather than anything #2317 introduced.
    # With that defect fixed they belong here, and they arrive TOGETHER — the
    # asymmetry the old note warned about is a real hazard, since a list-only
    # addition is exactly how #2317's tuple gap stayed invisible.
    "l-nested",
    "t-nested",
    "l-scalars",
    "l-dict",
    # The serialized-model map (#2322), on the chain axis too: what a SECOND
    # filter does with a value the renderer reads as an OBJECT rather than a
    # dict is a different question from what one filter does, and `object_str`
    # is the branch nothing else in this corpus reaches.
    "d-model",
]
#: The 3-chain axis stays a small hot subset — 16³ chains is already the
#: dominant cost — and the item-type axis above is measured at length 2, where
#: "what does the NEXT filter do with this item" is already answerable.
INPUTS_3 = ["s-img", "l-plain", "i-int", "l-marked"]

#: Time- or randomness-dependent: recorded as a marker, never as a value.
NONDET = {"random", "timesince", "timeuntil"}
NONDET_MARKER = re.compile(r"<NONDET len=\d+>")


def spec(name: str) -> str:
    arg = FILTER_ARGS.get(name)
    return f"{name}:{arg}" if arg else name


def render_both(tpl: str, ctx: dict, safe_keys: list[str] | None = None) -> tuple[str, str]:
    try:
        dj = Template(tpl).render(Context(ctx))
    except Exception as exc:  # noqa: BLE001 — a raise is a comparable outcome
        dj = f"<<EXC {type(exc).__name__}: {exc}>>"
    try:
        # `render_template` has no `safe_keys` parameter, so the CONTEXT-safety
        # axis has to go through `render_template_with_dirs` — the only Python
        # entry point that carries it (#2287). Same renderer either way; the
        # empty `template_dirs` keeps `{% include %}` resolution identical.
        if safe_keys:
            du = _rust.render_template_with_dirs(tpl, ctx, [], safe_keys)
        else:
            du = _rust.render_template(tpl, ctx)
    except Exception as exc:  # noqa: BLE001
        du = f"<<EXC {type(exc).__name__}: {exc}>>"
    return dj, du


def cells():
    for name in sorted(register.filters):
        for key in INPUTS:
            yield (name,), key
    for a, b in itertools.product(HOT2, HOT2):
        for key in INPUTS_2:
            yield (a, b), key
    for a, b, c in itertools.product(HOT3, HOT3, HOT3):
        for key in INPUTS_3:
            yield (a, b, c), key
    # The custom-filter axis (#2290). Each probe is swept alone, on BOTH sides
    # of every hot built-in, and behind a `safe` so the container grant is in
    # play — the built-in sweep above never dispatches through
    # `filter_registry::apply_custom_filter` at all, so without these cells a
    # change to what crosses the PyO3 boundary is unmeasured.
    for c in CUSTOM:
        for key in INPUTS:
            yield (c,), key
        for b in HOT2:
            for key in INPUTS_2:
                yield (b, c), key
                yield (c, b), key
                yield ("safe", b, c), key


#: The TAG-OPERAND axis (#2325).
#:
#: Every cell above is a `{{ p|… }}` chain, and that was the whole corpus. A
#: filter on a TAG operand — `{% for x in p|slice:":2" %}` — is a DIFFERENT
#: resolution path: Django builds a `FilterExpression` for both, djust had one
#: filter-aware resolver and four tags that open-coded a bare variable lookup
#: instead. So the filter chain was dropped entirely and the tag proceeded on
#: the miss, rendering an empty loop or echoing the expression's own source
#: text into the page — and this tool, which exists to catch exactly that
#: class, could not see any of it, because it constructs no tag cell at all.
#:
#: That is the same corpus-gap failure mode that let it report clean over a
#: live XSS (#2281): the tool is only ever as good as the shapes it builds.
#: A gap is silent by construction, so
#: `python/tests/test_filtered_operands_and_slice_2325_2326.py::
#: TestTheCorpusGapThatHidThisFromTheDifferential` pins that these shapes are
#: still here.
#:
#: `@EXPR@` is the filter expression, substituted with `str.replace` rather
#: than `%`: a Django tag body is full of `%}`, which `%`-formatting reads as a
#: conversion specifier and rejects.  Each shape renders its operand back out,
#: so the live-payload check applies to the tag path exactly as it does to
#: `{{ }}`.
TAG_SHAPES = {
    "for": "{% for x in p|@EXPR@ %}[{{ x }}]{% empty %}E{% endfor %}",
    "with": "{% with q=p|@EXPR@ %}[{{ q }}]{% endwith %}",
    "if": "{% if p|@EXPR@ %}Y{% else %}N{% endif %}",
}


def tag_cells():
    """Every filter alone, plus the hot 2-chains, on each tag operand.

    Deliberately not the full 3-chain product: the axis under test is the
    OPERAND, and a chain's escaping interactions are already swept at length 3
    through `{{ }}`. Length 2 is kept because a grant one filter mints and the
    next consumes is the interaction this corpus exists for.
    """
    for shape in TAG_SHAPES:
        for name in sorted(register.filters):
            for key in INPUTS:
                yield shape, (name,), key
        for a, b in itertools.product(HOT2, HOT2):
            for key in INPUTS_2:
                yield shape, (a, b), key


def measure(out_path: str) -> None:
    result: dict[str, list[str]] = {}
    for chain, key in cells():
        expr = "|".join(spec(c) for c in chain)
        cid = f"{expr}\t{key}"
        if cid in result:
            continue
        dj, du = render_both(
            "{{ p|" + expr + " }}",
            {"p": INPUTS[key]},
            CONTEXT_SAFE_KEYS.get(key),
        )
        if any(c in NONDET for c in chain):
            dj, du = f"<NONDET len={len(dj)}>", f"<NONDET len={len(du)}>"
        result[cid] = [dj, du]

    # The tag-operand axis (#2325). Its cell ids carry a third field so the
    # `{{ }}` ids above are byte-identical to what this script emitted before
    # the axis existed — an older baseline file stays comparable, and the new
    # cells simply show up as additions rather than renaming every row.
    for shape, chain, key in tag_cells():
        expr = "|".join(spec(c) for c in chain)
        cid = f"{expr}\t{key}\t{shape}"
        if cid in result:
            continue
        dj, du = render_both(
            TAG_SHAPES[shape].replace("@EXPR@", expr),
            {"p": INPUTS[key]},
            CONTEXT_SAFE_KEYS.get(key),
        )
        if any(c in NONDET for c in chain):
            dj, du = f"<NONDET len={len(dj)}>", f"<NONDET len={len(du)}>"
        result[cid] = [dj, du]

    agree = sum(1 for v in result.values() if v[0] == v[1])
    print(f"cells={len(result)}  agree={agree}  disagree={len(result) - agree}")
    with open(out_path, "w") as fh:
        json.dump(result, fh)
    print(f"wrote {out_path}")


def load(path: str) -> dict[str, list[str]]:
    # Collapse the nondeterministic marker: `random` picks a different element
    # each run, so its LENGTH is not comparable between two runs either.
    return {
        k: [NONDET_MARKER.sub("<NONDET>", a), NONDET_MARKER.sub("<NONDET>", b)]
        for k, (a, b) in json.load(open(path)).items()
    }


def live(out: str, key: str) -> set[str]:
    return {f for f in LIVE_FRAGMENTS.get(key, ()) if f in out}


#: A payload is only LIVE if the markup around it is unescaped. Every payload
#: in `INPUTS` is a tag, so an output with no unescaped tag opener at all
#: cannot have emitted live markup, whichever fragments it contains as text.
UNESCAPED_TAG = re.compile(r"<[a-zA-Z/!]")


def unmasked(cid: str, base: dict, after: dict) -> bool:
    """Did this TAG cell agree on the baseline only by COINCIDENCE? (#2325)

    A cell that agreed before and disagrees after is normally a regression.
    A tag-operand cell has a third way to have agreed: the operand bug made
    djust render NOTHING, and for a filter Django also fails on, Django
    rendered nothing too. Both empty, agreeing for unrelated reasons — and
    once the operand resolves, the underlying per-filter divergence shows.

    The test is mechanical: the same filter's `{{ p|expr }}` cell disagrees on
    BOTH builds. That divergence is untouched by this change (it is the same
    two numbers before and after), so the tag cell's new disagreement is the
    filter's, surfaced — not something the change broke.

    Empirically, when #2325 introduced the tag axis this classified 445 of 445
    reported regressions, leaving zero unexplained.
    """
    expr, key, *shape = cid.split("\t")
    if not shape:
        return False  # A `{{ }}` cell has no mask to be behind.
    twin = f"{expr}\t{key}"
    b, a = base.get(twin), after.get(twin)
    return b is not None and a is not None and b[0] != b[1] and a[0] != a[1]


def compare(base_path: str, after_path: str) -> int:
    base, after = load(base_path), load(after_path)
    if set(base) != set(after):
        print("FAIL: the two runs cover different cells and are not comparable")
        return 1

    agree_b = {k for k, (a, b) in base.items() if a == b}
    agree_a = {k for k, (a, b) in after.items() if a == b}
    print(f"cells        : {len(base)}")
    print(f"agree BEFORE : {len(agree_b)}")
    print(f"agree AFTER  : {len(agree_a)}")
    if len(agree_b) == len(agree_a):
        print(
            "FAIL: identical agreement counts — both files almost certainly came\n"
            "      from the SAME build, so the baseline is not real. Rebuild it."
        )
        return 1

    changed = sorted(agree_b - agree_a)
    coincidental = [c for c in changed if unmasked(c, base, after)]
    regressions = [c for c in changed if not unmasked(c, base, after)]
    print(f"newly AGREEING: {len(agree_a - agree_b)}")
    print(f"no longer agreeing: {len(changed)}")
    print(f"  coincidental (the filter itself diverges on both builds): {len(coincidental)}")
    print(f"  REGRESSIONS : {len(regressions)}")
    for cid in regressions:
        # Three fields on a tag-operand cell, two on a `{{ }}` one (#2325).
        expr, key, *shape = cid.split("\t")
        print(f"  !! {expr}  <{key}>{'  in ' + shape[0] if shape else ''}")
        print(f"       django={after[cid][0][:160]!r}")
        print(f"       djust ={after[cid][1][:160]!r}")

    def leaks(data):
        found, raised = {}, 0
        for cid, (dj, du) in data.items():
            key = cid.split("\t")[1]
            if dj.startswith("<<EXC "):
                raised += 1
                continue
            extra = live(du, key) - live(dj, key)
            if extra:
                found[cid] = sorted(extra)
        return found, raised

    lb, raised = leaks(base)
    la, _ = leaks(after)
    new = sorted(set(la) - set(lb))
    # `live()` substring-matches, and a fragment like `onerror=` also occurs
    # inside FULLY ESCAPED text — which is exactly what a tag cell emits when
    # Django renders nothing at all and djust renders the escaped input. Split
    # them: an output carrying no unescaped tag opener cannot be live markup.
    # Both lists are printed in full; only the live half gates the exit.
    hot = [c for c in new if UNESCAPED_TAG.search(after[c][1])]
    escaped_only = [c for c in new if c not in hot]
    print()
    print(f"live-payload leaks BEFORE : {len(lb)}   ({raised} cells where Django raised)")
    print(f"live-payload leaks AFTER  : {len(la)}")
    print(f"  closed                  : {len(set(lb) - set(la))}")
    print(f"  flagged as INTRODUCED   : {len(new)}")
    print(f"    fragment inside fully-escaped text (not live): {len(escaped_only)}")
    print(f"    LIVE (an unescaped tag opener)              : {len(hot)}")
    for cid in hot + escaped_only:
        mark = "!!" if cid in hot else "  "
        expr, key, *shape = cid.split("\t")
        print(f"  {mark} {expr}  <{key}>{'  in ' + shape[0] if shape else ''}  live={la[cid]}")
        print(f"       django={after[cid][0][:160]!r}")
        print(f"       djust ={after[cid][1][:160]!r}")

    if regressions or hot:
        return 1
    print("\nOK: no agreeing cell regressed, and nothing became more permissive.")
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--compare"]:
        sys.exit(compare(sys.argv[2], sys.argv[3]))
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    measure(sys.argv[1])
