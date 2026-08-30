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

Since #2334 / #2335 it carries two more, for the same reason both times — the
tool reported clean over the whole of both bugs because it built no cell that
could reach them:

* a **dict-view path** axis. Every tag cell writes `p|<filter>` as its operand,
  so the corpus contained no DOTTED path and nothing that iterated a dict
  without a filter in the way. `{% for k, v in p.items %}` — one of the most
  common Django loop idioms there is — rendered NOTHING, because `.items` is a
  callable rather than a key and `{% for %}` had no `Value::Object` arm.
* a **sequence-comparison** axis. Every `{% if %}` cell was a truthiness test on
  ONE operand; the tool bound no `q` at all, so `values_equal` / `try_compare`
  — which answered False for a list against itself — were entirely unmeasured.

`python/tests/test_dict_iteration_and_sequence_equality_2334_2335.py::
TestTheCorpusGapsThatHidTheseFromTheDifferential` pins both, because a corpus
gap is silent by construction and this is the third time one has hidden a
shipped bug.

Since #2345 it carries an **argument** axis, and `render_both` records a Rust
PANIC as a cell rather than dying on one. Both were the same blind spot seen
from two sides. `FILTER_ARGS` gave every filter exactly ONE argument and it was
always VALID, so:

* #2328 changed what every argument-taking built-in does with an unparseable
  or unresolvable argument and this tool reported **0 moved cells in both
  directions** — while its first pass shipped 508 regressed cells that neither
  this script nor the full green suite saw;
