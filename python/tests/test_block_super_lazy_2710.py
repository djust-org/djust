"""``{{ block.super }}`` renders the parent WHEN IT IS EVALUATED (#2710).

The divergence
--------------
``Node::BlockSuperScope`` rendered the parent body before entering the child
and bound the result, so a reference sitting in a branch Django never
evaluates still ran the parent. Static detection that a child body MENTIONS
``block.super`` — which is what builds the node in the first place
(``inheritance::nodes_reference_block_super``) — is not evidence that
evaluation will reach it.

Equal output hid unequal behaviour, which is why every case here counts
PARENT EVALUATIONS rather than comparing rendered strings alone::

    {% if show %}{{ block.super }}{% endif %}child   show=False

    django  'child'  parent_calls=0
    djust   'child'  parent_calls=1     <- before this fix

and why one case makes the parent RAISE: a false branch containing
``{{ block.super }}`` used to 500 on a parent that only raises when asked.

What the fix is
---------------
The scope carries a deferred SOURCE (``DeferredBlockSuper``: the parent nodes
plus an owned loader handle) and ``Context::resolve`` runs it when — and each
time — an expression asks for ``block.super``. ``resolve`` is the ONE resolver
every operand channel ends in, so ``{{ }}``, ``{% if %}``, ``{% with %}`` and
a filter argument get the same answer from one place (#1646).

Not memoized, and that is measured rather than assumed: Django's
``BlockNode.super()`` is a method call, so two references render the parent
twice and a ``{% for %}`` over three items renders it three times. A
memoizing version answers ``a-a`` for a parent containing
``{% cycle 'a' 'b' %}`` where Django answers ``a-b``.

Django is CALLED in every case below, never transcribed.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Engine  # noqa: E402

from djust.template import DjustTemplateBackend  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
RENDERER_RS = REPO / "crates" / "djust_templates" / "src" / "renderer.rs"
CONTEXT_RS = REPO / "crates" / "djust_core" / "src" / "context.rs"


class Counter:
    """A parent whose evaluation is COUNTABLE — the whole point of the file."""

    def __init__(self) -> None:
        self.calls = 0

    def tick(self) -> str:
        self.calls += 1
        return "P"


class Boom:
    """A parent that is harmless until asked, and fatal when it is."""

    def __init__(self) -> None:
        self.calls = 0

    def tick(self) -> str:
        self.calls += 1
        raise ValueError("parent exploded")


#: ``name -> child source``. The parents live in ``_templates``.
CASES: dict[str, str] = {
    # ---- the reported shape, and its true-branch twin -------------------
    "false_branch": '{% extends "base.html" %}{% block body %}'
    "{% if show %}{{ block.super }}{% endif %}child{% endblock %}",
    "true_branch": '{% extends "base.html" %}{% block body %}'
    "{% if yes %}{{ block.super }}{% endif %}child{% endblock %}",
    # ---- repeated references: Django does NOT memoize -------------------
    "repeated": '{% extends "base.html" %}{% block body %}'
    "{{ block.super }}{{ block.super }}{% endblock %}",
    "for_three": '{% extends "base.html" %}{% block body %}'
    "{% for i in three %}{{ block.super }}{% endfor %}{% endblock %}",
    "for_zero": '{% extends "base.html" %}{% block body %}'
    "{% for i in empty %}{{ block.super }}{% endfor %}z{% endblock %}",
    # ---- short-circuited operands ---------------------------------------
    "short_circuit_and": '{% extends "base.html" %}{% block body %}'
    "{% if show and block.super %}Y{% else %}N{% endif %}{% endblock %}",
    "short_circuit_or": '{% extends "base.html" %}{% block body %}'
    "{% if yes or block.super %}Y{% else %}N{% endif %}{% endblock %}",
    "firstof": '{% extends "base.html" %}{% block body %}'
    "{% firstof yes block.super %}{% endblock %}",
    # ---- filtered / aliased ----------------------------------------------
    "filtered": '{% extends "base.html" %}{% block body %}{{ block.super|upper }}{% endblock %}',
    "with_alias": '{% extends "base.html" %}{% block body %}'
    "{% with s=block.super %}{{ s }}{{ s }}{% endwith %}{% endblock %}",
    "with_alias_unused": '{% extends "base.html" %}{% block body %}'
    "{% with s=block.super %}x{% endwith %}{% endblock %}",
    # ---- multi-level inheritance ----------------------------------------
    "multilevel": '{% extends "mid.html" %}{% block body %}({{ block.super }}){% endblock %}',
    "multilevel_false": '{% extends "mid.html" %}{% block body %}'
    "{% if show %}({{ block.super }}){% endif %}c{% endblock %}",
    # ---- a parent with a SIDE EFFECT of its own ---------------------------
    "cycle_in_parent": '{% extends "cyc.html" %}{% block body %}'
    "{{ block.super }}-{{ block.super }}{% endblock %}",
    # ---- the Python-bridged operand channel ------------------------------
    "blocktranslate": '{% extends "base.html" %}{% load i18n %}{% block body %}'
    "{% blocktranslate with s=block.super %}v={{ s }}{% endblocktranslate %}{% endblock %}",
    # ---- autoescape: the parent's output is already escaped --------------
    "autoescape": '{% extends "amp.html" %}{% block body %}[{{ block.super }}]{% endblock %}',
    # ---- the raising parent ----------------------------------------------
    "boom_false": '{% extends "boom.html" %}{% block body %}'
    "{% if show %}{{ block.super }}{% endif %}ok{% endblock %}",
    "boom_true": '{% extends "boom.html" %}{% block body %}'
    "{% if yes %}{{ block.super }}{% endif %}ok{% endblock %}",
    # ---- a parent that needs the LOADER -----------------------------------
    # The deferred render happens after the `Node::BlockSuperScope` arm has
    # returned, so it cannot borrow the arm's `Option<&L>`; it carries an
    # owned `shared_handle()` instead. Without that, an `{% include %}` in
    # the parent body refuses with "Template loader not configured".
    "include_in_parent": '{% extends "inc.html" %}{% block body %}'
    "[{{ block.super }}]{% endblock %}",
    "include_in_parent_false": '{% extends "inc.html" %}{% block body %}'
    "{% if show %}[{{ block.super }}]{% endif %}c{% endblock %}",
    # ---- the control: no reference at all ---------------------------------
    "not_referenced": '{% extends "base.html" %}{% block body %}child{% endblock %}',
}

PARENTS = {
    "base.html": "{% block body %}{{ counter.tick }}{% endblock %}",
    "mid.html": '{% extends "base.html" %}{% block body %}[{{ block.super }}]{% endblock %}',
    "boom.html": "{% block body %}{{ boom.tick }}{% endblock %}",
    "cyc.html": "{% block body %}{% cycle 'a' 'b' %}{% endblock %}",
    "amp.html": "{% block body %}<b>&</b>{% endblock %}",
    "inc.html": '{% block body %}{% include "frag.html" %}{{ counter.tick }}{% endblock %}',
    "frag.html": "F",
}


@pytest.fixture(scope="module")
def template_dir() -> str:
    with tempfile.TemporaryDirectory() as directory:
        for name, source in PARENTS.items():
            Path(directory, name).write_text(source)
        yield directory


def _answer(engine: str, template_dir: str, source: str) -> tuple[str, int, int]:
    """``(rendered-or-exception, parent_calls, boom_calls)`` for one cell."""
    counter, boom = Counter(), Boom()
    data = {
        "counter": counter,
        "boom": boom,
        "show": False,
        "yes": True,
        "empty": [],
        "three": [1, 2, 3],
    }
    try:
        if engine == "django":
            # `libraries` explicitly: a bare `Engine` registers no `{% load %}`
            # target, so the `blocktranslate` cell would compare djust's real
            # answer against Django's "not a registered tag library".
            engine_obj = Engine(
                dirs=[template_dir],
                libraries={"i18n": "django.templatetags.i18n"},
            )
            rendered = engine_obj.from_string(source).render(DjangoContext(data))
        else:
            backend = DjustTemplateBackend(
                {"NAME": "t2710", "DIRS": [template_dir], "APP_DIRS": False, "OPTIONS": {}}
            )
            rendered = backend.from_string(source).render(data)
    except Exception as exc:  # noqa: BLE001 - the refusal IS the answer
        # djust wraps a propagated Python exception, so compare the MESSAGE
        # rather than the class: the parent's own `ValueError("parent
        # exploded")` has to reach the caller either way.
        rendered = "<<%s>>" % re.sub(r"\s+", " ", str(exc))[-40:]
    return rendered, counter.calls, boom.calls


class TestTheParentRendersOnlyWhenTheExpressionIsEvaluated:
    """Every case against live Django 5.2, comparing OUTPUT and COUNTS."""

    @pytest.mark.parametrize("case", sorted(CASES))
    def test_output_and_evaluation_count_match_django(self, case: str, template_dir: str) -> None:
        source = CASES[case]
        assert _answer("djust", template_dir, source) == _answer("django", template_dir, source), (
            case
        )

    def test_the_matrix_really_covers_the_acceptance_cases(self) -> None:
        """A silently shrunken matrix is how a differential stops being
        evidence. Each name below is an acceptance criterion of #2710."""
        for required in (
            "false_branch",
            "short_circuit_and",
            "short_circuit_or",
            "firstof",
            "for_zero",
            "repeated",
            "for_three",
            "filtered",
            "with_alias",
            "multilevel",
            "multilevel_false",
            "boom_false",
            "boom_true",
            "blocktranslate",
            "autoescape",
            "include_in_parent",
        ):
            assert required in CASES
        assert len(CASES) == 21


