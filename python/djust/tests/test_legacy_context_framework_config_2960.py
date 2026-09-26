"""Legacy template context carries no LiveView configuration defaults (#2960).

``ContextMixin.get_context_data`` walks class attributes for legacy views. The
walk stopped at ``ContextMixin``, and ``LiveView`` sits BEFORE it in every
view's MRO, so ``LiveView``'s own configuration defaults (``template``,
``login_required``, ``use_actors``, ``sticky``, ...) entered every legacy
view's template context -- and from there its session state. The walk now skips
every class in ``LiveView``'s MRO, the same boundary the ADR-038
``djust_exposure_inventory`` command draws (``_user_bases``). Attributes the
application declares on its own classes still flow through unchanged.
"""

from __future__ import annotations

import pytest

from djust import LiveView
from djust.live_view import _FRAMEWORK_INTERNAL_ATTRS


class _Mixin:
    mixin_label = "from a user mixin"


def _context(view_cls):
    view = view_cls()
    view.mount(None)
    return view.get_context_data()


def _framework_config_names() -> set[str]:
    """Plain data attributes LiveView's own MRO declares (derived, not listed)."""
    names: set[str] = set()
    for base in LiveView.__mro__:
        for name, value in vars(base).items():
            if name.startswith("_") or callable(value):
                continue
            if isinstance(value, (property, staticmethod, classmethod)):
                continue
            names.add(name)
    return names


def test_framework_config_defaults_are_not_template_context():
    cls = type(
        "Legacy2960",
        (_Mixin, LiveView),
        {"template": "<div dj-root>{{ greeting }}</div>", "greeting": "hi"},
    )
    context = _context(cls)
    leaked = {
        name
        for name in _framework_config_names()
        if name in context and name not in vars(cls) and name not in vars(_Mixin)
    }
    assert not leaked, leaked
    # The names the issue lists, spelled out.
    for name in ("login_required", "use_actors", "sticky", "tick_interval", "abstract"):
        assert name not in context


def test_user_declared_class_attributes_still_reach_the_context():
    cls = type(
        "Legacy2960b",
        (_Mixin, LiveView),
        {"template": "<div dj-root>{{ greeting }}</div>", "greeting": "hi", "items": [1, 2]},
    )
    context = _context(cls)
    assert context["greeting"] == "hi"
    assert context["items"] == [1, 2]
    assert context["mixin_label"] == "from a user mixin"


def test_a_user_subclass_chain_is_walked_to_the_framework_boundary():
    base = type("Base2960", (LiveView,), {"section": "base", "template": "<div dj-root></div>"})
    child = type("Child2960", (base,), {"page": "child"})
    context = _context(child)
    assert context["section"] == "base"
    assert context["page"] == "child"
    assert "login_required" not in context


@pytest.mark.parametrize("name", ["sticky", "use_actors", "login_required"])
def test_the_derived_config_set_covers_the_issue_names(name):
    # Guards the helper above: the set is derived from LiveView, so it must
    # contain the names the issue reports (and the framework already lists).
    assert name in _framework_config_names()
    assert name in _FRAMEWORK_INTERNAL_ATTRS
