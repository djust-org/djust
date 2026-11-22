"""The loop render cache never replays an item whose body read through a live object.

The loop render cache (#1967) keys an item's fragment by the hash of its loop bindings. Two
reads are not in that hash: a row's relation walk through the render sidecar (#4069, and the
``{% for p in u.user_permissions.all %}`` shape on main) and a field read through a state
model HANDLE (#3936, ``state_models_as_handles``: the handle hashes as ``str(model)``). Both
were right on the first render and replayed stale on every ``render_with_diff`` after a change
(train/2.0-ah review B2, B3). An item that read through a live object is now rendered every
time and never stored. Django on real rows is the oracle (#2727, #1650).
"""

# ruff: noqa: F811
from __future__ import annotations

import html as html_lib
import re

import pytest
from django.contrib.auth.models import Group, User
from django.test import override_settings

from djust.decorators import event_handler
from djust.live_view import LiveView
from djust.testing import LiveViewTestClient
from python.tests._scratch_models_3888 import scratch_models  # noqa: F401
from python.tests.test_custom_callable_model_argument_3949 import (  # noqa: F401
    HASH_MARK,
    SENT_C,
    _library,
    world,
)

FLAG_ON = {"state_models_as_handles": True}


def _body(html: str) -> str:
    match = re.search(r"<div [^>]*dj-root[^>]*>(.*?)<button", html, re.S)
    assert match is not None, html
    return html_lib.unescape(re.sub(r' dj-id="[^"]*"', "", match.group(1)))


@pytest.fixture
def people(db):
    staff = Group.objects.create(name="staff")
    for name in ("u0", "u1"):
        User.objects.create(username=name, email=f"{name}@old.example", password="x")
    return staff


def _view(source: str, policy: str, factory, mutate):
    class _V(LiveView):
        exposure_policy = policy
        template = f"<div dj-root>{source}<button dj-click='poke'>x</button></div>"

        if policy == "legacy":

            def mount(self, request, **kwargs):
                for name, value in factory().items():
                    setattr(self, name, value)

        else:

            def mount(self, request, **kwargs):
                self.tick = 0

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                ctx.update(factory())
                return ctx

        @event_handler
        def poke(self, **kwargs):
            mutate(self)

    return _V


def _diff_renders(view_cls, config) -> tuple:
    """The first render, then ``render_with_diff`` twice: before and after the event."""
    with override_settings(LIVEVIEW_CONFIG=config):
        client = LiveViewTestClient(view_cls)
        client.mount()
        view = client.view_instance
        first = _body(view.render_with_diff()[0])
        again = _body(view.render_with_diff()[0])
        client.send_event("poke")
        after = _body(view.render_with_diff()[0])
    return first, again, after


def _users():
    return {"users": User.objects.order_by("pk")}


def _add_group(view):
    User.objects.get(username="u0").groups.add(Group.objects.get(name="staff"))
    if hasattr(view, "users"):  # legacy holds the QuerySet: re-evaluate it
        view.users = User.objects.order_by("pk")


@pytest.mark.parametrize("policy", ["explicit", "legacy"])
@pytest.mark.parametrize("loop_cache", [True, False], ids=["lc-on", "lc-off"])
def test_row_relation_walk_follows_a_relation_change(people, policy, loop_cache):
    """B2: ``{{ u.groups.count }}`` in a loop row, ``render_with_diff`` after the change."""
    source = "{% for u in users %}<li>{{ u.username }}:{{ u.groups.count }}</li>{% endfor %}"
    cfg = {"loop_render_cache_enabled": loop_cache}
    first, again, after = _diff_renders(_view(source, policy, _users, _add_group), cfg)
    assert first == again == "<li>u0:0</li><li>u1:0</li>", first
    assert after == "<li>u0:1</li><li>u1:0</li>", after


