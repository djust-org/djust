"""A differential generator for the safe-markup guard's CALL axis (#3845, #3864).

``scripts/lib/safe_markup_taint.py`` judges a call of a function the file defines by what that
function returns for THIS call's arguments. Hand-written rows only show what their author thought of,
so this builds small modules from a grammar of

    (where the value comes from) x (the helper it goes through) x (where the result is placed)

RUNS each one with hostile payloads and compares the guard's verdict with what came out:

* **unsafe** (ground truth): the output, parsed as HTML, has an element ``x`` or an ``onmouseover``
  attribute that the developer did not write: the payload became markup;
* **bare passthrough**: unsafe, but the sink was handed the payload itself with no markup built around
  it (``mark_safe(ident(x))``). The guard's first documented limit trusts a bare parameter as markup
  passed in, so these are counted apart, not as misses;
* a **false negative** is any other unsafe case the guard does not flag; a **false positive** is a case
  the guard flags whose output is never unsafe for any payload.

It is deterministic: the full product is enumerated, then ``random.Random(seed)`` composes two
helpers for a bounded sample.
"""

from __future__ import annotations

import random
from html.parser import HTMLParser

from scripts.lib.safe_markup_taint import find_unsafe_markup

PAYLOADS = ("<x>", "a onmouseover=1", '"><x onmouseover=1>', "' onmouseover=1 x='")

HEADER = (
    "from django.utils.html import conditional_escape, escape\n"
    "from django.utils.safestring import mark_safe\n"
    "from djust.components.utils import safe_url\n"
)

#: where the value comes from (``x`` is the raw parameter)
SOURCES = {
    "raw": "x",
    "escape": "escape(x)",
    "url": "safe_url(x)",
    "digits": "str(len(x))",
    "literal": "'k'",
}

#: name -> (definitions at module level, the expression applied to ``V``). ``{w}`` is the helper's
#: name, so two helpers can be composed. Each is a FUNCTION of this file the guard follows.
HELPERS = {
    "none": ("", "V"),
    "ident": ("def {w}(u):\n    return u\n", "{w}(V)"),
    "lambda_ident": ("{w} = lambda u: u\n", "{w}(V)"),
    "text": ("def {w}(u):\n    return f'<p>{{u}}</p>'\n", "{w}(V)"),
    "attr_quoted": ("def {w}(u):\n    return f'<a href=\"{{u}}\">l</a>'\n", "{w}(V)"),
    "attr_unquoted": ("def {w}(u):\n    return f'<a href={{u}}>l</a>'\n", "{w}(V)"),
    "lambda_unquoted": ("{w} = lambda u: f'<a href={{u}}>l</a>'\n", "{w}(V)"),
    "plus_unquoted": ("def {w}(u):\n    return '<a href=' + u + '>l</a>'\n", "{w}(V)"),
    "format_quoted": ("def {w}(u):\n    return '<a title=\"{{}}\">l</a>'.format(u)\n", "{w}(V)"),
    "percent_unquoted": ("def {w}(u):\n    return '<a title=%s>l</a>' % u\n", "{w}(V)"),
    "escapes_itself": ("def {w}(u):\n    return f'<p>{{escape(u)}}</p>'\n", "{w}(V)"),
    "kw_default_none": (
        "def {w}(u, wrap=None):\n"
        "    if wrap is not None:\n"
        "        u = wrap(u)\n"
        "    return f'<p>{{u}}</p>'\n",
        "{w}(V)",
    ),
    "kw_default_none_given": (
        "def {w}(u, wrap=None):\n"
        "    if wrap is not None:\n"
        "        u = wrap(u)\n"
        "    return f'<p>{{u}}</p>'\n",
        "{w}(V, wrap=str.upper)",
    ),
    "flag_raw_branch": (
        "def {w}(u, trusted=False):\n"
        "    if trusted:\n"
        "        return f'<p>{{u}}</p>'\n"
        "    return f'<p>{{escape(u)}}</p>'\n",
        "{w}(V)",
    ),
    "flag_raw_branch_on": (
        "def {w}(u, trusted=False):\n"
        "    if trusted:\n"
        "        return f'<p>{{u}}</p>'\n"
        "    return f'<p>{{escape(u)}}</p>'\n",
        "{w}(V, True)",
    ),
    "second_param": ("def {w}(a, u):\n    return f'<i>{{a}}</i><p>{{u}}</p>'\n", "{w}('k', V)"),
    "method_unquoted": (
        "class {W}:\n"
        "    def tag(self, u):\n"
        "        return f'<a href={{u}}>l</a>'\n"
        "    def run(self, u):\n"
        "        return self.tag(u)\n",
        "{W}().run(V)",
    ),
}

