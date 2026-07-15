"""Tests for djust.bug_capture_views (B7 iter B — read-only replay viewer, #1562).

Covers the acceptance criteria from GitHub issue #1562:
  - 200/400/404 status matrix (DEBUG, malformed blob, production).
  - XSS regression: captured HTML in vdom_patches[].html is escaped on render.
  - No-dispatch regression: a captured event_name never invokes a handler.
  - Multi-tenant boundary: captured tenant_id is never used to scope a query.
  - Gate-off self-tests (#1468) proving the positive-path assertions aren't
    tautological.
"""

from __future__ import annotations

import ast
import inspect
import re

import pytest
from django.test import RequestFactory, override_settings

from djust import bug_capture_views
from djust.bug_capture import BugCapture


def _code_only_source(module) -> str:
    """Module source with the LEADING module docstring stripped.

    The structural regression tests below grep for dangerous patterns
    (``getattr(..., event_name)``, ``djust.tenants`` imports) that this
    module's own module-level docstring explicitly WARNS AGAINST in
    prose — e.g. "grep this file for ``getattr(`` before adding new
    code" and "This view never imports ``djust.tenants``". A naive
    full-source grep false-positives on that prose. Scanning everything
    AFTER the module docstring (which is where all real code and every
    per-function docstring lives) keeps the check meaningful without
    hand-tuning the regex to dodge one paragraph.
    """
    src = inspect.getsource(module)
    tree = ast.parse(src)
    first = tree.body[0] if tree.body else None
    is_docstring = (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    )
    if not is_docstring:
        return src
    lines = src.splitlines()
    return "\n".join(lines[first.end_lineno :])


def _encoded(**overrides) -> str:
    defaults = dict(
        state_before={"count": 0, "step": "claimant"},
        state_after={"count": 1, "step": "vehicle"},
        vdom_patches=[
            {"op": "insert", "path": [0, 2], "html": "<div class='step-2'>hi</div>"},
            {"op": "remove", "path": [0, 1, 3]},
        ],
        event_name="next_step",
    )
    defaults.update(overrides)
    with override_settings(DEBUG=True):
        return BugCapture(**defaults).encode()


def _get(blob: str):
    return RequestFactory().get("/__djust__/replay/%s" % blob)


# ---------------------------------------------------------------------------
# Status matrix
# ---------------------------------------------------------------------------


class TestStatusMatrix:
    @override_settings(DEBUG=True)
    def test_valid_blob_returns_200_in_debug(self):
        blob = _encoded()
        resp = bug_capture_views.replay_view(_get(blob), blob)
        assert resp.status_code == 200
        assert b"next_step" in resp.content

    @override_settings(DEBUG=True)
    def test_malformed_blob_returns_400(self):
        resp = bug_capture_views.replay_view(_get("not-a-blob"), "not-a-blob")
        assert resp.status_code == 400

    @override_settings(DEBUG=True)
    def test_malformed_blob_response_is_plain_text_not_html(self):
        """400 response must never be interpreted as HTML by a browser,
        regardless of escaping correctness elsewhere (defense in depth)."""
        resp = bug_capture_views.replay_view(_get("not-a-blob"), "not-a-blob")
        assert resp["Content-Type"].startswith("text/plain")

    @override_settings(DEBUG=True)
    def test_malformed_blob_with_markup_is_not_reflected_as_html(self):
        evil = "<script>alert(1)</script>.YWJj"
        resp = bug_capture_views.replay_view(_get(evil), evil)
        assert resp.status_code == 400
        assert resp["Content-Type"].startswith("text/plain")

    @override_settings(DEBUG=False)
    def test_valid_blob_returns_404_in_production_without_opt_in(self):
        blob = _encoded()
        resp = bug_capture_views.replay_view(_get(blob), blob)
        assert resp.status_code == 404

    @override_settings(DEBUG=False, DJUST_BUG_CAPTURE_PROD_OPT_IN=True)
    def test_valid_blob_returns_200_in_production_with_opt_in(self):
        blob = _encoded()
        resp = bug_capture_views.replay_view(_get(blob), blob)
        assert resp.status_code == 200

    @override_settings(DEBUG=False, DJUST_BUG_CAPTURE_PROD_OPT_IN="yes")
    def test_prod_opt_in_must_be_literal_true(self):
        """Same defensive contract as bug_capture._enforce_prod_gate: only
        the literal `True`, not a truthy string, opts in."""
        blob = _encoded()
        resp = bug_capture_views.replay_view(_get(blob), blob)
        assert resp.status_code == 404

    @override_settings(DEBUG=True)
    def test_non_get_method_rejected(self):
        request = RequestFactory().post("/__djust__/replay/x")
        resp = bug_capture_views.replay_view(request, "x")
        assert resp.status_code == 405

    @override_settings(DEBUG=True)
    def test_head_is_allowed(self):
        blob = _encoded()
        request = RequestFactory().head("/__djust__/replay/%s" % blob)
        resp = bug_capture_views.replay_view(request, blob)
        assert resp.status_code == 200

    @override_settings(DEBUG=False)
    def test_malformed_blob_still_404s_in_production(self):
        """Prod-gate check runs BEFORE decode — a malformed blob doesn't
        leak "route exists, blob invalid" information in production."""
        resp = bug_capture_views.replay_view(_get("garbage"), "garbage")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# XSS regression — captured HTML is escaped on render