class TestTheCountsAreNotVacuous:
    """The cases above compare djust to Django, so a shared wrong answer
    would pass. These state the numbers outright, so the file also says what
    Django's behaviour IS (#1200)."""

    @pytest.mark.parametrize(
        ("case", "expected_parent_calls"),
        [
            ("not_referenced", 0),
            ("false_branch", 0),
            ("short_circuit_and", 0),
            ("short_circuit_or", 0),
            ("firstof", 0),
            ("for_zero", 0),
            ("multilevel_false", 0),
            ("true_branch", 1),
            ("filtered", 1),
            # `{% with %}` resolves ONCE at the tag and aliases the string.
            ("with_alias", 1),
            ("with_alias_unused", 1),
            ("multilevel", 1),
            # NOT memoized: Django's `BlockNode.super()` is a method call.
            ("repeated", 2),
            ("for_three", 3),
        ],
    )
    def test_the_parent_is_evaluated_exactly_this_many_times(
        self, case: str, expected_parent_calls: int, template_dir: str
    ) -> None:
        _, calls, _ = _answer("djust", template_dir, CASES[case])
        assert calls == expected_parent_calls

    def test_a_raising_parent_is_harmless_in_a_branch_that_is_not_taken(
        self, template_dir: str
    ) -> None:
        """The sharpest row: before #2710 this rendered a 500 for a page
        whose false branch Django never evaluates."""
        rendered, _, boom_calls = _answer("djust", template_dir, CASES["boom_false"])
        assert rendered == "ok"
        assert boom_calls == 0

    def test_a_raising_parent_still_raises_when_it_IS_asked(self, template_dir: str) -> None:
        rendered, _, boom_calls = _answer("djust", template_dir, CASES["boom_true"])
        assert rendered.startswith("<<")
        assert "parent exploded" in rendered
        assert boom_calls == 1

    def test_the_deferred_render_still_has_the_loader(self, template_dir: str) -> None:
        """The owned `shared_handle()` is what makes this work: the deferred
        render runs after the scope arm returned, so it cannot borrow the
        arm's `Option<&L>`. Without the handle the `{% include %}` in the
        parent body refuses with "Template loader not configured" — a
        failure no OUTPUT-only comparison of the other cases would reach."""
        rendered, calls, _ = _answer("djust", template_dir, CASES["include_in_parent"])
        assert rendered == "[FP]", rendered
        assert calls == 1
        # And it is still lazy: an unselected branch loads nothing.
        rendered, calls, _ = _answer("djust", template_dir, CASES["include_in_parent_false"])
        assert rendered == "c"
        assert calls == 0

    def test_a_side_effect_in_the_parent_runs_once_per_reference(self, template_dir: str) -> None:
        """`{% cycle %}` state lives on the shared `Arc` a `Context` clone
        keeps, which is what makes two references answer `a-b`. A deferred
        render against a private copy of that state would answer `a-a`, and
        so would the pre-#2710 memoized string."""
        rendered, _, _ = _answer("djust", template_dir, CASES["cycle_in_parent"])
        assert rendered == "a-b"


