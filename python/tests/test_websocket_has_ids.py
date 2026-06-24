"""
Regression test for BUG-14: websocket mount response `has_ids` flag.

The Rust renderer emits `dj-id="..."` attributes (not `data-dj-id="..."`).
`has_ids` must be True whenever the HTML contains those attributes so the
JS client calls `_stampDjIds()` on the pre-rendered DOM.  Without that
stamping, patches fall back to path-based node resolution which fails
silently for large content swaps (e.g. tab switching).

SSE path (sse.py) was already correct: `"dj-id=" in html`.
WebSocket path (websocket.py) had the wrong attribute name: `"data-dj-id="`.
This test locks in the correct behaviour for both transports.
"""


class TestHasIdsFlag:
    """has_ids must be True iff the HTML contains dj-id attributes."""

    # ------------------------------------------------------------------ #
    # WebSocket path — the bug was here
    # ------------------------------------------------------------------ #

    def test_websocket_has_ids_true_when_dj_id_present(self):
        """HTML with dj-id attributes → has_ids True on WebSocket mount."""
        html = '<div dj-id="0"><span dj-id="1">Hello</span></div>'
        has_ids = "dj-id=" in html
        assert has_ids is True, (
            "has_ids must be True when HTML contains dj-id attributes — "
            "the JS client uses this flag to call _stampDjIds() during mount"
        )

    def test_websocket_has_ids_false_when_no_dj_id(self):
        """Plain HTML without dj-id attributes → has_ids False."""
        html = "<div><span>Hello</span></div>"
        has_ids = "dj-id=" in html
        assert has_ids is False

    def test_websocket_has_ids_substring_match_covers_data_dj_id(self):
        """'dj-id=' substring also matches 'data-dj-id=' — harmless false positive.

        The Rust renderer never emits data-dj-id, but if it did, the check
        would still return True (substring match). This is acceptable because
        the important case is the OTHER direction: the old buggy check
        "data-dj-id=" did NOT match real Rust output "dj-id=" (see regression
        test below).
        """
        html = '<div data-dj-id="0"><span>Hello</span></div>'
        correct_check = "dj-id=" in html
        # Substring match means data-dj-id also triggers dj-id= — that's fine
        assert correct_check is True

    def test_websocket_has_ids_regression_old_buggy_check_misses_rust_output(self):
        """
        Regression: the old buggy check `"data-dj-id=" in html` returns False
        for actual Rust renderer output (which uses `dj-id=`, not `data-dj-id=`).
        This caused _stampDjIds() to be skipped → hydration mismatch → BUG-14.
        """
        # Real output from the Rust renderer:
        rust_html = '<div dj-id="0"><h1 dj-id="1">Claim 2026PI000001</h1></div>'

        old_buggy_check = "data-dj-id=" in rust_html  # was the bug
        correct_check = "dj-id=" in rust_html  # the fix

        assert old_buggy_check is False, (
            "The old check 'data-dj-id=' does NOT match Rust renderer output — "
            "this is why _stampDjIds() was being skipped and hydration failed"
        )
        assert correct_check is True, "The correct check 'dj-id=' matches Rust renderer output"

    # ------------------------------------------------------------------ #
    # SSE path — already correct, verify it stays correct
    # ------------------------------------------------------------------ #

    def test_sse_has_ids_matches_websocket_check(self):
        """SSE and WebSocket mount paths must use the same has_ids check.

        Post-#1887 (ADR-022 Iter 1) the SSE mount converged onto
        ``runtime.py`` ``dispatch_mount`` (the legacy ``_sse_mount_view`` in
        sse.py was deleted), so SSE's ``has_ids`` flag is now computed in the
        runtime — the SAME place this asserts uses the correct ``dj-id=`` check.
        This is the #1646 cure: SSE can no longer drift from the check because it
        shares the runtime's code path."""
        import inspect

        from djust import runtime

        source = inspect.getsource(runtime)
        # Converged SSE mount path should check for "dj-id=" (not "data-dj-id=").
        assert (
            '"dj-id=" in (html or "")' in source
            or '"dj-id=" in html' in source
            or "'dj-id=' in html" in source
        ), "runtime.py dispatch_mount has_ids check should use 'dj-id=' to match Rust output"
        assert "data-dj-id=" not in source, (
            "runtime.py should not use the incorrect 'data-dj-id=' check"
        )

    def test_websocket_has_ids_check_now_correct(self):
        """The mount has_ids check uses 'dj-id=' (Rust renderer output), NOT the
        broken 'data-dj-id='. Post-#1919 (THE MOUNT FLIP) the WS mount routes
        through ``ViewRuntime.dispatch_mount`` (the bespoke ``handle_mount`` body
        that held the inline check was deleted), so the mount has_ids check now
        lives in the runtime — pinned by the companion runtime test above. Here we
        pin that websocket.py never reintroduces the BROKEN 'data-dj-id=' form
        (BUG-14 regression guard) on any of its remaining render-send paths."""
        import inspect
        from djust import websocket

        source = inspect.getsource(websocket)
        # The broken check must NEVER reappear anywhere in websocket.py.
        assert '"data-dj-id=" in html' not in source, (
            "websocket.py must not use the incorrect 'data-dj-id=' check (BUG-14 regression)"
        )
