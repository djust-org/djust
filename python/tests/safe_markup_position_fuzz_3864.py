"""A randomized differential for where the safe-markup guard places a part (#3864).

``_Module._misplaces`` reads the text in front of each part of a string a sink is handed: literal
text it can spell out (every spelling of a name, a branch, a constant table, a function of the
file) and, for a part it cannot, a symbol for what that part can do to the position. A part
behind one it cannot read sits at an unknown position and is reported (``_walk``).

This builds small modules from a grammar of

    (how the text in front is produced) x (its possible spellings) x (the part after it) x (how
    the string is built)

where the text in front is one of several HTML prefixes chosen by a module constant ``K`` the
guard does not evaluate. Each module is RUN once per value of ``K`` with the call fuzz's hostile
payloads and the output parsed (``safe_markup_call_fuzz_3864.injected``): a module is unsafe when
ANY spelling lets a payload out, and that is the verdict the guard must reach (it cannot know
``K``). Deterministic for a seed; the sample size bounds the time.
"""

from __future__ import annotations

import random

import safe_markup_call_fuzz_3864 as fuzz

from scripts.lib.safe_markup_taint import find_unsafe_markup

HEADER = fuzz.HEADER + "from django.utils.html import format_html\n"

#: HTML the text in front of the part can end with
PREFIXES = (
    "<p>",
    '<a title="',
    "<a title='",
    '<a href="',
    "<a href=",
    "<a ",
    '<a class="c" ',
    "<b onclick=\"go('",
    "<script>var a=",
    "<!-- ",
    '<a class="c">',
    "",
    '<iframe srcdoc="',
    '<a data-href="',
    "<",
    '<a href="/u/',
)

#: the part placed after it
VALUES = {
    "url": "safe_url(x)",
    "escape": "escape(x)",
    "attr_text": "attr_text(x)",
    "cond_escape": "conditional_escape(x)",
    "digits": "str(len(x))",
    "literal": "'k'",
}

#: what follows the part
SUFFIXES = ('">l</a>', ">l</a>", "')\">b</b>", ";</script>", " -->", "</p>")

#: name -> (module-level definitions, the expression of the text in front, ``f``'s first lines).
#: ``{A}`` is the tuple of spellings, ``{A0}`` the first. Each reads ``K``, which the guard does not
#: evaluate, so it must hold every spelling possible.
PRODUCERS: dict[str, tuple[str, str, str]] = {
    "helper_branches": (
        "def pre():\n    for i, a in enumerate({A}):\n        if i == K:\n            return a\n"
        "    return {A0}\n",
        "pre()",
        "",
    ),
    "helper_returns": ("", "pre()", ""),  # filled per spelling count in ``build``
    "conditional": ("", "p", "    p = {A}[0] if K == 0 else {A}[K]\n"),
    "table": ("T = {A}\n", "T[K]", ""),
    "dict_get": ("D = dict(enumerate({A}))\n", "D.get(K, {A0})", ""),
    "class_attribute": (
        "class C:\n    P = {A}\n    def pre(self):\n        return self.P[K]\n",
        "C().pre()",
        "",
    ),
    "loop": (
        "",
        "p",
        "    p = {A0}\n    for i, a in enumerate({A}):\n        if i == K:\n            p = a\n",
    ),
    "strip": ("T = {A}\n", "(T[K] + '  ').strip()", ""),
    "format": ("T = {A}\n", "'{{}}'.format(T[K])", ""),
    "percent": ("T = {A}\n", "'%s' % (T[K],)", ""),
    "fstring": ("T = {A}\n", "f'{{T[K]}}'", ""),
    "format_html_literal": ("", "format_html('{{}}' {A0}, '')", ""),
    "monkeypatched": (
        "import sys\ndef pre():\n    return '<p>'\nsys.modules[__name__].pre = lambda: {A}[K]\n",
        "pre()",
        "",
    ),
}

#: how the string the sink is handed is built
BUILDS = {
    "plus": "{P} + {V} + {S!r}",
    "fstring": "f'{{{P}}}{{{V}}}' + {S!r}",
    "format": "'{{}}{{}}{{}}'.format({P}, {V}, {S!r})",
    "percent": "'%s%s%s' % ({P}, {V}, {S!r})",
}


def build(
    producer: str, spellings: tuple[str, ...], value: str, suffix: str, how: str, k: int
) -> str:
    """One module; ``k`` is the value of ``K`` it runs with."""
    defs, expr, first = PRODUCERS[producer]
    a = repr(tuple(spellings))
    if producer == "helper_returns":
        lines = ["def pre():"]
        for i, s in enumerate(spellings[:-1]):
            lines.append(f"    if K == {i}:\n        return {s!r}")
        lines.append(f"    return {spellings[-1]!r}")
        defs = "\n".join(lines) + "\n"
    fill = {"A": a, "A0": repr(spellings[0])}
    sink = BUILDS[how].format(P=expr.format(**fill), V=VALUES[value], S=suffix)
    return (
        HEADER
        + f"K = {k}\n"
        + defs.format(**fill)
        + "def f(x):\n"
        + first.format(**fill)
        + f"    return mark_safe({sink})\n"
    )


def run_any(producer, spellings, value, suffix, how) -> bool:
    """Whether any spelling lets a payload out when run."""
    import sys
    import types

    for k in range(len(spellings)):
        source = build(producer, spellings, value, suffix, how, k)
        for payload in fuzz.PAYLOADS:
            module = types.ModuleType("position_fuzz_case")
            sys.modules["position_fuzz_case"] = module
            try:
                exec(compile(source, "<position-fuzz>", "exec"), module.__dict__)  # noqa: S102
                out = str(module.f(payload))
            finally:
                sys.modules.pop("position_fuzz_case", None)
            if fuzz.injected(out):
                return True
    return False


def cases(samples: int, seed: int) -> list[tuple]:
    rng = random.Random(seed)
    out = []
    for _ in range(samples):
        count = rng.choice((1, 2, 3))
        spellings = tuple(rng.choice(PREFIXES) for _ in range(count))
        out.append(
            (
                rng.choice(sorted(PRODUCERS)),
                spellings,
                rng.choice(sorted(VALUES)),
                rng.choice(SUFFIXES),
                rng.choice(sorted(BUILDS)),
            )
        )
    return out


def survey(samples: int, seed: int) -> dict:
    """``{"cases", "unsafe", "flagged", "false_negatives", "false_positives", "disagree"}``."""
    result: dict = {
        "cases": 0,
        "unsafe": 0,
        "flagged": 0,
        "false_negatives": [],
        "false_positives": [],
        "disagree": [],
    }
    for case in cases(samples, seed):
        verdicts = {bool(find_unsafe_markup(build(*case, k))) for k in range(len(case[1]))}
        if len(verdicts) != 1:
            result["disagree"].append(case)  # the guard read K
        flagged = True in verdicts
        unsafe = run_any(*case)
        result["cases"] += 1
        result["unsafe"] += unsafe
        result["flagged"] += flagged
        if unsafe and not flagged:
            result["false_negatives"].append(case)
        if flagged and not unsafe:
            result["false_positives"].append(case)
    return result
