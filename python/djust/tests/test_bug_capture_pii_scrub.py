"""Tests for the framework-level PII scrub (B7 iter C, #1561).

Two halves:

1. ``LiveView.time_travel_excluded_fields`` — the declarative scrub list that
   ``encode_view_state()`` applies BEFORE any caller-supplied ``scrub``, so the
   safety net does not depend on every call site remembering one.
2. The ``djust.V014`` system check — which warns when a time-travel-enabled
   view has model/form fields that look like PII and are not declared.

V014's whole design problem is false positives: ``email`` is on almost every
user model, and a check that fires on every project teaches people to ignore it
(#1060). The three gates that keep it quiet — ``time_travel_enabled``, token
matching, and field TYPE — each get their own cases, because the dogfood pass
against the demo exercised only the first (the demo has 0 time-travel views, so
V014 is silent there as-is; forcing the flag on produces 10 messages, all true
positives, and the type filter never fires on that data).
"""

from __future__ import annotations

import gc

import pytest
from django import forms
from django.db import models
from django.test import override_settings

from djust.bug_capture import BugCapture, encode_view_state, scrub_fields
from djust.checks.components import (
    _iter_declared_fields,
    _matched_pii_pattern,
    _pii_tokens,
    check_time_travel_pii_fields,
)
from djust.live_view import LiveView


# ---------------------------------------------------------------------------
# Fixtures: a fake time-travel buffer, so these tests exercise the real
# `encode_view_state` path without standing up a WebSocket.
# ---------------------------------------------------------------------------


class _FakeBuffer:
    def __init__(self, entries):
        self._entries = entries

    def __len__(self):
        return len(self._entries)

    def history(self):
        return list(self._entries)


def _view_with_state(before, after, **class_attrs):
    """A LiveView subclass carrying one recorded event.

    Built as a fresh subclass per call rather than by mutating a shared class
    (#1109), so class-level state never leaks between tests.
    """
    cls = type("_PiiScrubView", (LiveView,), dict(class_attrs))
    view = cls()
    view._time_travel_buffer = _FakeBuffer(
        [{"event_name": "submit", "state_before": before, "state_after": after}]
    )
    return view


@pytest.fixture(autouse=True)
def _collect_local_view_classes():
    """Drop locally-defined LiveView subclasses so V014 can't see them later.

    ``_walk_subclasses`` walks ``__subclasses__``, which holds weak references;
    collecting makes the disappearance deterministic rather than GC-timed, so a
    test class declared here never turns up in another test's check run.
    """
    yield
    gc.collect()


# ---------------------------------------------------------------------------
# 1. time_travel_excluded_fields
# ---------------------------------------------------------------------------


