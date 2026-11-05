"""#3801 -- residuals of the QuerySet ``.all`` handling after #3788.

Every case renders through the REAL native engine (a LiveView mount via
``LiveViewTestClient`` and ``DjustTemplateBackend``) with Django's own engine as
the oracle, on REAL ``User`` / ``Group`` rows (#1650 / #1638).
"""

from __future__ import annotations

import html as html_lib

import pytest
from django.contrib.auth.models import Group, User

from python.tests.test_queryset_all_followups_3788 import MAKE_CONTEXT, RENDERS  # noqa: F401
from python.tests.test_queryset_all_traversal_3777 import _django_render, world  # noqa: F401

from python.tests.test_queryset_all_traversal_3777 import _liveview_render  # noqa: E402


def through_count() -> int:
    return User.groups.through.objects.count()


def _users():
    return User.objects.all().order_by("pk")


U = "{% for u in users %}"
E = "{% endfor %}"
LOOP = "{% for g in u.groups.all %}{{ g.name }},{% endfor %}"

MIXED = {
    "count-then-loop": U + "[{{ u.groups.all.count }}:" + LOOP + "]" + E,
    "loop-then-count": U + "[" + LOOP + ":{{ u.groups.all.count }}]" + E,
    "length-and-count": U + "[{{ u.groups.all|length }}/{{ u.groups.all.count }}]" + E,
    "if-and-count": U + "[{% if u.groups.all %}{{ u.groups.all.count }}{% endif %}]" + E,
    "with-count": U + "[{% with gs=u.groups.all %}{{ gs.count }}{% endwith %}]" + E,
    "slice-and-count": U
    + "[{% for g in u.groups.all|slice:':1' %}{{ g.name }}{% endfor %}"
    + "{{ u.groups.all.count }}]"
    + E,
    "exists-and-loop": U + "[{{ u.groups.all.exists }}:" + LOOP + "]" + E,
    "first-and-loop": U + "[{{ u.groups.all.first.name }}:" + LOOP + "]" + E,
    "last-and-loop": U + "[{{ u.groups.all.last.name }}:" + LOOP + "]" + E,
    # The method read INSIDE the loop over the same relation.
    "count-inside-the-loop": U
    + "{% for g in u.groups.all %}{{ g.name }}/{{ u.groups.all.count }},{% endfor %}|"
    + E,
    "nested-with": U
    + "{% with gs=u.groups.all %}{% for g in gs %}{{ g.name }},{% endfor %}"
    + "{{ gs.count }}{{ gs.exists }}{% endwith %}|"
    + E,
    "index-and-count": U + "[{{ u.groups.all.0.name }}:{{ u.groups.all.count }}]" + E,
}

# The other side of the many-to-many (a reverse manager) and a reverse foreign key.
GROUPS_FROM_THE_OTHER_SIDE = (
    "{% for g in groups %}[{% for u in g.user_set.all %}{{ u.username }},{% endfor %}"
    ":{{ g.user_set.all.count }}:{{ g.user_set.all.exists }}]{% endfor %}"
)


