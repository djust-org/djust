"""#3936 / #3949 -- the render-time handle overlay (``state_models_as_handles``, default on since 2.0).

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
#: The kill-switch, spelled explicitly: since 2.0 the default is ON, so an empty config is ON too.
FLAG_OFF = {"state_models_as_handles": False}


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
    config = FLAG_ON if flag else FLAG_OFF
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
        with override_settings(LIVEVIEW_CONFIG=FLAG_ON if flag else FLAG_OFF):
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
    # The default since 2.0 is the overlay; the kill-switch keeps the dict.
    assert render_template(src, {"u": _title_value(u)}) == "2"
    with override_settings(LIVEVIEW_CONFIG=FLAG_OFF):
        assert render_template(src, {"u": _title_value(u)}) == ""


@pytest.mark.parametrize("policy", ["explicit", "legacy"])
def test_tag_arguments_stay_floored_json(people, policy, monkeypatch):
    """A native tag ARGUMENT stays the floored dict text with the flag on: ``data_table`` reads
    dict rows, and renders the same bytes as with the flag off.

    Non-vacuity: with the flag on the argument IS read through the handle's floored dict (the
    proxy's ``__djust_serialize__``): a serializer that drops ``username`` drops it from the
    flag-on table only."""
    from djust import serialization

    cols = [{"key": "username", "label": "U"}]

    def ctx():
        return {"us": list(User.objects.order_by("pk")), "cols": cols}

    src = "{% data_table rows=us columns=cols %}"
    on = _render(src, ctx, policy)
    assert "u0" in on and "u1" in on and HASH_MARK not in on, on
    assert on == _render(src, ctx, policy, flag=False)

    real = serialization._smp_serialize

    def without_username(proxy):
        row = dict(real(proxy))
        row.pop("username", None)
        return row

    monkeypatch.setattr(serialization, "_smp_serialize", without_username)
    assert "u0" not in _render(src, ctx, policy)
    assert "u0" in _render(src, ctx, policy, flag=False)


def test_change_detection_unchanged(people):
    """The overlay follows a REPLACED state model on every diff render (the handle is rebuilt on
    every sync): ``self.u`` becomes another ``User`` in the event, and ``render_with_diff`` reads
    the new one through the filtered rebinding and through a loop over a held list (whose items
    the loop render cache must not replay, train/2.0-ah review B3)."""
    from djust.decorators import event_handler

    class _V(LiveView):
        exposure_policy = "legacy"
        template = (
            "<div dj-root>{{ u.username }}|"
            "{% with q=u|default:u %}{{ q.username }}{% endwith %}|"
            "{% for x in us %}<i>{{ x.email }}</i>{% endfor %}"
            "<button dj-click='swap'>x</button></div>"
        )

        def mount(self, request, **kwargs):
            self.u = User.objects.get(username="u0")
            self.us = [User.objects.get(username="u0")]

        @event_handler
        def swap(self, **kwargs):
            self.u = User.objects.get(username="u1")
            self.us[0].email = "swapped@example.com"

    def text(html: str) -> str:
        return _body(
            re.sub(r' dj-id="[^"]*"', "", html).replace('<div dj-root="">', "<div dj-root>")
        )

    with override_settings(LIVEVIEW_CONFIG=dict(FLAG_ON, loop_render_cache_enabled=True)):
        client = LiveViewTestClient(_V)
        client.mount()
        view = client.view_instance
        assert text(view.render_with_diff()[0]).startswith("u0|u0|<i></i>")
        client.send_event("swap")
        assert text(view.render_with_diff()[0]).startswith("u1|u1|<i>swapped@example.com</i>")


#: The four Rust render entries that bind the overlay (``apply_render_handles``), each called on
#: the synced Rust view: ``render_result``, ``render_with_diff_detached``, ``render_binary_diff``,
#: and ``prepare_partial_page`` (the full-page root render; the authenticated partial-root page
#: renders through it). G1 (``set_render_handles({})``) turns every entry off at once, so a missing
#: call at ONE entry is only visible here (train/2.0-ah review S4).
RUST_ENTRIES = {
    "render": lambda rv: rv.render(),
    "render_with_diff": lambda rv: rv.render_with_diff()[0],
    "render_binary_diff": lambda rv: rv.render_binary_diff()[0],
    "prepare_partial_page": lambda rv: rv.render_full_root_provenance()[0],
}


@pytest.mark.parametrize("entry", list(RUST_ENTRIES))
@pytest.mark.parametrize("policy", ["explicit", "legacy"])
def test_every_render_entry_binds_the_overlay(world, policy, entry):
    """A custom filter is handed the floor proxy (not the dict) on every entry, both policies:
    a shape legacy's JIT does not answer by itself, so each entry's binding is what shows."""
    source = "{% load cc3949 %}{{ m|cc3949_filter }}"
    with override_settings(LIVEVIEW_CONFIG=FLAG_ON):
        client = LiveViewTestClient(_view(source, lambda: {"m": world["a1"]}, policy))
        client.mount()
        client.render()
        view = client.view_instance
        view._sync_state_to_rust()  # the overlay is set by the sync, read by each entry
        html = html_lib.unescape(RUST_ENTRIES[entry](view._rust_view))
    assert PROXY in html and HASH_MARK not in html, (entry, html)


class _SpyRustView:
    """Forwards to the real Rust view and records ``set_render_handles`` calls."""

    def __init__(self, real):
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "calls", [])

    def __getattr__(self, name):
        return getattr(self._real, name)

    def set_render_handles(self, values):
        self.calls.append(dict(values))
        return self._real.set_render_handles(values)


def test_flag_off_sync_sends_no_empty_overlay_to_a_view_without_one(people):
    """Review N3: with the flag off, a sync does not cross into Rust to clear an overlay the view
    never had; with it on, a key leaving state still sends the clearing call."""
    source = "{% with q=u|default:u %}[{{ q.groups.count }}]{% endwith %}"
    for flag, expect_calls in ((False, 0), (True, 2)):
        with override_settings(LIVEVIEW_CONFIG=FLAG_ON if flag else FLAG_OFF):
            client = LiveViewTestClient(_view(source, _ctx))
            client.mount()
            client.render()
            view = client.view_instance
            spy = _SpyRustView(view._rust_view)
            view._rust_view = spy
            view._sync_state_to_rust()  # flag on: the handles again
            if flag:
                view.get_context_data = lambda **kw: {}  # the key leaves state
                view._sync_state_to_rust()
            assert len(spy.calls) == expect_calls, (flag, spy.calls)
            if flag:
                assert spy.calls[-1] == {} and not spy._real.has_render_handles()
