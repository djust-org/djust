"""``{% ifchanged %}`` — Django's ``IfChangedNode``, natively (#2517).

Before this row the tag was a ``Node::UnsupportedTag``: every template
carrying one failed to PARSE, which is 25 cells of Django's own
``template_tests`` and a hard error for any project that used it.

The two forms are not variations on one behaviour — they compare different
things, and the difference is observable:

* ``{% ifchanged %}`` compares the RENDERED body, so the body must be
  rendered before the tag can decide. Django then reuses that string rather
  than rendering a second time (``return nodelist_true_output or …``); a body
  with a side effect — a ``{% cycle %}``, a custom tag — would otherwise fire
  twice per iteration. ``test_no_double_render_of_the_body`` pins that.
* ``{% ifchanged a b %}`` compares the RESOLVED operands, with
  ``ignore_failures=True``, so a missing operand compares as ``None`` — NOT
  as ``string_if_invalid``, and never an error.

The state is scoped to the innermost enclosing loop EXECUTION, which is what
makes an inner tag reset each time an outer loop re-enters it while
iterations of the same loop share it. Django gets that from binding one
``forloop`` dict per ``ForNode.render``; djust mints a ``loop_scope`` there
instead. ``test_inner_loop_state_resets_per_outer_iteration`` is the case
that separates the two readings — it is the ONLY test here that fails if the
scope is made per-render rather than per-loop-execution.

Every assertion is a differential against Django itself rather than a
hand-written expectation, so the oracle is the reference implementation (the
curated table below is paired with a randomized sweep for the reason the
milestone canon gives: a table samples one axis and blinds you on the next).
"""

from __future__ import annotations

import random
from typing import Any

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402


def _assert_agrees(source: str, ctx: dict[str, Any]) -> None:
    django_out = DjangoTemplate(source).render(DjangoContext(dict(ctx)))
    djust_out = _rust.render_template(source, dict(ctx))
    assert djust_out == django_out, (
        f"source={source!r} ctx={ctx!r}\n  django={django_out!r}\n  djust ={djust_out!r}"
    )


#: The shapes Django's own ``test_if_changed.py`` exercises, plus the
#: no-loop and missing-operand rows it does not.
_CASES: list[tuple[str, dict[str, Any]]] = [
    # -- compare-the-output form -------------------------------------------
    (
        "{% for x in v %}{% ifchanged %}{{ x }}{% endifchanged %}{% endfor %}",
        {"v": [1, 1, 2, 2, 3, 3]},
    ),
    (
        "{% for x in v %}{% ifchanged %}{{ x }}{% endifchanged %}{% endfor %}",
        {"v": [1, 2, 1, 2, 1]},
    ),
    ("{% for x in v %}{% ifchanged %}{{ x }}{% endifchanged %}{% endfor %}", {"v": []}),
    # -- operand form -------------------------------------------------------
    ("{% for x in v %}{% ifchanged x %}{{ x }}{% endifchanged %}{% endfor %}", {"v": [1, 1, 2]}),
    (
        "{% for x in v %}{% ifchanged x.a %}{{ x.a }}{% endifchanged %}{% endfor %}",
        {"v": [{"a": 1}, {"a": 1}, {"a": 2}]},
    ),
    # two operands behave as ONE tuple, not as an OR of two independent tags
    (
        "{% for a, b in v %}{% ifchanged a b %}{{ a }}{{ b }}{% endifchanged %}{% endfor %}",
        {"v": [[1, 1], [1, 2], [1, 2], [2, 2]]},
    ),
    # -- else arm -----------------------------------------------------------
    (
        "{% for x in v %}{% ifchanged x %}{{ x }}{% else %}.{% endifchanged %}{% endfor %}",
        {"v": [1, 1, 2, 2, 3]},
    ),
    # -- a MISSING operand is None, not string_if_invalid, and not an error --
    (
        "{% for x in v %}{% ifchanged nope %}{{ x }}{% else %}.{% endifchanged %}{% endfor %}",
        {"v": [1, 2, 3]},
    ),
    # -- outside any loop: effectively a no-op guard, but must not error -----
    ("{% ifchanged %}a{% endifchanged %}{% ifchanged %}a{% endifchanged %}", {}),
    ("{% ifchanged p %}{{ p }}{% endifchanged %}", {"p": 7}),
    # -- types must not collapse: 1 and "1" are different values ------------
    ("{% for x in v %}{% ifchanged x %}{{ x }}{% endifchanged %}{% endfor %}", {"v": [1, "1", 1]}),
    # -- nested loops: the inner tag resets per outer iteration -------------
    (
        "{% for row in rows %}{% for x in row %}"
        "{% ifchanged x %}{{ x }}{% endifchanged %}"
        "{% endfor %}|{% endfor %}",
        {"rows": [[1, 1, 2], [2, 2, 3], [1, 1, 1]]},
    ),
    # -- two sibling tags in one body keep independent state ----------------
    (
        "{% for x in v %}{% ifchanged x %}A{% endifchanged %}"
        "{% ifchanged forloop.counter %}B{% endifchanged %}{% endfor %}",
        {"v": [1, 1, 2]},
    ),
]


