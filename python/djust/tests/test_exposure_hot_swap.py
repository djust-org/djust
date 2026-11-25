"""ADR-038: hot view replacement cannot change or bypass the exposure policy.

``apply_class_swap`` assigns ``__class__`` in place, which never runs the
construction guard. A swap to an invalid configuration (a policy other than
``"explicit"`` assigned after the class statement included) would dodge the
guard entirely, so the swap re-validates and falls back to a full reload.
"""

import pytest

from djust import LiveView
from djust.decorators import state
from djust.hot_view_replacement import apply_class_swap


def _pair(old_body, new_body, name="SwapView"):
    old = type(name, (LiveView,), {"__module__": __name__, **old_body})
    new = type(name, (LiveView,), {"__module__": __name__, **new_body})
    return old, new


@pytest.mark.parametrize("assigned", ["legacy", "nonsense"])
def test_a_policy_assigned_after_the_class_statement_falls_back(assigned):
    """The class statement refuses any value but ``"explicit"``; one assigned
    to the class afterwards is caught by the swap, not carried into the view."""
    old, new = _pair({"exposure_policy": "explicit"}, {"exposure_policy": "explicit"})
    new.exposure_policy = assigned
    view = old()
    ok, reason = apply_class_swap(view, [(old, new)])
    assert not ok and "exposure" in reason
    assert type(view) is old


def test_swap_within_the_policy_still_applies():
    body = {"exposure_policy": "explicit", "count": state(0, persist="server")}
    old, new = _pair(body, dict(body))
    view = old()
    ok, reason = apply_class_swap(view, [(old, new)])
    assert ok, reason
    assert type(view) is new
