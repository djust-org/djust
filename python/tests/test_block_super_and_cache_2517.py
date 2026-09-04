"""``{{ block.super }}``, ``{% cache %}``, and dynamic ``{% extends %}`` (#2517).

Three gaps that shared one cause: the Django backend flattened ``{% extends %}``
in PYTHON, with a regex/string merge, before the Rust engine ever saw the
template. That merge stripped block wrappers, so ``{{ block.super }}`` had
nothing to resolve against; it matched ``{% extends %}`` with a regex, so a
relative or variable target never reached the code that understands one. The
Rust engine has had loader support for a long time — the workaround outlived
its premise, and running both left two implementations of one invariant
(CLAUDE.md #1646).

The fix routes inheritance through the single Rust path. These tests are the
pins for what that path must do, differentially against Django.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Engine  # noqa: E402
from django.template.backends.django import DjangoTemplates  # noqa: E402

from djust.template.backend import DjustTemplateBackend  # noqa: E402

#: A chain deep enough that a two-level `block.super` implementation passes and
#: a recursive one is required: `three` <- `two` <- `one`.
_TEMPLATES = {
    "three.html": "{% block c %}three{% endblock %}",
    "two.html": "{% extends 'three.html' %}{% block c %}{{ block.super }} two{% endblock %}",
    "one.html": "{% extends 'two.html' %}{% block c %}{{ block.super }} one{% endblock %}",
    # A block nested inside control flow in the PARENT — the shape that a
    # top-level-only block scan misses.
    "if_parent.html": "1{% if opt %}{% block c %}2{% endblock %}{% endif %}3",
    "if_child.html": "{% extends 'if_parent.html' %}{% block c %}new{{ block.super }}{% endblock %}",
    "for_parent.html": "{% for n in nums %}_{% block c %}{{ n }}{% endblock %}{% endfor %}_",
    "for_child.html": "{% extends 'for_parent.html' %}{% block c %}new{{ block.super }}{% endblock %}",
    # No `block.super`: the parent body must NOT be rendered for its side effects.
    "plain_child.html": "{% extends 'three.html' %}{% block c %}only{% endblock %}",
    # The parent's block ADVANCES A CYCLE. A child that does not reference
    # `block.super` must leave that cycle untouched, so the sibling after the
    # block still emits the FIRST value.
    "cycle_parent.html": ("{% block c %}{% cycle 'a' 'b' as k %}{% endblock %}|{% cycle k %}"),
    "cycle_child.html": "{% extends 'cycle_parent.html' %}{% block c %}only{% endblock %}",
    # A variable target.
    "var_child.html": "{% extends parent_name %}{% block c %}var{% endblock %}",
}


@pytest.fixture(scope="module")
def engines(tmp_path_factory):
    root = tmp_path_factory.mktemp("t2517")
    for name, body in _TEMPLATES.items():
        (root / name).write_text(body, encoding="utf-8")
    params = {"NAME": "x", "DIRS": [str(root)], "APP_DIRS": False, "OPTIONS": {}}
    return (
        DjangoTemplates({**params, "NAME": "dj"}),
        DjustTemplateBackend({**params, "NAME": "du"}),
    )


def _both(engines, name: str, ctx: dict[str, Any]) -> tuple[str, str]:
    django_engine, djust_engine = engines
    return (
        str(django_engine.get_template(name).render(dict(ctx))),
        str(djust_engine.get_template(name).render(dict(ctx))),
    )


@pytest.mark.parametrize(
    "name,ctx",
    [
        ("two.html", {}),
        # The recursive case: a two-level implementation renders "two one".
        ("one.html", {}),
        ("if_child.html", {"opt": True}),
        ("if_child.html", {"opt": False}),
        ("for_child.html", {"nums": "123"}),
        ("plain_child.html", {}),
    ],
)
def test_block_super_matches_django(engines, name: str, ctx: dict[str, Any]) -> None:
    django_out, djust_out = _both(engines, name, ctx)
    assert djust_out == django_out, f"{name} {ctx}"


def test_block_super_is_recursive_not_just_one_level(engines) -> None:
    """The assertion a two-level implementation fails.

    Gate-off: stop nesting the scope in `merge_blocks` (wrap against the
    immediate parent only) and this is the test that goes red — `two.html`
    still passes.
    """
    _, djust_out = _both(engines, "one.html", {})
    assert djust_out == "three two one"


def test_a_body_without_block_super_does_not_render_its_parent(engines) -> None:
    """Django resolves `block.super` lazily through `BlockContext`, so a body
    that never mentions it must not pay for — or OBSERVE THE SIDE EFFECTS of —
    rendering the parent's version.

    The assertion has to be on a side effect. Asserting the output is `"only"`
    (the first version of this test) passes whether or not the parent ran: the
    parent's render would go into a string that is then discarded. `{% cycle %}`
    is the observable: it advances per render, so if the parent body ran its
    cycle would be one step further along and the sibling below would emit the
    SECOND value instead of the first.
    """
    django_out, djust_out = _both(engines, "cycle_child.html", {})
    assert djust_out == django_out
    # `a` — not `b` — proves the parent's `{% cycle %}` never advanced.
    assert djust_out == "only|a"


#: The spellings the detection whitelist missed (PR #2650 review, finding 3).
#: Each rendered SILENTLY EMPTY, because the walk inspected `Variable` names
#: and `If` conditions but not `With` values, `For` iterables or `firstof`
#: operands — and its fallthrough was `_ => false`, so an undetected reference
#: meant no binding at all rather than a wasted render.
_BLOCK_SUPER_SPELLINGS = {
    "plain": "[{{ block.super }}]",
    "with": "{% with s=block.super %}[{{ s }}]{% endwith %}",
    "for": "{% for ch in block.super %}{{ ch }}{% endfor %}",
    "firstof": "[{% firstof block.super %}]",
    "if": "{% if block.super %}[{{ block.super }}]{% endif %}",
    "nested": "{% if 1 %}{% with s=block.super %}[{{ s }}]{% endwith %}{% endif %}",
}


@pytest.mark.parametrize(
    "body", list(_BLOCK_SUPER_SPELLINGS.values()), ids=list(_BLOCK_SUPER_SPELLINGS)
)
def test_block_super_resolves_in_every_expression_position(engines, body: str) -> None:
    """Gate-off: restore `_ => false` as the walker's fallthrough and the
    `for`/`firstof` rows go empty; drop the `With`/`For` expression checks and
    the `with`/`for` rows do."""
    django_engine, djust_engine = engines
    src = "{% extends 'three.html' %}{% block c %}" + body + "{% endblock %}"
    django_out = str(django_engine.from_string(src).render({}))
    djust_out = str(djust_engine.from_string(src).render({}))
    assert djust_out == django_out
    # Non-vacuity: the parent's text must actually be present, so a pair of
    # empty strings cannot pass.
    assert "three" in djust_out


def test_extends_accepts_a_context_variable(engines) -> None:
    """`{% extends parent_name %}` — Django compiles the operand as a
    FilterExpression, so an unquoted token is a lookup."""
    django_out, djust_out = _both(engines, "var_child.html", {"parent_name": "three.html"})
    assert djust_out == django_out == "var"


class TestCacheTag:
    """`{% cache %}` — the fragment is stored and reused."""

    @pytest.fixture(autouse=True)
    def _clear(self):
        from django.core.cache import cache

        cache.clear()
        yield
        cache.clear()

    @pytest.fixture
    def backend(self):
        return DjustTemplateBackend(
            {
                "NAME": "cachebe",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {"libraries": {"cache": "django.templatetags.cache"}},
            }
        )

    def test_second_render_of_the_same_key_returns_the_cached_fragment(self, backend) -> None:
        first = backend.from_string("{% load cache %}{% cache 60 k %}one{% endcache %}")
        second = backend.from_string("{% load cache %}{% cache 60 k %}two{% endcache %}")
        assert str(first.render({})) == "one"
        # Same fragment name, different body: the CACHED value wins, which is
        # the whole point of the tag.
        assert str(second.render({})) == "one"

    def test_vary_operands_separate_the_keys(self, backend) -> None:
        tpl = backend.from_string("{% load cache %}{% cache 60 k foo %}{{ foo }}{% endcache %}")
        assert str(tpl.render({"foo": 1})) == "1"
        assert str(tpl.render({"foo": 2})) == "2"
        assert str(tpl.render({"foo": 1})) == "1"

    def test_a_filter_expression_is_a_valid_expiry(self, backend) -> None:
        """`{% cache 2|add:1 k %}` — the operand is a FilterExpression, so the
        resolver must be Django's rather than a hand-rolled literal parser."""
        tpl = backend.from_string("{% load cache %}{% cache 2|add:1 k %}body{% endcache %}")
        assert str(tpl.render({})) == "body"

    def test_unresolvable_expiry_is_an_error_not_cache_forever(self, backend) -> None:
        """Django resolves the expiry WITHOUT `ignore_failures`, so a missing
        variable raises rather than silently meaning "forever"."""
        from django.template import TemplateSyntaxError

        tpl = backend.from_string("{% load cache %}{% cache nope k %}b{% endcache %}")
        with pytest.raises((TemplateSyntaxError, Exception), match="cache"):
            tpl.render({})

    def test_literal_none_means_forever_and_is_not_a_miss(self, backend) -> None:
        tpl = backend.from_string("{% load cache %}{% cache None k %}b{% endcache %}")
        assert str(tpl.render({})) == "b"

    def test_using_with_only_two_operands_is_the_fragment_name(self, backend) -> None:
        """Django guards the `using=` pop with `len(tokens) > 2`, so here the
        trailing token IS the fragment name. Guarding with `if tokens` popped
        it, left one operand, and raised `IndexError` — an unhandled crash on
        input Django renders (PR #2650 review, finding 2)."""
        src = '{% load cache %}{% cache 10 using="default" %}X{% endcache %}'
        assert str(backend.from_string(src).render({})) == "X"

    def test_an_expiry_that_resolves_to_none_caches_forever(self, backend) -> None:
        """`ignore_failures=True` answers `None` for BOTH "no such variable"
        and "the variable is None". Collapsing them made a legitimate `None`
        raise where Django caches forever (finding 5, row 1)."""
        src = "{% load cache %}{% cache t f_none %}Y{% endcache %}"
        assert str(backend.from_string(src).render({"t": None})) == "Y"

    def test_matches_django(self, backend) -> None:
        """Differential against Django — with DISTINCT fragment names.

        The first version of this test rendered the SAME fragment name through
        both engines into the same `LocMemCache`, so the second render was a
        cache HIT on the first engine's stored value and the assertion compared
        a string to itself. It could not fail: a wholly different Django output
        would still have compared equal. Distinct names give each engine its
        own key, so the comparison is of two real renders.
        """
        django_engine = Engine(libraries={"cache": "django.templatetags.cache"})
        djust_out = str(
            backend.from_string(
                "{% load cache %}{% cache 60 frag_djust %}payload{% endcache %}"
            ).render({})
        )
        django_out = django_engine.from_string(
            "{% load cache %}{% cache 60 frag_django %}payload{% endcache %}"
        ).render(DjangoContext({}))
        assert djust_out == django_out == "payload"
