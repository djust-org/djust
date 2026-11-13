"""V029: a handler changes a value a later event reads, and the value is not saved (#4053, #4107).

A view's public attributes and ``state()`` fields are saved server-side
between requests (#4107), so a counter in a plain attribute counts over the
HTTP page-POST fallback. Two things are still lost there, and after a
reconnect: a ``state()`` field declared ``persist=False`` that a handler
reads and then changes. The empirical canary drives the views through the real
``LiveView.get`` / ``.post`` and then shows V029 reports exactly the ones that
are stuck.

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


def _no_persist(views):
    @event_handler
    def increment(self, **kwargs):
        self.count += 1

    return views(
        "NoPersist",
        (LiveView,),
        {
            "exposure_policy": "explicit",
            "template": "<div dj-root>[count={{ count }}]</div>",
            "count": state(0),
            "increment": increment,
        },
    )


def _opted_out(views):
    @event_handler
    def increment(self, **kwargs):
        self.count += 1

    return views(
        "OptedOut",
        (LiveView,),
        {
            "exposure_policy": "explicit",
            "template": "<div dj-root>[count={{ count }}]</div>",
            "count": state(0, persist=False),
            "increment": increment,
        },
    )


def _private_counter(views):
    def mount(self, request, **kwargs):
        self._count = 0

    def get_context_data(self, **kwargs):
        return {**LiveView.get_context_data(self, **kwargs), "count": self._count}

    @event_handler
    def increment(self, **kwargs):
        self._count += 1

    return views(
        "PrivateCounter",
        (LiveView,),
        {
            "exposure_policy": "explicit",
            "template": "<div dj-root>[count={{ count }}]</div>",
            "mount": mount,
            "get_context_data": get_context_data,
            "increment": increment,
        },
    )


@pytest.mark.django_db
def test_canary_saved_state_counts_on_the_fallback_and_is_not_reported(views):
    """A plain attribute, a ``_private`` one, a ``state()`` field and a
    ``persist="server"`` field all count over the fallback (#4107: the view's
    state is saved as 1.3 saved it); V029 reports none of them."""
    for build in (_mount_only, _private_counter, _no_persist, _persisted):
        view = build(views)
        assert _post_three(view) == [1, 2, 3], view.__name__
        assert _messages_for(view) == [], view.__name__


@pytest.mark.django_db
def test_canary_a_persist_false_field_is_stuck_and_v029_reports_it(views):
    opted_out = _opted_out(views)
    assert _post_three(opted_out) == [1, 1, 1]
    reported = _messages_for(opted_out)
    assert len(reported) == 1, [m.msg for m in reported]
    message = reported[0]
    assert message.id == "djust.V029"
    assert "increment() changes state() field count, which a later event reads" in message.msg
    assert "declared persist=False" in message.msg
    assert "Drop persist=False" in message.hint
    assert message.line_number and message.file_path.endswith(".py")


def test_not_reported_shapes_that_are_saved_or_recomputed(views):
    class Base(LiveView):
        exposure_policy = "explicit"
        template = "<div dj-root></div>"
        inherited = state(0, persist="server")

    class Quiet(Base):
        own = state(0, persist="server")
        label_text = state("", persist=False)  # opted out, but only ever replaced
        plain_field = state(0)
        total = 0
        temporary_assigns = {"messages": []}

        @computed
        def doubled(self):
            return 2

        def mount(self, request, **kwargs):
            self.count = 0
            self._cursor = 0
            self.items = []
            self.messages = []

        @event_handler
        def change(self, **kwargs):
            self.count += 1  # public: saved
            self._cursor += 1  # private, created by mount(): saved
            self.total = self.total + 1
            self.items.append(1)
            self.plain_field += 1  # a state() field with no persist: saved
            self.own += 1
            self.inherited += 1
            self.page_title = "x"  # a framework setting
            self.label_text = "fresh"  # an opted-out field replaced, not read first
            self.messages.append("hi")  # a temporary_assigns key

    assert _messages_for(Quiet) == [], [m.msg for m in _messages_for(Quiet)]
    assert _messages_for(Base) == []


def test_a_handler_in_a_mixin_is_reported_once_naming_the_mixin(views):
    class CountsMixin:
        hits = state(0, persist=False)

        @event_handler
        def bump(self, **kwargs):
            self.hits += 1

    class First(CountsMixin, LiveView):
        exposure_policy = "explicit"
        template = "<div dj-root></div>"

    class Second(CountsMixin, LiveView):
        exposure_policy = "explicit"
        template = "<div dj-root></div>"

    reported = _messages_for(First) + _messages_for(Second)
    assert len(reported) == 1, [m.msg for m in reported]
    assert "(in " in reported[0].msg and "CountsMixin)" in reported[0].msg
    assert "field hits" in reported[0].msg


def test_a_legacy_view_is_not_checked(views):
    with pytest.warns(DeprecationWarning):

        class Legacy(LiveView):
            exposure_policy = "legacy"
            template = "<div dj-root></div>"

            @event_handler
            def increment(self, **kwargs):
                self.count += 1

    assert _messages_for(Legacy) == []


def test_suppressed(views, settings):
    opted_out = _opted_out(views)
    settings.DJUST_CONFIG = {"suppress_checks": ["V029"]}
    assert _messages_for(opted_out) == []