@pytest.mark.parametrize("source,ctx", _CASES, ids=range(len(_CASES)))
def test_matches_django(source: str, ctx: dict[str, Any]) -> None:
    _assert_agrees(source, ctx)


def test_inner_loop_state_resets_per_outer_iteration() -> None:
    """The case that distinguishes per-loop-EXECUTION state from per-render.

    With a per-render frame the second row's leading ``1`` would be
    suppressed by the first row's trailing ``1``; Django emits it because the
    inner loop gets a fresh ``forloop`` dict each time the outer loop enters
    it. Gate-off: make ``begin_loop_scope`` a no-op and only this test and
    the nested-loop table row fail.
    """
    source = (
        "{% for row in rows %}{% for x in row %}"
        "{% ifchanged x %}{{ x }}{% endifchanged %}"
        "{% endfor %};{% endfor %}"
    )
    # The rows must be chosen so the second row STARTS with the value the
    # first row ENDED with — otherwise a per-render frame produces the same
    # bytes and the test cannot tell the two readings apart. (The first
    # version of this test used [[1, 2], [1, 2]], which does not
    # discriminate: it passed with the frame gated off.)
    ctx = {"rows": [[1, 2], [2, 1]]}
    _assert_agrees(source, ctx)
    # Pin the actual bytes too, so the test states what it is defending:
    # per-render state would suppress the second row's leading 2 and give
    # "12;1;".
    assert _rust.render_template(source, dict(ctx)) == "12;21;"


def test_no_double_render_of_the_body() -> None:
    """The no-operand form renders its body ONCE per iteration.

    ``{% cycle %}`` is the observable side effect: a second render would
    advance it twice and the outputs would diverge from Django's.
    """
    source = "{% for x in v %}{% ifchanged %}{% cycle 'a' 'b' 'c' %}{% endifchanged %}{% endfor %}"
    _assert_agrees(source, {"v": [1, 2, 3]})


@pytest.mark.parametrize("seed", range(60))
def test_randomized_differential(seed: int) -> None:
    """Random value sequences over every form, against Django.

    The curated table above fixes the shapes; this fixes the VALUES, which is
    where an equality rule (``1`` vs ``"1"`` vs ``None``) goes wrong without
    any shape looking unusual.
    """
    rng = random.Random(seed)
    pool: list[Any] = [0, 1, 2, "1", "a", "", None, True, False]
    values = [rng.choice(pool) for _ in range(rng.randint(0, 8))]
    body, tag = rng.choice(
        [
            ("{{ x }}", "{% ifchanged %}"),
            ("{{ x }}", "{% ifchanged x %}"),
            ("-", "{% ifchanged x %}"),
        ]
    )
    els = rng.choice(["", "{% else %}.{% endifchanged %}"])
    close = "{% endifchanged %}" if not els else ""
    source = f"{{% for x in v %}}{tag}{body}{els}{close}{{% endfor %}}"
    _assert_agrees(source, {"v": values})
