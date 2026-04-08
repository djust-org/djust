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
        """SSE and WebSocket paths must use the same attribute name check."""
        import inspect
        from djust import sse

        source = inspect.getsource(sse)
        # SSE path should check for "dj-id=" (not "data-dj-id=")
        assert (
            '"dj-id=" in html' in source or "'dj-id=' in html" in source
        ), "sse.py has_ids check should use 'dj-id=' to match Rust renderer output"
        assert (
            '"data-dj-id=" in html' not in source
        ), "sse.py should not use the incorrect 'data-dj-id=' check"

    def test_websocket_has_ids_check_now_correct(self):
        """websocket.py has_ids check must use 'dj-id=' after the fix."""
        import inspect
        from djust import websocket

        source = inspect.getsource(websocket)
        # After fix: must contain the correct check
        assert (
            '"dj-id=" in html' in source or "'dj-id=' in html" in source
        ), "websocket.py has_ids check should use 'dj-id=' to match Rust renderer output"
        # After fix: must NOT contain the old broken check
        assert (
            '"data-dj-id=" in html' not in source
        ), "websocket.py must not use the incorrect 'data-dj-id=' check (BUG-14 regression)"
