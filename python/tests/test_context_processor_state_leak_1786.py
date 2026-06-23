"""Regression for #1786: context-processor outputs + the request object must
NOT leak into persisted / change-tracked LiveView state.

Symptom (production djust.org ``/insights/``, djust 1.0.4): a ``LiveView`` whose
``mount()`` assigns only JSON-serializable scalars (``self.days`` int,
``self.show_raw`` bool) still emitted, on EVERY render/event, a flood of
``serialization`` warnings naming values the view never assigned to ``self``::

    LiveView state contains non-serializable value: ASGIRequest ...
    LiveView state contains non-serializable value: PermWrapper ...
    LiveView state contains non-serializable value: FallbackStorage ...
    LiveView state contains non-serializable value: SimpleLazyObject / UserLazyObject ...

plus ``dict '_prev_context_refs' has N keys — key fingerprint truncated``.

Root cause: ``_sync_state_to_rust`` folds the request + the standard
context-processor outputs (``request`` / ``user`` / ``perms`` / ``messages``)
into the render context via ``_apply_context_processors``. On the first render
(and on every event, since those values get a fresh ``id()`` each cycle) they
flowed into:

  (a) ``normalize_django_value`` — the non-serializable fallback at
      ``serialization.py`` logs a warning per value, and
  (b) ``self._prev_context_refs`` — the change-detection fingerprint, bloating
      it and marking those values "changed" on every render.

Fix (#1786): ``_apply_context_processors`` records the keys it added on
``self._context_processor_keys``; ``_sync_state_to_rust`` excludes those keys
(plus ``request``) from the change-detection fingerprint, and skips the
non-serializable ones from the ``update_state`` / ``normalize_django_value``
warning path. They still reach the Rust renderer via the raw-value sidecar, so
``{{ user }}`` / ``{% csrf_token %}`` keep rendering (#1779 contract preserved).
"""

import logging
import os
import tempfile

import pytest
from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings

try:
    from djust import LiveView, RustLiveView
except ImportError:  # pragma: no cover
    LiveView = None
    RustLiveView = None

from djust.mixins.context import _context_processors_cache, _resolved_processors_cache
from djust.utils import clear_template_dirs_cache

pytestmark = pytest.mark.skipif(
    LiveView is None or RustLiveView is None,
    reason="djust.LiveView / RustLiveView not available",
)

# The exact non-serializable context-processor / request types named in #1786.
_LEAK_TYPE_NAMES = (
    "ASGIRequest",
    "WSGIRequest",
    "PermWrapper",
    "FallbackStorage",
    "SimpleLazyObject",
    "UserLazyObject",
)


class _AnalyticsInsightsView(LiveView):
    """Mirrors the production view: assigns ONLY JSON-serializable scalars."""

    template_name = "_1786_insights.html"

    def mount(self, request, **kwargs):
        self.days = 30
        self.show_raw = False

    def set_days(self, value=7, **kwargs):
        self.days = int(value)


@pytest.fixture
def template_dir():
    # dj-root template uses a request-bound tag ({% csrf_token %}) AND a
    # context-processor var ({{ user.username }}) so we also confirm the
    # #1779 contract is preserved by the fix.
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, "_1786_insights.html"), "w") as f:
        f.write(
            "<div dj-root>"
            "{% csrf_token %}"
            "<span>u={{ user.username }}|days={{ days }}|raw={{ show_raw }}</span>"
            "</div>"
        )
    yield tmp


def _templates_setting(template_dir):
    # The standard request/auth/messages processors — the exact set that
    # contributes the non-serializable request-scoped values in #1786.
    return [
        {
            "BACKEND": "djust.template_backend.DjustTemplateBackend",
            "DIRS": [template_dir],
            "APP_DIRS": False,
            "OPTIONS": {
                "context_processors": [
                    "django.template.context_processors.request",
                    "django.contrib.auth.context_processors.auth",
                    "django.contrib.messages.context_processors.messages",
                ],
            },
        },
    ]


def _make_request(user):
    rf = RequestFactory().get("/insights/")
    rf.user = user
    rf.session = SessionStore()
    rf.session.create()
    # FallbackStorage is the messages backend named in #1786.
    rf._messages = FallbackStorage(rf)
    return rf


def _make_view(request):
    clear_template_dirs_cache()
    _resolved_processors_cache.clear()
    _context_processors_cache.clear()
    view = _AnalyticsInsightsView()
    view.setup(request)
    view._initialize_temporary_assigns()
    view.mount(request)
    view.request = request
    return view


