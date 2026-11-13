"""A differential generator for the safe-markup guard's CALL axis (#3845, #3864).

``scripts/lib/safe_markup_taint.py`` judges a call of a function the file defines by what that
function returns for THIS call's arguments. Hand-written rows only show what their author thought of,
so this builds small modules from a grammar of

    (where the value comes from) x (the helper it goes through) x (where the result is placed)

RUNS each one with hostile payloads and compares the guard's verdict with what came out:

* **unsafe** (ground truth): the output, parsed as HTML, has an element ``x`` or an ``onmouseover``
  attribute that the developer did not write; a URL attribute that starts ``javascript:``; or code a
  browser runs (an event handler, a ``<script>`` body) or a ``<style>`` body where the payload's
  ``INJ`` got out of the string or the block the developer wrote it in. HTML escaping does not stop
  the last three: the browser decodes an attribute before it runs it;
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

PAYLOADS = (
    "<x>",
    "a onmouseover=1",
    '"><x onmouseover=1>',
    "' onmouseover=1 x='",
    "javascript:INJ()",
    "x';INJ();'",
    'x";INJ();"',
    "1;INJ()",
    "x}INJ{y",
    '" onmouseover=1 x="',
    "');INJ();//",
    "</script><x>",
    "</textarea><x>",
    "--><x>",
)

HEADER = (
    "from django.utils.html import conditional_escape, escape\n"
    "from django.utils.safestring import mark_safe\n"
    "from djust.components.utils import attr_text, safe_url\n"
)

#: where the value comes from (``x`` is the raw parameter)
SOURCES = {
    "raw": "x",
    "escape": "escape(x)",
    "url": "safe_url(x)",
    "attr_text": "attr_text(x)",
    "cond_escape": "conditional_escape(x)",
    "digits": "str(len(x))",
    "literal": "'k'",
}

#: name -> (definitions at module level, the expression applied to ``V``[, a statement ``f`` runs
#: first]). ``{w}`` / ``{W}`` / ``{G}`` name the helper, its class and its global, so two helpers can be
#: composed. Each is a FUNCTION of this file the guard follows.
HELPERS: dict[str, tuple] = {
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
    # where escaping for HTML is not enough
    "event": ("def {w}(u):\n    return f'<b onclick=\"go(\\'{{u}}\\')\">b</b>'\n", "{w}(V)"),
    "script": ("def {w}(u):\n    return f\"<script>var a='{{u}}';</script>\"\n", "{w}(V)"),
    "script_unquoted": ("def {w}(u):\n    return f'<script>var a={{u}};</script>'\n", "{w}(V)"),
    "style": ("def {w}(u):\n    return f'<style>.c{{{{color:{{u}}}}}}</style>'\n", "{w}(V)"),
    # a helper that reads data that is not a parameter, next to a literal argument: what it returns
    # is clean, and not free to sit anywhere
    "self_attr": (
        "class {W}:\n"
        "    def __init__(self, q):\n"
        "        self.q = q\n"
        "    def w(self, k):\n"
        '        return f\'<i title="{{escape(self.q)}}" data-k="{{k}}">\'\n',
        "{W}(x).w(V)",
    ),
    "global_quoted": (
        '{G} = {{}}\ndef {w}(k):\n    return f\'<i title="{{escape({G}["q"])}}" data-k="{{k}}">\'\n',
        "{w}(V)",
        "{G}['q'] = x",
    ),
    # a helper that RETURNS an escaper's output, or whole attributes built from one
    "returns_url": ("def {w}(u):\n    return safe_url(u)\n", "{w}(V)"),
    "returns_attr_text": ("def {w}(u):\n    return attr_text(u)\n", "{w}(V)"),
    "returns_escape": ("def {w}(u):\n    return escape(u)\n", "{w}(V)"),
    "whole_attr": ("def {w}(u):\n    return f' aria-label=\"{{escape(u)}}\"'\n", "{w}(V)"),
    "whole_attr_href": ("def {w}(u):\n    return f' href=\"{{u}}\"'\n", "{w}(V)"),
    "attr_name": (
        "def {w}(u, n='title'):\n    return f'<i {{n}}=\"{{escape(u)}}\">'\n",
        "{w}(V, V)",
    ),
    "global_text": (
        "{G} = {{}}\ndef {w}(k):\n    return f'{{k}}-{{escape({G}[\"q\"])}}'\n",
        "{w}(V)",
        "{G}['q'] = x",
    ),
}

#: the sink, with ``E`` the value expression
PLACEMENTS = {
    "bare": "mark_safe(E)",
    "text": "mark_safe(f'<div>{E}</div>')",
    "quoted": "mark_safe(f'<div title=\"{E}\">d</div>')",
    "single_quoted": "mark_safe(f\"<div title='{E}'>d</div>\")",
    "unquoted": "mark_safe(f'<div title={E}>d</div>')",
    "between": "mark_safe(f'<div{E}>d</div>')",
    "attr_name": "mark_safe(f'<div {E}=\"1\">d</div>')",
    "tag_name": "mark_safe(f'<{E}>d</div>')",
    "url": "mark_safe(f'<a href=\"{E}\">l</a>')",
    "event": "mark_safe(f'<b onclick=\"go(\\'{E}\\')\">b</b>')",
    "style_attr": "mark_safe(f'<b style=\"color:{E}\">b</b>')",
    "script": "mark_safe(f\"<script>var a='{E}';</script>\")",
    "script_unquoted": "mark_safe(f'<script>var a={E};</script>')",
    "style": "mark_safe(f'<style>.c{{color:{E}}}</style>')",
    "srcdoc": "mark_safe(f'<iframe srcdoc=\"{E}\"></iframe>')",
    "textarea": "mark_safe(f'<textarea>{E}</textarea>')",
    "comment": "mark_safe(f'<!-- {E} -->')",
}
#: one placement per URL attribute name the guard knows about (``URL_ATTRIBUTES`` below)
#: Attributes a browser reads as a URL
URL_ATTRIBUTES = (
    "href",
    "src",
    "action",
    "formaction",
    "srcset",
    "poster",
    "background",
    "cite",
    "data",
    "ping",
    "xlink:href",
)
for _name in URL_ATTRIBUTES[1:]:
    PLACEMENTS["url_" + _name.replace(":", "_")] = "mark_safe(f'<a " + _name + '="{E}">l</a>\')'


class _Collect(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.attrs: list[tuple[str, str]] = []
        self.code: list[str] = []  # event handlers and script bodies, as the browser runs them
        self.css: list[str] = []
        self._open = ""

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self._open = tag
        for name, value in attrs:
            self.attrs.append((name, value or ""))
            if name.startswith("on"):
                self.code.append(value or "")
            elif name == "style":
                self.css.append(value or "")
            elif name == "srcdoc":  # a document of its own: parsed as one
                inner = _Collect()
                inner.feed(value or "")
                inner.close()
                self.tags += inner.tags
                self.attrs += inner.attrs
                self.code += inner.code
                self.css += inner.css

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self._open = ""

    def handle_endtag(self, tag):
        self._open = ""

    def handle_data(self, data):
        if self._open == "script":
            self.code.append(data)
        elif self._open == "style":
            self.css.append(data)


def _runs_outside_a_string(code: str) -> bool:
    """``INJ(`` in JavaScript that is not inside a string literal."""
    quote = ""
    i = 0
    while i < len(code):
        ch = code[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "'\"`":
            quote = ch
        elif code.startswith("INJ(", i):
            return True
        i += 1
    return False


def injected(html: str) -> bool:
    """The payload became markup or code: an ``x`` element, an ``onmouseover`` attribute, a
    ``javascript:`` URL, ``INJ(`` running outside a string in a handler or a script, or a CSS block
    the payload opened."""
    parser = _Collect()
    parser.feed(html)
    parser.close()
    if "x" in parser.tags or any(name == "onmouseover" for name, _ in parser.attrs):
        return True
    if any(
        (name in URL_ATTRIBUTES or name.endswith("href"))
        and "javascript:" in value.strip().lower().replace(" ", "")[:11]
        for name, value in parser.attrs
    ):
        return True
    return any(_runs_outside_a_string(c) for c in parser.code) or any(
        "INJ{" in c or "INJ(" in c for c in parser.css
    )


def build(source: str, helpers: list[str], placement: str) -> str:
    """One module: helper definitions, then ``f(x)`` with ``placement`` applied to the chain."""
    defs, setup = [], []
    expr = SOURCES[source]
    for i, name in enumerate(helpers):
        code, apply, *first = HELPERS[name]
        names = {"w": f"w{i}", "W": f"C{i}", "G": f"G{i}"}
        defs.append(code.format(**names))
        setup.extend("    " + line.format(**names) + "\n" for line in first)
        expr = apply.format(**names).replace("V", expr)
    sink = PLACEMENTS[placement].replace("E", expr)
    return HEADER + "".join(defs) + "def f(x):\n" + "".join(setup) + f"    return {sink}\n"


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


def cases(
    samples: int, seed: int, exhaustive: bool = True, placement: str | None = None
) -> list[tuple[str, list[str], str]]:
    """Every (source, one helper, placement) unless ``exhaustive`` is off (only ``placement``, when
    given), then ``samples`` random two-helper chains."""
    places = [placement] if placement else list(PLACEMENTS)
    out = [(s, [h], p) for s in SOURCES for h in HELPERS for p in places] if exhaustive else []
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


def survey(
    samples: int = 300, seed: int = 3864, exhaustive: bool = True, placement: str | None = None
) -> dict:
    result: dict = {
        "cases": 0,
        "unsafe": 0,
        "bare": 0,
        "flagged": 0,
        "false_negatives": [],
        "false_positives": [],
    }
    for source, helpers, placement in cases(samples, seed, exhaustive, placement):
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


def matrix() -> list[tuple[str, list[str], str]]:
    """The FULL cross product of one helper: every source x every helper x every placement."""
    return [(s, [h], p) for s in SOURCES for h in HELPERS for p in PLACEMENTS]


def misses(guard, cases: list[tuple[str, list[str], str]]) -> dict:
    """``{"misses": [...], "unsafe": n, "cleared": n}``: the cases that let a payload through when
    RUN (not as a bare passthrough) and that ``guard`` (a ``find_unsafe_markup``) clears."""
    out: dict = {"misses": [], "unsafe": 0, "cleared": 0}
    for source, helpers, placement in cases:
        module = build(source, helpers, placement)
        flagged = bool(guard(module))
        unsafe, bare = run(module)
        out["unsafe"] += unsafe and not bare
        out["cleared"] += not flagged
        if unsafe and not bare and not flagged:
            out["misses"].append([source, list(helpers), placement])
    return out


if __name__ == "__main__":  # pragma: no cover - regenerates the baseline from another guard
    # python python/tests/safe_markup_call_fuzz_3864.py <main's safe_markup_taint.py> <out.json>
    import importlib.util
    import json
    import sys

    import django
    from django.conf import settings

    settings.configure()
    django.setup()
    spec = importlib.util.spec_from_file_location("baseline_taint", sys.argv[1])
    guard = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["baseline_taint"] = guard
    spec.loader.exec_module(guard)  # type: ignore[union-attr]
    result = misses(guard.find_unsafe_markup, matrix())
    with open(sys.argv[2], "w") as fh:
        json.dump(
            {
                "_comment": (
                    "Cases of safe_markup_call_fuzz_3864.matrix() that are unsafe when RUN and that "
                    "the guard of main at the commit below clears. test_safe_markup_guard_3864 "
                    "asserts the guard clears none outside this list (monotone against main)."
                ),
                "main_commit": sys.argv[3] if len(sys.argv) > 3 else "",
                "misses": sorted(result["misses"]),
            },
            fh,
            indent=1,
        )
        fh.write("\n")
    print(len(result["misses"]), "misses,", result["unsafe"], "unsafe of", len(matrix()))
