"""The template sidecar proxies do not hand out the object they wrap.

Template handles do not expose what they wrap to Python code that walks a dotted name with ``getattr``.

``_SidecarModelProxy`` and ``_SidecarQuerySetProxy`` hold the model / queryset in ``__slots__``. Template
resolution never touches ``_``-prefixed names (Django-parity), so the proxies refuse a single-underscore
name from outside.

The rule: a single-underscore name is refused on the proxy; the protocol (dunder) names stay reachable;
``copy`` / ``deepcopy`` rebuild a handle (``__copy__`` / ``__deepcopy__``); a handle is not picklable and
``__reduce__`` does not return what it wraps; the proxy's own code reads its slots directly.
"""

from __future__ import annotations

import copy
import pickle

import pytest
from django.contrib.auth.models import Group, User
from django.db.models import Model, QuerySet
from django.http import QueryDict
from django.template import Context
from django.test import RequestFactory

from djust import LiveView
from djust.serialization import (
    _protect_sidecar_value,
    _SidecarModelProxy,
    _SidecarQuerySetProxy,
    normalize_django_value,
)
from djust.template_backend import DjustTemplateBackend
from djust.template_tags import TagHandler, register
from djust.testing import LiveViewTestClient

pytestmark = pytest.mark.django_db

STORED = "pbkdf2_sha256$1$SALT$STOREDVALUE"
MODEL_SLOTS = ("_obj", "_denied", "_allowed", "_optout")
QUERYSET_SLOTS = ("_qs", "_is_projection")


def _user():
    user = User(username="alice", email="alice@example.org")
    user.pk = 3
    user.password = STORED
    return user


# ---------------------------------------------------------------------------
# The proxies themselves
# ---------------------------------------------------------------------------


class TestModelProxy:
    @pytest.mark.parametrize("name", MODEL_SLOTS + ("_meta", "_state", "_anything"))
    def test_a_single_underscore_name_is_refused(self, name):
        proxy = _protect_sidecar_value(_user())
        with pytest.raises(AttributeError):
            getattr(proxy, name)
        assert not hasattr(proxy, name)

    def test_the_wrapped_instance_is_not_reachable_through_a_dotted_walk(self):
        proxy = _protect_sidecar_value(_user())
        with pytest.raises(AttributeError):
            proxy._obj.password  # noqa: B018

    def test_protocol_names_and_ordinary_fields_stay_reachable(self):
        user = _user()
        proxy = _protect_sidecar_value(user)
        assert isinstance(proxy, _SidecarModelProxy)
        assert proxy.username == "alice"
        assert str(proxy) == str(user)
        assert repr(proxy) == repr(user)
        assert proxy.__class__ is _SidecarModelProxy
        assert proxy.__djust_serialize__() == normalize_django_value(user)

    def test_the_floor_still_holds(self):
        proxy = _protect_sidecar_value(_user())
        with pytest.raises(AttributeError):
            proxy.password  # noqa: B018

    def test_copy_and_deepcopy_keep_the_proxy_working(self):
        proxy = _protect_sidecar_value(_user())
        for clone in (copy.copy(proxy), copy.deepcopy(proxy)):
            assert isinstance(clone, _SidecarModelProxy)
            assert clone.username == "alice"
            with pytest.raises(AttributeError):
                clone.password  # noqa: B018
            with pytest.raises(AttributeError):
                clone._obj  # noqa: B018

    def test_a_handle_is_not_picklable_and_reduce_does_not_return_the_wrapped_object(self):
        for proxy in (_protect_sidecar_value(_user()), _protect_sidecar_value(Group.objects.all())):
            with pytest.raises(TypeError):
                proxy.__reduce__()
            with pytest.raises(TypeError):
                proxy.__reduce_ex__(2)
            with pytest.raises(TypeError):
                pickle.dumps(proxy)

    def test_a_query_dict_holding_the_proxy_can_be_copied(self):
        qd = QueryDict("a=1&a=2", mutable=True)
        qd.setlist("u", [_protect_sidecar_value(_user())])
        for clone in (qd.copy(), copy.deepcopy(qd)):
            member = clone.getlist("u")[0]
            assert isinstance(member, _SidecarModelProxy)
            assert member.username == "alice"

    def test_djust_itself_still_reads_the_wrapped_model(self):
        user = _user()
        proxy = _protect_sidecar_value(user)
        assert object.__getattribute__(proxy, "_obj") is user
        assert normalize_django_value(user) == proxy.__djust_serialize__()


class TestQuerySetProxy:
    @pytest.mark.parametrize("name", QUERYSET_SLOTS + ("_result_cache", "_anything"))
    def test_a_single_underscore_name_is_refused(self, name):
        proxy = _protect_sidecar_value(Group.objects.all())
        assert isinstance(proxy, _SidecarQuerySetProxy)
        with pytest.raises(AttributeError):
            getattr(proxy, name)

    def test_ordinary_use_and_copy_still_work(self):
        Group.objects.create(name="staff-d")
        proxy = _protect_sidecar_value(Group.objects.filter(name="staff-d"))
        assert len(proxy) == 1
        assert [item.name for item in proxy] == ["staff-d"]
        for clone in (copy.copy(proxy), copy.deepcopy(proxy)):
            assert isinstance(clone, _SidecarQuerySetProxy)
            assert len(clone) == 1
            with pytest.raises(AttributeError):
                clone._qs  # noqa: B018


def _protected_handles():
    return {
        "model": (_protect_sidecar_value(_user()), _SidecarModelProxy, "_obj"),
        "queryset": (_protect_sidecar_value(Group.objects.all()), _SidecarQuerySetProxy, "_qs"),
    }