@pytest.fixture
def captured_warnings():
    """Capture WARNING records on every logger the production warning could
    surface under (the serialization fallback + the snapshot fingerprint)."""
    records = []

    class _H(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = _H()
    loggers = [
        logging.getLogger(name)
        for name in ("djust.serialization", "serialization", "djust", "djust.websocket")
    ]
    prev_levels = {}
    for lg in loggers:
        prev_levels[lg] = lg.level
        lg.addHandler(handler)
        lg.setLevel(logging.WARNING)
    try:
        yield records
    finally:
        for lg in loggers:
            lg.removeHandler(handler)
            lg.setLevel(prev_levels[lg])


@pytest.mark.django_db
class TestContextProcessorStateLeak1786:
    def test_no_non_serializable_warnings_on_render(self, template_dir, captured_warnings):
        """Initial + event re-render must emit ZERO 'non-serializable value'
        warnings naming the request / context-processor types (#1786)."""
        user = User.objects.create_user("alice", password="x")
        request = _make_request(user)
        with override_settings(TEMPLATES=_templates_setting(template_dir)):
            view = _make_view(request)
            view.render(request=request)  # initial render
            view.set_days(7)
            view.render(request=request)  # event re-render

        leaks = [
            m
            for m in captured_warnings
            if "non-serializable value" in m and any(t in m for t in _LEAK_TYPE_NAMES)
        ]
        assert not leaks, (
            "request / context-processor outputs leaked into serialized state "
            f"(#1786). Warnings emitted: {leaks!r}"
        )

    def test_request_scoped_keys_kept_out_of_snapshot_fingerprint(self, template_dir):
        """The change-detection snapshot (``_snapshot_assigns``) is the source of
        the ``dict '_prev_context_refs' has N keys — fingerprint truncated``
        warning. ``_prev_context_refs`` is an instance attr, so once it carries
        the request + context-processor outputs it both balloons the key count
        and shows as "changed" every event. Assert it does NOT contain those
        request-scoped values (#1786)."""
        from djust.websocket import _snapshot_assigns

        user = User.objects.create_user("bob", password="x")
        request = _make_request(user)
        with override_settings(TEMPLATES=_templates_setting(template_dir)):
            view = _make_view(request)
            view.render(request=request)

            snapshot = _snapshot_assigns(view)

        # ``_prev_context_refs`` is captured in the snapshot (it's a non-
        # framework instance attr); its VALUE is the fingerprint dict that
        # would overflow. Read it directly off the view.
        prev_refs = getattr(view, "_prev_context_refs", {})
        for leaked in ("request", "user", "perms", "messages"):
            assert leaked not in prev_refs, (
                f"{leaked!r} leaked into _prev_context_refs fingerprint "
                f"(would inflate the key count / churn every event)"
            )
        # The snapshot itself must not carry the request as a tracked public attr.
        assert "request" not in snapshot, (
            f"request leaked into change-detection snapshot: {sorted(snapshot)}"
        )

    def test_prev_context_refs_excludes_request_scoped_keys(self, template_dir):
        """The change-detection fingerprint must not contain the request or the
        standard context-processor outputs (#1786)."""
        user = User.objects.create_user("carol", password="x")
        request = _make_request(user)
        with override_settings(TEMPLATES=_templates_setting(template_dir)):
            view = _make_view(request)
            view.render(request=request)

        prev_refs = getattr(view, "_prev_context_refs", {})
        for leaked in ("request", "user", "perms", "messages", "DEFAULT_MESSAGE_LEVELS"):
            assert leaked not in prev_refs, (
                f"request-scoped key {leaked!r} leaked into _prev_context_refs "
                f"(keys: {sorted(prev_refs)})"
            )
        # Genuine user state must still be tracked.
        assert "days" in prev_refs
        assert "show_raw" in prev_refs

    def test_serialized_rust_state_excludes_request_objects(self, template_dir):
        """The JSON state pushed to the Rust backend (``update_state``) must not
        carry the request or the non-serializable processor objects.

        We inspect ``_rust_view.get_state()`` after a render — the canonical
        serialized state the in-memory / Redis state backend persists.
        """
        user = User.objects.create_user("dave", password="x")
        request = _make_request(user)
        with override_settings(TEMPLATES=_templates_setting(template_dir)):
            view = _make_view(request)
            view.render(request=request)

        rust_state = view._rust_view.get_state()
        # Request-scoped processor outputs must not be present in serialized state.
        for leaked in ("request", "perms", "messages"):
            assert leaked not in rust_state, (
                f"{leaked!r} leaked into serialized Rust state: keys={sorted(rust_state)}"
            )
        # No stringified request / processor objects should appear as values.
        for value in rust_state.values():
            if isinstance(value, str):
                assert not any(t in value for t in _LEAK_TYPE_NAMES), (
                    f"stringified non-serializable object leaked into state: {value!r}"
                )
        # Genuine user state survives.
        assert rust_state.get("days") == 7 or rust_state.get("days") == 30
        assert "show_raw" in rust_state

    def test_user_and_csrf_still_render(self, template_dir):
        """#1779 contract: excluding request-scoped values from change-tracking
        must NOT blank ``{{ user }}`` / ``{% csrf_token %}`` — they reach the
        Rust template via the raw-value sidecar."""
        user = User.objects.create_user("erin", password="x")
        request = _make_request(user)
        with override_settings(TEMPLATES=_templates_setting(template_dir)):
            view = _make_view(request)
            html1 = view.render(request=request)
            view.set_days(7)
            html2 = view.render(request=request)

        assert "csrfmiddlewaretoken" in html1, f"csrf blanked: {html1!r}"
        assert "u=erin" in html1, f"{{ user.username }} blanked: {html1!r}"
        assert "csrfmiddlewaretoken" in html2, f"csrf blanked after event: {html2!r}"
        assert "u=erin" in html2, f"{{ user.username }} blanked after event: {html2!r}"
        assert "days=7" in html2, f"state change not rendered: {html2!r}"