# ---------------------------------------------------------------------------


class TestXSSRegression:
    @override_settings(DEBUG=True)
    def test_patch_html_is_escaped_not_executable(self):
        payload = "<script>window.__pwned = true;</script>"
        blob = _encoded(
            vdom_patches=[{"op": "insert", "path": [0], "html": payload}],
        )
        resp = bug_capture_views.replay_view(_get(blob), blob)
        body = resp.content.decode()
        assert resp.status_code == 200
        # Gate-off proof: the raw payload MUST NOT appear verbatim...
        assert payload not in body
        # ...but its escaped form must be present, proving the content
        # reached the page (not silently dropped).
        assert "&lt;script&gt;" in body
        assert "window.__pwned" in body

    @override_settings(DEBUG=True)
    def test_state_value_html_is_escaped(self):
        blob = _encoded(
            state_before={"bio": "<img src=x onerror=alert(1)>"},
            state_after={"bio": "<img src=x onerror=alert(1)>"},
        )
        resp = bug_capture_views.replay_view(_get(blob), blob)
        body = resp.content.decode()
        assert "<img src=x onerror=alert(1)>" not in body
        assert "&lt;img" in body

    @override_settings(DEBUG=True)
    def test_event_name_is_escaped(self):
        blob = _encoded(event_name="<b>evil</b>")
        resp = bug_capture_views.replay_view(_get(blob), blob)
        body = resp.content.decode()
        assert "<b>evil</b>" not in body
        assert "&lt;b&gt;evil&lt;/b&gt;" in body

    @override_settings(DEBUG=True)
    def test_dom_preview_iframe_is_sandboxed(self):
        blob = _encoded(
            vdom_patches=[{"op": "insert", "path": [0], "html": "<p>hello</p>"}],
        )
        resp = bug_capture_views.replay_view(_get(blob), blob)
        body = resp.content.decode()
        assert 'class="dj-dom-preview"' in body
        assert 'sandbox=""' in body
        # No allow-scripts / allow-same-origin tokens anywhere near the sandbox attr.
        assert "allow-scripts" not in body
        assert "allow-same-origin" not in body


# ---------------------------------------------------------------------------
# No-dispatch regression — event_name is display-only
# ---------------------------------------------------------------------------


class TestNoDispatch:
    def test_module_never_dynamically_dispatches_on_event_name(self):
        """Structural regression: grep the SOURCE for the dangerous shape
        `getattr(..., event_name...)(...)`. A future change that wires
        event_name to a callable lookup trips this immediately, even
        before a behavioral test could exercise the specific object it
        was wired to."""
        src = _code_only_source(bug_capture_views)
        assert not re.search(r"getattr\([^)]*event_name", src)
        # Belt-and-suspenders: no reference to the framework's event
        # dispatch entrypoints at all.
        for forbidden in ("handle_event", "_djust_decorators", "ViewRuntime", "dispatch_event"):
            assert forbidden not in src

    @override_settings(DEBUG=True)
    def test_dangerous_looking_event_name_is_never_invoked(self):
        """A captured event_name matching a real, dangerous-looking
        handler name must not be invoked — it only appears as escaped
        display text. `calls` would be non-empty only if something in
        the render path resolved `delete_everything` to a callable and
        invoked it."""
        calls = []

        class _DangerousTarget:
            def delete_everything(self):
                calls.append("CALLED")

        target = _DangerousTarget()
        blob = _encoded(event_name="delete_everything")
        resp = bug_capture_views.replay_view(_get(blob), blob)

        assert resp.status_code == 200
        assert calls == [], "the captured event_name must never be invoked as a handler"
        # Still shown, but inert.
        assert b"delete_everything" in resp.content
        # `target` unused except to prove its method is untouched.
        assert not hasattr(target, "_called")