@pytest.mark.parametrize("kind", ["model", "queryset"])
class TestCopiesOfAHandle:
    def test_a_copy_is_a_distinct_handle_that_still_refuses(self, kind):
        proxy, cls, slot = _protected_handles()[kind]
        for clone in (
            copy.copy(proxy),
            copy.deepcopy(proxy),
            proxy.__copy__(),
            proxy.__deepcopy__({}),
        ):
            assert clone is not proxy
            assert type(clone) is cls
            with pytest.raises(AttributeError):
                getattr(clone, slot)
            assert object.__getattribute__(clone, slot) is object.__getattribute__(proxy, slot)

    def test_the_callers_memo_receives_no_model_or_queryset(self, kind):
        proxy, _cls, _slot = _protected_handles()[kind]
        memo: dict = {}
        copy.deepcopy(proxy, memo)
        proxy.__deepcopy__(memo)
        held = list(memo.values()) + [
            v for value in memo.values() if isinstance(value, list) for v in value
        ]
        assert not [v for v in held if isinstance(v, (Model, QuerySet))], held

    def test_containers_holding_a_handle_copy_to_protected_handles(self, kind):
        proxy, cls, slot = _protected_handles()[kind]
        qd = QueryDict("a=1", mutable=True)
        qd.setlist("h", [proxy])
        containers = {
            "dict": ({"h": proxy}, lambda c: c["h"]),
            "list": ([proxy], lambda c: c[0]),
            "querydict": (qd, lambda c: c.getlist("h")[0]),
            "context": (Context({"h": proxy}), lambda c: c["h"]),
        }
        for name, (container, get) in containers.items():
            for how in (copy.copy, copy.deepcopy):
                member = get(how(container))
                assert type(member) is cls, (name, how)
                with pytest.raises(AttributeError):
                    getattr(member, slot)
        for clone in (qd.copy(), copy.deepcopy(qd)):
            assert type(clone.getlist("h")[0]) is cls


# ---------------------------------------------------------------------------
# A custom tag handler that resolves a dotted token with ``getattr``: HTTP GET (page shell),
# the plain template backend, and the LiveView render that carries the sidecar (WebSocket path)
# ---------------------------------------------------------------------------

SEEN: dict = {}
PROBE_NAME = "djust_proxy_probe_d"


class _ProbeTag(TagHandler):
    """Resolves each token the way a custom handler does and records what came back."""

    RESOLVE_ARG_POSITIONS = frozenset()

    def render(self, args, context):
        SEEN.clear()
        for token in args:
            SEEN[token] = self._resolve_arg(token, context)
        return ""


@pytest.fixture
def probe_tag():
    """Register the probe handler for one test and take it out again afterwards.

    The handler registry is process-global with no teardown of its own, so a handler left behind is
    seen by every later test in the process (the registry enumeration in
    ``test_custom_tag_return_escape_2379`` counts handlers defined under ``djust.``). The entry that
    was there before, if any, is put back.
    """
    from djust import _rust
    from djust import template_tags

    registry = template_tags._registered_handlers
    previous = registry.get(PROBE_NAME)
    register(PROBE_NAME)(_ProbeTag)
    try:
        yield
    finally:
        registry.pop(PROBE_NAME, None)
        _rust.unregister_tag_handler(PROBE_NAME)
        if previous is not None:
            registry[PROBE_NAME] = previous
            _rust.register_tag_handler(PROBE_NAME, previous)
        SEEN.clear()


TOKENS = ("u._obj.password", "u._obj", "u._denied", "qs._qs", "u.username")
SOURCE = "{% " + PROBE_NAME + " " + " ".join(TOKENS) + " %}"


def _assert_walk(seen):
    assert seen, "the probe handler did not run"
    assert seen["u.username"] == "alice"  # the ordinary dotted walk still works
    for token in TOKENS[:4]:
        # an unresolved token comes back as the token itself, never as the wrapped object
        assert seen[token] == token, (token, seen[token])
    assert STORED not in repr(seen)


def _context():
    return {"u": _user(), "qs": Group.objects.all()}


def test_the_backend_entry_does_not_resolve_a_private_name(probe_tag):
    backend = DjustTemplateBackend(
        params={"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
    )
    SEEN.clear()
    backend.from_string(SOURCE).render(context=_context())
    _assert_walk(dict(SEEN))


def test_the_http_get_entry_does_not_resolve_a_private_name(probe_tag):
    values = _context()

    class _V(LiveView):
        def mount(self, request, **kwargs):
            for key, value in values.items():
                setattr(self, key, value)

        def get_context_data(self, **kwargs):
            base = super().get_context_data(**kwargs)
            base.update(values)
            return base

    _V.template = "<div dj-root>inner</div>"
    _V._full_template = f"<html><body><div dj-root>inner</div><nav>{SOURCE}</nav></body></html>"
    request = RequestFactory().get("/probe-d/")
    from django.contrib.sessions.middleware import SessionMiddleware

    SessionMiddleware(lambda r: r).process_request(request)
    request.session.save()
    view = _V()
    view.setup(request)
    view.mount(request)
    view._full_template = _V._full_template
    SEEN.clear()
    view.render_full_template(request, serialized_context=view.get_context_data())
    _assert_walk(dict(SEEN))


def test_the_websocket_render_entry_does_not_resolve_a_private_name(probe_tag):
    values = _context()

    class _V(LiveView):
        def mount(self, request, **kwargs):
            for key, value in values.items():
                setattr(self, key, value)

    _V.template = f"<div dj-root>{SOURCE}</div>"
    client = LiveViewTestClient(_V)
    client.mount()
    SEEN.clear()
    client.render_with_patches()
    _assert_walk(dict(SEEN))
