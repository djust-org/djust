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
from decimal import Decimal
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


#: Value classes the first randomized sweep never generated, each of which
#: broke the comparison key (PR #2650 review, finding 4). Kept as a CURATED
#: table beside the sweep because a random pool reaches them rarely: a
#: `Decimal` must equal an equal int/float (Python's `numbers.Number` rule), an
#: integer past 2**53 must not collapse onto its `f64` neighbour, NaN is never
#: equal to itself, and `-0.0 == 0`.
_NUMERIC_EQUALITY_CASES: list[list[Any]] = [
    [Decimal("1"), 1, Decimal("1.0")],
    [Decimal("1.10"), Decimal("1.1")],
    [Decimal("2.50"), 2.5],
    [Decimal("0"), 0, False, -0.0],
    [9007199254740993, 9007199254740992],
    [2**62 + 1, 2**62 + 3],
    # (the NaN rows live in their own tests below — identity-dependent)
    [float("inf"), float("inf"), float("-inf")],
    [1, "1"],
    [0, ""],
]


@pytest.mark.parametrize("values", _NUMERIC_EQUALITY_CASES, ids=range(len(_NUMERIC_EQUALITY_CASES)))
def test_equality_key_matches_python_for_hard_numeric_cases(values: list[Any]) -> None:
    """The key must agree with Python's `==`, not with a string spelling.

    Gate-off: key `Decimal` by its text (the pre-fix behaviour) and case 0
    fails; cast integers through `f64` and case 4 fails.
    """
    _assert_agrees(
        "{% for x in v %}{% ifchanged x %}C{% else %}s{% endifchanged %}{% endfor %}",
        {"v": values},
    )


@pytest.mark.parametrize("seed", range(60))
def test_randomized_differential(seed: int) -> None:
    """Random value sequences over every form, against Django.

    The curated table above fixes the shapes; this fixes the VALUES, which is
    where an equality rule (``1`` vs ``"1"`` vs ``None``) goes wrong without
    any shape looking unusual.
    """
    rng = random.Random(seed)
    pool: list[Any] = [
        0,
        1,
        2,
        "1",
        "a",
        "",
        None,
        True,
        False,
        # A Decimal in the pool is what would have caught finding 4 originally.
        Decimal("1"),
        Decimal("1.0"),
        Decimal("2.50"),
        2.5,
        1.0,
    ]
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


def test_include_scoping_matches_django(tmp_path) -> None:
    """`{% ifchanged %}` state across an include — both directions.

    Django carries the state across a PLAIN include and starts CLEAN in an
    `only` include (`Template.render` pushes a fresh `render_context` state for
    the included template). A first pass shared the store into the `only`
    branch "for symmetry with `{% cycle %}`" and moved `CCC` to `Css`; nothing
    tested it. Both rows are pinned here so the next symmetry argument has to
    argue with a measurement.
    """
    from django.template.backends.django import DjangoTemplates

    from djust.template.backend import DjustTemplateBackend

    (tmp_path / "ifc.html").write_text(
        "{% ifchanged %}C{% else %}s{% endifchanged %}", encoding="utf-8"
    )
    params = {"NAME": "n", "DIRS": [str(tmp_path)], "APP_DIRS": False, "OPTIONS": {}}
    django_engine = DjangoTemplates({**params, "NAME": "dj"})
    djust_engine = DjustTemplateBackend({**params, "NAME": "du"})

    for source, expected in [
        ("{% for x in v %}{% include 'ifc.html' %}{% endfor %}", "Css"),
        ("{% for x in v %}{% include 'ifc.html' only %}{% endfor %}", "CCC"),
    ]:
        django_out = str(django_engine.from_string(source).render({"v": [1, 1, 2]}))
        djust_out = str(djust_engine.from_string(source).render({"v": [1, 1, 2]}))
        assert djust_out == django_out == expected, source


_NAN = float("nan")

_SOURCE = "{% for x in v %}{% ifchanged x %}C{% else %}s{% endifchanged %}{% endfor %}"


@pytest.mark.parametrize(
    "values,expected",
    [
        # The SAME NaN object repeated reads as UNCHANGED: Django compares a
        # LIST of resolved operands, and Python's list comparison short-circuits
        # on identity before calling `==`.
        ([_NAN, _NAN], "Cs"),
        ([5, _NAN, 5], "CCC"),
        ([_NAN, _NAN, 1], "CsC"),
    ],
)
def test_repeated_nan_object_matches_django(values, expected: str) -> None:
    """Gate-off: make NaN "never equal" (skip the state store) and all three
    of these regress — that was a real first attempt at this."""
    _assert_agrees(_SOURCE, {"v": values})
    assert _rust.render_template(_SOURCE, {"v": values}) == expected


def test_two_distinct_nan_objects_diverge_from_django() -> None:
    """A KNOWN divergence, pinned so it is not mistaken for parity later.

    Django's answer depends on OBJECT IDENTITY: `[nan, nan]` built from one
    object compares equal (list identity short-circuit, the test above), while
    two separately-constructed NaNs compare unequal and Django reports both as
    changed. Identity does not survive the conversion into the Rust `Value`, so
    no key can reproduce both — `#nan` matches the repeated-object case and
    misses this one; a never-equal key matches this one and misses three.

    The repeated-object reading is kept because it is also what the pre-#2517
    engine did, so this pins a limit rather than a regression. Revisit only if
    `Value` ever carries object identity.
    """
    values = [float("nan"), float("nan")]
    django_out = DjangoTemplate(_SOURCE).render(DjangoContext({"v": values}))
    djust_out = _rust.render_template(_SOURCE, {"v": values})
    assert django_out == "CC", "Django's behaviour changed — re-derive this limit"
    assert djust_out == "Cs", "djust keys both NaNs the same"
