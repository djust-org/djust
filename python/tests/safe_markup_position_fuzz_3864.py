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

import json as _json
import os as _os
import random
import shutil as _shutil
import subprocess as _subprocess
from pathlib import Path as _Path

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
    # what the browser's tokenizer reads otherwise than a simple scan would (#3864 review)
    "<!--><a title=",
    "<!---><a title=",
    "<textarea><a title=",
    "<title><a title=",
    '<xmp><b title="</xmp><a title=',
    "<script><!--<script></script><a title=",
    "<script>1</scripts><a title=",
    '<svg><![CDATA[<a title="]]><a title=',
    "<a title=a'b ",
    '<a href="&#10',
    '<a href="&#9;',
    '<a href="&#106',
    '<iframe src="data:text/html,',
)

#: the part placed after it
VALUES = {
    "url": "safe_url(x)",
    "escape": "escape(x)",
    "attr_text": "attr_text(x)",
    "cond_escape": "conditional_escape(x)",
    "digits": "str(len(x))",
    "literal": "'k'",
    # an escaper's output, then something done to it
    "escape_spec": "f'{escape(x):>1}'",
    "url_spec_s": "f'{safe_url(x)!s:>2}'",
    "escape_format_spec": "'{:>3}'.format(escape(x))",
    "escape_undone": "escape(x).replace('&quot;', '\"')",
    "escape_upper": "escape(x).upper()",
    "escape_repr": "repr(escape(x))",
    "digits_cut": "str(len(x))[5:]",
    "char_quote": "'%c' % 34 + escape(x)",
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


#: payloads beyond the call fuzz's, for the tokenizer states (a scheme split by white space, a
#: character reference to complete, the end tag of a raw-text element)
PAYLOADS = (
    *fuzz.PAYLOADS,
    " \tjava\nscript:INJ()",
    "x onmouseover=1",
    "a/onmouseover=1",
    "`onmouseover=1",
    "</title><x>",
    "</xmp><x>",
    "]]><x>",
    "6;avascript:INJ()",
    ";avascript:INJ()",
    "text/html,<x>",
)


#: a payload that lets nothing out: what the developer's own literal text does (a ``data:`` URL
#: they wrote) is subtracted from what a payload does
BENIGN = "k"


def outputs(producer, spellings, value, suffix, how, payloads=PAYLOADS) -> list[str]:
    """What the module returns for every payload under every spelling (an exception is no
    output)."""
    import sys
    import types

    out: list[str] = []
    for k in range(len(spellings)):
        source = build(producer, spellings, value, suffix, how, k)
        for payload in payloads:
            module = types.ModuleType("position_fuzz_case")
            sys.modules["position_fuzz_case"] = module
            try:
                exec(compile(source, "<position-fuzz>", "exec"), module.__dict__)  # noqa: S102
                out.append(str(module.f(payload)))
            except (TypeError, ValueError):
                pass  # the spelling raises: nothing is rendered
            finally:
                sys.modules.pop("position_fuzz_case", None)
    return out


def run_any(producer, spellings, value, suffix, how) -> bool:
    """Whether any spelling lets a payload out when run (Python's parser)."""
    return any(fuzz.injected(o) for o in outputs(producer, spellings, value, suffix, how))


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


def survey(samples: int, seed: int, browser: bool = False) -> dict:
    """``{"cases", "unsafe", "flagged", "false_negatives", "false_positives", "disagree"}``.
    ``browser``: a case is also unsafe when a browser's parser lets a payload out (jsdom)."""
    result: dict = {
        "cases": 0,
        "unsafe": 0,
        "flagged": 0,
        "false_negatives": [],
        "false_positives": [],
        "disagree": [],
    }
    all_cases = cases(samples, seed)
    outs = [outputs(*case) for case in all_cases]
    benign = [outputs(*case, payloads=(BENIGN,)) for case in all_cases]
    in_browser: list[bool] = [False] * len(all_cases)
    if browser:
        flat = [o for case_outs in outs for o in case_outs]
        flat_benign = [o for case_outs in benign for o in case_outs]
        verdicts_flat = browser_verdicts(flat + flat_benign)
        payload_verdicts, benign_verdicts = verdicts_flat[: len(flat)], verdicts_flat[len(flat) :]
        i = j = 0
        for n, case_outs in enumerate(outs):
            # by kind (``url src``, ``attr onmouseover``): a reason names the value it saw, which
            # differs per payload even where the payload changes nothing
            own = {r.split("=")[0] for v in benign_verdicts[j : j + len(benign[n])] for r in v}
            j += len(benign[n])
            in_browser[n] = any(
                {r.split("=")[0] for r in v} - own for v in payload_verdicts[i : i + len(case_outs)]
            )
            i += len(case_outs)
    for n, case in enumerate(all_cases):
        verdicts = {bool(find_unsafe_markup(build(*case, k))) for k in range(len(case[1]))}
        if len(verdicts) != 1:
            result["disagree"].append(case)  # the guard read K
        flagged = True in verdicts
        unsafe = in_browser[n] or (
            any(fuzz.injected(o) for o in outs[n]) and not any(fuzz.injected(o) for o in benign[n])
        )
        result["cases"] += 1
        result["unsafe"] += unsafe
        result["flagged"] += flagged
        if unsafe and not flagged:
            result["false_negatives"].append(case)
        if flagged and not unsafe:
            result["false_positives"].append(case)
    return result


# ---------------------------------------------------------------------------------------------
# A browser's parser as the oracle (#3864): Python's ``HTMLParser`` does not follow the WHATWG
# tokenizer (script data escape states, abrupt comments, RCDATA, character references), so a
# generated module is also judged by jsdom (parse5), through node, when the toolchain has it.
# ---------------------------------------------------------------------------------------------

_ORACLE = _Path(__file__).with_name("safe_markup_browser_oracle_3864.cjs")
_ROOT = _Path(__file__).resolve().parents[2]


def browser_available() -> bool:
    return bool(_shutil.which("node")) and (_ROOT / "node_modules" / "jsdom").is_dir()


#: Set in the CI job that installs jsdom (#4212): there a missing browser oracle is a failure.
REQUIRE_BROWSER_ENV = "DJUST_REQUIRE_BROWSER_ORACLE"


def require_browser() -> None:
    """Return when the browser oracle can run; otherwise skip the test, or fail it where
    :data:`REQUIRE_BROWSER_ENV` is ``1`` (a skip there would hide that the rows never ran)."""
    import os

    import pytest

    if browser_available():
        return
    message = "node and jsdom are not installed here (npm ci)"
    if os.environ.get(REQUIRE_BROWSER_ENV) == "1":
        pytest.fail(message + f"; {REQUIRE_BROWSER_ENV}=1 requires them")
    pytest.skip(message)


def browser_verdicts(
    htmls: list[str], deadline: float = 120, heap_mb: int = 1024
) -> list[list[str]]:
    """For each HTML string, how a browser's parser lets a payload out (``[]``: it does not).
    Bounded: node's heap is capped at ``heap_mb`` and the run at ``deadline`` seconds; a run that
    hits either raises, it is never read as "safe"."""
    out: list[list[str]] = []
    for i in range(0, len(htmls), 1500):
        chunk = htmls[i : i + 1500]
        done = _subprocess.run(
            ["node", f"--max-old-space-size={heap_mb}", str(_ORACLE)],
            input=_json.dumps(chunk),
            capture_output=True,
            text=True,
            timeout=deadline,
            cwd=_ROOT,
            env={**_os.environ, "NODE_PATH": str(_ROOT / "node_modules")},
            check=False,
        )
        if done.returncode:
            raise RuntimeError(f"the browser oracle failed: {done.stderr[-500:]}")
        result = _json.loads(done.stdout)
        if len(result) != len(chunk):
            raise RuntimeError("the browser oracle answered for a different number of strings")
        out += result
    return out