#: the sink, with ``E`` the value expression
PLACEMENTS = {
    "bare": "mark_safe(E)",
    "text": "mark_safe(f'<div>{E}</div>')",
    "quoted": "mark_safe(f'<div title=\"{E}\">d</div>')",
    "unquoted": "mark_safe(f'<div title={E}>d</div>')",
}


class _Collect(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.attrs: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs.extend(name for name, _ in attrs)

    handle_startendtag = handle_starttag


def injected(html: str) -> bool:
    """The payload became markup: an ``x`` element or an ``onmouseover`` attribute."""
    parser = _Collect()
    parser.feed(html)
    parser.close()
    return "x" in parser.tags or "onmouseover" in parser.attrs


def build(source: str, helpers: list[str], placement: str) -> str:
    """One module: helper definitions, then ``f(x)`` with ``placement`` applied to the chain."""
    defs = []
    expr = SOURCES[source]
    for i, name in enumerate(helpers):
        code, apply = HELPERS[name]
        w, cls = f"w{i}", f"C{i}"
        defs.append(code.format(w=w, W=cls))
        expr = apply.format(w=w, W=cls).replace("V", expr)
    sink = PLACEMENTS[placement].replace("E", expr)
    return HEADER + "".join(defs) + f"def f(x):\n    return {sink}\n"


def run(module: str) -> tuple[bool, bool]:
    """``(unsafe, bare passthrough)`` over every payload."""
    unsafe = bare = False
    for payload in PAYLOADS:
        namespace: dict = {"__name__": "call_fuzz_case"}
        exec(compile(module, "<call-fuzz>", "exec"), namespace)  # noqa: S102 - generated, trusted
        out = str(namespace["f"](payload))
        if injected(out):
            unsafe = True
            bare = bare or out == payload
    return unsafe, bare


def cases(samples: int, seed: int, exhaustive: bool = True) -> list[tuple[str, list[str], str]]:
    """Every (source, one helper, placement) unless ``exhaustive`` is off, then ``samples`` random
    two-helper chains."""
    out = [(s, [h], p) for s in SOURCES for h in HELPERS for p in PLACEMENTS] if exhaustive else []
    rng = random.Random(seed)
    chainable = sorted(HELPERS)
    for _ in range(samples):
        out.append(
            (
                rng.choice(sorted(SOURCES)),
                [rng.choice(chainable), rng.choice(chainable)],
                rng.choice(sorted(PLACEMENTS)),
            )
        )
    return out


def survey(samples: int = 300, seed: int = 3864, exhaustive: bool = True) -> dict:
    result: dict = {
        "cases": 0,
        "unsafe": 0,
        "bare": 0,
        "flagged": 0,
        "false_negatives": [],
        "false_positives": [],
    }
    for source, helpers, placement in cases(samples, seed, exhaustive):
        module = build(source, helpers, placement)
        flagged = bool(find_unsafe_markup(module))
        unsafe, bare = run(module)
        result["cases"] += 1
        result["unsafe"] += unsafe
        result["bare"] += bare
        result["flagged"] += flagged
        label = (source, tuple(helpers), placement)
        if unsafe and not flagged and not bare:
            result["false_negatives"].append(label)
        if flagged and not unsafe:
            result["false_positives"].append(label)
    return result
