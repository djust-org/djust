"""Regression for #3061: the full-template (page-shell) render must not send
context-processor values through the LiveView-state normalizer.

Symptom (djustlive, djust 1.2.0): every HTTP GET of a LiveView whose project
uses Django's default context processors logged::

    LiveView state contains non-serializable value: FallbackStorage ...

from ``render_full_template`` -> ``_render_full_template_inner`` ->
``normalize_django_value``. #1786 had already kept request-scoped values
(``request`` plus whatever ``_apply_context_processors`` added) out of the
normalizer on the dj-root path (``_sync_state_to_rust``); the page-shell
render in ``mixins/template.py`` was a parallel path that still normalized
them, stringifying ``messages`` / ``perms`` / ``request`` for the shell.

Contract pinned here:

* a GET with the default processors logs no non-serializable warning;
* ``messages`` stays iterable, both inside the LiveView root and in the page
  shell outside it;
* ``user`` / ``perms`` / ``csrf_token`` still render;
* a genuinely non-serializable public state attribute still warns (the fix
  must not blanket-silence the warning);
* a view that assigns its own ``messages`` keeps it as state (view context wins
  over processors, so the key is not request-scoped).
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

import pytest
from django.contrib import messages as django_messages
from django.contrib.auth.models import AnonymousUser, User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.mixins.context import _context_processors_cache, _resolved_processors_cache
from djust.utils import clear_template_dirs_cache

_LEAK_TYPE_NAMES = (
    "ASGIRequest",
    "WSGIRequest",
    "PermWrapper",
    "FallbackStorage",
    "SimpleLazyObject",
    "UserLazyObject",
)

# The processors ``django-admin startproject`` writes.
_DEFAULT_PROCESSORS = [
    "django.template.context_processors.debug",
    "django.template.context_processors.request",
    "django.contrib.auth.context_processors.auth",
    "django.contrib.messages.context_processors.messages",
]

_BASE = (
    "<!DOCTYPE html><html><head><title>T</title></head><body>"
    "<ul id='shell-msgs'>{% for m in messages %}<li>shell:{{ m }}</li>{% endfor %}</ul>"
    "<p id='shell-user'>shell-user={{ user.username }}</p>"
    "<div dj-root>{% block content %}{% endblock %}</div>"
    "</body></html>"
)

_CHILD = (
    '{% extends "base_3061.html" %}{% block content %}'
    "<ul id='root-msgs'>{% for m in messages %}<li>root:{{ m }}</li>{% endfor %}</ul>"
    "<span>u={{ user.username }}|n={{ count }}|perm={{ perms.auth }}</span>"
    "<form>{% csrf_token %}</form>"
    "{% endblock %}"
)


@pytest.fixture
def template_dir():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "base_3061.html").write_text(_BASE)
    (tmp / "child_3061.html").write_text(_CHILD)
    templates = [
        {
            "BACKEND": "djust.template_backend.DjustTemplateBackend",
            "DIRS": [str(tmp)],
            "APP_DIRS": False,
            "OPTIONS": {"context_processors": _DEFAULT_PROCESSORS},
        }
    ]
    with override_settings(TEMPLATES=templates):
        clear_template_dirs_cache()
        _resolved_processors_cache.clear()
        _context_processors_cache.clear()
        yield tmp
    clear_template_dirs_cache()
    _resolved_processors_cache.clear()
    _context_processors_cache.clear()
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def warnings_seen():
    records: list[str] = []

    class _H(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = _H()
    loggers = [logging.getLogger(n) for n in ("djust.serialization", "djust")]
    prev = {lg: lg.level for lg in loggers}
    for lg in loggers:
        lg.addHandler(handler)
        lg.setLevel(logging.WARNING)
    try:
        yield records
    finally:
        for lg in loggers:
            lg.removeHandler(handler)
            lg.setLevel(prev[lg])


class _Service:
    """A plain object that is not JSON-serializable: real view state."""


def _view_class(name: str, mount=None):
    def _mount(self, request, **kwargs):
        self.count = 3
        if mount is not None:
            mount(self, request)

    return type(name, (LiveView,), {"template_name": "child_3061.html", "mount": _mount})


def _get(view_cls, user=None, message=None):
    request = RequestFactory().get("/page-3061/")
    request.user = user if user is not None else AnonymousUser()
    request.session = SessionStore()
    request.session.create()
    request._messages = FallbackStorage(request)
    if message:
        django_messages.info(request, message)
    response = view_cls.as_view()(request)
    return response.content.decode()


def _non_serializable(records):
    return [m for m in records if "non-serializable value" in m]


@pytest.mark.django_db
class TestFullTemplateContextProcessors3061:
    def test_default_processors_log_no_warning(self, template_dir, warnings_seen):
        user = User.objects.create_user("alice3061", password="x")
        html = _get(_view_class("V3061NoWarn"), user=user, message="hello")
        assert "n=3" in html
        leaks = _non_serializable(warnings_seen)
        assert not leaks, f"context-processor values reached the state normalizer: {leaks!r}"

    def test_messages_iterable_inside_root_and_shell(self, template_dir):
        html = _get(_view_class("V3061Msgs"), message="saved!")
        assert "<li>root:saved!</li>" in html, html
        assert "<li>shell:saved!</li>" in html, html
        # A stringified FallbackStorage would render its repr, or iterate as
        # characters; neither may appear.
        assert "FallbackStorage" not in html
        assert "<li>shell:<" not in html

    def test_user_perms_and_csrf_still_render(self, template_dir):
        user = User.objects.create_user("bob3061", password="x")
        html = _get(_view_class("V3061User"), user=user)
        assert "u=bob3061" in html, html
        assert "shell-user=bob3061" in html, html
        assert "csrfmiddlewaretoken" in html
        assert "PermWrapper" not in html
        assert "WSGIRequest" not in html

    def test_real_non_serializable_state_still_warns(self, template_dir, warnings_seen):
        def mount(self, request):
            self.service = _Service()

        _get(_view_class("V3061Svc", mount=mount))
        hits = [m for m in _non_serializable(warnings_seen) if "_Service" in m]
        assert hits, f"a non-serializable view attribute must still warn: {warnings_seen!r}"
        leaks = [
            m for m in _non_serializable(warnings_seen) if any(t in m for t in _LEAK_TYPE_NAMES)
        ]
        assert not leaks, leaks

    def test_view_owned_messages_stays_state(self, template_dir):
        # View context wins over the messages processor, so ``messages`` is
        # the view's own value (not request-scoped) and renders as state.
        def mount(self, request):
            self.messages = ["from-view"]

        html = _get(_view_class("V3061Own", mount=mount), message="from-processor")
        assert "<li>root:from-view</li>" in html, html
        assert "<li>shell:from-view</li>" in html, html
        assert "from-processor" not in html

    def test_render_with_diff_after_get_logs_no_warning(self, template_dir, warnings_seen):
        # The WS mount / event path renders through render_with_diff ->
        # _sync_state_to_rust (#1786). Pin it alongside the shell path so the
        # two can't drift again.
        request = RequestFactory().get("/page-3061/")
        request.user = AnonymousUser()
        request.session = SessionStore()
        request.session.create()
        request._messages = FallbackStorage(request)
        django_messages.info(request, "diffed")
        view = _view_class("V3061Diff")()
        view.setup(request)
        view._initialize_temporary_assigns()
        view.mount(request)
        view.request = request
        view.get_template()
        html, _patches, _version = view.render_with_diff(request)
        assert "root:diffed" in html, html
        assert not _non_serializable(warnings_seen), warnings_seen

    def test_http_fallback_event_render_logs_no_warning(self, template_dir, warnings_seen):
        # The HTTP POST fallback injects processor output as view attributes
        # (``_processor_context``, #717) before ``render_with_diff``. Those
        # attributes are request-scoped too and must not warn as state.
        request = RequestFactory().post("/page-3061/")
        request.user = AnonymousUser()
        request.session = SessionStore()
        request.session.create()
        request._messages = FallbackStorage(request)
        django_messages.info(request, "posted")
        view = _view_class("V3061Post")()
        view.setup(request)
        view._initialize_temporary_assigns()
        view.mount(request)
        view.request = request
        view.get_template()
        with view._processor_context(request):
            html, _patches, _version = view.render_with_diff(request)
        assert "root:posted" in html, html
        assert not _non_serializable(warnings_seen), warnings_seen
