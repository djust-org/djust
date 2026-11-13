"""V029: an explicit view's handler changes an attribute that is not declared state (#4053).

Under explicit exposure the HTTP page-POST fallback builds a new view for each
event and runs ``mount()`` again. A counter held in a plain attribute set in
``mount()`` answers every fallback event from its ``mount()`` value; a field
declared ``state(0, persist="server")`` counts. The empirical canary drives the
issue's two views through the real ``LiveView.get`` / ``.post`` and then shows
V029 reports exactly the broken one.

The views are built inside each test and dropped afterwards: the system checks
enumerate every loaded ``LiveView`` subclass, and these must not leak into
other tests' check runs.
"""

from __future__ import annotations

import gc
import json
import re
import sys

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory

from djust import LiveView, state
from djust.checks.components import check_undeclared_handler_state
from djust.decorators import computed, event_handler

PATH = "/v029/"


def _messages_for(view):
    label = "%s.%s" % (view.__module__, view.__qualname__)
    return [m for m in check_undeclared_handler_state(None) if m.msg.startswith(label + " ")]


@pytest.fixture
def views():
    """Build test views per test; drop them so later check runs do not see them."""
    built: list = []

    def make(name, bases, body):
        body = {"__module__": __name__, "__qualname__": name, **body}
        cls = type(name, bases, body)
        setattr(sys.modules[__name__], name, cls)  # resolvable for the POST path
        built.append(cls)
        return cls

    yield make
    for cls in built:
        if getattr(sys.modules[__name__], cls.__name__, None) is cls:
            delattr(sys.modules[__name__], cls.__name__)
    built.clear()
    gc.collect()


def _mount_only(views):
    def mount(self, request, **kwargs):
        self.count = 0

    def get_context_data(self, **kwargs):
        return {**LiveView.get_context_data(self, **kwargs), "count": self.count}

    @event_handler
    def increment(self, **kwargs):
        self.count += 1

    return views(
        "MountOnly",
        (LiveView,),
        {
            "exposure_policy": "explicit",
            "template": "<div dj-root>[count={{ count }}]</div>",
            "mount": mount,
            "get_context_data": get_context_data,
            "increment": increment,
        },
    )


def _persisted(views):
    @event_handler
    def increment(self, **kwargs):
        self.count += 1

    return views(
        "Persisted",
        (LiveView,),
        {
            "exposure_policy": "explicit",
            "template": "<div dj-root>[count={{ count }}]</div>",
            "count": state(0, persist="server"),
            "increment": increment,
        },
    )


def _post_three(view_cls):
    get = RequestFactory().get(PATH)
    get.user = AnonymousUser()
    get.tenant = None
    get.session = SessionStore()
    get.session.create()
    view_cls().get(get)
    seen = []
    for _ in range(3):
        request = RequestFactory().post(
            PATH,
            data=json.dumps({}),
            content_type="application/json",
            HTTP_X_DJUST_EVENT="increment",
        )
        request.user, request.session, request.tenant = get.user, get.session, None
        body = view_cls().post(request).content.decode("utf-8")
        found = re.search(r"count=(\d+)", body)
        assert found, body
        seen.append(int(found.group(1)))
    return seen


@pytest.mark.django_db
def test_canary_the_mount_only_counter_is_stuck_on_the_fallback_and_v029_reports_it(views):
    mount_only, persisted = _mount_only(views), _persisted(views)
    assert _post_three(mount_only) == [1, 1, 1]  # the issue's table
    assert _post_three(persisted) == [1, 2, 3]

    reported = _messages_for(mount_only)
    assert len(reported) == 1, [m.msg for m in reported]
    message = reported[0]
    assert message.id == "djust.V029"
    assert "increment() changes self.count" in message.msg
    assert 'count = state(<default>, persist="server")' in message.hint
    assert message.line_number and message.file_path.endswith(".py")
    assert _messages_for(persisted) == []


def test_not_reported_private_framework_declared_descriptor_and_non_handler(views):
    class Base(LiveView):
        exposure_policy = "explicit"
        template = "<div dj-root></div>"
        inherited = state(0)

    class DeclaresMixin:
        from_mixin = state(0)

    class Quiet(DeclaresMixin, Base):
        own = state(0)

        @computed
        def doubled(self):
            return 2

        @property
        def label(self):
            return "x"

        @label.setter
        def label(self, value):
            self._label = value

        def mount(self, request, **kwargs):
            self.plain_in_mount = 0

        @event_handler
        def change(self, **kwargs):
            self._scratch = 1  # private
            self.own += 1  # declared here, augmented
            self.inherited += 1  # declared on a base
            self.from_mixin = 2  # declared on a plain mixin
            self.page_title = "x"  # a framework setting (a property)
            self.template_name = "other.html"  # a framework setting (a plain value)
            self.label = "y"  # a property with a setter

        def not_a_handler(self):
            self.plain = 1

    assert _messages_for(Quiet) == []
    assert _messages_for(Base) == []


def test_reported_mount_only_handler_only_and_class_level_default(views):
    class Loud(LiveView):
        exposure_policy = "explicit"
        template = "<div dj-root></div>"
        total = 0  # a plain class value is not declared state

        def mount(self, request, **kwargs):
            self.count = 0

        @event_handler
        def increment(self, **kwargs):
            self.count += 1  # set only in mount()
            self.total = self.total + 1

        @event_handler
        def search(self, value: str = "", **kwargs):
            self.query = value  # set only by the handler

    texts = sorted(m.msg for m in _messages_for(Loud))
    assert len(texts) == 3, texts
    assert any("increment() changes self.count" in t for t in texts)
    assert any("increment() changes self.total" in t for t in texts)
    assert any("search() changes self.query" in t for t in texts)


def test_a_handler_in_a_mixin_is_reported_once_naming_the_mixin(views):
    class CountsMixin:
        @event_handler
        def bump(self, **kwargs):
            self.bumps = getattr(self, "bumps", 0) + 1

    class First(CountsMixin, LiveView):
        exposure_policy = "explicit"
        template = "<div dj-root></div>"

    class Second(CountsMixin, LiveView):
        exposure_policy = "explicit"
        template = "<div dj-root></div>"

    reported = _messages_for(First) + _messages_for(Second)
    assert len(reported) == 1, [m.msg for m in reported]
    assert "(in " in reported[0].msg and "CountsMixin)" in reported[0].msg


def test_a_legacy_view_is_not_checked(views):
    with pytest.warns(DeprecationWarning):

        class Legacy(LiveView):
            exposure_policy = "legacy"
            template = "<div dj-root></div>"

            @event_handler
            def increment(self, **kwargs):
                self.count = 1

    assert _messages_for(Legacy) == []


def test_suppressed(views, settings):
    mount_only = _mount_only(views)
    settings.DJUST_CONFIG = {"suppress_checks": ["V029"]}
    assert _messages_for(mount_only) == []
