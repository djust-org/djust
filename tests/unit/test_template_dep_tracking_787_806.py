"""Regression tests for template-dep-tracking gaps (#787, #806).

#787 — filter arguments that are bare identifiers must count as template
       dependencies. Without this, a template like
       ``{{ value|default:fallback }}`` would track only ``value`` and
       silently drop re-renders when ``fallback`` changes.

#806 — ``{% for x in foo.bar %}`` iterables must resolve through
       ``Context::resolve`` so getattr-backed iterables (Django
       QuerySets, related managers, dataclass attributes, etc.) render
       correctly instead of silently appearing empty.
"""

from __future__ import annotations

import pytest

from djust._rust import extract_template_variables, render_template


# ---------------------------------------------------------------------------
# #787 — filter-argument dep tracking
# ---------------------------------------------------------------------------


def test_default_filter_bare_identifier_is_a_dep():
    vars = extract_template_variables("{{ value|default:fallback }}")
    assert "fallback" in vars, (
        "|default:fallback must be tracked as a dep — otherwise a "
        "nested {% if %} guarding this variable will not re-render "
        "when only `fallback` changes"
    )
    assert "value" in vars


def test_default_filter_string_literal_is_not_a_dep_double_quoted():
    vars = extract_template_variables('{{ value|default:"none" }}')
    assert list(vars.keys()) == ["value"]


def test_default_filter_string_literal_is_not_a_dep_single_quoted():
    vars = extract_template_variables("{{ value|default:'none' }}")
    assert list(vars.keys()) == ["value"]


def test_default_filter_numeric_literal_is_not_a_dep():
    vars = extract_template_variables("{{ value|default:0 }}")
    assert list(vars.keys()) == ["value"]


def test_default_filter_negative_numeric_literal_is_not_a_dep():
    vars = extract_template_variables("{{ value|default:-1 }}")
    assert list(vars.keys()) == ["value"]


def test_default_filter_dotted_identifier_arg_is_a_dep():
    vars = extract_template_variables("{{ value|default:settings.FALLBACK }}")
    assert "settings" in vars
    # Tracks the sub-path so the narrowed serializer knows to include it.
    assert "FALLBACK" in vars["settings"]


def test_multiple_filters_each_contribute_deps():
    vars = extract_template_variables("{{ value|default:fallback|truncatechars:limit }}")
    assert "value" in vars
    assert "fallback" in vars
    assert "limit" in vars


def test_if_with_filter_arg_dep_catches_both(tmp_path):
    """End-to-end: {% if %}{{ x|default:dynamic }}{% endif %} — changing
    only `dynamic` must produce a re-render.
    """
    template = "{% if show %}{{ name|default:fallback }}{% endif %}"
    vars = extract_template_variables(template)
    assert "fallback" in vars
    # `show` → If-condition dep, `name`/`fallback` → Variable + filter-arg dep
    assert set(vars.keys()) >= {"show", "name", "fallback"}


# ---------------------------------------------------------------------------
# #806 — for-iterables via Context::resolve (getattr fallback)
# ---------------------------------------------------------------------------


class _UserStub:
    """Acts like a Django model with a related manager / relation.

    Uses plain instance attributes (not ``@property``) so the
    ``Value::extract`` fast-path picks up the relation field via
    ``__dict__``. ``@property``-backed relations require the
    ``raw_py_objects`` sidecar path that ``render_template`` doesn't
    populate today; that is a separate follow-up to #806 and not what
    this change is exercising.
    """

    def __init__(self, orders):
        self.orders = orders


def test_for_iterable_via_getattr_resolves_relation():
    """Issue #806 — dotted iterable must walk getattr, not value-stack.

    Previously ``context.get(iterable)`` was consulted, which only hits
    the root key and couldn't see ``user.orders``. The rendered
    template was an empty string because the iterable resolved to Null.
    Now the for-iterable uses ``Context::resolve`` which walks the
    Value stack's getattr-equivalent (Object → List).
    """
    user = _UserStub(["apple", "banana", "cherry"])
    out = render_template(
        "{% for o in user.orders %}{{ o }},{% endfor %}",
        {"user": user},
    )
    assert out == "apple,banana,cherry,"


def test_for_iterable_via_getattr_deeply_nested():
    class _Root:
        def __init__(self, leaf):
            self.child = _Mid(leaf)

    class _Mid:
        def __init__(self, leaf):
            self.leaf = leaf

    root = _Root([1, 2, 3])
    out = render_template(
        "{% for n in root.child.leaf %}[{{ n }}]{% endfor %}",
        {"root": root},
    )
    assert out == "[1][2][3]"


def test_for_iterable_getattr_empty_renders_empty_block():
    user = _UserStub([])
    out = render_template(
        "{% for o in user.orders %}{{ o }}{% empty %}EMPTY{% endfor %}",
        {"user": user},
    )
    assert out == "EMPTY"


def test_for_iterable_top_level_context_still_works():
    """Regression-guard: the resolve-first change must not regress the
    common case where the iterable is a top-level context variable.
    """
    out = render_template(
        "{% for n in nums %}{{ n }}{% endfor %}",
        {"nums": [1, 2, 3]},
    )
    assert out == "123"


def test_for_iterable_missing_attr_falls_through_silently():
    """An iterable that doesn't resolve (no such attr) renders as if
    the iterable were empty — matching Django's "invalid → empty
    string" default.
    """

    class _NoOrders:
        name = "bob"

    out = render_template(
        "{% for o in user.orders %}{{ o }}{% empty %}NONE{% endfor %}",
        {"user": _NoOrders()},
    )
    assert out == "NONE"


@pytest.mark.parametrize(
    "tmpl,expected_extra",
    [
        ("{{ v|add:amount }}", "amount"),
        ("{{ v|stringformat:fmt }}", "fmt"),
        ("{{ v|truncatechars:max_len }}", "max_len"),
    ],
)
def test_other_filters_with_ident_arg_tracked(tmpl, expected_extra):
    vars = extract_template_variables(tmpl)
    assert expected_extra in vars, f"filter arg `{expected_extra}` in `{tmpl}` must be tracked"
