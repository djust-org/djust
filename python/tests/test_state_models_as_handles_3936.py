"""#3936 / #3949 -- the render-time handle overlay (``state_models_as_handles``, default off).

A model held as LiveView state reached the Rust engine only as its serialized dict: a filtered
rebinding (``{% with q=u|default:u %}{{ q.groups.count }}``) and a filtered loop operand rendered
empty on an explicit view, and a custom filter or tag was handed a ``dict`` where the Django
backend hands a floor proxy. With the flag on, each state key holding a model gets a floored
HANDLE bound above the state for the render only; state, change detection and the wire keep the
dict. Django on real instances is the oracle (#2727); real ``User`` / ``Group`` rows (#1650).
"""

# ruff: noqa: F811
from __future__ import annotations

import html as html_lib
import json
import re

import pytest
from django.contrib.auth.models import Group, User
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate
from django.test import override_settings

from djust.live_view import LiveView
from djust.testing import LiveViewTestClient
from python.tests._scratch_models_3888 import scratch_models  # noqa: F401
from python.tests.test_custom_callable_model_argument_3949 import (  # noqa: F401
    HASH_MARK,
    PROXY,
    SENT_A,
    SENT_B,
    _library,
    world,
)

FLAG_ON = {"state_models_as_handles": True}


def _body(html: str) -> str:
    match = re.search(r"<div dj-root[^>]*>(.*)</div>", html, re.S)
    assert match is not None, html
    return html_lib.unescape(match.group(1))


def _view(source: str, factory, policy: str = "explicit"):
    class _V(LiveView):
        exposure_policy = policy

        if policy == "legacy":

            def mount(self, request, **kwargs):
                for name, value in factory().items():
                    setattr(self, name, value)

        else:

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                ctx.update(factory())
                return ctx

    _V.template = f"<div dj-root>{source}</div>"
    return _V


def _render(source: str, factory, policy: str = "explicit", flag: bool = True) -> str:
    config = FLAG_ON if flag else {}
    with override_settings(LIVEVIEW_CONFIG=config):
        client = LiveViewTestClient(_view(source, factory, policy))
        client.mount()
        return _body(client.render())


@pytest.fixture
def people(db):
    staff = Group.objects.create(name="staff")
    ops = Group.objects.create(name="ops")
    u0 = User.objects.create(username="u0", password=f"pbkdf2_sha256$1$s${HASH_MARK}")
    u0.groups.add(staff, ops)
    u1 = User.objects.create(username="u1", password=f"pbkdf2_sha256$1$s${HASH_MARK}")
    u1.groups.add(staff)
    return {"u0": u0, "u1": u1}


def _ctx():
    return {"u": User.objects.get(username="u0"), "us": User.objects.order_by("pk")}


SHAPES = [
    pytest.param("{% with q=u|default:u %}{{ q.groups.count }}{% endwith %}", id="count"),
    pytest.param(
        "{% with q=u|default:u %}{% if q.groups.exists %}y{% endif %}{% endwith %}", id="exists"
    ),
    pytest.param("{% with q=u|default:u %}{{ q.groups.all.0.name }}{% endwith %}", id="first-name"),
    pytest.param(
        "{% with q=u|default:u %}{% for g in q.groups.all %}{{ g.name }},{% endfor %}{% endwith %}",
        id="loop-relation",
    ),
    pytest.param(
        "{% for x in us|slice:':2' %}{{ x.groups.count }},{% endfor %}", id="filtered-operand"
    ),
    pytest.param("{% with q=u|default:u %}{{ q.get_full_name }}.{% endwith %}", id="method"),
]


@pytest.mark.parametrize("policy", ["explicit", "legacy"])
@pytest.mark.parametrize("source", SHAPES)
def test_filtered_rebinding_reads_reverse_relation(people, source, policy):
    expected = DjangoTemplate(source).render(DjangoContext(_ctx()))
    assert expected
    assert _render(source, _ctx, policy) == expected


def test_flag_off_is_unchanged_and_the_flag_is_live(people):
    """Non-vacuity: the explicit default path still renders the filtered rebinding empty with
    the flag off (the declared gap), and the flag turns it into Django's answer."""
    source = "{% with q=u|default:u %}{{ q.groups.count }}{% endwith %}"
    assert _render(source, _ctx, flag=False) == ""
    assert _render(source, _ctx, flag=True) == "2"


def test_overlay_never_hands_raw_instance(world):
    """A custom filter sees the floor proxy: no ``password``, no ``_meta``, not subscriptable,
    the model's computed members readable."""
    src = "{% load cc3949 %}{{ m|cc3949_filter }}"
    got = json.loads(_render(src, lambda: {"m": world["a1"]}))
    assert got["type"] == PROXY and got["is_model"] is False, got
    assert got["pw_read"].startswith("ERR:") and got["meta_read"].startswith("ERR:"), got
    assert got["subscript"].startswith("ERR:"), got
    assert got["owner_code"] == SENT_A and got["note_text"] == SENT_B, got
    assert HASH_MARK not in json.dumps(got)
    # flag off: the dict, as pinned by #3949
    off = json.loads(_render(src, lambda: {"m": world["a1"]}, flag=False))
    assert off["type"] == "dict", off