class TestExcludedFields:
    @pytest.fixture(autouse=True)
    def _debug_on(self, settings):
        """`BugCapture.encode` is gated off outside DEBUG (iter A)."""
        settings.DEBUG = True

    def test_declared_fields_are_removed_from_both_states(self):
        view = _view_with_state(
            {"password": "hunter2", "count": 0},
            {"password": "hunter2", "count": 1},
            time_travel_excluded_fields=["password"],
        )
        decoded = BugCapture.decode(encode_view_state(view, patches=[]))
        assert "password" not in decoded.state_before
        assert "password" not in decoded.state_after
        assert decoded.state_after["count"] == 1

    def test_removed_names_are_wire_visible(self):
        view = _view_with_state(
            {"ssn": "111-22-3333"},
            {"ssn": "111-22-3333"},
            time_travel_excluded_fields=["ssn"],
        )
        decoded = BugCapture.decode(encode_view_state(view, patches=[]))
        assert decoded.scrubbed_fields == ["ssn"]

    def test_no_declaration_leaves_state_untouched(self):
        """The default must not change iter A behaviour."""
        view = _view_with_state({"password": "hunter2"}, {"password": "hunter2"})
        decoded = BugCapture.decode(encode_view_state(view, patches=[]))
        assert decoded.state_before == {"password": "hunter2"}
        assert decoded.scrubbed_fields == []

    def test_a_declared_field_that_is_absent_is_not_listed(self):
        view = _view_with_state(
            {"count": 0}, {"count": 1}, time_travel_excluded_fields=["password"]
        )
        decoded = BugCapture.decode(encode_view_state(view, patches=[]))
        assert decoded.scrubbed_fields == []

    # -- ordering: the class attribute runs FIRST ------------------------

    def test_the_caller_scrub_sees_state_already_redacted(self):
        """The load-bearing ordering assertion, made from inside the caller's scrub.

        Asserting only on the final output cannot distinguish "excluded ran
        first" from "excluded ran second" — both remove the key. Observing the
        state the caller's callable is HANDED is what pins the order.
        """
        seen = {}

        def _observing_scrub(capture):
            seen["before"] = dict(capture.state_before)
            return capture

        view = _view_with_state(
            {"password": "hunter2", "note": "x"},
            {"password": "hunter2"},
            time_travel_excluded_fields=["password"],
        )
        encode_view_state(view, patches=[], scrub=_observing_scrub)
        assert "password" not in seen["before"], (
            "time_travel_excluded_fields must be applied BEFORE the caller's "
            "scrub, so a caller can add to the redaction but never has to "
            "know about the view's declared safety net"
        )
        assert "note" in seen["before"]

    def test_both_sources_end_up_in_scrubbed_fields(self):
        view = _view_with_state(
            {"password": "hunter2", "ssn": "111", "count": 0},
            {"password": "hunter2", "ssn": "111", "count": 1},
            time_travel_excluded_fields=["password"],
        )
        decoded = BugCapture.decode(encode_view_state(view, patches=[], scrub=scrub_fields("ssn")))
        assert sorted(decoded.scrubbed_fields) == ["password", "ssn"]
        assert decoded.state_after == {"count": 1}

    def test_a_caller_scrub_alone_still_works(self):
        view = _view_with_state({"ssn": "111"}, {"ssn": "111"})
        decoded = BugCapture.decode(encode_view_state(view, patches=[], scrub=scrub_fields("ssn")))
        assert decoded.scrubbed_fields == ["ssn"]

    # -- shapes the attribute takes in the wild --------------------------

    @pytest.mark.parametrize(
        "declared",
        [
            ["password"],
            ("password",),
            {"password"},
            frozenset({"password"}),
        ],
    )
    def test_any_iterable_of_names_is_accepted(self, declared):
        """The contract is an iterable of names, not specifically a list (#1108)."""
        view = _view_with_state(
            {"password": "x"}, {"password": "x"}, time_travel_excluded_fields=declared
        )
        decoded = BugCapture.decode(encode_view_state(view, patches=[]))
        assert decoded.scrubbed_fields == ["password"]

    def test_a_bare_string_is_one_name_not_eight_characters(self):
        """``= "password"`` would otherwise iterate as characters and scrub nothing."""
        view = _view_with_state(
            {"password": "x"}, {"password": "x"}, time_travel_excluded_fields="password"
        )
        decoded = BugCapture.decode(encode_view_state(view, patches=[]))
        assert "password" not in decoded.state_before
        assert decoded.scrubbed_fields == ["password"]

    def test_a_non_iterable_declaration_warns_and_does_not_crash(self, caplog):
        view = _view_with_state(
            {"password": "x"}, {"password": "x"}, time_travel_excluded_fields=object()
        )
        decoded = BugCapture.decode(encode_view_state(view, patches=[]))
        assert decoded.state_before == {"password": "x"}
        assert "not iterable" in caplog.text


class TestNoParallelSink:
    """The WS Share button must go through ``encode_view_state``, not around it.

    If ``handle_bug_capture_share`` built a ``BugCapture`` itself, the class
    attribute would silently not apply on the one path a developer is most
    likely to use — the exact parallel-path drift shape (#1646). Pinned by
    source, so the next person to add a capture surface trips this.
    """

    def test_the_websocket_share_handler_calls_encode_view_state(self):
        import inspect

        from djust import websocket

        source = inspect.getsource(websocket.LiveViewConsumer.handle_bug_capture_share)
        assert "encode_view_state(" in source

    def test_no_capture_surface_constructs_a_bugcapture_from_a_view(self):
        import inspect

        from djust import bug_capture, websocket

        for module in (websocket,):
            assert "BugCapture(" not in inspect.getsource(module), (
                "%s constructs a BugCapture directly, bypassing "
                "encode_view_state() and therefore bypassing "
                "time_travel_excluded_fields (#1646)" % module.__name__
            )
        # The one legitimate construction site.
        assert "BugCapture(" in inspect.getsource(bug_capture)