class TestTheDeferralHasOneStatementPerBoundary:
    """Structural pins on the caller SET, not a floor (#1125/#1646).

    The mechanism has exactly two entry points — the renderer's scope arm
    arms it, and `Context::resolve` runs it — plus one escape hatch for the
    Python bridge. A future site that renders `super_nodes` on its own, or a
    `to_hashmap()` that skips the bridge, is the drift these catch.
    """

    def test_arm_block_super_has_exactly_one_caller(self) -> None:
        source = RENDERER_RS.read_text()
        # `(?<!dis)` so `disarm_block_super()` — a different operation, with
        # two legitimate sites — does not count as an arming.
        arms = re.findall(r"(?<!dis)arm_block_super\(", source)
        assert len(arms) == 1, (
            f"the deferred source is armed at {len(arms)} sites; "
            "`Node::BlockSuperScope` is the only scope that has one"
        )

    def test_the_parent_nodes_are_rendered_in_exactly_one_place(self) -> None:
        source = RENDERER_RS.read_text()
        # The two arms of `render_block_super` (loader / no loader).
        assert source.count("render_nodes_with_loader_mut(&self.super_nodes") == 2
        # And the pre-#2710 EAGER form is gone. This is the shape that would
        # come back: a render of the borrowed `super_nodes` at the scope arm.
        assert "render_nodes_with_loader_mut(super_nodes" not in source, (
            "the parent is being rendered from the borrowed `super_nodes` — "
            "that is the eager render #2710 removed"
        )

    def test_every_python_bridge_goes_through_the_one_materialiser(self) -> None:
        """A bridged tag receives the context as a FLAT MAP, which is the one
        boundary the deferral cannot cross. Every builder of that map must go
        through `bridged_context_map`, or `{% blocktranslate with
        s=block.super %}` silently resolves to the empty string — which is
        exactly what the first pass of this fix did."""
        source = RENDERER_RS.read_text()
        assert source.count("bridged_context_map(context)?") == 7, (
            "the Python-bridge caller SET moved — every builder of the flat "
            "context map must go through the one materialiser (#1646)"
        )
        # Exactly ONE raw `to_hashmap()` remains, and it is the materialiser's
        # own not-armed fast path. A second is a bridge bypassing it.
        raw = source.count("context.to_hashmap()")
        assert raw == 1, f"{raw} raw `to_hashmap()` calls; only the fast path may have one"
        helper = source.split("fn bridged_context_map", 1)[1][:900]
        assert "context.to_hashmap()" in helper

    def test_resolve_is_the_only_reader_of_the_armed_source(self) -> None:
        source = CONTEXT_RS.read_text()
        # The trait declaration, `Context::resolve`'s call, and
        # `render_armed_block_super`'s (the Python bridge's escape hatch).
        assert source.count("render_block_super(") == 3


class TestTheSourceIsDeferredRatherThanPreRendered:
    """The gate-off's own target: a version that renders at arm time passes
    every OUTPUT comparison above and fails only on the counts."""

    def test_the_scope_arm_binds_no_rendered_string(self) -> None:
        source = RENDERER_RS.read_text()
        # The RENDER arm, not `nodes_contain_elements`'s arm on the same
        # pattern — split on the last occurrence.
        arm = source.rsplit("Node::BlockSuperScope { super_nodes, nodes } =>", 1)[1][:4000]
        assert "arm_block_super(source)" in arm
        assert "Value::String(parent_html)" not in arm, (
            "the scope arm is binding a pre-rendered parent again — the shape #2710 removed"
        )