@pytest.mark.django_db
class TestALoopAndAMethodOnOneAll3801:
    @pytest.mark.parametrize("render", RENDERS)
    @pytest.mark.parametrize("shape", list(MAKE_CONTEXT))
    @pytest.mark.parametrize("name", list(MIXED))
    def test_agrees_with_django(self, world, render, shape, name) -> None:
        source = MIXED[name]
        expected = _django_render(source, MAKE_CONTEXT[shape]())
        # ``<!--dj-if-->`` is the LiveView's placeholder for a false ``{% if %}``.
        got = html_lib.unescape(render(source, MAKE_CONTEXT[shape]())).replace("<!--dj-if-->", "")
        assert got == expected, f"{name} {shape}: Django {expected!r}, got {got!r}"


    @pytest.mark.parametrize("render", RENDERS)
    @pytest.mark.parametrize(
        "make",
        [
            pytest.param(lambda: {"groups": Group.objects.order_by("pk")}, id="QuerySet"),
            pytest.param(lambda: {"groups": list(Group.objects.order_by("pk"))}, id="list[Model]"),
        ],
    )
    def test_a_reverse_manager_agrees_with_django(self, world, render, make) -> None:
        expected = _django_render(GROUPS_FROM_THE_OTHER_SIDE, make())
        assert expected == "[u0,u1,:2:True][u0,:1:True]", expected
        assert html_lib.unescape(render(GROUPS_FROM_THE_OTHER_SIDE, make())) == expected

    @pytest.mark.parametrize("render", RENDERS)
    def test_a_row_model_with_a_count_field_answers_the_method(self, bins, render) -> None:
        """A model with a ``count`` field keeps ``all`` a list of rows, so the
        method read used to stay empty; the relation's own QuerySet answers it."""
        source = (
            "{% for b in bins %}{% for i in b.items.all %}{{ i.count }},{% endfor %}"
            "#{{ b.items.all.count }}|{% endfor %}"
        )
        make = lambda: {"bins": QSBin.objects.order_by("pk")}  # noqa: E731
        expected = _django_render(source, make())
        assert expected == "7,9,#2|#0|", expected
        assert html_lib.unescape(render(source, make())) == expected


@pytest.mark.django_db
class TestTheMethodReadWidensNothing3801:
    """The method read goes through the engine's guarded getattr walk: it calls
    what Django calls and refuses what Django refuses (#3815)."""

    def test_a_method_marked_alters_data_is_not_called(self, bins) -> None:
        source = (
            "{% for b in bins %}{% for n in b.notes.all %}{{ n.name }},{% endfor %}"
            "[{{ b.notes.all.first.name }}|{{ b.notes.all.count }}]{% endfor %}"
        )
        make = lambda: {"bins": QSBin.objects.order_by("pk")}  # noqa: E731
        CALLS.clear()
        expected = _django_render(source, make())
        assert expected == "n1,n2,[|2][|0]", expected  # Django refuses ``first``
        assert "first" not in CALLS and "count" in CALLS, CALLS  # non-vacuous control
        CALLS.clear()
        assert html_lib.unescape(_liveview_render(source, make())) == expected
        assert "first" not in CALLS, CALLS
        assert "count" in CALLS, CALLS  # the new path really ran the permitted method

    def test_a_method_marked_do_not_call_in_templates_is_not_called(self, bins) -> None:
        source = (
            "{% for b in bins %}{% for n in b.notes.all %}{{ n.name }},{% endfor %}"
            "[{{ b.notes.all.exists }}]{% endfor %}"
        )
        make = lambda: {"bins": QSBin.objects.order_by("pk")}  # noqa: E731
        CALLS.clear()
        _django_render(source, make())
        assert "exists" not in CALLS, CALLS  # Django prints the method un-called (control)
        CALLS.clear()
        got = html_lib.unescape(_liveview_render(source, make()))
        # The un-called method prints as a repr that carries an address, so the
        # text itself is not comparable; what matters is it was never run.
        assert got.startswith("n1,n2,["), got
        assert "exists" not in CALLS, CALLS

    @pytest.mark.parametrize("call", ["u.groups.all.delete", "u.groups.clear", "u.delete"])
    def test_a_destructive_method_is_not_reached(self, world, call) -> None:
        source = U + "[" + LOOP + "{{ u.groups.all.count }}{{ " + call + " }}]" + E
        before = (User.objects.count(), Group.objects.count(), through_count())
        html_lib.unescape(_liveview_render(source, MAKE_CONTEXT["QuerySet"]()))
        assert (User.objects.count(), Group.objects.count(), through_count()) == before

    def test_the_serialization_floor_still_holds(self, world) -> None:
        """A secret reached through the method path is not rendered."""
        User.objects.filter(username="u0").update(password="pbkdf2$SECRET$hash")
        source = (
            U + "[" + LOOP + "{{ u.groups.all.count }}"
            "{{ u.groups.all.first.user_set.first.password }}]" + E
        )
        got = _liveview_render(source, MAKE_CONTEXT["QuerySet"]())
        assert "SECRET" not in got and "pbkdf2" not in got, got
        assert "SECRET" in _django_render(source, MAKE_CONTEXT["QuerySet"]())  # control

    def test_an_event_rerender_answers_from_the_current_rows(self, world) -> None:
        """The kept rows are rebuilt every render: a handler that changes a
        relation is reflected in the count, not read from the first render."""
        from djust.live_view import LiveView
        from djust.testing import LiveViewTestClient

        class _V(LiveView):
            def mount(self, request, **kwargs):
                self.users = _users()

            def join(self, **kwargs):
                User.objects.get(username="u2").groups.add(Group.objects.get(name="ops"))

        _V.template = f"<div dj-root>{MIXED['count-then-loop']}</div>"
        client = LiveViewTestClient(_V)
        client.mount()
        assert "[2:staff,ops,][1:staff,][0:]" in client.render()
        client.send_event("join")
        assert "[2:staff,ops,][1:staff,][1:ops,]" in client.render()

    def test_only_a_name_read_both_ways_is_kept(self, world) -> None:
        """A plain loop, and a method-only read, build no extra sidecar entry."""
        from djust.live_view import LiveView
        from djust.testing import LiveViewTestClient

        def kept(source: str) -> dict:
            class _V(LiveView):
                def mount(self, request, **kwargs):
                    self.users = _users()

            _V.template = f"<div dj-root>{source}</div>"
            client = LiveViewTestClient(_V)
            client.mount()
            client.render()
            return dict(client.view_instance.__dict__.get("_all_method_rows", {}))

        assert "users" in kept(MIXED["count-then-loop"])
        assert kept(U + LOOP + E) == {}
        assert kept(U + "{{ u.groups.all.count }}" + E) == {}


