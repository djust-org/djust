"""Tests for djust.bug_capture (B7 iter A — sharable bug-capture artifacts).

Covers the round-trip, the scrub hook, the DEBUG-gate, untrusted-input
rejection, and the `encode_view_state` integration with the existing
time-travel buffer + the framework's vdom-patch attribute conventions.
"""

from __future__ import annotations

import base64
import json
import re

import pytest
from django.test import override_settings

from djust.bug_capture import (
    WIRE_VERSION,
    BugCapture,
    encode_view_state,
    scrub_fields,
)


def _capture(**overrides) -> BugCapture:
    defaults = dict(
        state_before={"count": 0, "step": "claimant"},
        state_after={"count": 1, "step": "vehicle"},
        vdom_patches=[
            {"op": "insert", "path": [0, 2], "html": "<div class='step-2'>...</div>"},
            {"op": "remove", "path": [0, 1, 3]},
        ],
        event_name="next_step",
    )
    defaults.update(overrides)
    return BugCapture(**defaults)


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    @override_settings(DEBUG=True)
    def test_encoded_has_versioned_prefix(self):
        encoded = _capture().encode()
        assert encoded.startswith(f"{WIRE_VERSION}.")

    @override_settings(DEBUG=True)
    def test_round_trip_preserves_all_fields(self):
        original = _capture()
        decoded = BugCapture.decode(original.encode())
        assert decoded.state_before == original.state_before
        assert decoded.state_after == original.state_after
        assert decoded.vdom_patches == original.vdom_patches
        assert decoded.event_name == original.event_name
        assert decoded.scrubbed_fields == []

    @override_settings(DEBUG=True)
    def test_round_trip_with_nested_state(self):
        original = _capture(
            state_before={"nested": {"deep": [1, 2, {"k": "v"}]}, "arr": []},
            state_after={"nested": {"deep": [], "new_key": True}},
        )
        decoded = BugCapture.decode(original.encode())
        assert decoded.state_before == original.state_before
        assert decoded.state_after == original.state_after

    @override_settings(DEBUG=True)
    def test_encoded_uses_url_safe_characters_only(self):
        encoded = _capture().encode()
        # urlsafe-base64 produces [A-Za-z0-9_-]; literal dots separate version.
        # No '+', '/', or '=' should appear.
        assert re.fullmatch(r"[A-Za-z0-9._-]+", encoded), (
            f"encoded form must be URL-safe, got: {encoded!r}"
        )

    @override_settings(DEBUG=True)
    def test_empty_event_name_round_trips(self):
        decoded = BugCapture.decode(_capture(event_name="").encode())
        assert decoded.event_name == ""

    @override_settings(DEBUG=True)
    def test_empty_state_dicts_round_trip(self):
        decoded = BugCapture.decode(
            _capture(state_before={}, state_after={}, vdom_patches=[]).encode()
        )
        assert decoded.state_before == {}
        assert decoded.state_after == {}
        assert decoded.vdom_patches == []


# ---------------------------------------------------------------------------
# scrub hook
# ---------------------------------------------------------------------------


class TestScrub:
    @override_settings(DEBUG=True)
    def test_scrub_removes_named_fields_from_both_states(self):
        original = _capture(
            state_before={"username": "alice", "password": "secret", "count": 0},
            state_after={"username": "alice", "password": "secret", "count": 1},
        )
        encoded = original.encode(scrub=scrub_fields("password"))
        decoded = BugCapture.decode(encoded)
        assert "password" not in decoded.state_before
        assert "password" not in decoded.state_after
        assert decoded.state_before["count"] == 0
        assert decoded.state_after["count"] == 1

    @override_settings(DEBUG=True)
    def test_scrubbed_fields_recorded_on_wire(self):
        encoded = _capture(
            state_before={"ssn": "111-22-3333"},
            state_after={"ssn": "111-22-3333"},
        ).encode(scrub=scrub_fields("ssn"))
        decoded = BugCapture.decode(encoded)
        assert "ssn" in decoded.scrubbed_fields

    @override_settings(DEBUG=True)
    def test_scrub_silently_ignores_absent_fields(self):
        encoded = _capture(
            state_before={"count": 0},
            state_after={"count": 1},
        ).encode(scrub=scrub_fields("password", "ssn"))
        decoded = BugCapture.decode(encoded)
        # Neither field existed; neither recorded as scrubbed.
        assert decoded.scrubbed_fields == []

    @override_settings(DEBUG=True)
    def test_scrub_records_field_present_in_only_one_state(self):
        encoded = _capture(
            state_before={"draft_token": "abc"},
            state_after={"count": 1},
        ).encode(scrub=scrub_fields("draft_token"))
        decoded = BugCapture.decode(encoded)
        assert "draft_token" in decoded.scrubbed_fields

    @override_settings(DEBUG=True)
    def test_scrub_does_not_mutate_original(self):
        original = _capture(
            state_before={"secret": "x"},
            state_after={"secret": "y"},
        )
        original.encode(scrub=scrub_fields("secret"))
        # Original untouched — important for callers that might encode the
        # same capture multiple times with different scrub policies.
        assert original.state_before == {"secret": "x"}
        assert original.state_after == {"secret": "y"}

    @override_settings(DEBUG=True)
    def test_custom_scrub_callable(self):
        """A user-supplied scrub callable can do arbitrary redaction."""

        def redact_emails(cap: BugCapture) -> BugCapture:
            def mask(d):
                return {
                    k: ("<redacted>" if isinstance(v, str) and "@" in v else v)
                    for k, v in d.items()
                }

            return BugCapture(
                state_before=mask(cap.state_before),
                state_after=mask(cap.state_after),
                vdom_patches=cap.vdom_patches,
                event_name=cap.event_name,
                scrubbed_fields=cap.scrubbed_fields + ["<email-pattern>"],
            )

        encoded = _capture(
            state_before={"contact": "alice@example.com", "count": 0},
            state_after={"contact": "alice@example.com", "count": 1},
        ).encode(scrub=redact_emails)
        decoded = BugCapture.decode(encoded)
        assert decoded.state_before["contact"] == "<redacted>"
        assert "<email-pattern>" in decoded.scrubbed_fields