# ---------------------------------------------------------------------------
# 2. V014 — the system check
# ---------------------------------------------------------------------------


class _PiiScrubModel(models.Model):
    """Model fields covering all three V014 gates."""

    email = models.EmailField()
    email_verified = models.BooleanField(default=False)
    phone_confirmed_at = models.DateTimeField(null=True)
    telephone_pole = models.CharField(max_length=32)
    nickname = models.CharField(max_length=32)

    class Meta:
        app_label = "demo_app"
        managed = False


class _CleanModel(models.Model):
    title = models.CharField(max_length=32)
    created_at = models.DateTimeField(null=True)

    class Meta:
        app_label = "demo_app"
        managed = False


class _PiiForm(forms.Form):
    password = forms.CharField(widget=forms.PasswordInput)
    password_confirm = forms.CharField(widget=forms.PasswordInput)
    nickname = forms.CharField()


class _CleanForm(forms.Form):
    nickname = forms.CharField()
    subscribed = forms.BooleanField(required=False)


def _messages_for(cls):
    """Run V014 and keep only the messages about *cls*."""
    label = "%s.%s" % (cls.__module__, cls.__qualname__)
    return [m for m in check_time_travel_pii_fields(None) if m.msg.startswith(label + ":")]


def _view(name, **attrs):
    return type(name, (LiveView,), dict(attrs))


class TestV014Firing:
    def test_fires_on_a_form_field_named_like_a_password(self):
        cls = _view("V014FormView", time_travel_enabled=True, form_class=_PiiForm)
        messages = _messages_for(cls)
        assert len(messages) == 1
        assert messages[0].id == "djust.V014"
        assert "password" in messages[0].msg
        assert "password_confirm" in messages[0].msg
        assert "nickname" not in messages[0].msg

    def test_fires_on_a_model_field_named_like_an_email(self):
        cls = _view("V014ModelView", time_travel_enabled=True, model=_PiiScrubModel)
        messages = _messages_for(cls)
        assert len(messages) == 1
        assert "email" in messages[0].msg

    def test_finds_a_model_reached_through_a_queryset(self):
        cls = _view(
            "V014QuerysetView",
            time_travel_enabled=True,
            queryset=_PiiScrubModel.objects.all(),
        )
        assert len(_messages_for(cls)) == 1

    def test_the_message_names_the_fields_and_the_fix(self):
        cls = _view("V014HintView", time_travel_enabled=True, form_class=_PiiForm)
        message = _messages_for(cls)[0]
        assert "time_travel_excluded_fields" in message.hint
        assert 'time_travel_excluded_fields = ["password", "password_confirm"]' in (
            message.fix_hint
        )

    def test_it_reports_the_view_source_location(self):
        cls = _view("V014LocationView", time_travel_enabled=True, form_class=_PiiForm)
        message = _messages_for(cls)[0]
        assert message.file_path.endswith("test_bug_capture_pii_scrub.py")