* `{{ p|stringformat:"" }}` (#2343) did not merely diverge, it PANICKED — and
  because `pyo3_runtime.PanicException` derives from `BaseException`, the
  `except Exception` in `render_both` did not catch it and the sweep ABORTED.
  #2343 was found by that traceback, not by a cell.

So `ARG_SPELLINGS` sweeps nineteen argument spellings per argument-taking
filter, and a panic is recorded as `<<PANIC …>>` — kept distinct from
`<<EXC …>>` on purpose, because a raise is contained by
`LiveViewConsumer.receive` and a panic is not. `--compare` reports newly
panicking cells on their own line and exits non-zero on any, since "how many
cells agree" is the wrong number to read a transport-level failure off.

Since #2345 the axes are DECLARED, and the corpus reports what it cannot reach
---------------------------------------------------------------------------
The paragraphs above are five blind spots, five hand-added axes and five
bespoke coupling tests — and #2345 was the fifth, the point at which adding a
sixth stopped being the answer. A corpus gap is silent BY CONSTRUCTION: "no
axis reported a problem" and "no axis exists for the problem" print the same
thing.

So the corpus now DECLARES its axes in `AXES`, each naming the set the ENGINE
says it must cover — recomputed at check time from Django's live registry or
from the Rust source, never transcribed. `--manifest` prints what is covered
and what is NOT; a results file carries its own manifest and the `_rust`
build's digest, so a baseline states what it could see.

Eight axes: `filter`, `chain`, `whitespace`, `argument`, `tag`, `entrypoint`,
`grant-shape`, and `input-shape` — declared UNVERIFIED, because nothing in
either engine's source says a dict's keys must be hostile (#2334) or that a
tuple must sit at the nesting position (#2317). That is the class this design
does NOT close, and it is printed rather than left as a silence.

`--manifest` prints the current list; more have been added since, and #2411's
`masked-refusal` is the seventh blind spot the design did not close on its own.
An axis was declared for the tag OPERAND and one for the argument SPELLING, and
neither could construct a cell that had both at once — so 1,227 `{% if %}`
templates Django refuses to compile rendered here, with every axis reporting
`0 MISSING`. A cross of two covered axes is its own axis.

Two more things follow from it:

* **The same-build guard is answered rather than inferred.** Identical
  agreement counts used to mean "the baseline is not real"; that is one of TWO
  causes, and #2328 hit the other — two genuinely different builds, zero moved
  cells, because the change was on an axis the corpus could not construct.
  Each file now records the `_rust` build's digest, so a two-build run with no
  movement is reported as what it is.
* **`--require-moved <axis>`** turns that note into a failure, for a change
  that declares which axis it is about.

`python/tests/test_differential_reachability_manifest_2345.py` is the empirical
canary: it rebuilds each pre-fix corpus in a COPY of this file and asserts what
the manifest says. It goes red for #2296, #2305, #2325, #2290 and #2345, and
NOT for #2334 — whose two halves are pinned there as the limit.

Usage
-----
::

    python scripts/filter-parity-differential.py --manifest
    python scripts/filter-parity-differential.py after.json
    python scripts/filter-parity-differential.py --compare base.json after.json \\
        --require-moved argument
"""

from __future__ import annotations

import copy
import datetime
import hashlib
import inspect
import itertools
import json
import ast
import pathlib
import re
import sys
import typing
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


# ---------------------------------------------------------------------------
# The CUSTOM-TAG corpus (#2356)
# ---------------------------------------------------------------------------
#
# The #2290 gap, one registry over. `register_tag_handler`,
# `register_block_tag_handler` and `register_assign_tag_handler` had been on
# the `_rust` module the whole time and the corpus dispatched through NONE of
# them — every tag cell above goes through a tag the RUST engine implements
# natively, so the Python-callable boundary a project's tag crosses was
# unmeasured in exactly the way a custom FILTER's was before #2290.
#
# Three probes per registry, mirroring `cf_ident` / `cf_canon` / `cf_cond`:
# one that returns its input untouched (the sharpest, since Django escapes iff
# the FINAL value lacks `__html__`), one that `mark_safe`s, and one that
# `conditional_escape`s. Registered on BOTH engines from the same function
# body, so the same source renders through both with no `{% load %}`.
#
# WHAT THIS AXIS FOUND, on its first run: djust inserts a tag handler's return
# value into the page RAW. Django escapes a `simple_tag` / `simple_block_tag`
# return unless it carries `__html__` (`SimpleNode.render` →
# `conditional_escape`), so a handler returning a plain `str` built from user
# data is emitted LIVE by djust and escaped by Django — the fail-OPEN half of
# the same asymmetry #2290 found on the way IN. Tracked at #2379; every
# `ct-ident` / `cb-plain` cell here is that divergence, and they are the
# corpus's standing record of it rather than a note in a file.


@_CUSTOM_LIBRARY.simple_tag(name="ct_ident")
def _ct_ident(value):
    """Returns its argument untouched — the sharpest probe there is."""
    return value


@_CUSTOM_LIBRARY.simple_tag(name="ct_safe")
def _ct_safe(value):
    """`mark_safe`s, so Django must NOT escape the result."""
    return mark_safe("[" + str(value) + "]")


@_CUSTOM_LIBRARY.simple_tag(name="ct_cond")
def _ct_cond(value):
    """`conditional_escape` — reads the SafeData marker on the way IN.

    The half of #2290's finding that applies to arguments rather than returns:
    a `mark_safe`d context value reaches a Rust-dispatched handler as a bare
    `str`, so this escapes where Django does not.
    """
    return conditional_escape(value)


def _cb_ident(content):
    """A block probe returning the rendered body untouched (already SafeData)."""
    return content


def _cb_plain(content):
    """A block probe returning a PLAIN `str` — not SafeData.

    The block-registry twin of `ct_ident`: Django escapes this return, so it is
    the cell that says whether the block path carries the same fail-open shape
    as the simple-tag one.
    """
    return "[" + str(content) + "]"


if hasattr(_CUSTOM_LIBRARY, "simple_block_tag"):  # Django >= 5.1
    _CUSTOM_LIBRARY.simple_block_tag(name="cb_ident", end_name="endcb_ident")(_cb_ident)
    _CUSTOM_LIBRARY.simple_block_tag(name="cb_plain", end_name="endcb_plain")(_cb_plain)
else:  # pragma: no cover — Django 4.2 / 5.0, which `pyproject.toml` still allows

    def _register_block_probe(name, fn):
        """`simple_block_tag`'s node, hand-rolled for Django < 5.1.

        A transcription, and named as one: it exists so the axis is PRESENT on
        an older Django rather than silently absent, which is the failure mode
        this whole manifest is about. On Django >= 5.1 the real decorator is
        used and this branch never runs.
        """

        class _Node(_django_template.Node):
            def __init__(self, nodelist):
                self.nodelist = nodelist

            def render(self, context):
                output = fn(self.nodelist.render(context))
                return conditional_escape(output) if context.autoescape else output

        @_CUSTOM_LIBRARY.tag(name=name)
        def _compile(parser, token):
            nodelist = parser.parse((f"end{name}",))
            parser.delete_first_token()
            return _Node(nodelist)

    _register_block_probe("cb_ident", _cb_ident)
    _register_block_probe("cb_plain", _cb_plain)


@_CUSTOM_LIBRARY.simple_tag(name="ca_ident")
def _ca_ident(value):
    """The ASSIGN probe. Django spells an assignment tag `{% t x as v %}`."""
    return value


class _RustTagProbe:
    """The `.render(args, context)` shape `registry.rs` calls a handler with.

    One adapter class rather than three: the three registries differ only in
    the handler signature, and a per-registry copy is the parallel-path shape
    this repo keeps retiring (#1646).
    """

    def __init__(self, fn, kind):
        self.fn, self.kind = fn, kind

    def render(self, args, *rest):
        if self.kind == "block":
            content, _context = rest
            return self.fn(content, *args)
        if self.kind == "assign":
            # `{% ca_ident p as v %}` → args = [<resolved p>, "as", "v"].
            return {args[-1]: self.fn(args[0])}
        return self.fn(*args)


_rust.register_tag_handler("ct_ident", _RustTagProbe(_ct_ident, "tag"))
_rust.register_tag_handler("ct_safe", _RustTagProbe(_ct_safe, "tag"))
_rust.register_tag_handler("ct_cond", _RustTagProbe(_ct_cond, "tag"))
_rust.register_block_tag_handler(
    "cb_ident", "endcb_ident", _RustTagProbe(lambda content: _cb_ident(content), "block")
)
_rust.register_block_tag_handler(
    "cb_plain", "endcb_plain", _RustTagProbe(lambda content: _cb_plain(content), "block")
)
_rust.register_assign_tag_handler("ca_ident", _RustTagProbe(_ca_ident, "assign"))

#: One shape per probe, rendered over every input.
#:
#: Not the filter grid: a custom TAG takes its operand as an argument rather
#: than composing, so "which filter precedes it" is already the `tag` axis's
#: question. What varies here is the VALUE the handler is handed and what its
#: return does on the way out — `ct-filtered` is the one shape that puts a
#: filter in front, because a chain's output is the value most likely to
#: arrive at the boundary with a marker the boundary drops.
#:
#: The four LITERAL shapes are the argument axis crossed with the operand
#: SPELLING (#2416), which is its own axis: `ct-literal` alone could not
#: separate the two defects stacked on `{% ct_ident "<b>" %}` — Django's
#: `Variable.__init__` both strips the quotes AND `mark_safe`s, so a cell that
#: fixed one and not the other still diverges and reads the same.
#:
#: * `ct-literal-plain` is the quotes ALONE, over a literal with no markup in
#:   it: `{% t "post" %}` handed the handler `"post"` WITH the quotes, and that
#:   is the half nothing here could see, because every other literal cell's
#:   divergence could be read as the escaping.
#: * `ct-literal-cond` is the GRANT alone: `conditional_escape` inside the
#:   handler is a no-op only if the literal arrived as `SafeData`, so this cell
#:   moves for the marker and not for the quotes.
#: * `ct-literal-filtered` is the literal crossed with a FILTER — a cross of
#:   two covered axes, and so its own axis. `ct-filtered` writes `p|upper` (a
#:   NAME base) and `ct-literal` writes a bare literal; neither could construct
#:   a filter over a literal, which is where `get_value_safe`'s seed lost the
#:   grant `Node::Variable` kept. `lower` rather than `upper` on purpose:
#:   Django registers `lower` `is_safe=True`, so a safe input stays safe and
#:   the cell can tell the two seeds apart. Over `upper` (`is_safe=False`) both
#:   seeds give the same answer, which is why `ct-filtered` never moved.
CUSTOM_TAG_SHAPES = {
    "ct-ident": "{% ct_ident p %}",
    "ct-safe": "{% ct_safe p %}",
    "ct-cond": "{% ct_cond p %}",
    "ct-filtered": "{% ct_ident p|upper %}",
    "ct-safe-filtered": "{% ct_cond p|safe %}",
    "ct-literal": '{% ct_ident "<b>" %}',
    "ct-literal-plain": '{% ct_ident "post" %}',
    "ct-literal-cond": '{% ct_cond "<b>" %}',
    "ct-literal-filtered": '{% ct_ident "<B>"|lower %}',
    "cb-ident": "{% cb_ident %}{{ p }}{% endcb_ident %}",
    "cb-plain": "{% cb_plain %}{{ p }}{% endcb_plain %}",
    "ca-ident": "{% ca_ident p as v %}[{{ v }}]",
}


def custom_tag_cells():
    for shape in CUSTOM_TAG_SHAPES:
        for key in INPUTS:
            yield shape, key


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
    # A DATE-SHAPED string (#2344). A Python `datetime` crosses into Rust as
    # exactly this text and has no other spelling, so it is what `date`,
    # `time`, `timesince` and `timeuntil` actually receive on a real page —
    # and no other input is one, which meant every cell of those four took the
    # unreadable-value branch and returned its input. The reachability manifest
    # reported `timesince`'s "not a date or datetime" argument error as
    # UNREACHABLE for exactly that reason: the fix parses the VALUE first
    # (Django's order), so a corpus with no readable date can never reach the
    # argument logic of the two filters whose argument is the subject.
    "s-datetime": "2020-01-01 12:00:00",
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
    # NON-FINITE floats (#2349). Every numeric entry above is finite, so the
    # corpus could not construct a single cell where the
    # `(a - b).abs() < f64::EPSILON` idiom is UNDEFINED — and it is undefined
    # for exactly these: `(inf - inf)` is NaN and every comparison against NaN
    # is false, so the tolerance answered "not equal" for two infinities and
    # the ordering chain fell through its `else` to "greater" for every NaN
    # pair. 26 divergent cells, and this tool reported clean over all of them
    # because `INPUTS` had no `inf` and no `nan`.
    #
    # All three, not one: `inf` and `-inf` order NORMALLY in Python while a NaN
    # answers False for all four operators, so a corpus carrying only one of
    # them cannot tell an `is_nan` guard (correct) from an `!is_finite` guard
    # (which would break `-inf < 1`). They matter most on the `@cmp` axis,
    # where `q` is the second operand, but are swept through the filters too:
    # `floatformat`, `add` and `stringformat` all branch on finiteness.
    "f-inf": float("inf"),
    "f-ninf": float("-inf"),
    "f-nan": float("nan"),
    # The same three as DECIMALS, which reach the `numeric_pair` WILDCARD
    # rather than any typed arm — two of the six sites the fix touches are
    # reachable only this way.
    "dec-inf": Decimal("Infinity"),
    "dec-nan": Decimal("NaN"),
    # The non-iterable shapes a `{% for %}` operand can hold (#2382). Every
    # scalar entry above is an `int` or a `float`, so the corpus could build a
    # `{% for x in p %}` over those two and over NOTHING ELSE Django refuses —
    # and `Node::For`'s refusal arm names four Python types, not two. The issue
    # that surfaced this framed it as a bool problem; measuring the axis showed
    # an `int`, a `float` and a `Decimal` all raise too, and neither a bool nor
    # a finite `Decimal` nor an `int` past `i64` could be spelled here.
    #
    # `b-false` as well as `b-true`, for the reason `ARG_SPELLINGS` carries all
    # three builtins: `False` is falsy and `True` is not, so a corpus with only
    # one of them cannot tell a rule about ITERABILITY from a rule about
    # TRUTHINESS — which is exactly the reading #2382's own issue had to
    # correct. `i-big` is a `Value::BigInt` rather than a `Value::Integer`, and
    # `dec-plain` a finite `Value::Decimal`: four Rust variants reach the
    # refusal and only two were reachable.
    "b-true": True,
    "b-false": False,
    "i-big": 12345678901234567890,
    "dec-plain": Decimal("2.5"),
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
    # A dict whose KEYS are hostile (#2334). `{% for k in d %}` iterates a
    # dict's keys, so the KEY is what reaches the page — and `d-plain`'s keys
    # are `k` and `j`, which cannot show a key-escaping defect at all.
    #
    # It also carries a key spelled `"1"` whose value is marked safe, which is
    # the exact collision shape the loop safe-key mapping has on a dict:
    # `_collect_safe_keys` writes a dict's paths BY NAME (`p.1`), while the
    # mapping asserts the loop variable is `p.<INDEX>`. Index 1 is the SECOND
    # key — a different, attacker-controlled string — so registering the
    # mapping for a dict would emit it unescaped. Ordered so the payload is at
    # index 1; a corpus with the marked key second could not build that cell.
    "d-hostile-key": {
        "1": mark_safe("<b>ok</b>"),
        "<img src=x onerror=alert(1)>": "v",
    },
    # A dict whose keys are NOT strings (#2339). Every other dict on this axis
    # is string-keyed, so no cell could show that a non-string-keyed dict was
    # not a mapping AT ALL — it fell through to its own `repr`, which
    # `{% for k in d %}` then iterated one CHARACTER at a time and
    # `{{ d|length }}` counted the characters of.
    #
    # Carries one key of each kind the key type models, because the extraction
    # order is load-bearing: a Python `bool` IS an `int`, so an `i64`-first arm
    # order silently turns `True` into `1`.
    "d-typed-key": {0: "a", True: "b", None: "c", 1.5: "d", (1, "t"): "e"},
    # The same, with the payload as a NON-string key's neighbour — so the
    # key-escaping question is asked for a mapping whose iteration order is
    # not the string-keyed one.
    "d-typed-hostile": {0: "a", "<img src=x onerror=alert(1)>": "v"},
    # A dict carrying the SAME segment spelled both ways (#2371). Django's
    # `_resolve_lookup` tries a string item lookup before an integer index, so
    # `{{ p.1 }}` on this resolves through step 1 to the marked value and never
    # reaches the int key — and no other entry can construct that cell, because
    # no other dict holds `"1"` and `1` at once. The int key `0` carries the
    # payload so the same row measures the permissiveness half of a newly
    # RESOLVING path: before #2371 every `{{ p.0 }}` on a dict rendered
    # nothing, so the tool's live-fragment check had never seen this value
    # reach the page at all.
    "d-numeric-key": {
        0: "<img src=x onerror=alert(1)>",
        "1": mark_safe("<b>by-string</b>"),
        1: "by-int",
    },
    # A dict with a marked value AND an unmarked hostile one, both under
    # ORDINARY identifier keys (#2375). `d-hostile-key` and `d-numeric-key`
    # carry a marked value too, but under the key `"1"` — which is also a
    # numeric path segment, so a cell reading `q.1` cannot separate "the grant
    # followed the bind" from "the index step resolved". These keys are `a` and
    # `z`, so `{{ q.a }}` and `{{ q.z }}` ask exactly one question each: does
    # the grant follow, and does it stay where it belongs.
    "d-marked-value": {
        "a": mark_safe("<b>ok</b>"),
        "z": "<img src=x onerror=alert(1)>",
    },
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
    # The KEY is the payload here, and it is never marked. `<b>ok</b>` IS
    # marked and Django emits it live, which is why it is not listed.
    "d-hostile-key": ["<img", "onerror="],
    # The `z` value is the payload and is NEVER marked; `a` IS marked and
    # Django emits it live, which is why `<b>ok</b>` is not listed (#2375).
    "d-marked-value": ["<img", "onerror="],
    # Same, for the typed-key dict (#2339). `d-typed-key` carries no payload
    # at all and so has no entry.
    "d-typed-hostile": ["<img", "onerror="],
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
    # The two ECHOING filters (#2359). Neither is in a safety set, so the
    # enforcing test does not require them; they are here because
    # **echo-on-failure composed with a safety grant is a live-markup class**,
    # and this axis could not construct a single cell of it for these two.
    #
    # The class, measured on `main` before #2359: a filter that returns its
    # INPUT when it cannot do its job, followed by anything that marks the
    # result safe, puts attacker-controlled markup on the page where Django
    # puts nothing. `add` was already on this list and reported 70 such cells
    # — every one of them a CONTAINER input, because `"<img …>" + "1"`
    # SUCCEEDS and Django emits it live too, so a string never reaches `add`'s
    # echo path.
    #
    # That made the class look like "containers only". It is not: `date` and
    # `time` echo for EVERY non-date, so `{{ p|date:"Y-m-d"|safe }}` over a
    # plain STRING is live on `main` and empty in Django. With `date`/`time`
    # absent from this list, no cell could say so — the corpus sampled one
    # echoing filter and the conclusion generalised to the axis.
    "date",
    "time",
    # The two arms #2401 / #2403 gave a per-CALL grant. Both are
    # return-the-INPUT branches — `yesno`'s `len(bits) < 2` and `get_digit`'s
    # `except ValueError` — so they join the `default` / `cut` / `add` family
    # the paragraph above is about, and the enforcing test
    # (`test_every_safety_set_member_is_in_the_differential_hot_sets`) requires
    # them here as soon as the arm exists. It is what made this edit part of the
    # same commit.
    "yesno",
    "get_digit",
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
    # The date-shaped value (#2344), on the chain axis too: what a SECOND
    # filter does with a rendered duration is a different question from what
    # one filter does, and it is the only input for which `timesince` produces
    # anything but its own input back.
    "s-datetime",
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


def nondet_agreement(dj: str, du: str) -> tuple[str, str]:
    """A nondeterministic ARGUMENT cell, recorded by whether the two AGREED.

    The `{{ }}` corpus rewrites a `NONDET` cell to `<NONDET len=N>` on both
    sides, and `load()` collapses that to a bare `<NONDET>` — so the two sides
    always compare EQUAL. That is correct for `random`, whose draw is not
    comparable at all, and it is BLIND for `timesince`/`timeuntil` on the
    ARGUMENT axis, which is the one axis where their argument is the subject.

    Measured, and this is the reason the helper exists: #2344 makes those two
    filters read their argument as the comparison instant — 120 argument cells
    move — and a length-collapsed corpus reports **zero**. The tool built to
    catch a corpus that cannot see a change could not see that one.

    So the comparable property here is the AGREEMENT BIT, which is stable: both
    engines read the same wall clock microseconds apart, so a clock-dependent
    cell's agreement does not flap, while a cell made deterministic by its
    argument reports honestly. The LENGTH is still not recorded — a clock moves
    between the two renders and `<NONDET len=N>` was never comparable anyway.

    Scoped to the argument cells on purpose: applying it to the `{{ }}` corpus
    would rewrite the value of every existing `timesince` cell and make an older
    baseline incomparable, for a question that axis does not ask.
    """
    return "<NONDET>", "<NONDET>" if _outcome(dj) == _outcome(du) else "<NONDET differs>"


def _outcome(out: str) -> str:
    """A cell's answer reduced to what is COMPARABLE across the two engines.

    A raise is compared by the fact of it, never by its text. Django raises
    `AttributeError` / `ValueError` / `ZeroDivisionError` from Python; djust
    raises a `RuntimeError` wrapping a Rust error that names the filter and the
    argument. Those strings cannot match, so comparing them marks every cell
    where BOTH raise as disagreeing — and a fix to the raise bit then reads as
    "differs" before and after.

    Measured, and the reason this exists: #2344 makes `timesince`/`timeuntil`
    raise where Django raises, moving 336 cells, and the raw-text comparison
    reported ZERO on every axis. The tool built to catch a corpus that cannot
    see a change could not see that one.

    A PANIC stays distinct from a raise, per #2343: a raise is contained by
    `LiveViewConsumer.receive` and produces an error frame, while a panic walks
    past it and takes the session down. Folding them would let a cell that
    started panicking read as unchanged.
    """
    if out.startswith("<<PANIC "):
        return "<<PANICKED>>"
    if out.startswith("<<EXC "):
        return "<<RAISED>>"
    return out


def spec(name: str) -> str:
    arg = FILTER_ARGS.get(name)
    return f"{name}:{arg}" if arg else name


def render_both(tpl: str, ctx: dict, safe_keys: list[str] | None = None) -> tuple[str, str]:
    try:
        # `dict(ctx)`, and it is load-bearing (#2355). `Context(d)` keeps `d`
        # itself as `dicts[-1]`, and `Context.__setitem__` writes THERE — so a
        # Django ASSIGNMENT tag (`{% firstof … as v %}`, `{% widthratio … as w %}`,
        # `{% regroup … as g %}`) mutates the caller's dict, and the djust render
        # below then runs against a context Django wrote into. The two engines
        # stop being handed the same input, which is the one thing a
        # differential must guarantee.
        #
        # Found by adding the `as`-form shapes: djust looked like it assigned
        # `v` (and double-escaped it), when in fact Django had put the key
        # there. Shallow is enough — Django's assignment writes a NEW top-level
        # key; the `@cmp` axis, which needs two structurally-equal operands
        # that are not the same object, does its own `deepcopy`.
        dj = Template(tpl).render(Context(dict(ctx)))
    except (KeyboardInterrupt, SystemExit):
        raise
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
    except (KeyboardInterrupt, SystemExit):
        # Re-raised BEFORE the `BaseException` arm below, which would otherwise
        # swallow them and make a 95,000-cell sweep un-interruptible. They are
        # the operator ending the run, not the engine failing.
        raise
    except Exception as exc:  # noqa: BLE001
        du = f"<<EXC {type(exc).__name__}: {exc}>>"
    except BaseException as exc:  # noqa: BLE001
        # A Rust PANIC (#2343/#2345). `pyo3_runtime.PanicException` derives
        # from `BaseException`, so an `except Exception` here does not catch it
        # and the sweep ABORTS mid-run — which is literally how #2343 was
        # found: by the traceback, not by a cell. Recorded with its own marker
        # rather than folded into `<<EXC …>>`, because a panic and a raise are
        # not the same outcome: a raise is contained by
        # `LiveViewConsumer.receive`'s `except Exception` and produces an error
        # frame, while a panic walks past it and takes the session down. Two
        # builds that differ only in whether a cell panics MUST show as moved.
        du = f"<<PANIC {type(exc).__name__}: {exc}>>"
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
#: The six added in #2355 are the same class one tag over, and they are NOT a
#: coverage tidy-up: `for`/`with`/`if` were three of the four operand channels
#: #2325 found broken, and the manifest's first run named these six as the
#: unswept remainder. Each is spelled to render its operand back out, so a
#: `p|safe` chain reaching the page is visible to the live-payload half.
#:
#: A shape KEY is not a tag name — `_swept_tags` reads the tag out of the
#: SOURCE — so a tag can carry more than one shape where more than one of its
#: operands is a filter expression. Three do:
#:
#: * `widthratio` / `firstof` take an `as <var>` form whose contract is
#:   "assign, emit NOTHING". That is a different question from the emitting
#:   form and cannot share its cell.
#: * `regroup`'s `by` operand is its own filter expression — Django compiles
#:   `<var>.<attr>` with `compile_filter`, so `by k|upper` applies `upper` per
#:   item. #2333 fixed the SOURCE operand; the `by` one was never measured.
#:
#: `filter` is the one shape whose `@EXPR@` is not preceded by `p|`: the tag
#: takes the chain itself (`{% filter upper %}`), and Django compiles it by
#: prepending a variable. `ifchanged` and `filter` are both UNSUPPORTED by the
#: Rust engine today, so every one of their cells is the same refusal — kept
#: anyway, because "the corpus builds no cell for it" and "the corpus builds
#: cells that all say `<<EXC …>>`" are different states, and only the second
#: goes red the day someone implements the tag and gets its escaping wrong.
TAG_SHAPES = {
    "for": "{% for x in p|@EXPR@ %}[{{ x }}]{% empty %}E{% endfor %}",
    "with": "{% with q=p|@EXPR@ %}[{{ q }}]{% endwith %}",
    "if": "{% if p|@EXPR@ %}Y{% else %}N{% endif %}",
    # --- the #2355 six ---
    "cycle": "{% cycle p|@EXPR@ 'z' %}",
    "firstof": "{% firstof p|@EXPR@ 'F' %}",
    "firstof-as": "{% firstof p|@EXPR@ 'F' as v %}[{{ v }}]",
    "widthratio": "{% widthratio p|@EXPR@ 10 100 %}",
    "widthratio-as": "{% widthratio p|@EXPR@ 10 100 as w %}[{{ w }}]",
    "ifchanged": "{% ifchanged p|@EXPR@ %}[{{ p }}]{% endifchanged %}",
    "filter": "{% filter @EXPR@ %}{{ p }}{% endfilter %}",
    "regroup": "{% regroup p|@EXPR@ by k as g %}[{{ g|length }}]",
    "regroup-by": (
        "{% regroup p by k|@EXPR@ as g %}[{{ g|length }}]"
        "{% for x in g %}({{ x.grouper }}){% endfor %}"
    ),
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


#: The DICT-VIEW PATH axis (#2334).
#:
#: The tag axis above only ever writes `p|<filter>` as its operand, so every
#: cell's operand is a bare name plus a filter chain. A DOTTED path that ends
#: in a dict method — `{% for k, v in p.items %}`, one of the most common
#: Django loop idioms there is — is a third resolution shape: `.items` is a
#: CALLABLE, not a key, so `Context::get`'s nested walk missed it and the loop
#: rendered nothing. Neither did anything iterate a dict at all without a
#: filter in the way.
#:
#: Same corpus-gap failure mode as #2325 and #2281 before it, one shape over:
#: the tool reported clean across the whole of #2334 because it built no cell
#: that could see it.
#:
#: Swept over EVERY input, not just the dicts. A non-dict `p` is the arm that
#: must keep answering exactly what it answered before, and leaving it out
#: would make the axis measure only the half expected to change.
PATH_SHAPES = {
    "for-bare": "{% for x in p %}[{{ x }}]{% empty %}E{% endfor %}",
    "for-items": "{% for k, v in p.items %}[{{ k }}={{ v }}]{% empty %}E{% endfor %}",
    # The SPELLING of the unpack list (#2377). Django joins the tokens before
    # `in` and splits the result on `","`, so `a,b`, `a, b` and `a ,b` are one
    # three-name loop; djust split on WHITESPACE and read `a,b` as a single
    # variable literally spelled `a,b`, which never resolves — so the whole
    # loop body rendered NOTHING, silently. Every shape above and every tag
    # cell writes the SPACED spelling, so the corpus could not construct a
    # single cell that shows it. Same corpus-gap class as #2325 and #2334, on
    # the one axis nothing else varies: whitespace inside a tag's own argument
    # list. All three spellings, because a fix that splits on `","` without
    # trimming answers a name spelled ` b`.
    "for-unspaced": "{% for a,b in p %}[{{ a }}={{ b }}]{% empty %}E{% endfor %}",
    "for-spaced": "{% for a, b in p %}[{{ a }}={{ b }}]{% empty %}E{% endfor %}",
    "for-space-before": "{% for a ,b in p %}[{{ a }}={{ b }}]{% empty %}E{% endfor %}",
    "for-three-unspaced": "{% for a,b,c in p %}[{{ a }}{{ b }}{{ c }}]{% empty %}E{% endfor %}",
    "for-items-unspaced": "{% for k,v in p.items %}[{{ k }}={{ v }}]{% empty %}E{% endfor %}",
    "for-keys": "{% for x in p.keys %}[{{ x }}]{% empty %}E{% endfor %}",
    "for-values": "{% for x in p.values %}[{{ x }}]{% empty %}E{% endfor %}",
    "for-rev": "{% for x in p reversed %}[{{ x }}]{% empty %}E{% endfor %}",
    "var-items": "[{{ p.items }}]",
    "var-keys": "[{{ p.keys }}]",
    "if-items": "{% if p.items %}Y{% else %}N{% endif %}",
    "with-keys": "{% with q=p.keys %}[{{ q }}]{% endwith %}",
    # The path AND a filter, which is where the two resolution shapes meet.
    "for-keys-len": "[{{ p.keys|length }}]",
    "for-keys-join": "[{{ p.keys|join:'-' }}]",
    "for-items-slice": "{% for x in p.items|slice:':2' %}[{{ x }}]{% empty %}E{% endfor %}",
    # A view is not a list (#2340), and the axis could not see the half of that
    # which MATTERS: Python's view is not subscriptable and not
    # JSON-serializable, so Django RAISES on these, while `slice` catches the
    # TypeError and returns the view UNCHANGED. None of those cells existed.
    "var-values": "[{{ p.values }}]",
    "var-items-first": "[{{ p.items|first }}]",
    "var-keys-last": "[{{ p.keys|last }}]",
    "var-keys-json": "[{{ p.keys|json_script:'i' }}]",
    "var-keys-slice": "[{{ p.keys|slice:':1' }}]",
    # A third of the filter registry sees the view's `str()` rather than its
    # elements, so the container's spelling is an INPUT and not a cosmetic
    # detail. `safe` is the sharpest: Django emits the repr LIVE there too, so
    # the cell is a genuine permissiveness comparison rather than a parity nit.
    "var-keys-safe": "[{{ p.keys|safe }}]",
    "var-keys-pprint": "[{{ p.keys|pprint }}]",
    "var-keys-trunc": "[{{ p.keys|truncatewords:2 }}]",
    # A NUMERIC path segment (#2371). Every shape above spells its segment
    # with letters, so the corpus could not construct a single cell in which
    # Django's step-3 integer index runs — and `{{ d.0 }}` resolved nothing on
    # a dict of ANY key type, silently, for as long as the tool has existed.
    # Both `0` and `1` because they answer through different steps on
    # `d-numeric-key` (int key vs string key), and the filtered forms because
    # `divisibleby` turns the silent empty into a definite **False** that an
    # `{% if %}` gate reads as an answer.
    "var-0": "[{{ p.0 }}]",
    "var-1": "[{{ p.1 }}]",
    "var-0-div": "[{{ p.0|divisibleby:'2' }}]",
    "var-0-len": "[{{ p.0|length }}]",
    "if-0": "{% if p.0 %}Y{% else %}N{% endif %}",
    "with-0": "{% with q=p.0 %}[{{ q }}]{% endwith %}",
    "for-0": "{% for x in p.0 %}[{{ x }}]{% empty %}E{% endfor %}",
    # Step 3 applied TWICE (#2373). A character sliced out of a string is
    # itself a `str`, so Django runs the index step again and `{{ s.0.0 }}` on
    # `"abc"` is `'a'`. Every shape above stops after one segment past the
    # root, so none can show a recursion that stops at depth 1 — and the arm
    # that recurses is the only thing standing between agreement and empty.
    "var-0-0": "[{{ p.0.0 }}]",
    # The bound dict VIEW, indexed. Python's `dict_keys` is not subscriptable,
    # so this is empty on both engines — and it is the ONLY shape that hands a
    # view to the index step at all (`{{ p.keys.0 }}` dies at the `keys`
    # segment before any view exists).
    "with-keys-0": "{% with q=p.keys %}[{{ q.0 }}]{% endwith %}",
    # A grant on a SUB-PATH, across a binding (#2375). `Context::bind` moves
    # the grant at the NAME granularity, and `_collect_safe_keys` writes a
    # dict's marks at `p.<key>` — so `{% with q=p %}{{ q.a }}{% endwith %}`
    # asked `is_safe("q.a")`, which nothing ever wrote. Every `with` cell above
    # emits the bound name WHOLE, so none could see it.
    #
    # Seven cells, not one, because the fix is asymmetric and two of its halves
    # are the UNDER-escaping direction:
    #
    # * the marked sub-path must come through LIVE;
    # * the UNMARKED sibling must stay escaped — a grant that leaks sideways is
    #   a live XSS;
    # * a FILTERED binding must grant nothing at all (`dictsort` reorders,
    #   `slice` shifts — the #2334 correspondence);
    # * a REBIND of the NAME must retire the alias (#2378's "a bind REPLACES
    #   the grant", one path segment down);
    # * a REBIND of the TARGET must retire it too — `{% with q=p %}{% with
    #   p=…|safe %}{{ q }}` emitted `q`'s ORIGINAL value raw in this fix's
    #   first version, because `set_safety` marks the NAME `p` and the
    #   surviving `q -> p` alias read it;
    # * a MULTI-ASSIGNMENT tag resolves every value against the OUTER context,
    #   so `{% with a=p q=a %}` binds the outer `a` — and an alias `q -> a`
    #   would read the brand-new mark on `a`. Second live XSS, same mechanism.
    "with-subpath-marked": "{% with q=p %}[{{ q.a }}]{% endwith %}",
    "with-subpath-unmarked": "{% with q=p %}[{{ q.z }}]{% endwith %}",
    "with-subpath-filtered": "{% with q=p|dictsort:'a' %}[{{ q.a }}]{% endwith %}",
    "with-subpath-rebound": ("{% with q=p %}{% with q=p.z %}[{{ q.a }}]{% endwith %}{% endwith %}"),
    "with-target-rebound": (
        "{% with q=p %}{% with p=p.z|safe %}[{{ q }}]{% endwith %}{% endwith %}"
    ),
    # Spelled with `p` on BOTH sides deliberately: the corpus binds exactly one
    # name, so `{% with a=p q=a %}` — the natural spelling — resolves the outer
    # `a` to nothing and the cell can see no defect at all. Measured, not
    # assumed: with the exclusion gated off, that spelling reports ZERO leaks
    # and this one reports the dict repr going out RAW.
    "with-multi-assign": "{% with p=p.a q=p %}[{{ q }}]{% endwith %}",
    "for-unpack-subpath": "{% for a, b in p %}[{{ b.z }}]{% empty %}E{% endfor %}",
    # A dict VIEW as the BARE dotted operand of an assign tag (#2368). Every
    # `regroup` cell the tag axis builds writes `p|<filter>`, and the shapes
    # above that write a dotted path put it in `{% for %}` / `{{ }}` / `{% if %}`
    # — so nothing reached `resolve_tag_operand`'s NON-pipe branch with a path
    # only `Context::resolve` can answer. That branch used `Context::get`,
    # which has no dict-view arm, so the tag fell back to its "unresolved ⇒
    # keep the raw token" contract and `{{ g|length }}` rendered `0`.
    #
    # The `|slice` twin is the CONTROL: it goes through the pipe branch, which
    # #2333 already routed through `get_value`, and agreed on both builds.
    # Without it the two halves of the channel cannot be told apart.
    "regroup-values-bare": "{% regroup p.values by k as g %}[{{ g|length }}]",
    "regroup-keys-bare": "{% regroup p.keys by k as g %}[{{ g|length }}]",
    "regroup-items-bare": "{% regroup p.items by k as g %}[{{ g|length }}]",
    "regroup-values-piped": "{% regroup p.values|slice:':2' by k as g %}[{{ g|length }}]",
    # The GROUPERS, not just the count: two empty groups have the same length
    # as two real ones, and a cell that cannot tell them apart would agree
    # while the rows never arrived.
    "regroup-values-groupers": (
        "{% regroup p.values by k as g %}{% for x in g %}({{ x.grouper }}){% endfor %}"
    ),
    # The LOOP VARIABLE (#2402). Every shape above reads the loop's ITEM;
    # none reads `forloop`, so the corpus could construct no cell in which
    # Django's own `loop_dict` appears — and all seven of its members rendered
    # EMPTY, on every engine path, for as long as the tool has existed. A
    # numbered list with no numbers, `{% if forloop.first %}` never true, and
    # `{% if not forloop.last %},{% endif %}` a comma after the last element.
    # Same silent-empty class as #2325 / #2334 / #2377, and the sixth corpus
    # gap of that shape.
    #
    # One cell per member, not one composite, because they fail
    # independently: `counter` and `counter0` differ by one, `revcounter`
    # needs the sequence LENGTH (which a streaming implementation would not
    # have), `first`/`last` are the two boundaries, and `parentloop` is the
    # only one whose value is a dict — at the outermost level Django's own
    # empty dict, which renders `{}` and is NOT the same answer as missing.
    "forloop-counter": "{% for x in p %}[{{ forloop.counter }}]{% empty %}E{% endfor %}",
    "forloop-counter0": "{% for x in p %}[{{ forloop.counter0 }}]{% empty %}E{% endfor %}",
    "forloop-revcounter": "{% for x in p %}[{{ forloop.revcounter }}]{% empty %}E{% endfor %}",
    "forloop-revcounter0": "{% for x in p %}[{{ forloop.revcounter0 }}]{% empty %}E{% endfor %}",
    "forloop-first": "{% for x in p %}[{{ forloop.first }}]{% empty %}E{% endfor %}",
    "forloop-last": "{% for x in p %}[{{ forloop.last }}]{% empty %}E{% endfor %}",
    "forloop-parentloop": "{% for x in p %}[{{ forloop.parentloop }}]{% empty %}E{% endfor %}",
    # The dict ITSELF, which is the one cell that can see a member being
    # added, removed, renamed or REORDERED: `{{ forloop }}` renders the whole
    # repr in insertion order.
    "forloop-whole": "{% for x in p %}[{{ forloop }}]{% empty %}E{% endfor %}",
    # The dict through a SERIALIZER, which is a different question from the
    # repr above and the one `forloop-whole` cannot ask (#2405). Both engines
    # spell `{{ forloop }}` in the same order, so a reordering visible only in
    # `json.dumps` — where djust ran `parts.sort()` and Python preserves
    # insertion order — passed that cell and every other `forloop` cell here.
    # An explicit `id` is load-bearing: `{{ p|json_script }}` diverges on the
    # `id` ATTRIBUTE on both builds, which would mask the body this cell is
    # about.
    "forloop-json": '{% for x in p %}{{ forloop|json_script:"d" }}{% empty %}E{% endfor %}',
    # NESTED, where `parentloop` is a real loop rather than the empty dict —
    # and where the inner `{% empty %}` must see the OUTER `forloop`, because
    # Django writes its own `loop_dict` only AFTER the `len < 1` early return.
    "forloop-nested": (
        "{% for x in p %}{% for y in p %}[{{ forloop.parentloop.counter }}.{{ forloop.counter }}]"
        "{% endfor %}{% endfor %}"
    ),
    "forloop-nested-empty": (
        "{% for x in p %}{% for y in empty %}Z{% empty %}[{{ forloop.counter }}]"
        "{% endfor %}{% endfor %}"
    ),
    # REVERSED, the one shape where the iteration ordinal and the item's own
    # index disagree: Django reverses the sequence and THEN enumerates, so
    # `counter` counts 1,2,3 in RENDER order. An implementation reading the
    # item's original index instead agrees on every forward loop and silently
    # reverses the numbering here.
    "forloop-rev": (
        "{% for x in p reversed %}[{{ x }}:{{ forloop.counter }}:{{ forloop.revcounter }}]"
        "{% empty %}E{% endfor %}"
    ),
    # Through the OTHER resolution shapes, which reach the name by different
    # code than `{{ }}` does — a `{% if %}` operand, a filter chain, a `with`
    # binding, and a tag argument.
    "forloop-if": (
        "{% for x in p %}{% if forloop.first %}F{% endif %}{% if not forloop.last %},{% endif %}"
        "{% endfor %}"
    ),
    "forloop-filtered": "{% for x in p %}[{{ forloop.counter|add:'10' }}]{% empty %}E{% endfor %}",
    "forloop-with": (
        "{% for x in p %}{% with c=forloop.counter %}[{{ c }}]{% endwith %}{% empty %}E{% endfor %}"
    ),
    "forloop-firstof": "{% for x in p %}{% firstof forloop.counter 'F' %}{% empty %}E{% endfor %}",
    # SHADOWING, in both directions. The corpus binds `p` and nothing else, so
    # these are the only cells in which the name `forloop` is BOTH a context
    # variable and the engine's own dict: the loop must shadow the outer value
    # for its body and leave it intact outside, and a loop VARIABLE spelled
    # `forloop` must win over the dict (Django writes `loop_dict` at the top of
    # the iteration and the loop variable at the bottom). The safety half
    # matters too — a grant on the outer `forloop` must not survive into the
    # body and emit the engine's dict raw.
    "forloop-shadowed": (
        "[{{ forloop }}]{% for x in p %}[{{ forloop.counter }}]{% endfor %}[{{ forloop }}]"
    ),
    "forloop-is-loopvar": "{% for forloop in p %}[{{ forloop }}]{% empty %}E{% endfor %}",
    # NOT `random` / `timesince` / `timeuntil`: this axis has no `NONDET`
    # collapse, so a nondeterministic cell would differ between two runs of the
    # SAME build and read as a regression.
}


#: The SEQUENCE-COMPARISON axis (#2335).
#:
#: Every `{% if %}` cell above is a TRUTHINESS test on one operand — the tool
#: could not construct a comparison at all, because it binds only `p`. So
#: `values_equal` and `try_compare`, which two sequences reach and which
#: answered False for a list against ITSELF, were entirely unmeasured.
#:
#: `in` is here because it is the third caller of `values_equal` and would
#: otherwise be the one arm the axis misses (#1646).
CMP_OPS = ["==", "!=", "<", ">", "<=", ">=", "in"]


def path_cells():
    for shape in PATH_SHAPES:
        for key in INPUTS:
            yield shape, key


def cmp_cells():
    for op in CMP_OPS:
        for ka in INPUTS:
            for kb in INPUTS:
                yield op, ka, kb


#: The ARGUMENT axis (#2345).
#:
#: `FILTER_ARGS` gives every filter exactly ONE argument and it is always a
#: VALID one, so the whole corpus above is blind on the argument axis. Measured,
#: not asserted: #2328 changed what EVERY argument-taking built-in does with an
#: unparseable or unresolvable argument and this tool reported **0 moved cells
#: in both directions** — while its first pass shipped 508 regressed cells that
#: neither this script nor the full green suite saw. #2343 is the sharper case:
#: `{{ p|stringformat:"" }}` PANICKED, and a panic is not a cell this tool could
#: report at all, because it aborted the run (see `render_both`).
#:
#: The spellings are the ones a throwaway harness used for #2328; every one of
#: them was load-bearing at least once. They are grouped by what they exercise:
#:
#: * VALID — the parse itself, including the spellings Python's `int()` accepts
#:   and Rust's `parse()` does not (` 5 `, `+5`, `1_0`), and the
#:   quoted-vs-bare distinction that decides whether `2.7` is a `str` (so
#:   `int()` raises and Django concatenates) or a float literal (so `int()`
#:   truncates).
#: * INVALID — `"notanumber"` and `""`. The empty one is #2343's cell.
#: * LOOKUPS — a miss, a dotted miss, and a resolvable name, which are three
#:   different answers since #2328 made an unresolvable bare identifier raise.
#: * LITERALS Django resolves WITHOUT a lookup — `True`/`None` are
#:   `django.template.context.builtins` keys and RESOLVE (#2347); `7.` and
#:   `0x10` are the spellings `Variable.__init__` reads as a float and as a
#:   name respectively.
#:
#: Swept over the HOT input subset rather than all of `INPUTS`: the axis under
#: test is the ARGUMENT, the value axis is already swept exhaustively above at
#: one argument each, and the full product would roughly double the corpus for
#: no new question.

#: The two characters Django's variable lexer splits an expression on, read
#: from Django rather than written out — `filter_raw_string` interpolates
#: exactly these (`re.escape(FILTER_SEPARATOR)` and
#: `re.escape(FILTER_ARGUMENT_SEPARATOR)`) — so the `separator-in-constant`
#: axis below and the spellings it checks for cannot drift apart from the
#: grammar, or from each other.
_FILTER_SEPARATOR = _django_template.base.FILTER_SEPARATOR
_FILTER_ARGUMENT_SEPARATOR = _django_template.base.FILTER_ARGUMENT_SEPARATOR

ARG_SPELLINGS = [
    '"5"',
    "5",
    '"2.7"',
    "2.7",
    '" 5 "',
    '"+5"',
    '"1_0"',
    '"-3"',
    "-3",
    '"0"',
    '"notanumber"',
    '""',
    "missingvar",
    "no.such.path",
    "known",
    # All THREE builtins, not the two #2345 listed. `False` is the one whose
    # answer differs from both of the others — `int(False)` is 0, where `True`
    # is 1 and `None` raises — so a corpus carrying only `True` and `None`
    # cannot distinguish a fix that READS the bool from one that hardcodes 1
    # (#2347).
    "True",
    "False",
    "None",
    "7.",
    "0x10",
    # A width that PARSES and saturates past `isize`. Added because the
    # reachability manifest reported `pad_width`'s cap — the guard standing
    # between a template-supplied width and an allocator ABORT (#2328) —
    # UNREACHABLE from the nineteen above: every one of them either parses
    # to a small number, fails to parse, or fails to resolve. The manifest
    # reporting a gap in the corpus it ships alongside is the whole point
    # of it, and `test_the_nineteen_spellings_leave_the_pad_cap_unreachable`
    # removes this row again to prove the report was real.
    '"99999999999999999999"',
    # Arguments whose resolved PYTHON TYPE is the subject (#2366). Every
    # spelling above resolves to a string or to a literal, so the corpus could
    # not construct a single cell where `int(arg)` is a **TypeError** rather
    # than a ValueError — the distinction four filters' Django source turns on,
    # because they catch `ValueError` only. The tool reported `0 newly
    # agreeing` over the whole of that fix on its first run, for the same
    # reason it has reported clean over every other gap: it built no cell that
    # could reach it.
    #
    # The `dt` row is the counter-example that makes the axis honest: a
    # `datetime` is `Value::String` by the time any filter sees it, so it
    # measures the EXTRACTION-boundary residue rather than the dispatch table,
    # and it must keep diverging where the other three now agree.
    "known_list",
    "known_tuple",
    "known_dict",
    "known_dt",
    # Arguments carrying a SEPARATOR inside the quotes (#2409). Django's
    # `constant_string` grammar admits any character between the quotes,
    # including the two characters the expression is split on — so
    # `{{ p|cut:"a|b" }}` and `{{ p|date:"H:i" }}` are one filter with one
    # argument. Every spelling above is separator-free, so the corpus could
    # not construct a cell where the SPLIT is what is under test: djust cut
    # `"a|b"` in half, saw a second filter named `b"`, and raised
    # `Unknown filter` where Django renders; and it took the FIRST colon, so a
    # second `:arg` was folded into the first and rendered rather than being
    # refused. Spelled from `FILTER_SEPARATOR` / `FILTER_ARGUMENT_SEPARATOR`
    # rather than written out, so the `separator-in-constant` axis below reads
    # the same two characters Django's own lexer is built from.
    f'"a{_FILTER_SEPARATOR}b"',
    f'"a{_FILTER_ARGUMENT_SEPARATOR}b"',
    # Names `Variable.__init__` REFUSES, and the two literal arms that run
    # before it (#2418). Every spelling above is a name Django accepts, so the
    # corpus could not construct one argument cell carrying a refused name —
    # and 401 of the 495 divergent cells #2418 measured were exactly that. The
    # `_( … )` row is the control that keeps the fix from being stricter than
    # Django: it BEGINS with `_` and Django compiles it, because the translate
    # arm strips the wrapper before the underscore check.
    "_x",
    "p._priv",
    '_("_x")',
]

#: Bound so the `known*` spellings above have something to resolve TO.
#:
#: `known` is a plain string, because the question IT asks is "did the lookup
#: happen". The other four are typed, because #2366's question is "what is
#: this argument's Python TYPE" — `int()` raises TypeError for a list, a tuple
#: and a dict, and Django's `except ValueError` does not catch it.
ARG_CONTEXT = {
    "known": "3",
    # BOUND on purpose (#2418). An unbound `_x` is refused by djust for the
    # unrelated "does not resolve" reason, which is the masking that made the
    # underscore rule look like part of #2411 — its sweep bound no `_x` and so
    # reported zero rows on the three shapes that never swallow anything.
    "_x": 9,
    "known_list": [1, 2],
    "known_tuple": (1, 2),
    "known_dict": {"a": 1},
    "known_dt": datetime.datetime(2020, 1, 1, 15, 30),
}


def django_argument_filters() -> list[str]:
    """Django's built-ins that take a TEMPLATE argument, read from the registry.

    NOT `FILTER_ARGS`, which is a different question with a 25/29 overlap:
    that dict is the ESCAPING axis's table of one benign argument per filter,
    and using it here left `json_script`, `timesince`, `timeuntil` and
    `urlencode` out of the argument sweep entirely — reported by the
    reachability manifest's `argument-filter` axis, which exists so the two
    cannot drift again.

    `needs_autoescape=True` injects an `autoescape` kwarg that is not a
    template argument, so it is excluded here — but NOT because `args_check`
    excludes it. It does not: `args_check` reads the RAW argspec, so
    `{{ p|urlize:"x" }}` passes the compile-time check and the CALL then raises
    `TypeError: got multiple values for argument 'autoescape'`. An earlier
    version of this docstring stated the opposite, and that is exactly the
    premise #2400 turns on — see `arity_cells` below, which needs both bounds.
    """
    names = []
    for name, fn in sorted(register.filters.items()):
        args = [a for a in inspect.getfullargspec(inspect.unwrap(fn)).args if a != "autoescape"]
        if len(args) >= 2:
            names.append(name)
    return names


def arg_cells():
    """Every argument-taking built-in × every spelling × the hot inputs."""
    for name in django_argument_filters():
        for arg in ARG_SPELLINGS:
            for key in INPUTS_2:
                yield name, arg, key


# ---------------------------------------------------------------------------
# The ARITY axis (#2400) — the eleventh, and the sixth blind spot
# ---------------------------------------------------------------------------
#
# The corpus swept every filter, every argument SPELLING, every value shape and
# every tag — and could not construct one cell with the wrong argument COUNT.
# `cells()` gives each argument-taking filter exactly one VALID argument out of
# `FILTER_ARGS` and gives every other filter none; `arg_cells()` sweeps
# spellings of a single argument over the filters that take one. Neither can
# write `{{ p|upper:"x" }}` or `{{ p|default }}`.
#
# So the manifest reported `0 MISSING` on ten axes over ~345,000 cells while 48
# of Django's 57 built-ins rendered a template Django REFUSES TO COMPILE — the
# largest single class of divergence in the whole corpus, invisible because
# nothing here built a cell that could see it.
#
# Both bounds are needed and they are not the same, which is the finding this
# axis exists to keep visible:
#
#   * `args_check` runs at COMPILE time and reads the RAW argspec, so it counts
#     `autoescape` as a slot;
#   * the CALL supplies `autoescape` as a keyword, so a positional argument in
#     that slot passes the compile check and raises `TypeError` at render.
#
# Five built-ins sit between them (`linebreaks`, `linebreaksbr`, `linenumbers`,
# `unordered_list`, `urlize`): Django compiles `{{ p|urlize:"x" }}` and refuses
# it at render. An axis that used one bound would either miss those five or
# demand a refusal Django does not make.


def django_arity_bounds(name: str) -> tuple[int, int, int]:
    """`(min, parse_max, call_max)` on the number of TEMPLATE arguments.

    Read out of DJANGO — the same `getfullargspec` on the same unwrapped
    callable that `FilterExpression.args_check` reads — and deliberately NOT
    out of `crates/djust_templates/src/filter_arity.rs`. A requirement derived
    from djust's own table would agree with it by construction and could never
    report it wrong, which is the trap #2404's `loop-variable` axis was written
    to avoid one axis over.
    """
    spec = inspect.getfullargspec(inspect.unwrap(register.filters[name]))
    args, dlen = spec.args, len(spec.defaults or [])
    has_autoescape = "autoescape" in args
    n_template = len(args) - 1 - (1 if has_autoescape else 0)
    d_template = max(0, dlen - (1 if has_autoescape else 0))
    return n_template - d_template, len(args) - 1, n_template


def django_lexer_max_arguments() -> int:
    """How many filter arguments Django's variable LEXER can EXPRESS.

    A different bound from `django_arity_bounds` and one layer above it:
    `filter_raw_string`'s argument group is optional and NON-repeating, so a
    second `:arg` is a `TemplateSyntaxError` for every filter — including ones
    whose signature would happily take it — before any filter is looked up.

    PROBED against Django's own compiler rather than transcribed from the
    regex, because a transcription of a regex is exactly the second copy this
    file exists to avoid. `default` is the probe: it takes one REQUIRED
    argument, so one argument compiles and the first count that does not fails
    on the lexer rather than on `args_check`. The wording is asserted, so a
    Django release that moved this boundary for some other reason fails here
    instead of silently lowering the bound the corpus builds cells up to.
    """
    for n in range(1, 8):
        try:
            Template("{{ p|default" + ':"x"' * n + " }}")
        except _django_template.TemplateSyntaxError as exc:
            if "Could not parse the remainder" not in str(exc):
                raise AssertionError(
                    f"the lexer probe stopped at {n} arguments for a reason that is "
                    f"not the lexer: {exc}"
                ) from exc
            return n - 1
    raise AssertionError("Django's lexer accepted 7 filter arguments — probe is wrong")


def django_refuses_arity(name: str, provided: int) -> bool:
    """Does Django refuse this count AT ALL — lexer, compile time, or the call?

    The lexer bound is checked FIRST and applies to every filter regardless of
    signature (#2409). Reading only the argspec would stop requiring a
    two-argument cell the day Django shipped a two-argument filter, while the
    lexer would still refuse it — which is the shape of requirement that is
    satisfiable by the omission it exists to detect.
    """
    if provided > django_lexer_max_arguments():
        return True
    lo, _parse_max, call_max = django_arity_bounds(name)
    return provided < lo or provided > call_max


#: The counts the corpus builds cells for — one past what Django's LEXER can
#: express, so the two-argument shape is constructed rather than declared out
#: of scope (#2409).
#:
#: It read `(0, 1)` until then, on the premise that "djust cannot SPELL two
#: arguments, so a two-argument cell would be measuring the lexer rather than
#: the arity". Both halves were true and the conclusion was wrong: djust
#: could not spell two arguments because it silently FOLDED them into one, so
#: `{{ p|cut:"a":"b" }}` handed `cut` the argument `"a":"b"`, found no such
#: substring and rendered the page unchanged — a template Django refuses to
#: compile. Measuring the lexer is the point, not a reason to skip the cell.
ARITY_COUNTS = tuple(range(django_lexer_max_arguments() + 2))


#: The MASKED-REFUSAL axis (#2411) — the twelfth, and the seventh blind spot.
#:
#: The `arity` axis writes `{{ p|name:"x"… }}` and the `tag` axis writes
#: `{% if p|name %}` with one VALID argument out of `FILTER_ARGS`. Neither can
#: write the cell #2411 is about, which needs all three at once: a TAG operand,
#: an argument that does not RESOLVE, and a refusal AFTER it. So the corpus
#: reported clean while 1,227 `{% if %}` templates Django refuses to compile
#: rendered here as a silently falsy branch.
#:
#: The mechanism is resolution ORDER, not the filters. djust reached a tag
#: operand's chain only at render time, left to right, and `{% if %}` absorbs a
#: `VariableDoesNotExist` — which is Django's own `IfNode.render`, so narrowing
#: the catch is NOT the fix. Django simply never got that far: it had refused
#: the template at COMPILE time.
#:
#: `MASK_POSITIONS` is the axis proper. Each writes the SAME refusing operand
#: somewhere djust's render-time walk could fail to reach it — behind an
#: unresolvable argument, behind a short-circuit, inside a branch that never
#: renders, or in `{% elif %}`, which is a second parse site for `Node::If`.
#: `direct` is the control: it was never masked, so a cell that starts failing
#: there is a plain regression rather than this class.
MASK_POSITIONS = {
    "direct": "{% if p|@SPEC@ %}Y{% else %}N{% endif %}",
    "after-unresolvable-arg": "{% if p|date:.|@SPEC@ %}Y{% else %}N{% endif %}",
    "after-missing-name-arg": "{% if p|date:missingvar|@SPEC@ %}Y{% else %}N{% endif %}",
    "short-circuit-and": "{% if 0 and p|@SPEC@ %}Y{% else %}N{% endif %}",
    "short-circuit-or": "{% if 1 or p|@SPEC@ %}Y{% else %}N{% endif %}",
    "elif": "{% if 0 %}A{% elif p|@SPEC@ %}B{% else %}C{% endif %}",
    # --- the `{{ }}` half of the class, added by #2419. Every position above
    # writes a TAG operand, so the corpus could report the tag half of a
    # compile-time refusal closed while the `{{ }}` half stayed open — and
    # `Invalid filter` was open on BOTH. A cross of two covered axes is its own
    # axis, which is the lesson this whole axis exists to carry.
    "dead-branch-var": "{% if 0 %}{{ p|@SPEC@ }}{% endif %}",
    "else-branch-var": "{% if 1 %}A{% else %}{{ p|@SPEC@ }}{% endif %}",
    "block-in-dead-branch-var": "{% block b2419 %}{% if 0 %}{{ p|@SPEC@ }}{% endif %}{% endblock %}",
    "dead-branch-if": "{% if 0 %}{% if p|@SPEC@ %}Y{% endif %}{% endif %}",
    "dead-branch-for": "{% if 0 %}{% for x in p|@SPEC@ %}Y{% endfor %}{% endif %}",
    "dead-branch-with": "{% if 0 %}{% with v=p|@SPEC@ %}Y{% endwith %}{% endif %}",
    # --- the four #2418 wired, each a tag arm that resolves a NAME and that
    # #2411 did not reach. The manifest reported all four MISSING the moment
    # the engine grew the call, which is what this axis is for.
    "cycle": "{% cycle p|@SPEC@ 'z' %}",
    "firstof": "{% firstof p|@SPEC@ 'F' %}",
    "widthratio": "{% widthratio p|@SPEC@ 10 100 %}",
    "include": '{% include "x.html" with v=p|@SPEC@ %}',
}

#: One spec per COMPILE-time refusal class, so a fix that closes one and not
#: the others is visible per-cell rather than as a single number.
#:
#: Every spec here is CLOSED, and each one was open when it was added — a spec
#: that stops refusing is exactly what this axis exists to report, so they stay.
#: `date:_y` was closed by #2418; `nosuchfilter` by #2419, which moved the name
#: LOOKUP to parse time for `{{ }}` and the tag operands together (one site,
#: `parser::parse_filter_specs`) after measuring that djust's filter registry is
#: populated during `django.setup()`, before any template can be parsed.
MASK_SPECS = [
    "cut",  # args_check: too FEW
    "join",  # args_check: too few, second filter
    'upper:"x"',  # args_check: too MANY
    'cut:"a":"b"',  # the lexer bound (#2409), not the signature
    "nosuchfilter",  # Invalid filter — the name lookup (#2419)
    "date:_y",  # Variable.__init__'s underscore rule (#2418)
]


def mask_cells():
    """Every refusal class × every position djust's walk could fail to reach.

    Both halves are needed. A position with one spec cannot tell "this position
    is reached" from "this refusal is implemented"; a spec at one position
    cannot tell a parse-time check from a render-time one.
    """
    for spec in MASK_SPECS:
        # The premise, PROBED rather than declared: Django must refuse the bare
        # shape at COMPILE time, or the cell is measuring something else.
        try:
            Template("{{ p|" + spec + " }}")
        except _django_template.TemplateSyntaxError:
            pass
        else:
            raise AssertionError(f"django compiles {spec!r} — it is not a refusal cell")
        for position in MASK_POSITIONS:
            for key in INPUTS_3:
                yield spec, position, key


#: The VARIABLE-NAME axis (#2418) — the eighth blind spot.
#:
#: `Variable.__init__` refuses a name beginning with `_`, or carrying `._`
#: anywhere, while the template is being COMPILED. It is a rule about the NAME
#: and not about the value, so it fires whether or not the name resolves —
#: which is why it survived #2411's 13,202-template sweep: that sweep bound no
#: `_x`, so djust refused those cells for the unrelated "argument does not
#: resolve" reason and they never showed as divergent.
#:
#: This corpus could not see it either, and for a plainer reason: every head it
#: writes is `p` and every argument spelling was a name Django accepts. A rule
#: Django applies at three positions went unmeasured while ~345,000 cells
#: reported clean. The `argument` position also rides `ARG_SPELLINGS` above, so
#: it crosses every argument-taking built-in; the rows here are what make the
#: HEAD and OPERAND positions constructible at all.
#:
#: One spelling per arm of Django's own ordering, so a fix that reproduces the
#: refusal but not the two arms that run BEFORE it is visible per-cell:
NAME_SPELLINGS = {
    "leading": "_x",  # refused: `var[0] == "_"`
    "dotted": "p._priv",  # refused: `"._" in var`
    "quoted": '"_x"',  # NOT refused: the literal arm runs first
    "i18n": '_("_x")',  # NOT refused: `_( … )` is stripped first
}

#: The positions the rule runs at. Which ones are REQUIRED is read out of
#: `parser.rs` in `_required_name_positions`, so a fourth call site reports
#: this axis MISSING rather than passing silently.
NAME_POSITIONS = {
    "head": "{{ @NAME@ }}",
    "head-filtered": "{{ @NAME@|upper }}",
    "argument": "{{ p|default:@NAME@ }}",
    "operand-if": "{% if @NAME@ %}Y{% else %}N{% endif %}",
    "operand-for": "{% for x in @NAME@ %}[{{ x }}]{% empty %}E{% endfor %}",
    "operand-with": "{% with q=@NAME@ %}[{{ q }}]{% endwith %}",
    "operand-firstof": "{% firstof @NAME@ 'F' %}",
}


def name_cells():
    """Every name spelling × every position the engine resolves a name at."""
    for spelling in NAME_SPELLINGS:
        for position in NAME_POSITIONS:
            for key in INPUTS_3:
                yield spelling, position, key


def arity_cells():
    """Every built-in × every argument COUNT its own signature refuses.

    The LEGAL counts are already swept by the `filter` and `argument` axes, so
    this axis carries only the refused ones — the cells that did not exist.
    """
    for name in sorted(register.filters):
        for provided in ARITY_COUNTS:
            if not django_refuses_arity(name, provided):
                continue
            for key in INPUTS_3:
                yield name, provided, key


#: The BUILTIN-VALUE axis (#2347).
#:
#: Every cell above binds `p` and writes `p` as the expression, so the corpus
#: could not construct a bare `True` / `False` / `None` in the VALUE position
#: at all — and that is precisely where djust diverged: `{{ True }}` rendered
#: the empty string where Django renders `True`, because
#: `django.template.context.builtins` puts the three names in every context and
#: djust's `Context::resolve` had no arm for them. The argument axis above
#: reaches them as ARGUMENTS; this reaches them as the value.
#:
#: Swept through every filter, not just bare, because the divergence composes:
#: `{{ True|yesno }}` was `maybe` (the `Missing` answer) rather than `yes`, and
#: a fix that only special-cased the bare form would leave that.
#:
#: The tag shapes are here for the opposite reason — `{% if True %}` and
#: `{% firstof None False True %}` were ALREADY right, because
#: `renderer::get_value_safe` carried its own literal arms. They are the
#: non-regression half: the fix converges both resolvers onto one helper, and
#: these cells are what would go red if that convergence changed the answer on
#: the side that was already correct.
#: The same axis carries the QUOTED and NUMERIC literals (#2376), for the same
#: reason and one step further along: every cell outside this axis writes `p`
#: as the expression, so the corpus could not construct a bare `{{ "x" }}` at
#: all — and that is precisely where djust diverged. `Node::Variable` had NO
#: literal arm of any kind, so `{{ "hello" }}`, `{{ 5 }}` and `{{ 5.5 }}` all
#: resolved through the context, missed, and rendered the EMPTY STRING. The
#: `{% if %}` / `{% with %}` / `{% firstof %}` shapes reached them only because
#: `renderer::get_value_safe` carries its own literal arms — the same
#: two-resolvers-one-blind split (#1646) that #2347 found for `True`.
#:
#: They also carry the SAFETY question the builtin names cannot: Django's
#: `Variable.__init__` does `mark_safe(unescape_string_literal(var))`, so a
#: quoted literal is `SafeData` and `{{ "<b>" }}` renders LIVE. A fix that
#: resolves the literal without granting it renders `&lt;b&gt;` — a THIRD
#: answer, neither the bug's nor Django's — so both cells must be on the axis
#: at once. Composed through every filter for the re-taint half:
#: `{{ "<b>"|upper }}` is ESCAPED in Django, because `upper` is registered
#: `is_safe=False`.
#:
#: The hostile spelling is the template AUTHOR's own text, which is why it is
#: not in `LIVE_FRAGMENTS` (these cells render over `s-plain`): Django emits it
#: live too, so djust doing the same is parity, not permissiveness.
BUILTIN_NAMES = [
    "True",
    "False",
    "None",
    '"<b>ok</b>"',
    # Single-quoted, and deliberately free of SPACES: a quoted token carrying
    # a space is a `smart_split` question rather than a literal one, and
    # conflating the two would make this axis measure two things at once.
    "'<script>alert(1)</script>'",
    '"abc"',
    # A quoted DIGIT string, which is a different cell from the bare number:
    # `{{ "5"|add:1 }}` is 6 in Django because `add` coerces, and a
    # recognizer that returned `Integer(5)` for the quoted form would agree
    # there while being wrong about `{{ "5"|length }}`.
    '"5"',
    "5",
    "5.5",
    "-5",
    "1e3",
]
BUILTIN_SHAPES = {
    "var": "{{ @NAME@ }}",
    "if": "{% if @NAME@ %}Y{% else %}N{% endif %}",
    "with": "{% with q=@NAME@ %}[{{ q }}]{% endwith %}",
    "for": "{% for x in @NAME@ %}[{{ x }}]{% empty %}E{% endfor %}",
    "firstof": "{% firstof @NAME@ p %}",
    "eq": "{% if p == @NAME@ %}Y{% else %}N{% endif %}",
    "is": "{% if p is @NAME@ %}Y{% else %}N{% endif %}",
}


#: Mapping-shaped corpus inputs, derived rather than listed — a hand-written
#: list is the transcription this whole design exists to avoid, and it would
#: silently stop covering a mapping added to INPUTS later.
def mapping_inputs():
    return {key for key, value in INPUTS.items() if isinstance(value, dict)}


#: The COMPOSITION shape (#2372). Deliberately not a `BUILTIN_SHAPES` entry:
#: those bind only `p`, so a builtin literal there can never meet a mapping.
#: This one crosses the two axes, which is the whole point.
BUILTIN_X_MAPPING = "{% if @NAME@ in @KEY@ %}Y{% else %}N{% endif %}"


def builtin_cells():
    for lit in BUILTIN_NAMES:
        for name in sorted(register.filters):
            yield lit, name, "var-filtered"
        for shape in BUILTIN_SHAPES:
            yield lit, None, shape


def builtin_x_mapping_cells():
    """A context builtin as the needle of a membership test over a mapping.

    The cell #2360 x #2339 would have been wrong in, and which neither axis
    could construct alone: `builtin` binds only `p`, and `cmp` sweeps `in` over
    corpus values on both sides, so a literal never meets a mapping.
    """
    for lit in BUILTIN_NAMES:
        for key in sorted(mapping_inputs()):
            yield lit, key


# ---------------------------------------------------------------------------
# The reachability MANIFEST (#2345)
# ---------------------------------------------------------------------------
#
# Five times now this tool has reported clean over a surface it could not
# construct, and each time the remedy was to hand-add one more axis and one more
# bespoke coupling test:
#
#   #2296  a filter added to a safety set and not the hot sets   -> a live XSS
#   #2325  tag operands — no tag cell existed at all             -> four sites
#   #2334  dict-view paths, every dict with tame keys            -> two bugs
#   #2290  the custom-filter path — no built-in dispatches there -> SafeData
#   #2345  invalid filter arguments                              -> 508 cells
#
# A corpus gap is silent BY CONSTRUCTION, so "no axis reported a problem" and
# "no axis exists for the problem" print the same thing. The fix is not a sixth
# bespoke coupling: it is to make the corpus DECLARE its axes, and to make each
# axis name the set the ENGINE says it must cover, recomputed from the engine
# rather than transcribed. Then the tool can say what it CANNOT reach.
#
# What this design catches
# ------------------------
# * An engine-derived set gaining a member the corpus does not sweep — a new
#   safety-set entry (#2296), a new whitespace boundary the engine branches on,
#   a new argument-error the chokepoint can raise, a Django release adding a
#   filter or a tag. The requirement is RECOMPUTED at check time, so it cannot
#   fall behind.
# * A render entry point the extension module exposes that no cell calls
#   (#2290's `register_custom_filter`, #2287's `render_template_with_dirs`).
# * A whole axis existing but reaching NOTHING — every declared axis must
#   produce cells, and `--require-moved` fails a run where the axis a change is
#   about moved zero cells.
# * The misdiagnosis in #2345 itself: identical agreement counts used to be
#   reported as "the baseline is not real", which was WRONG for two genuinely
#   different builds. The results file now records the `_rust` build's digest,
#   so that question is answered rather than guessed.
#
# What it CANNOT catch, stated plainly
# ------------------------------------
# The `input-shape` axis is declared UNVERIFIED, and it is the honest limit.
# Nothing in either engine's source says "a dict's keys must be hostile"
# (#2334) or "a tuple must appear at the nesting position" (#2317) — those are
# VALUE choices inside an axis that already exists, and they were both found by
# a person noticing, not by a check. A manifest lists what you declared; an
# axis nobody has conceived of has no row and no missing member. This design
# narrows that class (an unswept ENTRY POINT or TAG is now mechanically
# missing rather than merely unthought-of) without closing it.

REPO = pathlib.Path(__file__).resolve().parents[1]


def _crate_source(crate: str, module: str) -> str:
    return (REPO / "crates" / crate / "src" / f"{module}.rs").read_text(encoding="utf-8")


def _rust_const(name: str) -> set[str]:
    """The string literals of a `const NAME: [&str; N] = [...]` in renderer.rs."""
    src = _crate_source("djust_templates", "renderer")
    body = src.split(f"const {name}: [&str;", 1)[1].split("[", 1)[1].split("];", 1)[0]
    return set(re.findall(r'"(\w+)"', body))


def _rust_char_set(module: str, predicate: str) -> set[str]:
    """The `char` literals of a `matches!(c, ...)` predicate in a Rust module."""
    body = _crate_source("djust_templates", module).split(f"fn {predicate}(", 1)[1]
    body = body.split("\n}", 1)[0]
    escapes = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", "'": "'", "0": "\0"}
    found: set[str] = set()
    for uni, esc, plain in re.findall(r"'(?:\\u\{([0-9a-fA-F]+)\}|\\(.)|([^'\\]))'", body):
        if uni:
            found.add(chr(int(uni, 16)))
        elif esc:
            found.add(escapes[esc])
        else:
            found.add(plain)
    return found


def _required_chain_filters() -> dict[str, str]:
    """Every name the engine grants output safety to, by either channel.

    Channel one is `renderer.rs`'s three name constants; channel two is
    `filters::builtin_produced_safe`, which answers per CALL for the filters
    whose safety depends on which branch of the body ran. A name granted safety
    and absent from the composed sets is a blind spot aimed exactly where the
    grant was made — that is the `dictsort` XSS (#2296) in one sentence.
    """
    out: dict[str, str] = {}
    for const in (
        "SAFE_OUTPUT_FILTERS",
        "ITEM_SAFE_OUTPUT_FILTERS",
        "ITEM_SAFETY_PRESERVING_FILTERS",
    ):
        for name in _rust_const(const):
            out[name] = f"renderer.rs::{const}"
    body = _crate_source("djust_templates", "filters").split("fn builtin_produced_safe", 1)[1]
    body = body.split("\npub fn ", 1)[0]
    for arm in re.findall(r"^\s{8}((?:\"\w+\"\s*\|\s*)*\"\w+\") =>", body, re.M):
        for name in re.findall(r'"(\w+)"', arm):
            out.setdefault(name, "filters.rs::builtin_produced_safe")
    return out


def _required_whitespace() -> dict[str, str]:
    """Every character the engine's own whitespace predicates branch on."""
    out = {}
    for module, predicate in (
        ("pprint", "py_is_line_break"),
        ("textwrap", "is_textwrap_space"),
    ):
        for char in _rust_char_set(module, predicate):
            out.setdefault(f"U+{ord(char):04X}", f"{module}.rs::{predicate}")
    # `truncate::py_is_space` is a RANGE, not a literal set, so it cannot be
    # parsed the same way; these are its two members that neither literal set
    # contains and both are load-bearing (`\xa0` is a WORD to textwrap's
    # splitter that `drop_whitespace` discards; `\x1f` survives `splitlines`
    # while stripping to empty).
    out.setdefault("U+00A0", "truncate.rs::py_is_space (range)")
    out.setdefault("U+001F", "truncate.rs::py_is_space (range)")
    return out


def _swept_whitespace() -> set[str]:
    """EVERY character the corpus's string inputs contain, not just the spaces.

    Deliberately unfiltered. The requirement is "the engine branches on this
    character somewhere", and a first draft OR-ed an `isspace()`-filtered set
    with this one — a strict subset, so the filtered half could never change
    the answer. Two mechanisms where one is doing the work is the shape this
    file's own manifest exists to make visible; it is one comprehension now.

    `truncate::py_is_space` also counts `\\x1c`-`\\x1f`, which `str.isspace()`
    does not, so filtering would additionally have been wrong.
    """
    return {f"U+{ord(c):04X}" for v in INPUTS.values() if isinstance(v, str) for c in v}


#: Every `format!` in `filters.rs` naming a FILTER and its ARGUMENT — i.e. the
#: complete set of failures the argument chokepoint can produce. Parsed rather
#: than listed, so a new one is a missing manifest member until a corpus
#: spelling reaches it.
#: What an argument-error message looks like. TWO spellings, because the
#: engine has two: an error raised by shared code interpolates the filter name
#: (`filter '{filter_name}'`), while one raised inside a module that knows
#: which filter it is names it literally (`filter 'floatformat'`).
#:
#: The placeholder-only form was the original, and it UNDER-REPORTED: #2346
#: added `floatformat`'s empty-argument `IndexError`, which has neither
#: property — it lives in `floatformat.rs` and names its filter literally — so
#: no requirement row demanded it be reachable. A requirement source that can
#: miss a requirement is the failure this manifest exists to prevent, one level
#: in, which is why it is widened here rather than worked around.
_ARG_ERROR_MARK = re.compile(r"filter '(?:\{filter_name\}|\w+)'")

#: Every module that can raise an argument error. `filters.rs` holds the shared
#: chokepoint; `floatformat.rs` has its own `parse_arg` and raises directly.
_ARG_ERROR_MODULES = ("filters", "floatformat")


def _required_argument_errors() -> dict[str, str]:
    out = {}
    for module in _ARG_ERROR_MODULES:
        # Rust string literals continue across a trailing `\`; rejoin them,
        # then keep the ones that name a filter — every argument error does,
        # and nothing else in these modules does.
        joined = re.sub(r"\\\n\s*", "", _crate_source("djust_templates", module))
        for literal in re.findall(r'"((?:[^"\\]|\\.)*)"', joined):
            if not _ARG_ERROR_MARK.search(literal):
                continue
            out.setdefault(_error_signature(literal), f"{module}.rs")
    return out


def _rust_unescape(literal: str) -> str:
    """A Rust string literal's SOURCE text, decoded to the string it denotes.

    Load-bearing, not tidiness. The requirement set is read out of Rust source,
    where a quote inside a string is written `\\"`; the message the engine
    actually raises has a real quote. A signature built from the raw source can
    therefore never match the runtime message, and the manifest reports an
    error it can in fact reach as UNREACHABLE.

    Invisible until #2346, because the first five argument errors contain no
    escape at all. Its `floatformat` message is the first that does — it quotes
    the empty argument — and the manifest reported it missing.
    """
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t", "r": "\r"}.get(m[1], m[1]), literal)


def _error_signature(fmt: str) -> str:
    """A format string reduced to its literal pieces, `{}`-placeholders gone.

    The pieces are what an OBSERVED message must contain, in order, for the
    corpus to have reached that error. Comparing whole messages would fail on
    the interpolated filter name; comparing a hand-picked fragment would be a
    transcription that drifts.
    """
    decoded = _rust_unescape(fmt)
    pieces = [p for p in re.split(r"\{[^{}]*\}", decoded) if len(p.strip()) >= 4]
    return _SIGNATURE_GAP.join(p.strip() for p in pieces)


#: Joins a signature's literal pieces. A character no Rust source literal in
#: this file contains, so splitting a signature back apart is unambiguous.
_SIGNATURE_GAP = " … "


def _signature_matches(signature: str, observed: str) -> bool:
    position = 0
    for piece in signature.split(_SIGNATURE_GAP):
        found = observed.find(piece, position)
        if found < 0:
            return False
        position = found + len(piece)
    return True


def _swept_argument_errors() -> set[str]:
    """The argument errors the corpus's spellings actually reach, MEASURED.

    Rendered rather than reasoned about: which spelling triggers which arm of
    the chokepoint is exactly the thing this axis exists to stop guessing at.

    Iterates `arg_cells()` ITSELF rather than re-deriving the product. This
    open-coded `sorted(FILTER_ARGS) x ARG_SPELLINGS x INPUTS_2` until #2344,
    and when `arg_cells` moved to `django_argument_filters()` — 29 names, where
    `FILTER_ARGS` has 25 — this copy stayed behind. The axis then measured a
    NARROWER corpus than the one it ships and reported #2344's new error
    unreachable, though the corpus reaches it on the first `timesince` cell it
    builds. Two copies of one product is the drift this file exists to make
    visible, and it had grown one inside the file itself.
    """
    required = _required_argument_errors()
    reached: set[str] = set()
    for name, spelling, key in arg_cells():
        _, du = render_both("{{ p|%s:%s }}" % (name, spelling), {"p": INPUTS[key], **ARG_CONTEXT})
        if not du.startswith("<<EXC "):
            continue
        for signature in required:
            if _signature_matches(signature, du):
                reached.add(signature)
    return reached


#: Every Django built-in tag, and whether the corpus builds a cell for it.
#:
#: #2325 is the reason this axis exists: the corpus had NO tag cell at all, so
#: a filter chain on a tag operand — a different resolution path, which djust
#: had open-coded four times — was entirely unmeasured. With this row, the
#: pre-#2325 corpus reports 25 missing tags instead of reporting clean.
#:
#: A tag NOT swept needs a reason here — and the reason has to be a PROPERTY of
#: the tag, not a note that nobody got to it. Six rows here once read "TAKES A
#: FILTER-EXPRESSION OPERAND and is not swept"; that is an admission, and #2355
#: closed it. Sweeping `cycle` / `firstof` / `ifchanged` / `regroup` /
#: `widthratio` / `filter` found six divergences, three of them silent.
#:
#: What is left needs no operand cell BECAUSE OF WHAT IT IS: it emits nothing,
#: it takes no operand, it needs a second template on disk, a URLconf, a
#: request, or the wall clock. `autoescape` is the one row that is a real
#: unmeasured surface rather than a non-question, and it says so.
TAGS_NOT_SWEPT = {
    "autoescape": (
        "changes the ESCAPING POLICY for a block. Every cell renders under the "
        "engine's pinned autoescape=on, so `{% autoescape off %}` is a real "
        "unmeasured surface — but it is a policy axis, not an operand one, and "
        "it needs its own cell shape rather than a tag entry"
    ),
    "block": "template inheritance; needs a second template, which `render_template` has none of",
    "extends": "template inheritance; `resolve_template_inheritance` is its own surface (#1801)",
    "include": "template inclusion; needs a second template on disk",
    "comment": "emits nothing; no operand of any kind",
    "verbatim": "emits its body verbatim; no operand",
    "templatetag": "emits a fixed delimiter string; no operand",
    "load": "a parse-time registry action; emits nothing",
    "debug": "dumps the whole context; output is environment, not a comparable cell",
    "csrf_token": "emits a per-request token; not deterministic and not an operand",
    "now": "reads the wall clock; would be a NONDET cell with no operand",
    "lorem": "generates random words; NONDET, and its operand is a count not a chain",
    "querystring": "reads `request.GET`; the corpus binds no request",
    "url": "resolves a URLconf; the corpus configures no ROOT_URLCONF",
    "spaceless": "whitespace-strips its body; no operand",
    "resetcycle": "names a `{% cycle %}`; no filter expression of its own",
}


#: The member names Django's own `ForNode.render` writes into `forloop`.
#:
#: Parsed out of the REFERENCE implementation's source, not transcribed and not
#: read out of djust's — a transcription is a second copy that drifts, and
#: reading djust's own source would make the requirement satisfiable by the
#: very omission it exists to detect. The day Django adds a member, this axis
#: reports it MISSING until the corpus builds a cell for it.
#:
#: Two spellings because `ForNode.render` has two: six members are assigned
#: `loop_dict["<name>"] = …` per iteration, while `parentloop` is written once,
#: in the dict literal `context["forloop"] = {"parentloop": parentloop}`.
_FORLOOP_MEMBER = re.compile(
    r"""loop_dict\[\s*["'](\w+)["']\s*\]|\{\s*["'](\w+)["']\s*:\s*parentloop\s*\}"""
)


def _required_arities() -> dict[str, str]:
    """Every `(filter, count)` Django refuses, read out of Django's registry.

    `getfullargspec` on the unwrapped callable — the same call
    `FilterExpression.args_check` makes — so the requirement cannot be
    satisfied by the omission it exists to detect (#2400).
    """
    return {
        f"{name}:{provided}": "django defaultfilters.register (getfullargspec)"
        for name in register.filters
        for provided in ARITY_COUNTS
        if django_refuses_arity(name, provided)
    }


def _swept_arities() -> set[str]:
    """Derived from the cells the corpus BUILDS, never from the requirement."""
    return {f"{name}:{provided}" for name, provided, _key in arity_cells()}


#: The three answers `ForNode.render` can give a resolved operand, and the
#: source line each is read from. Parsed rather than transcribed, so a Django
#: rewrite that adds or drops a branch fails here instead of drifting.
#:
#: `refused` is the one djust could not give until #2382: the `list(values)`
#: call is what raises, and it is reached only for an operand with no
#: `__len__` — so a corpus with no such input measures nothing about it.
_FOR_OUTCOME_BRANCHES = {
    "empty-branch": r"if\s+len_values\s*<\s*1",
    "refused": r'if\s+not\s+hasattr\(values,\s*"__len__"\)',
    "iterated": r"for\s+i,\s*item\s+in\s+enumerate\(values\)",
}


def _required_for_outcomes() -> dict[str, str]:
    """The outcomes a `{% for %}` operand must be swept through (#2382).

    Read out of Django's own `ForNode.render`, which decides an operand's fate
    in three steps and NOT in one::

        values = self.sequence.resolve(context, ignore_failures=True)
        if values is None:                       # -> the empty branch
            values = []
        if not hasattr(values, "__len__"):       # -> list(), which can RAISE
            values = list(values)
        len_values = len(values)
        if len_values < 1:                       # -> the empty branch
            return self.nodelist_empty.render(context)

    djust rendered the empty branch for every operand that was not a sequence,
    which collapses the second step's two answers — materialised, or refused —
    into the first's. So the corpus must be able to construct an operand that
    reaches each.

    WHAT THIS DOES NOT CLOSE, and it is the `input-shape` limit one axis over:
    it requires the three OUTCOMES, not the set of Python TYPES that reach the
    refusal. Django's source names no such set; `INPUTS` now carries a bool, a
    finite `Decimal` and an `int` past `i64` alongside the `int` and `float`
    that were already there, and that choice is a person's rather than a
    derivation. `python/tests/test_for_non_iterable_2382.py` measures which of
    them Django refuses, from Django.
    """
    source = inspect.getsource(_django_template.defaulttags.ForNode.render)
    out: dict[str, str] = {}
    for name, pattern in _FOR_OUTCOME_BRANCHES.items():
        if re.search(pattern, source):
            out[name] = "django defaulttags.ForNode.render"
    if len(out) != len(_FOR_OUTCOME_BRANCHES):
        # The `_ARG_ERROR_MARK` lesson: a source the parse can silently stop
        # matching is the failure this axis exists to prevent, one level in.
        missing = sorted(set(_FOR_OUTCOME_BRANCHES) - set(out))
        raise AssertionError(
            f"ForNode.render no longer spells {missing} — re-read it before trusting this axis"
        )
    return out


def _swept_for_outcomes() -> set[str]:
    """Derived by ASKING DJANGO what each corpus input does, never by reading
    the same table the requirement came from (#1859: a self-comparison could
    never go red)."""
    shape = "{% for x in p %}[{{ x }}]{% empty %}E{% endfor %}"
    # Read out of the shapes the corpus BUILDS: deleting `for-bare` must be
    # noticed, and iterating `INPUTS` alone would not notice it.
    if not any(shape == src for src in PATH_SHAPES.values()):
        return set()
    reached: set[str] = set()
    for value in INPUTS.values():
        try:
            rendered = Template(shape).render(Context({"p": value}))
        except Exception:  # noqa: BLE001 — a refusal is an outcome, not a skip
            reached.add("refused")
            continue
        reached.add("empty-branch" if rendered == "E" else "iterated")
    return reached


def _required_separators_in_constants() -> dict[str, str]:
    """The characters a QUOTED filter argument must be swept carrying (#2409).

    Django's `constant_string` is `"[^"\\\\]*(?:\\\\.[^"\\\\]*)*"` — every
    character except the closing quote, the two SEPARATORS included. So a
    quoted argument carrying `|` or `:` is one argument, and an expression
    splitter that scans for those characters without respecting quoting gets
    both wrong. Read from `django.template.base`, so the requirement is the
    grammar's own alphabet rather than a transcription of it.
    """
    return {
        _FILTER_SEPARATOR: "django.template.base.FILTER_SEPARATOR",
        _FILTER_ARGUMENT_SEPARATOR: "django.template.base.FILTER_ARGUMENT_SEPARATOR",
    }


def _swept_separators_in_constants() -> set[str]:
    """Derived from the SPELLINGS the corpus builds, never from the requirement.

    A separator counts as swept only when it appears INSIDE a quoted spelling:
    `"a|b"` exercises the split, `missingvar` does not, and an unquoted `|`
    would be a second filter rather than an argument.
    """
    reached: set[str] = set()
    for spelling in ARG_SPELLINGS:
        if len(spelling) < 2 or spelling[0] not in "\"'" or spelling[-1] != spelling[0]:
            continue
        for char in spelling[1:-1]:
            if char in (_FILTER_SEPARATOR, _FILTER_ARGUMENT_SEPARATOR):
                reached.add(char)
    return reached


def _required_forloop_members() -> dict[str, str]:
    source = inspect.getsource(_django_template.defaulttags.ForNode.render)
    out: dict[str, str] = {}
    for assigned, in_literal in _FORLOOP_MEMBER.findall(source):
        out.setdefault(assigned or in_literal, "django defaulttags.ForNode.render")
    # A source the parse can silently stop matching is the failure this axis
    # exists to prevent, one level in (the `_ARG_ERROR_MARK` lesson). Django
    # has had exactly these seven since 1.0; fewer means the regex stopped
    # reading `ForNode.render`, not that Django removed a member.
    if len(out) < 7:
        raise AssertionError(
            f"_FORLOOP_MEMBER matched {len(out)} members in ForNode.render "
            f"({sorted(out)}) — expected at least 7. The parse has drifted "
            f"from Django's source; fix the regex rather than the assertion."
        )
    return out


def _swept_forloop_members() -> set[str]:
    """The members the corpus's cells actually REFERENCE.

    Derived from the cells the corpus BUILDS, not from the requirement it is
    checked against — a self-comparison could never go red (#1859). A corpus
    that dropped `revcounter0` back out fails here.
    """
    swept: set[str] = set()
    for source in _corpus_templates():
        swept |= set(re.findall(r"\bforloop\.(\w+)", source))
    return swept


def _swept_builtin_x_mapping() -> set[str]:
    """The builtin-x-mapping cells the corpus is WIRED to emit.

    Read out of this module's own source, the way `_swept_tags` reads tags out
    of the shape tables — not by re-running the generator the requirement is
    built from, which would be a self-comparison that could never go red
    (#1859), and not from a set the measurement pass populates at runtime,
    which would make `--manifest` report missing forever.

    So the check is: does the measurement pass actually iterate
    `builtin_x_mapping_cells()` and emit an `@bxm` cell for each? Delete either
    and this collapses to empty, and all 77 members go MISSING.
    """
    # Walk the AST rather than grep the text. A string check CANNOT work here:
    # the needle would appear inside this very function, so the check would
    # match its own source and never go red. That is the same
    # instrument-measures-itself failure this manifest exists to catch, and the
    # first version of this function had it.
    tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
    here = _swept_builtin_x_mapping.__name__
    wired = any(
        isinstance(node, ast.For)
        and isinstance(node.iter, ast.Call)
        and getattr(node.iter.func, "id", None) == "builtin_x_mapping_cells"
        and getattr(fn, "name", None) != here
        for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef)
        for node in ast.walk(fn)
    )
    if not wired:
        return set()
    return {f"{lit} in {key}" for lit, key in builtin_x_mapping_cells()}


def _corpus_templates() -> list[str]:
    """Every template string the corpus renders, from every shape table."""
    return (
        list(TAG_SHAPES.values())
        + list(PATH_SHAPES.values())
        + list(CUSTOM_TAG_SHAPES.values())
        + list(BUILTIN_SHAPES.values())
    )


def _required_tags() -> dict[str, str]:
    out = {}
    for library in Engine.get_default().template_builtins:
        for name in library.tags:
            out.setdefault(name, "django Engine.template_builtins")
    return out


def _swept_tags() -> set[str]:
    """Every built-in tag any shape's SOURCE opens, read out of the source.

    Read out of the source rather than off `TAG_SHAPES`'s KEYS (#2355). The
    keys were the answer while every shape was named for its tag, and that
    coupling is exactly the kind that quietly stops being true: `regroup-by`
    and `firstof-as` are second shapes for a tag that already has one, and a
    key-derived set would have reported them as unswept tags named
    `regroup-by` / `firstof-as` — which are not tags at all, so they would
    have been filtered out and the real tag would have looked missing.
    Deriving from the source is the same mechanism `PATH_SHAPES` already used
    and cannot drift from what the corpus actually renders.
    """
    swept = set()
    sources = (
        list(TAG_SHAPES.values()) + list(PATH_SHAPES.values()) + list(CUSTOM_TAG_SHAPES.values())
    )
    for source in sources:
        swept |= set(re.findall(r"\{%\s*(\w+)", source))
    return {tag for tag in swept if tag in _required_tags()}


def _required_mask_positions() -> dict[str, str]:
    """Every TAG-OPERAND parse site, read out of `parser.rs`.

    Read from the RUST source rather than from `MASK_POSITIONS`'s keys, for the
    reason `_swept_tags` gives one axis up: a key-derived requirement agrees
    with the corpus by construction and could never report it short (#1859).
    The set is the tag arms that call `validate_tag_operand` /
    `validate_if_operands` (#2411) — so the day a fifth operand-bearing tag
    grows the call, the manifest reports this axis MISSING a position until a
    cell is built for it.
    """
    src = _crate_source("djust_templates", "parser")
    out: dict[str, str] = {}
    for name, body in re.findall(r'"(\w+)" =>(.*?)(?=\n                "|\Z)', src, re.S):
        if "validate_tag_operand(" in body or "validate_if_operands(" in body:
            out[name] = "parser.rs tag arm calling the operand validator"
    if "validate_if_operands(args)?" in src.split('name == "elif"', 1)[-1][:900]:
        out["elif"] = "parser.rs parse_if_block's {% elif %} arm"
    return out


def _swept_mask_positions() -> set[str]:
    """The TAGS the masked-refusal positions actually open, read out of their
    own source — the same mechanism `_swept_tags` uses, so a position renamed
    without its template changing cannot pass for coverage."""
    swept: set[str] = set()
    for source in MASK_POSITIONS.values():
        swept |= set(re.findall(r"\{%\s*(\w+)", source))
    return {tag for tag in swept if tag in _required_mask_positions()}


#: How each `validate_variable_name` call site in `parser.rs` maps to a
#: corpus POSITION. Spelled here rather than derived, because the mapping is
#: the claim: a fourth call site whose argument is not one of these three
#: raises in `_required_name_positions` rather than being silently ignored.
_NAME_CALLSITE_POSITION = {
    "expr_part": "head",  # parse_token's `Token::Variable` arm
    "arg": "argument",  # parse_filter_specs, per filter spec
    "&parts[0]": "operand",  # validate_tag_operand's own head
}


def _required_name_positions() -> dict[str, str]:
    """The positions Django's underscore rule runs at, read out of `parser.rs`.

    Read from the RUST source rather than from `NAME_POSITIONS`'s keys, for the
    reason `_required_mask_positions` gives: a key-derived requirement agrees
    with the corpus by construction and could never report it short (#1859).
    """
    src = _crate_source("djust_templates", "parser")
    out: dict[str, str] = {}
    for callsite in re.findall(r"validate_variable_name\(([^)]*)\)\?", src):
        position = _NAME_CALLSITE_POSITION.get(callsite.strip())
        if position is None:
            raise AssertionError(
                f"parser.rs calls validate_variable_name({callsite!r}) and this "
                f"corpus has no position for it. Add the row to "
                f"_NAME_CALLSITE_POSITION and a cell to NAME_POSITIONS in the "
                f"SAME commit as the engine change."
            )
        out[position] = f"parser.rs validate_variable_name({callsite.strip()})"
    return out


def _swept_name_positions() -> set[str]:
    """The positions the corpus's own templates actually WRITE the name at.

    Read out of the templates rather than off `NAME_POSITIONS`'s keys, so a
    position renamed without its template changing cannot pass for coverage —
    the same mechanism `_swept_mask_positions` uses.
    """
    swept: set[str] = set()
    for template in NAME_POSITIONS.values():
        if re.search(r"\{\{\s*@NAME@", template):
            swept.add("head")
        if ":@NAME@" in template:
            swept.add("argument")
        if re.search(r"\{%[^%]*@NAME@", template):
            swept.add("operand")
    # `ARG_SPELLINGS` carries the refused names too, which is what crosses the
    # argument position with every argument-taking built-in rather than with
    # `default` alone.
    if any(s.startswith("_") or "._" in s for s in ARG_SPELLINGS):
        swept.add("argument")
    return swept


#: Every `_rust` function that RENDERS a template or CHANGES how one renders.
#:
#: #2290 is the reason this axis exists: `register_custom_filter` had been on
#: the module the whole time and no cell called it, so the entire custom-filter
#: dispatch path — a real Python call across the PyO3 boundary that no built-in
#: reaches — was unmeasured, and `SafeData` was invisible across it. #2287 is
#: the same shape one function over (`render_template_with_dirs` is the only
#: entry point carrying `safe_keys`).
#:
#: An unexercised entry point needs a reason here.
ENTRY_POINTS_NOT_SWEPT = {
    "set_number_format": (
        "the localization channel (#2221). Every cell renders under the default "
        "English format; a localized sweep is a second corpus, not a spelling"
    ),
    "set_active_timezone": "the timezone channel (#2227); same reason as set_number_format",
    "set_django_value_repr": (
        "the #2203 value-repr flag. Cells render under the shipped default; both "
        "settings would double the corpus for one boolean"
    ),
    "set_virtual_keyed_ops": "a VDOM keyed-ops flag; nothing to do with template rendering",
}


def _required_entry_points() -> dict[str, str]:
    return {
        name: "djust._rust module surface"
        for name in dir(_rust)
        if name.startswith(("render_template", "register_", "set_"))
    }


def _swept_entry_points() -> set[str]:
    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    return set(re.findall(r"_rust\.(\w+)\(", source))


#: The Rust `Value` variants `Context::items_are_safe` accepts, mapped to the
#: Python shape a context value must have to reach each. The only slice of the
#: INPUT axis with a mechanical source — see the `input-shape` row, which is
#: UNVERIFIED precisely because the rest of it has none.
_GRANT_SHAPE_TO_PYTHON = {"List": list, "Tuple": tuple}


def _required_grant_shapes() -> dict[str, str]:
    """Every container shape the CONTEXT can grant item safety on (#2305).

    `items_are_safe` accepts `Value::List` and `Value::Tuple`; the corpus
    carried only a marked LIST, so `mark_input_safety`'s missing `PyTuple` arm
    was invisible to the tool built to see it. Adding `t-marked` moved 80
    cells, so this is load-bearing rather than shape-coverage tidiness.
    """
    src = (REPO / "crates" / "djust_core" / "src" / "context.rs").read_text(encoding="utf-8")
    body = src.split("pub fn items_are_safe", 1)[1].split("\n    pub fn ", 1)[0]
    accepted = set(re.findall(r"Value::(\w+)\(items\)", body))
    assert accepted, "the `items_are_safe` match did not parse"
    return {variant: "context.rs::items_are_safe" for variant in accepted}


def _swept_grant_shapes() -> set[str]:
    swept = set()
    for variant, py_type in _GRANT_SHAPE_TO_PYTHON.items():
        if any(
            type(value) is py_type and value and all(isinstance(v, SafeData) for v in value)
            for value in INPUTS.values()
        ):
            swept.add(variant)
    return swept


class Axis(typing.NamedTuple):
    """One axis of the corpus, and the engine-derived set it must cover.

    `required` returns `{member: where it came from}`, recomputed from Django's
    live registry or from the Rust source — never transcribed, because a
    transcription is a second copy that drifts and the whole point is that the
    corpus cannot fall behind the code it measures.

    `required=None` declares the axis UNVERIFIED: no mechanical source names
    what it must cover. That is a statement the manifest PRINTS, not a silence.
    """

    name: str
    what: str
    swept: typing.Callable[[], set[str]]
    required: typing.Callable[[], dict[str, str]] | None = None
    exempt: dict[str, str] = {}  # noqa: RUF012 — a NamedTuple default, never mutated
    unverified: str = ""


AXES = [
    Axis(
        name="filter",
        what="every filter in Django's live registry, alone, over every input",
        # Derived from the cells the corpus BUILDS, not from the registry the
        # requirement is read out of. A self-comparison could never go red
        # (#1859): the point is that a corpus which stopped iterating the live
        # registry — a transcribed list, a filtered subset — fails here.
        swept=lambda: {name for chain, _key in cells() for name in chain},
        required=lambda: {n: "django defaultfilters.register" for n in register.filters},
    ),
    Axis(
        name="chain",
        what="the 2- and 3-chains, over the names the engine grants safety to",
        swept=lambda: set(HOT2) | set(HOT3) | NONDET,
        required=_required_chain_filters,
    ),
    Axis(
        name="whitespace",
        what="the characters the engine's whitespace predicates branch on",
        swept=_swept_whitespace,
        required=_required_whitespace,
    ),
    Axis(
        name="argument",
        what="the argument spellings, over every failure the chokepoint can raise",
        swept=_swept_argument_errors,
        required=_required_argument_errors,
    ),
    Axis(
        name="argument-filter",
        what="the FILTERS the argument spellings are swept over",
        # Derived from the cells the corpus BUILDS, so a sweep that narrowed
        # back to a convenient subset fails here (#1859: a self-comparison
        # could never go red).
        swept=lambda: {name for name, _arg, _key in arg_cells()},
        required=lambda: {
            n: "django defaultfilters.register (argspec >= 2)" for n in django_argument_filters()
        },
    ),
    Axis(
        name="tag",
        what="filter chains on a TAG operand, over Django's built-in tags",
        swept=_swept_tags,
        required=_required_tags,
        exempt=TAGS_NOT_SWEPT,
    ),
    Axis(
        name="masked-refusal",
        what="the TAG-OPERAND parse sites a compile-time refusal must survive",
        # Derived from the cells the corpus BUILDS, so a sweep that narrowed to
        # a convenient subset of positions fails here (#1859).
        swept=_swept_mask_positions,
        required=_required_mask_positions,
    ),
    Axis(
        name="variable-name",
        what="the positions Django's `Variable.__init__` underscore rule runs at",
        swept=_swept_name_positions,
        required=_required_name_positions,
    ),
    Axis(
        name="entrypoint",
        what="the `_rust` functions that render, or change how rendering works",
        swept=_swept_entry_points,
        required=_required_entry_points,
        exempt=ENTRY_POINTS_NOT_SWEPT,
    ),
    Axis(
        name="grant-shape",
        what="the container shapes the CONTEXT can grant item safety on",
        swept=_swept_grant_shapes,
        required=_required_grant_shapes,
    ),
    Axis(
        name="for-operand-outcome",
        what="the answers Django's own `ForNode.render` can give a `{% for %}` operand",
        swept=_swept_for_outcomes,
        required=_required_for_outcomes,
    ),
    Axis(
        name="loop-variable",
        what="the `forloop` members Django's own `ForNode.render` writes",
        swept=_swept_forloop_members,
        required=_required_forloop_members,
    ),
    # ---- COMPOSITION rows (#2372) -------------------------------------
    #
    # Two axes that are each individually covered are NOT thereby covered
    # together. #2360 (a builtin resolves to a real bool) and #2339 (a dict key
    # keeps its Python type) are each correct alone; #2360 landing on the OLD
    # string coercion would have made `{% if True in d %}` match any dict merely
    # HAVING a key spelled "True". It was caught because a person checked the
    # interaction, not because anything asked them to.
    #
    # Deliberately NOT the full N-squared. Most pairs are genuinely orthogonal
    # and a row each would be noise — which is the failure mode of
    # over-declaring, and noise is how a manifest stops being read. A pair earns
    # a row when both axes touch the SAME RESOLUTION STEP, because that is where
    # two correct changes compose into a wrong one.
    Axis(
        name="builtin x mapping",
        what="a context builtin as the needle of a membership test over a mapping",
        # Read back out of the cell ids the corpus EMITS, not out of the
        # generator the requirement is built from. My first version used the
        # generator for both sides — a self-comparison that could never go red,
        # which is the exact #1859 shape this file's own Axis docstring warns
        # about. Deleting the emission below now fails this axis.
        swept=_swept_builtin_x_mapping,
        required=lambda: {
            f"{lit} in {key}": "BUILTIN_NAMES x the mapping-shaped INPUTS"
            for lit in BUILTIN_NAMES
            for key in sorted(mapping_inputs())
        },
    ),
    Axis(
        name="arity",
        what="the argument COUNTS Django refuses — the lexer's bound and each built-in's own",
        swept=_swept_arities,
        required=_required_arities,
    ),
    Axis(
        name="separator-in-constant",
        what="the expression separators a QUOTED argument must be swept carrying",
        swept=_swept_separators_in_constants,
        required=_required_separators_in_constants,
    ),
    Axis(
        name="input-shape",
        what="the VALUE shapes each cell is rendered over",
        swept=lambda: set(INPUTS),
        unverified=(
            "no mechanical source names which value shapes matter, OUTSIDE the "
            "`grant-shape` slice above. Neither engine's source says a dict's "
            "keys must be hostile (#2334) or that a tuple must sit at the "
            "nesting position (#2317); both were found by a person noticing. "
            "This is the class the manifest cannot catch."
        ),
    ),
]


def manifest() -> dict:
    """What this corpus can and cannot reach, computed rather than asserted."""
    rows = []
    for axis in AXES:
        row: dict = {
            "axis": axis.name,
            "what": axis.what,
            "unverified": axis.unverified,
            "exempt": dict(axis.exempt),
        }
        if axis.required is None:
            row["swept"] = sorted(axis.swept())
            row["required"] = None
            row["missing"] = []
        else:
            required, swept = axis.required(), axis.swept()
            row["swept_count"] = len(swept)
            row["required"] = required
            row["missing"] = sorted(set(required) - swept - set(axis.exempt))
            row["stale_exemptions"] = sorted(set(axis.exempt) & swept)
        rows.append(row)
    return {"axes": rows}


def build_digest() -> str:
    """A digest of the compiled `_rust` extension this run measured.

    The reason the same-build guard was WRONG (#2345). It inferred "both files
    came from one build" from identical agreement counts, which is also what a
    real change on an axis the corpus does not sweep produces — and it told the
    reader to rebuild the baseline, which was the wrong diagnosis for a
    genuinely two-build run. Recording the digest answers the question instead
    of guessing at it.
    """
    path = pathlib.Path(_rust.__file__)
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


#: Metadata rows in a results file. Prefixed `@@` so they cannot collide with a
#: cell id (`@path`/`@cmp` cells use one `@`), and STRIPPED by `load()` so a
#: baseline written before #2345 stays comparable — it simply carries no
#: metadata, and `compare` says so rather than failing.
META_PREFIX = "@@"


def axis_of(cid: str) -> str:
    """Which declared axis a cell id belongs to. Mechanical, from the id alone.

    A cell whose prefix is not listed here falls through to the `{{ }}` split
    at the bottom and is reported as `filter` or `chain` — which is WRONG
    rather than merely imprecise, because the per-axis movement report is what
    tells a reader an axis moved nothing. Any new `@`-prefixed cell family
    needs a line here, and `test_every_cell_prefix_has_an_axis` fails until it
    gets one.
    """
    if cid.startswith("@arity "):
        return "arity"
    if cid.startswith("@arg "):
        return "argument"
    if cid.startswith("@builtin "):
        return "builtin"
    if cid.startswith("@bxm "):
        return "builtin x mapping"
    if cid.startswith("@cmp "):
        return "cmp"
    if cid.startswith("@mask "):
        return "masked-refusal"
    if cid.startswith("@name "):
        return "variable-name"
    if cid.startswith("@ctag "):
        return "ctag"
    if cid.startswith("@path"):
        return "path"
    expr, _key, *shape = cid.split("\t")
    if shape:
        return "tag"
    names = expr.split("|")
    if any(n.split(":")[0] in CUSTOM for n in names):
        return "custom"
    return "chain" if len(names) > 1 else "filter"


def _cells_by_axis(result: dict[str, list[str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for cid in result:
        counts[axis_of(cid)] = counts.get(axis_of(cid), 0) + 1
    return counts


def report_manifest(data: dict) -> int:
    """Print the manifest; return the number of axes with a missing member."""
    print()
    print("reachability manifest — what this corpus can and cannot reach")
    broken = 0
    for row in data["axes"]:
        if row["unverified"]:
            print(f"  ~  {row['axis']:12} UNVERIFIED — {row['unverified'].splitlines()[0]}")
            continue
        mark = "!!" if row["missing"] else "  "
        print(
            f"  {mark} {row['axis']:12} {len(row['required'])} required, "
            f"{len(row['exempt'])} exempt, {len(row['missing'])} MISSING"
        )
        for member in row["missing"]:
            print(f"       missing: {member!r}  (from {row['required'][member]})")
        for member in row.get("stale_exemptions", []):
            print(f"       STALE EXEMPTION, now swept — delete its row: {member!r}")
        if row["missing"]:
            broken += 1
    if broken:
        print(
            f"\n  {broken} axis/axes have a member the corpus cannot reach. Every cell "
            "\n  the sweep builds is blind to any behaviour that turns on it — which is "
            "\n  how #2296, #2325, #2334, #2290 and #2345 each reported clean."
        )
    return broken


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

    # The dict-view PATH axis (#2334). A fourth-field-free three-field id, the
    # same shape the tag axis uses, so nothing above is renamed.
    for shape, key in path_cells():
        cid = f"@path\t{key}\t{shape}"
        if cid in result:
            continue
        dj, du = render_both(
            PATH_SHAPES[shape],
            {"p": INPUTS[key]},
            CONTEXT_SAFE_KEYS.get(key),
        )
        result[cid] = [dj, du]

    # The sequence-COMPARISON axis (#2335). Four fields: the second operand's
    # key is the last. `q` is a DEEP COPY so the two operands are never the
    # same object — Python's `==` would answer True on identity alone for a
    # list, and a corpus that could only compare a value to itself would not
    # be able to tell a structural comparison from an identity one.
    #
    # No `safe_keys`: every cell's output is `Y` or `N`, so the escaping axis
    # has nothing to say here, and passing them would mean two entry points
    # for one question.
    for op, ka, kb in cmp_cells():
        cid = f"@cmp {op}\t{ka}\tcmp\t{kb}"
        if cid in result:
            continue
        dj, du = render_both(
            "{%% if p %s q %%}Y{%% else %%}N{%% endif %%}" % op,
            {"p": INPUTS[ka], "q": copy.deepcopy(INPUTS[kb])},
        )
        result[cid] = [dj, du]

    # The ARGUMENT axis (#2345). Four fields, `@arg` first, so no id above is
    # renamed. The context carries `known` alongside `p` for the resolvable-
    # lookup spelling.
    for name, arg, key in arg_cells():
        cid = f"@arg {name}:{arg}\t{key}\targ"
        if cid in result:
            continue
        ctx = {"p": INPUTS[key], **ARG_CONTEXT}
        dj, du = render_both(
            "{{ p|" + name + ":" + arg + " }}",
            ctx,
            CONTEXT_SAFE_KEYS.get(key),
        )
        if name in NONDET:
            dj, du = nondet_agreement(dj, du)
        result[cid] = [dj, du]

    # The ARITY axis (#2400). Four fields, `@arity` first, so no id above is
    # renamed. No `safe_keys`: every cell here is one Django REFUSES, so there
    # is no rendered output for a grant to change — and the payload inputs are
    # carried by `INPUTS_3` so a refusal that leaked the value would show.
    for name, provided, key in arity_cells():
        spelling = ':"x"' * provided
        cid = f"@arity {name}{spelling}\t{key}\tarity"
        if cid in result:
            continue
        dj, du = render_both("{{ p|" + name + spelling + " }}", {"p": INPUTS[key]})
        result[cid] = [dj, du]

    # The MASKED-REFUSAL axis (#2411). Four fields, `@mask` first, so no id
    # above is renamed. No `safe_keys`: every cell is one Django REFUSES, so
    # there is no rendered output for a grant to change — and `INPUTS_3` carries
    # the payload inputs, so a refusal that leaked the value would show.
    #
    # `missingvar` is deliberately NOT bound: the `after-missing-name-arg`
    # position needs it to fail to resolve, which is the whole mechanism.
    # `refusal`, not `spec`: `measure` already binds a module-level `spec()`
    # helper that the chain axes call, and shadowing it here is a NameError
    # thousands of cells later rather than at this line.
    for refusal, position, key in mask_cells():
        cid = f"@mask {refusal}\t{key}\tmask\t{position}"
        if cid in result:
            continue
        dj, du = render_both(
            MASK_POSITIONS[position].replace("@SPEC@", refusal),
            {"p": INPUTS[key]},
        )
        result[cid] = [dj, du]

    # The VARIABLE-NAME axis (#2418). Four fields, `@name` first, so no id
    # above is renamed. `ARG_CONTEXT` is passed so `_x` and `known` are BOUND —
    # an unbound underscore name is refused for the unrelated resolution
    # reason, and a cell that refuses for the wrong reason measures nothing.
    for spelling, position, key in name_cells():
        cid = f"@name {spelling}\t{key}\tname\t{position}"
        if cid in result:
            continue
        dj, du = render_both(
            NAME_POSITIONS[position].replace("@NAME@", NAME_SPELLINGS[spelling]),
            {"p": INPUTS[key], **ARG_CONTEXT},
        )
        result[cid] = [dj, du]

    # The CUSTOM-TAG axis (#2356). Three fields, `@ctag` first, so no id above
    # is renamed. `safe_keys` is passed for the same reason the `{{ }}` corpus
    # passes it: the marker the CONTEXT grants is half of what this axis is
    # about — a `mark_safe`d value reaching a Python handler as a bare `str` is
    # #2290's finding, and without the grant the cell cannot ask.
    for shape, key in custom_tag_cells():
        cid = f"@ctag {shape}\t{key}\tctag"
        if cid in result:
            continue
        dj, du = render_both(
            CUSTOM_TAG_SHAPES[shape],
            {"p": INPUTS[key]},
            CONTEXT_SAFE_KEYS.get(key),
        )
        result[cid] = [dj, du]

    # The BUILTIN-VALUE axis (#2347). `p` stays bound so the `firstof` / `==` /
    # `is` shapes have a second operand; the LITERAL is what varies.
    for lit, name, shape in builtin_cells():
        if name is not None:
            source = "{{ " + lit + "|" + spec(name) + " }}"
            cid = f"@builtin {lit}|{spec(name)}\ts-plain\tbuiltin"
        else:
            source = BUILTIN_SHAPES[shape].replace("@NAME@", lit)
            cid = f"@builtin {lit}\ts-plain\t{shape}"
        if cid in result:
            continue
        dj, du = render_both(source, {"p": INPUTS["s-plain"]})
        if name in NONDET:
            dj, du = f"<NONDET len={len(dj)}>", f"<NONDET len={len(du)}>"
        result[cid] = [dj, du]

    # The COMPOSITION axis (#2372): a builtin literal as the needle of a
    # membership test over a mapping. Neither parent axis can build this —
    # `builtin` binds only `p`, and `cmp` sweeps `in` over corpus values on both
    # sides, so a literal never meets a mapping.
    for lit, key in builtin_x_mapping_cells():
        source = BUILTIN_X_MAPPING.replace("@NAME@", lit).replace("@KEY@", "p")
        cid = f"@bxm {lit} in {key}\t{key}\tbuiltin-x-mapping"
        if cid in result:
            continue
        dj, du = render_both(source, {"p": INPUTS[key]}, CONTEXT_SAFE_KEYS.get(key))
        result[cid] = [dj, du]

    agree = sum(1 for v in result.values() if v[0] == v[1])
    panicked = sum(1 for v in result.values() if v[1].startswith("<<PANIC "))
    print(f"cells={len(result)}  agree={agree}  disagree={len(result) - agree}")
    # Printed separately and always, even at zero: a panic is a transport-level
    # failure rather than a rendering one, so "how many cells disagree" is the
    # wrong number to read it off (#2343).
    print(f"cells where djust PANICKED: {panicked}")
    payload: dict = dict(result)
    payload[META_PREFIX + "build"] = build_digest()
    payload[META_PREFIX + "cells_by_axis"] = _cells_by_axis(result)
    payload[META_PREFIX + "manifest"] = manifest()
    with open(out_path, "w") as fh:
        json.dump(payload, fh)
    print(f"wrote {out_path}  (_rust build {payload[META_PREFIX + 'build']})")
    report_manifest(payload[META_PREFIX + "manifest"])


#: Metadata rows in a results file. Prefixed `@@` so they cannot collide with a
#: cell id (`@path`/`@cmp`/`@arg` cells use one `@`), and STRIPPED by `load()`
#: so a baseline written before #2345 stays comparable — it simply carries no
#: metadata, and `compare` says so rather than failing.
META_PREFIX = "@@"


def load(path: str) -> dict[str, list[str]]:
    # Collapse the nondeterministic marker: `random` picks a different element
    # each run, so its LENGTH is not comparable between two runs either.
    raw = json.load(open(path))
    # The metadata filter has to come BEFORE the unpack, not after: a `for k,
    # (a, b) in ...` target destructures every row the comprehension iterates,
    # including the ones the `if` would have dropped, so a metadata value that
    # is not a 2-sequence raises there rather than being skipped.
    cells_only = ((k, v) for k, v in raw.items() if not k.startswith(META_PREFIX))
    return {
        k: [NONDET_MARKER.sub("<NONDET>", a), NONDET_MARKER.sub("<NONDET>", b)]
        for k, (a, b) in cells_only
    }


def load_meta(path: str) -> dict:
    """The metadata rows, or `{}` for a results file written before #2345."""
    raw = json.load(open(path))
    return {k[len(META_PREFIX) :]: v for k, v in raw.items() if k.startswith(META_PREFIX)}


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

    There is NO `@ctag` arm any more, and its absence is deliberate (#2416).

    #2379 added one: a custom-tag cell had a second coincidence of the same
    kind, because the `SafeData` marker on a context value did not survive the
    PyO3 hop (#2290's finding, on the argument side) and — while the bridge
    emitted a handler's return RAW — that loss was CANCELLED on the way out.
    Two wrongs, agreeing with Django for the wrong reason. The arm excused a
    cell whose new output was Django's escaped once more WHEN the `ct-cond`
    probe over the same input diverged on both builds.

    #2416 fixed the input-side loss, so `ct-cond` AGREES on any build carrying
    it and the arm's second condition can never hold again: it became a
    classifier that could only ever mask a future custom-tag regression, which
    is a second mechanism shadowing the first (#2233). Deleted rather than
    tested around. A `@ctag` cell now falls to the generic rule below, has no
    `{{ p|expr }}` twin, and is therefore always REPORTED — which is what a
    regression on this axis should be.
    """
    expr, key, *shape = cid.split("\t")
    if not shape:
        return False  # A `{{ }}` cell has no mask to be behind.
    twin = f"{expr}\t{key}"
    b, a = base.get(twin), after.get(twin)
    return b is not None and a is not None and b[0] != b[1] and a[0] != a[1]


def compare(base_path: str, after_path: str, require_moved: tuple[str, ...] = ()) -> int:
    base, after = load(base_path), load(after_path)
    meta_b, meta_a = load_meta(base_path), load_meta(after_path)
    if set(base) != set(after):
        print("FAIL: the two runs cover different cells and are not comparable")
        return 1

    agree_b = {k for k, (a, b) in base.items() if a == b}
    agree_a = {k for k, (a, b) in after.items() if a == b}
    print(f"cells        : {len(base)}")
    print(f"agree BEFORE : {len(agree_b)}")
    print(f"agree AFTER  : {len(agree_a)}")

    # Per-axis movement, so "0 moved" is never reported without saying WHERE.
    moved_by_axis: dict[str, int] = {}
    for cid, (_dj, du) in after.items():
        if base[cid][1] != du:
            moved_by_axis[axis_of(cid)] = moved_by_axis.get(axis_of(cid), 0) + 1
    cells_by_axis = _cells_by_axis(after)
    print("djust output changed, by axis:")
    for axis in sorted(cells_by_axis):
        print(f"  {axis:10} {moved_by_axis.get(axis, 0):>6} moved  of {cells_by_axis[axis]:>6}")

    # The same-build guard, answered rather than inferred (#2345).
    #
    # It used to read identical agreement counts as "both files came from one
    # build, so the baseline is not real". That is one of TWO causes and it is
    # the less likely one: a change on an axis this corpus does not sweep
    # produces the same reading, and #2328 hit exactly that — two genuinely
    # different builds, zero moved cells, and the tool telling the reader to
    # rebuild a baseline that was already correct.
    if meta_b.get("build") and meta_a.get("build"):
        if meta_b["build"] == meta_a["build"]:
            print(
                f"\nFAIL: both files record the SAME `_rust` build ({meta_b['build']}), so\n"
                "      the baseline is not real. Rebuild it and re-measure."
            )
            return 1
        print(f"\nbuilds       : {meta_b['build']} -> {meta_a['build']}  (genuinely two builds)")
        if len(agree_b) == len(agree_a):
            print(
                "NOTE: identical agreement counts, and the two builds DIFFER — so this is\n"
                "      not a stale baseline. Either the change moves nothing, or it moves\n"
                "      an axis this corpus does not sweep. The axes with zero moved cells\n"
                "      are listed above; the manifest below lists what cannot be reached\n"
                "      at all. #2328 was the second case: it moved 1,601 argument-axis\n"
                "      cells and this corpus built none of them."
            )
    elif len(agree_b) == len(agree_a):
        print(
            "\nFAIL: identical agreement counts, and at least one file predates the build\n"
            "      digest (#2345), so the same-build question cannot be answered.\n"
            "      Re-measure both sides with a current build of this script."
        )
        return 1

    if meta_a.get("manifest"):
        report_manifest(meta_a["manifest"])

    unreachable = [a for a in require_moved if moved_by_axis.get(a, 0) == 0]
    if unreachable:
        print(
            f"\nFAIL: --require-moved named {sorted(unreachable)}, and this comparison moved\n"
            "      ZERO cells there. A change declared to be about an axis the corpus\n"
            "      could not move is unmeasured, not verified."
        )

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

    # PANICS, reported as their own line and gating on their own (#2343/#2345).
    # A panic is not a wrong answer; it is a transport-level failure that
    # `except Exception` cannot contain, so it must never be read off the
    # agreement count. Newly-panicking cells fail the run outright.
    panic_b = {k for k, (_, du) in base.items() if du.startswith("<<PANIC ")}
    panic_a = {k for k, (_, du) in after.items() if du.startswith("<<PANIC ")}
    print()
    print(f"cells where djust PANICKED before: {len(panic_b)}")
    print(f"cells where djust PANICKED after : {len(panic_a)}")
    print(f"  closed                         : {len(panic_b - panic_a)}")
    print(f"  INTRODUCED                     : {len(panic_a - panic_b)}")
    for cid in sorted(panic_a - panic_b):
        print(f"  !! {cid}  {after[cid][1][:160]!r}")

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

    if regressions or hot or unreachable or (panic_a - panic_b):
        return 1
    print(
        "\nOK: no agreeing cell regressed, nothing became more permissive, "
        "and no cell newly panics."
    )
    return 0


def main(argv: list[str]) -> int:
    argv = list(argv)
    require_moved = []
    while "--require-moved" in argv:
        index = argv.index("--require-moved")
        require_moved.append(argv[index + 1])
        del argv[index : index + 2]

    if argv[:1] == ["--manifest"]:
        data = manifest()
        if "--json" in argv:
            print(json.dumps(data))
            return 0
        return 1 if report_manifest(data) else 0
    if argv[:1] == ["--compare"]:
        if len(argv) != 3:
            print(__doc__)
            return 2
        return compare(argv[1], argv[2], tuple(require_moved))
    if len(argv) != 1:
        print(__doc__)
        return 2
    measure(argv[0])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
