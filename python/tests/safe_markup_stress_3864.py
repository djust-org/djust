"""Stress programs for the two passes of the safe-markup guard (#3864).

Written by the third review of the call binding: small modules with recursive and mutually recursive
helpers, a helper that only does something for a flag, closures, lambdas, the same method name in two
classes, a sink handed the call (``mark_safe(h(x))``) before or after an unbound use, module-level and
function-scope sinks, helpers defined after use. Deterministic for a seed. The review found 26 of 2,400
that lost a finding when the call judgement shared a cache with the guard as it was; the two passes
(``find_unsafe_markup``) make that impossible, and ``test_safe_markup_guard_3864`` checks it against
the findings of ``main``'s guard (``safe_markup_main_baseline_3864.json``).
"""

from __future__ import annotations

import random

HDR = (
    "from django.utils.safestring import mark_safe\n"
    "from django.utils.html import conditional_escape, escape, format_html\n"
    "from djust.components.utils import safe_url, attr_text\n"
)
ESC = [
    "safe_url(x)",
    "conditional_escape(safe_url(x))",
    "attr_text(x)",
    "safe_url(x) + ''",
    "x",
    "escape(x)",
    "f'{x}'",
]


def helper(rng, name):
    e = rng.choice(ESC)
    kind = rng.choice(
        ["flag", "rec", "mutual", "closure", "plain", "lambda", "method", "flagrec", "nested_if"]
    )
    if kind == "flag":
        return (
            f"def {name}(x, flag=None):\n    s = 'a'\n    if flag is not None:\n        s = {e}\n    return s\n",
            kind,
        )
    if kind == "flagrec":
        return (
            f"def {name}(x, flag=None):\n    s = 'a'\n    if flag is not None:\n        s = {e}\n    if x == 'n':\n        return f'<i>{{{name}(x, flag)}}</i>'\n    return s\n",
            kind,
        )
    if kind == "nested_if":
        return (
            f"def {name}(x, wrap=False):\n    s = 'a'\n    if not wrap:\n        s = {e}\n    if x == 'n':\n        return '<i>' + {name}(x, wrap) + '</i>'\n    return s\n",
            kind,
        )
    if kind == "rec":
        return (
            f"def {name}(x, n=0):\n    if n:\n        return {name}(x, n - 1)\n    return {e}\n",
            kind,
        )
    if kind == "mutual":
        return (
            f"def {name}(x, flag=None):\n    s = 'a'\n    if flag:\n        s = {e}\n    if x == 'n':\n        return {name}_b(x, flag)\n    return s\n"
            f"def {name}_b(x, flag=None):\n    return f'<i>{{{name}(x, flag)}}</i>'\n"
        ), kind
    if kind == "closure":
        return (
            f"def {name}(x, flag=None):\n    s = 'a'\n    if flag is not None:\n        s = {e}\n    def k():\n        return s\n    return k()\n",
            kind,
        )
    if kind == "plain":
        return f"def {name}(x):\n    return {e}\n", kind
    if kind == "lambda":
        return f"{name} = lambda x, flag=None: ({e}) if flag else 'a'\n", kind
    return (
        f"class C_{name}:\n    def go(self, x, flag=None):\n        s = 'a'\n        if flag:\n            s = {e}\n        return s\n"
        f"class D_{name}:\n    def go(self, x, flag=None):\n        return 'b'\n"
        f"def {name}(x, flag=None):\n    return C_{name}().go(x, flag)\n"
    ), kind


PLACE = [
    "<a href={V}>",
    '<a href="{V}">',
    "<p>{V}</p>",
    "<script>var a={V};</script>",
    '<a onclick="{V}">',
    "<a title={V} x>",
]


def site(rng, h, i, bound):
    args = (
        rng.choice(
            ["x", "x, 1", "x, None", "x, flag=1", "x, flag=None", "x, 0", "x, True", "x, False"]
        )
        if not bound
        else rng.choice(["x", "x, None", "x, 0", "x, False", "x, 1"])
    )
    call = f"{h}({args})"
    nm = f"{rng.choice('azm')}_s{i}"
    if bound:
        return f"def {nm}(x):\n    return mark_safe({call})\n"
    pl = rng.choice(PLACE).replace("{V}", "{" + call + "}")
    form = rng.choice(["f", "plus", "pct"])
    if form == "f":
        body = "f'" + pl.replace("'", "\\'") + "'"
    else:
        a, b = pl.split("{" + call + "}")
        body = (
            (repr(a) + " + " + call + " + " + repr(b))
            if form == "plus"
            else (repr(a + "%s" + b) + " % " + call)
        )
    return f"def {nm}(x):\n    return mark_safe({body})\n"


def program(seed):
    rng = random.Random(seed)
    parts = []
    hs = []
    for j in range(rng.choice([1, 1, 2])):
        name = rng.choice(["h", "tag", "render"]) + str(j)
        src, k = helper(rng, name)
        hs.append(name)
        parts.append(src)
    sites = []
    for i in range(rng.choice([2, 3, 4])):
        sites.append(site(rng, rng.choice(hs), i, rng.random() < 0.5))
    if rng.random() < 0.3:
        sites.append(f"X = mark_safe({rng.choice(hs)}(input()))\n")
    if rng.random() < 0.15:
        sites.append(f"def shadow(x):\n    {hs[0]} = str\n    return mark_safe({hs[0]}(x))\n")
    allp = parts + sites
    if rng.random() < 0.4:
        rng.shuffle(allp)  # helpers defined after use
    return HDR + "\n".join(allp)