# ---------------------------------------------------------------------------
# DEBUG gate
# ---------------------------------------------------------------------------


class TestDebugGate:
    @override_settings(DEBUG=False)
    def test_encode_refuses_when_debug_is_false(self):
        with pytest.raises(RuntimeError, match="disabled in production"):
            _capture().encode()

    @override_settings(DEBUG=False, DJUST_BUG_CAPTURE_PROD_OPT_IN=True)
    def test_encode_allowed_when_prod_opt_in_is_true(self):
        encoded = _capture().encode()
        assert encoded.startswith(f"{WIRE_VERSION}.")

    @override_settings(DEBUG=False, DJUST_BUG_CAPTURE_PROD_OPT_IN="yes")
    def test_prod_opt_in_must_be_literal_true(self):
        """Defensive: only `is True` opts in, not just truthy.

        Prevents accidental enable-via-typo (e.g., setting the value to
        a non-empty string for a config-loader workaround)."""
        with pytest.raises(RuntimeError, match="disabled in production"):
            _capture().encode()

    @override_settings(DEBUG=False)
    def test_decode_works_regardless_of_debug(self):
        """Decode is read-only; the prod-gate is on encode, not decode.
        A maintainer can paste a capture URL into a non-DEBUG repl and
        inspect it."""
        # Build a valid encoded blob via DEBUG=True override...
        with override_settings(DEBUG=True):
            encoded = _capture().encode()
        # ...then decode it with DEBUG=False — should succeed.
        decoded = BugCapture.decode(encoded)
        assert decoded.event_name == "next_step"


# ---------------------------------------------------------------------------
# Untrusted-input rejection
# ---------------------------------------------------------------------------


class TestUntrustedInput:
    def test_rejects_non_string(self):
        with pytest.raises(ValueError, match="must be a string"):
            BugCapture.decode(b"djbug1.xxx")

    def test_rejects_missing_version_prefix(self):
        with pytest.raises(ValueError, match="missing version prefix"):
            BugCapture.decode("no-dot-here")

    def test_rejects_unknown_outer_version(self):
        with pytest.raises(ValueError, match="unsupported BugCapture version"):
            BugCapture.decode("djbug2.dGVzdA")

    def test_rejects_malformed_base64(self):
        with pytest.raises(ValueError, match="malformed base64"):
            BugCapture.decode("djbug1.!!!not-valid-base64!!!")

    def test_rejects_base64_that_decodes_to_non_json(self):
        garbage = base64.urlsafe_b64encode(b"not json").rstrip(b"=").decode()
        with pytest.raises(ValueError, match="malformed JSON"):
            BugCapture.decode(f"djbug1.{garbage}")

    def test_rejects_base64_that_decodes_to_non_object(self):
        not_dict = base64.urlsafe_b64encode(b"[1,2,3]").rstrip(b"=").decode()
        with pytest.raises(ValueError, match="must be a JSON object"):
            BugCapture.decode(f"djbug1.{not_dict}")

    def test_rejects_inner_version_mismatch(self):
        """Outer 'djbug1.' prefix gates the format, but the JSON payload
        also carries a 'v' field — these must agree."""
        evil = {"v": "djbug2", "state_before": {}, "state_after": {}, "vdom_patches": []}
        b64 = base64.urlsafe_b64encode(json.dumps(evil).encode()).rstrip(b"=").decode()
        with pytest.raises(ValueError, match="inner version mismatch"):
            BugCapture.decode(f"djbug1.{b64}")

    def test_rejects_missing_required_fields(self):
        partial = {"v": WIRE_VERSION, "state_before": {}}  # missing state_after + vdom_patches
        b64 = base64.urlsafe_b64encode(json.dumps(partial).encode()).rstrip(b"=").decode()
        with pytest.raises(ValueError, match="missing required fields"):
            BugCapture.decode(f"djbug1.{b64}")

    def test_rejects_wrong_field_types(self):
        bad = {
            "v": WIRE_VERSION,
            "state_before": [],  # should be dict
            "state_after": {},
            "vdom_patches": [],
        }
        b64 = base64.urlsafe_b64encode(json.dumps(bad).encode()).rstrip(b"=").decode()
        with pytest.raises(ValueError, match="must be a JSON object"):
            BugCapture.decode(f"djbug1.{b64}")

    def test_wire_format_is_not_pickle(self):
        """If a future maintainer reaches for pickle 'for efficiency',
        this assertion fails — encoded bytes must be plain JSON post-base64."""
        with override_settings(DEBUG=True):
            encoded = _capture().encode()
        b64 = encoded.split(".", 1)[1]
        pad = "=" * ((4 - len(b64) % 4) % 4)
        raw = base64.urlsafe_b64decode(b64 + pad)
        assert not raw.startswith(b"\x80"), "BugCapture wire format must not be pickle"
        # Affirmative: parses as JSON.
        json.loads(raw)


