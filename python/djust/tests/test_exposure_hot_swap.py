"""ADR-038: hot view replacement cannot change or bypass the exposure policy.

``apply_class_swap`` assigns ``__class__`` in place, which never runs the
construction guard. A swap that changed the policy would keep state built
under one exposure contract inside a view governed by another, and a swap to
an invalid configuration would dodge the guard entirely. Both fall back to a
full reload.
"""

import pytest

from djust import LiveView
from djust.decorators import state
from djust.hot_view_replacement import apply_class_swap


def _pair(old_body, new_body, name="SwapView"):
    old = type(name, (LiveView,), {"__module__": __name__, **old_body})
    new = type(name, (LiveView,), {"__module__": __name__, **new_body})
    return old, new


@pytest.mark.parametrize("old_policy,new_policy", [("legacy", "explicit"), ("explicit", "legacy")])
def test_policy_change_falls_back_to_full_reload(old_policy, new_policy):
    old, new = _pair({"exposure_policy": old_policy}, {"exposure_policy": new_policy})
    view = old()
    ok, reason = apply_class_swap(view, [(old, new)])
    assert not ok and "exposure" in reason
    assert type(view) is old


def test_swap_to_an_invalid_configuration_falls_back():
    old, new = _pair(
        {"exposure_policy": "explicit"},
        {"exposure_policy": "explicit", "use_actors": True},
    )
    view = old()
    ok, reason = apply_class_swap(view, [(old, new)])
    assert not ok and "exposure" in reason
    assert type(view) is old


def test_swap_within_a_policy_still_applies():
    for policy in ("legacy", "explicit"):
        body = {"exposure_policy": policy}
        if policy == "explicit":
            body["count"] = state(0, persist="server")
        old, new = _pair(body, dict(body))
        view = old()
        ok, reason = apply_class_swap(view, [(old, new)])
        assert ok, reason
        assert type(view) is new
