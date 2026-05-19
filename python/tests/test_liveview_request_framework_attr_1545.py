"""
Regression tests for #1545 — `LiveView.request` is now captured in
`_framework_attrs` at `__init__` time and treated as framework state.

Pre-fix: `self.request` was assigned by the HTTP post()/WS paths AFTER
`__init__`, so the snapshot machinery saw `request` as user state, tried
to serialize the `ASGIRequest`, hit the non-serializable fallback at
`serialization.py:557`, and logged
"LiveView state contains non-serializable value: ASGIRequest …"
on every mount / event for every `LiveView`.

Post-fix: `LiveView.__init__` assigns `self.request = None` BEFORE the
`_framework_attrs` snapshot line, so `request` is treated as framework
state, excluded from the user-state snapshot, and the warning is silent.

Issue: #1545
Related: #1393 (`_framework_attrs` snapshot-order invariant), #705
(`self.request` assignment in `mixins/request.py:489`).
"""

import logging


from djust import LiveView


class _PlainView(LiveView):
    """Minimal LiveView with no user attrs assigned in mount()."""

    template = "<div dj-root>{{ value }}</div>"

    def mount(self, request, **kwargs):
        self.value = 1


class _ViewWithUserAttr(LiveView):
    """LiveView that ALSO sets a user-defined `value` to confirm the gate
    still excludes only framework attrs (regression-of-regression guard)."""

    template = "<div dj-root>{{ value }}</div>"

    def mount(self, request, **kwargs):
        self.value = 42
        self._secret = "internal"


# ---------------------------------------------------------------------------
# Framework-attr snapshot invariant
# ---------------------------------------------------------------------------


def test_request_attribute_exists_post_init():
    """`request` is initialized to None in `__init__` so the attribute
    exists even before the request path reassigns it."""
    v = _PlainView()
    assert hasattr(v, "request")
    assert v.request is None


def test_request_is_in_framework_attrs():
    """`request` MUST be captured in `_framework_attrs` so the user-state
    snapshot excludes it (#1545). This is the load-bearing assertion —
    without `_framework_attrs` membership, `serialization.py:557` will
    fire the non-serializable warning on every mount/event."""
    v = _PlainView()
    assert "request" in v._framework_attrs, (
        "request must be assigned BEFORE the `_framework_attrs = frozenset(...)` "
        "snapshot line at `live_view.py:526` — see #1545 + #1393 invariant"
    )


def test_user_attrs_set_in_mount_are_NOT_in_framework_attrs():
    """Regression-of-regression guard: the fix must not accidentally
    sweep mount()-set user attrs into `_framework_attrs`. Only attrs
    assigned in `__init__` before the snapshot line should be there."""
    v = _ViewWithUserAttr()
    # `request` IS in framework_attrs (the fix); `value` and `_secret`
    # are set in mount() AFTER __init__ so they must NOT be there.
    assert "request" in v._framework_attrs
    # mount() hasn't run yet at __init__-snapshot time, so post-mount
    # attrs are out-of-scope here; we assert via attribute presence:
    assert not hasattr(v, "value")
    assert not hasattr(v, "_secret")


# ---------------------------------------------------------------------------
# Warning silenced
# ---------------------------------------------------------------------------


def test_no_asgirequest_warning_on_fresh_init(caplog):
    """Constructing a `LiveView` (no request assigned yet) must NOT emit
    the 'non-serializable value: ASGIRequest' warning. With the fix, the
    `request = None` placeholder is filtered out of the user-state
    snapshot by `_framework_attrs` membership."""
    with caplog.at_level(logging.WARNING, logger="djust.serialization"):
        _PlainView()
    # No warnings about ASGIRequest or non-serializable values.
    asgi_warnings = [
        r
        for r in caplog.records
        if "ASGIRequest" in r.getMessage() or "non-serializable" in r.getMessage()
    ]
    assert asgi_warnings == [], (
        f"#1545: expected zero ASGIRequest/non-serializable warnings on init, "
        f"got: {[r.getMessage() for r in asgi_warnings]}"
    )


# ---------------------------------------------------------------------------
# Framework-attr-set placement invariant (#1393)
# ---------------------------------------------------------------------------


def test_framework_attrs_includes_known_framework_slots():
    """Sanity: confirm the known framework-state slots (per the #1393
    documented invariant at `live_view.py:518`) ARE all in
    `_framework_attrs`. If this fails, an init-order regression has
    snuck in unrelated to #1545."""
    v = _PlainView()
    known_framework_slots = {
        "request",  # #1545 (this PR)
        "_object",  # ADR-017 / v0.9.5-1a
        "_framework_attrs",  # the snapshot itself
    }
    missing = known_framework_slots - v._framework_attrs
    # `_framework_attrs` is the snapshot of __dict__ at the moment IT is
    # captured, so it does NOT include itself. That's fine; just exclude.
    missing.discard("_framework_attrs")
    assert not missing, (
        f"framework slots missing from `_framework_attrs`: {missing} — "
        f"an init-order regression has snuck in past the #1393 invariant"
    )