def test_protect_dropped_rust_refloors(world, monkeypatch):
    """Two layers: with the Python floor skipped for the overlay, the Rust conversion re-floors
    a raw model (``floored_model_proxy``), so a filter still never sees the instance."""
    from djust.mixins import rust_bridge

    monkeypatch.setattr(
        rust_bridge, "_render_handles", lambda raw, ctx, skip=(): {k: v for k, v in raw.items()}
    )
    got = json.loads(_render("{% load cc3949 %}{{ m|cc3949_filter }}", lambda: {"m": world["a1"]}))
    assert got["is_model"] is False and got["pw_read"].startswith("ERR:"), got
    assert HASH_MARK not in json.dumps(got)


def test_fail_closed_when_floor_raises(people, monkeypatch):
    """A key whose floor raises keeps its dict: the output equals the flag-off output."""
    from djust.mixins import rust_bridge

    def boom(*a, **k):
        raise RuntimeError("floor refused")

    source = "{% with q=u|default:u %}[{{ q.groups.count }}]{{ q.password }}{% endwith %}"
    off = _render(source, _ctx, flag=False)
    monkeypatch.setattr(rust_bridge, "_overlay_floor", boom)
    on = _render(source, _ctx)
    assert on == off == "[]", (on, off)


def test_key_leaving_state_leaves_no_stale_handle(people):
    class _V(LiveView):
        exposure_policy = "explicit"
        template = (
            "<div dj-root>{% with q=u|default:'none' %}[{{ q.groups.count }}]{% endwith %}"
            "<button dj-click='drop'>x</button></div>"
        )

        def get_context_data(self, **kwargs):
            ctx = super().get_context_data(**kwargs)
            if not self.request_flags.get("dropped"):
                ctx["u"] = User.objects.get(username="u0")
            return ctx

        def mount(self, request, **kwargs):
            self.request_flags = {}

        from djust.decorators import event_handler

        @event_handler
        def drop(self, **kwargs):
            self.request_flags["dropped"] = True

    with override_settings(LIVEVIEW_CONFIG=FLAG_ON):
        client = LiveViewTestClient(_V)
        client.mount()
        assert "[2]" in _body(client.render())
        client.send_event("drop")
        assert "[]" in _body(client.render())


def test_handle_never_serialised(people):
    """The Rust state (what msgpack writes) and the client HTML are identical flag on and off;
    the msgpack round trip carries no handle, and the next sync restores it."""
    source = "{{ u.username }}|{{ u.groups.count }}"
    states = {}
    for flag in (False, True):
        with override_settings(LIVEVIEW_CONFIG=FLAG_ON if flag else {}):
            client = LiveViewTestClient(_view(source, _ctx))
            client.mount()
            html = client.render()
            view = client.view_instance
            rust_view = view._rust_view
            restored = type(rust_view).deserialize_msgpack(rust_view.serialize_msgpack())
            state, restored_state = (
                json.dumps(
                    {k: v for k, v in rv.get_state().items() if k != "csrf_token"},
                    sort_keys=True,
                    default=repr,
                )
                for rv in (rust_view, restored)
            )
            states[flag] = (state, restored_state, _body(html))
    assert states[False] == states[True]
    assert states[True][2] == "u0|2"


def test_title_reads_reverse_relation_flag_on(people):
    from djust.runtime import _title_value

    u = User.objects.get(username="u0")
    from djust._rust import render_template

    src = "{% with q=u|default:u %}{{ q.groups.count }}{% endwith %}"
    with override_settings(LIVEVIEW_CONFIG=FLAG_ON):
        assert render_template(src, {"u": _title_value(u)}) == "2"
    assert render_template(src, {"u": _title_value(u)}) == ""


@pytest.mark.parametrize("policy", ["explicit", "legacy"])
def test_tag_arguments_stay_floored_json(people, policy):
    """A native tag ARGUMENT stays the floored dict text with the flag on: ``data_table`` reads
    dict rows, and renders the same bytes as with the flag off."""
    cols = [{"key": "username", "label": "U"}]

    def ctx():
        return {"us": list(User.objects.order_by("pk")), "cols": cols}

    src = "{% data_table rows=us columns=cols %}"
    on = _render(src, ctx, policy)
    assert "u0" in on and "u1" in on and HASH_MARK not in on, on
    assert on == _render(src, ctx, policy, flag=False)


def test_change_detection_unchanged(people):
    """A field mutated behind a stable ``id()`` still re-renders with the flag on, and the
    overlay follows it (the handle is rebuilt on every sync)."""
    from djust.decorators import event_handler

    class _V(LiveView):
        exposure_policy = "legacy"
        template = (
            "<div dj-root>{{ u.username }}|"
            "{% with q=u|default:u %}{{ q.username }}{% endwith %}"
            "<button dj-click='rename'>x</button></div>"
        )

        def mount(self, request, **kwargs):
            self.u = User.objects.get(username="u0")

        @event_handler
        def rename(self, **kwargs):
            self.u.username = "renamed"

    with override_settings(LIVEVIEW_CONFIG=FLAG_ON):
        client = LiveViewTestClient(_V)
        client.mount()
        assert _body(client.render()).startswith("u0|u0")
        client.send_event("rename")
        assert _body(client.render()).startswith("renamed|renamed")