# ===========================================================================
# 7. A container deeper than the bound is reported, once
# ===========================================================================
@pytest.mark.django_db
class TestTheDepthBoundIsReported3801:
    def test_a_queryset_past_the_bound_is_warned_about_once(self, monkeypatch, caplog) -> None:
        import logging

        from djust.mixins import context as ctx

        monkeypatch.setattr(ctx, "_DEPTH_BOUND_REPORTED", False)
        deep = {"a": {"b": {"c": {"d": {"e": User.objects.none()}}}}}
        with caplog.at_level(logging.WARNING, logger="djust.mixins.context"):
            assert not ctx._holds_db_value(deep)
            assert not ctx._holds_db_value(deep)
        warned = [r for r in caplog.records if "nested more than" in r.getMessage()]
        assert len(warned) == 1, [r.getMessage() for r in caplog.records]
        assert "QuerySet" in warned[0].getMessage()

    def test_a_queryset_inside_the_bound_and_plain_data_are_silent(
        self, monkeypatch, caplog
    ) -> None:
        import logging

        from djust.mixins import context as ctx

        monkeypatch.setattr(ctx, "_DEPTH_BOUND_REPORTED", False)
        with caplog.at_level(logging.WARNING, logger="djust.mixins.context"):
            assert ctx._holds_db_value({"a": {"b": {"c": {"d": User.objects.none()}}}})
            assert not ctx._holds_db_value({"a": {"b": {"c": {"d": {"e": [1, "x", None]}}}}})
        assert not [r for r in caplog.records if "nested more than" in r.getMessage()]


# ===========================================================================
# 4. A row's integer ``count`` field in the Python codegen (list[Model])
# ===========================================================================
from python.tests.test_queryset_all_followups_3788 import (  # noqa: E402
    CALLS,
    QSBin,
    QSItem,  # noqa: F401
    QSNote,
    bins,  # noqa: F401
)

from djust.optimization.codegen import compile_serializer, generate_serializer_code  # noqa: E402