# ---------------------------------------------------------------------------
# encode_view_state integration with view conventions
# ---------------------------------------------------------------------------


class _StubBuffer:
    """Minimal stand-in for `TimeTravelBuffer` exposing the bits encode_view_state reads."""

    def __init__(self, entries):
        self._entries = entries

    def __len__(self):
        return len(self._entries)

    def history(self):
        return list(self._entries)


class _StubView:
    """Test view exposing the framework conventions encode_view_state pulls from."""

    def __init__(self, entries, patches):
        self._time_travel_buffer = _StubBuffer(entries)
        self._last_vdom_patches = patches


class TestEncodeViewState:
    @override_settings(DEBUG=True)
    def test_picks_latest_snapshot_by_default(self):
        entries = [
            {
                "event_name": "earlier",
                "state_before": {"x": 1},
                "state_after": {"x": 2},
            },
            {
                "event_name": "latest",
                "state_before": {"x": 10},
                "state_after": {"x": 20},
            },
        ]
        view = _StubView(entries, '[{"op":"replace","path":[0],"html":"<div/>"}]')
        encoded = encode_view_state(view)
        decoded = BugCapture.decode(encoded)
        assert decoded.event_name == "latest"
        assert decoded.state_before == {"x": 10}
        assert decoded.state_after == {"x": 20}
        assert decoded.vdom_patches == [{"op": "replace", "path": [0], "html": "<div/>"}]

    @override_settings(DEBUG=True)
    def test_picks_specific_event_when_named(self):
        entries = [
            {
                "event_name": "next_step",
                "state_before": {"step": "claimant"},
                "state_after": {"step": "vehicle"},
            },
            {
                "event_name": "unrelated_click",
                "state_before": {"step": "vehicle"},
                "state_after": {"step": "vehicle", "highlighted": True},
            },
        ]
        view = _StubView(entries, "[]")
        decoded = BugCapture.decode(encode_view_state(view, event_name="next_step"))
        assert decoded.event_name == "next_step"

    @override_settings(DEBUG=True)
    def test_raises_when_event_name_not_in_buffer(self):
        view = _StubView(
            [{"event_name": "click", "state_before": {}, "state_after": {}}],
            "[]",
        )
        with pytest.raises(ValueError, match="no recorded event named"):
            encode_view_state(view, event_name="never_happened")

    @override_settings(DEBUG=True)
    def test_raises_when_view_has_no_buffer(self):
        class _NoBuffer:
            pass

        with pytest.raises(ValueError, match="no time-travel buffer"):
            encode_view_state(_NoBuffer())

    @override_settings(DEBUG=True)
    def test_raises_when_buffer_is_empty(self):
        view = _StubView([], "[]")
        with pytest.raises(ValueError, match="no recorded events"):
            encode_view_state(view)

    @override_settings(DEBUG=True)
    def test_raises_when_view_has_no_patches(self):
        class _NoPatches:
            def __init__(self):
                self._time_travel_buffer = _StubBuffer(
                    [{"event_name": "e", "state_before": {}, "state_after": {}}]
                )

        with pytest.raises(ValueError, match="no recent VDOM patches"):
            encode_view_state(_NoPatches())

    @override_settings(DEBUG=True)
    def test_patches_as_list_works_too(self):
        """Accepts both JSON string and pre-decoded list — useful for tests
        and for any internal caller that already has the patches parsed."""
        view = _StubView(
            [{"event_name": "e", "state_before": {}, "state_after": {}}],
            [{"op": "insert", "path": [0], "html": "<p/>"}],
        )
        decoded = BugCapture.decode(encode_view_state(view))
        assert decoded.vdom_patches == [{"op": "insert", "path": [0], "html": "<p/>"}]

    @override_settings(DEBUG=True)
    def test_scrub_forwarded_to_encode(self):
        view = _StubView(
            [
                {
                    "event_name": "submit",
                    "state_before": {"password": "secret", "form_id": "x"},
                    "state_after": {"password": "secret", "form_id": "x"},
                }
            ],
            "[]",
        )
        decoded = BugCapture.decode(encode_view_state(view, scrub=scrub_fields("password")))
        assert "password" not in decoded.state_before
        assert "password" in decoded.scrubbed_fields