class TestV014StaysQuiet:
    def test_gate_1_time_travel_disabled_is_never_inspected(self):
        """The primary false-positive control: the check doesn't look."""
        cls = _view("V014OffView", form_class=_PiiForm, model=_PiiScrubModel)
        assert _messages_for(cls) == []

    def test_a_declared_field_is_not_reported(self):
        cls = _view(
            "V014DeclaredView",
            time_travel_enabled=True,
            form_class=_PiiForm,
            time_travel_excluded_fields=["password", "password_confirm"],
        )
        assert _messages_for(cls) == []

    def test_a_partially_declared_view_reports_only_the_remainder(self):
        cls = _view(
            "V014PartialView",
            time_travel_enabled=True,
            form_class=_PiiForm,
            time_travel_excluded_fields=["password"],
        )
        message = _messages_for(cls)[0]
        assert "password_confirm" in message.msg
        assert ": password_confirm." in message.msg

    def test_a_view_with_no_pii_shaped_fields_is_silent(self):
        cls = _view(
            "V014CleanView",
            time_travel_enabled=True,
            form_class=_CleanForm,
            model=_CleanModel,
        )
        assert _messages_for(cls) == []

    def test_gate_3_a_boolean_named_email_verified_is_not_pii(self):
        """The type gate — the reason ``email`` on every user model is bearable."""
        cls = _view("V014BoolView", time_travel_enabled=True, model=_PiiScrubModel)
        message = _messages_for(cls)[0]
        assert "email_verified" not in message.msg
        assert "phone_confirmed_at" not in message.msg

    def test_gate_2_telephone_pole_does_not_contain_the_token_phone(self):
        """The token gate — substring matching would have flagged this."""
        cls = _view("V014TokenView", time_travel_enabled=True, model=_PiiScrubModel)
        message = _messages_for(cls)[0]
        assert "telephone_pole" not in message.msg

    def test_the_check_can_be_suppressed(self):
        cls = _view("V014SuppressedView", time_travel_enabled=True, form_class=_PiiForm)
        with override_settings(DJUST_CONFIG={"suppress_checks": ["V014"]}):
            assert _messages_for(cls) == []

    def test_the_demo_project_is_clean_as_shipped(self):
        """Dogfood pin (#1060): a project not using time travel sees nothing.

        The demo declares PII-carrying contact/registration forms — forcing
        ``time_travel_enabled`` on every demo view produces 10 V014 messages,
        all true positives — and none of those views opts into time travel, so
        V014 must be silent. If a future demo view turns time travel on, this
        goes red and somebody decides deliberately.

        The import + precondition below are load-bearing: without them the
        filtered list is empty because the demo classes were never loaded, and
        the assertion would pass no matter what V014 did (#1200).
        """
        from djust.checks.utils import _walk_subclasses

        import demo_app.views.forms_demo  # noqa: F401  (registers the subclasses)

        demo_views = [
            c
            for c in _walk_subclasses(LiveView)
            if (getattr(c, "__module__", "") or "").startswith("demo_app.")
        ]
        assert len(demo_views) >= 5, (
            "precondition: the demo's LiveViews must be imported for this "
            "assertion to be about anything; found %d" % len(demo_views)
        )
        assert any(
            _matched_pii_pattern(name) for c in demo_views for name, _ in _iter_declared_fields(c)
        ), "precondition: the demo must declare at least one PII-shaped field"

        demo = [
            m
            for m in check_time_travel_pii_fields(None)
            if (m.msg.startswith("demo_app.") or m.msg.startswith("djust_forms."))
        ]
        assert demo == [], "V014 must be silent on the demo project as shipped"


class TestTokenMatching:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("password", "password"),
            ("Password1", "password"),
            ("passwords", "password"),
            ("confirm_password", "password"),
            ("passwd", "passwd"),
            ("ssn", "ssn"),
            ("applicant_ssn", "ssn"),
            ("credit_card", "credit_card"),
            ("creditCard", "credit_card"),
            ("credit_card_number", "credit_card"),
            ("tax_id", "tax_id"),
            ("email", "email"),
            ("user_email_address", "email"),
            ("phone", "phone"),
            ("phone_number", "phone"),
            ("home-phone", "phone"),
        ],
    )
    def test_matches(self, name, expected):
        assert _matched_pii_pattern(name) == expected

    @pytest.mark.parametrize(
        "name",
        [
            "telephone_pole",
            "emailer",
            "passwordless",
            "headphones",
            "nickname",
            "credit_limit",
            "card_type",
            "taxonomy",
            "id",
            "",
        ],
    )
    def test_does_not_match(self, name):
        assert _matched_pii_pattern(name) is None

    def test_tokenizer_strips_digits_plurals_and_splits_camel_case(self):
        assert _pii_tokens("creditCard2") == ("credit", "card")
        assert _pii_tokens("USER_EMAILS") == ("user", "email")
        assert _pii_tokens("phone-number") == ("phone", "number")