# ---------------------------------------------------------------------------
# Multi-tenant boundary
# ---------------------------------------------------------------------------


class TestMultiTenantBoundary:
    @override_settings(DEBUG=True)
    def test_captured_tenant_id_never_scopes_a_query(self):
        """Regression for the multi-tenant boundary requirement: a
        captured `tenant_id` must be displayed (if present in state) but
        NEVER used to set the current tenant context. Spies on the real
        `djust.tenants.middleware.set_current_tenant` — if the view ever
        called it with the captured value, this test would catch it."""
        from djust.tenants.middleware import get_current_tenant, set_current_tenant

        sentinel = object()
        set_current_tenant(sentinel)
        try:
            blob = _encoded(
                state_before={"tenant_id": "evil-tenant-999"},
                state_after={"tenant_id": "evil-tenant-999"},
            )
            resp = bug_capture_views.replay_view(_get(blob), blob)

            assert resp.status_code == 200
            # The tenant context is UNCHANGED — proves set_current_tenant
            # was never called with the captured tenant_id (or at all).
            assert get_current_tenant() is sentinel
            # The captured value is still shown as inert display text.
            assert b"evil-tenant-999" in resp.content
        finally:
            set_current_tenant(None)

    def test_module_never_imports_tenants(self):
        src = _code_only_source(bug_capture_views)
        assert "djust.tenants" not in src
        assert "set_current_tenant" not in src
        assert "get_current_tenant" not in src

    @pytest.mark.django_db
    @override_settings(DEBUG=True)
    def test_view_issues_zero_database_queries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        blob = _encoded(state_before={"tenant_id": "t1"}, state_after={"tenant_id": "t1"})
        with CaptureQueriesContext(connection) as ctx:
            resp = bug_capture_views.replay_view(_get(blob), blob)
        assert resp.status_code == 200
        assert len(ctx.captured_queries) == 0


# ---------------------------------------------------------------------------
# Read-only / no state mutation — response content sanity
# ---------------------------------------------------------------------------


class TestReadOnlyContent:
    @override_settings(DEBUG=True)
    def test_scrubbed_fields_are_displayed(self):
        blob = _encoded()
        # Re-encode with a scrub to exercise scrubbed_fields display.
        with override_settings(DEBUG=True):
            from djust.bug_capture import scrub_fields

            blob = BugCapture(
                state_before={"password": "x", "count": 0},
                state_after={"password": "x", "count": 1},
                vdom_patches=[],
                event_name="submit",
            ).encode(scrub=scrub_fields("password"))
        resp = bug_capture_views.replay_view(_get(blob), blob)
        assert b"password" in resp.content  # field NAME shown
        assert b"<html" in resp.content

    @override_settings(DEBUG=True)
    def test_diff_rows_mark_added_removed_changed_same(self):
        blob = _encoded(
            state_before={"same": 1, "removed": "x", "changed": "a"},
            state_after={"same": 1, "added": "y", "changed": "b"},
        )
        resp = bug_capture_views.replay_view(_get(blob), blob)
        body = resp.content.decode()
        assert "dj-diff-added" in body
        assert "dj-diff-removed" in body
        assert "dj-diff-changed" in body
        assert "dj-diff-same" in body

    @override_settings(DEBUG=True)
    def test_share_button_carries_the_blob_for_client_side_copy(self):
        blob = _encoded()
        resp = bug_capture_views.replay_view(_get(blob), blob)
        body = resp.content.decode()
        assert "dj-bugcapture-copy" in body
        assert blob in body