@pytest.mark.parametrize("policy", ["explicit", "legacy"])
def test_nested_relation_loop_follows_a_db_change(people, policy):
    """The same staleness on main: a loop over a row's relation (``u.groups.all``)."""
    source = (
        "{% for u in users %}<li>{% for g in u.groups.all %}{{ g.name }},{% endfor %}</li>"
        "{% endfor %}"
    )
    cfg = {"loop_render_cache_enabled": True}
    first, _again, after = _diff_renders(_view(source, policy, _users, _add_group), cfg)
    assert first == "<li></li><li></li>", first
    assert after == "<li>staff,</li><li></li>", after


def _held_users():
    return {"us": list(User.objects.order_by("pk"))}


def _change_email(view):
    rows = getattr(view, "us", None)
    if rows is None:  # explicit: the context re-reads the rows, so change the DB
        User.objects.filter(username="u0").update(email="u0@new.example")
    else:
        rows[0].email = "u0@new.example"


@pytest.mark.parametrize("policy", ["explicit", "legacy"])
@pytest.mark.parametrize("flag", [True, False], ids=["handles-on", "handles-off"])
def test_state_model_loop_follows_a_field_change(people, policy, flag):
    """B3: a loop over a state model list reads ``x.email`` (not in ``str(model)``)."""
    source = "{% for x in us %}<li>{{ x.email }}</li>{% endfor %}"
    cfg = dict(FLAG_ON if flag else {}, loop_render_cache_enabled=True)
    first, again, after = _diff_renders(_view(source, policy, _held_users, _change_email), cfg)
    assert first == again == "<li>u0@old.example</li><li>u1@old.example</li>", first
    assert after == "<li>u0@new.example</li><li>u1@old.example</li>", after


def _perm_user():
    return {"u": User.objects.get(username="u0")}


@pytest.mark.parametrize("policy", ["explicit", "legacy"])
def test_loop_over_a_relation_follows_a_field_change(people, policy):
    """The review's main-branch shape: rows a relation walk returns are live handles hashed by
    ``str(permission)``, so ``{{ p.codename }}`` (not in ``__str__``) replayed stale."""
    from django.contrib.auth.models import Permission

    perm = Permission.objects.order_by("pk").first()
    User.objects.get(username="u0").user_permissions.add(perm)
    old = perm.codename

    def rename(view):
        Permission.objects.filter(pk=perm.pk).update(codename="renamed_codename")

    source = "{% for p in u.user_permissions.all %}<li>{{ p.codename }}</li>{% endfor %}"
    cfg = {"loop_render_cache_enabled": True}
    first, again, after = _diff_renders(_view(source, policy, _perm_user, rename), cfg)
    assert first == again == f"<li>{old}</li>", first
    assert after == "<li>renamed_codename</li>", after


@pytest.mark.parametrize("policy", ["explicit", "legacy"])
def test_custom_filter_over_a_handle_follows_a_field_change(world, policy):
    """B3, the half no attribute read sees: a custom filter handed the loop row's HANDLE reads a
    field through Python. Only the binding check (a live opaque handle in the item's bindings)
    keeps that item out of the cache."""
    import json as json_lib

    Account = world["Account"]

    def rows():
        return {"us": list(Account.objects.order_by("pk"))}

    def rename(view):
        held = getattr(view, "us", None)
        if held is None:
            Account.objects.filter(username="a1").update(first_name="CHANGED-FIRST")
        else:
            held[0].first_name = "CHANGED-FIRST"

    source = "{% load cc3949 %}{% for r in us %}<li>{{ r|cc3949_filter }}</li>{% endfor %}"
    cfg = dict(FLAG_ON, loop_render_cache_enabled=True)
    first, again, after = _diff_renders(_view(source, policy, rows, rename), cfg)
    assert first == again and SENT_C in first and HASH_MARK not in first, first
    items = re.findall(r"<li>(.*?)</li>", after, re.S)
    assert "CHANGED-FIRST" in items[0] and SENT_C in items[1], after
    assert json_lib.loads(items[0])  # the filter's own JSON, not a stale fragment