class TestARowFieldNamedLikeAMethod3801:
    FIELD_SOURCE = (
        "{% for b in bins %}{% for i in b.items.all %}"
        "{{ i.count }}/{{ i.first }};{% endfor %}|{% endfor %}"
    )

    @pytest.mark.parametrize("render", RENDERS)
    @pytest.mark.parametrize(
        "make",
        [
            pytest.param(lambda: {"bins": QSBin.objects.order_by("pk")}, id="QuerySet"),
            pytest.param(lambda: {"bins": list(QSBin.objects.order_by("pk"))}, id="list[Model]"),
        ],
    )
    def test_the_loop_over_such_a_field_renders_it(self, bins, render, make) -> None:
        expected = _django_render(self.FIELD_SOURCE, make())
        assert expected == "7/F-a;9/F-b;||", expected  # non-vacuous control
        assert html_lib.unescape(render(self.FIELD_SOURCE, make())) == expected

    def test_the_codegen_twin_ships_the_integer_field(self, bins) -> None:
        """The generated leaf branch called a leaf named ``count`` / ``exists`` /
        ``all`` as a method, so an integer ``count`` field raised ``TypeError``
        (swallowed, logged at DEBUG) and was left out of the row."""
        code = generate_serializer_code("QSBin", ["items.all.count", "items.all.first"], "g3801")
        rows = compile_serializer(code, "g3801")(QSBin.objects.get(name="full"))["items"]["all"]
        assert [(r["count"], r["first"]) for r in rows] == [(7, "F-a"), (9, "F-b")]


# ===========================================================================
# The entries that are not a template call: HTTP GET and WebSocket mount
# ===========================================================================
import re  # noqa: E402

from djust.live_view import LiveView  # noqa: E402


class MixedView(LiveView):
    login_required = False
    template = """<div dj-view="test_queryset_all_residuals_3801.MixedView">
    {% for u in users %}<p>{{ u.username }}:{{ u.groups.all.count }}[{% for g in u.groups.all %}{{ g.name }},{% endfor %}]</p>{% endfor %}</div>"""

    def mount(self, request, **kwargs):
        self.users = User.objects.all().order_by("pk")


def _paragraphs(html: str) -> list[str]:
    return re.findall(r"<p>.*?</p>", re.sub(r' dj-id="[^"]*"', "", html))


def _expected() -> list[str]:
    return [
        f"<p>{u.username}:{u.groups.count()}"
        f"[{''.join(g.name + ',' for g in u.groups.order_by('pk'))}]</p>"
        for u in User.objects.order_by("pk")
    ]


def _seed() -> None:
    staff = Group.objects.create(name="staff")
    User.objects.create(username="solo").groups.add(staff)
    User.objects.create(username="alone")


def test_http_get_renders_the_rows_and_the_count(transactional_db) -> None:
    from django.contrib.sessions.middleware import SessionMiddleware
    from django.test import RequestFactory

    _seed()
    request = RequestFactory().get("/x/")
    SessionMiddleware(lambda request: None).process_request(request)
    response = MixedView.as_view()(request)
    assert response.status_code == 200
    got = _paragraphs(response.content.decode())
    assert got == _expected()
    assert "<p>solo:1[staff,]</p>" in got  # non-vacuous: a relation really rendered


@pytest.mark.asyncio
async def test_websocket_mount_renders_the_rows_and_the_count(transactional_db) -> None:
    from channels.db import database_sync_to_async
    from channels.testing import WebsocketCommunicator
    from django.test import override_settings

    from djust.websocket import LiveViewConsumer

    @database_sync_to_async
    def seed() -> list[str]:
        _seed()
        return _expected()

    want = await seed()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        try:
            connected, _ = await communicator.connect()
            assert connected
            await communicator.receive_json_from()
            await communicator.send_json_to({"type": "mount", "view": f"{__name__}.MixedView"})
            message = await communicator.receive_json_from()
            assert message["type"] == "mount", message
            assert _paragraphs(message["html"]) == want
            assert "<p>solo:1[staff,]</p>" in want
        finally:
            await communicator.disconnect()
