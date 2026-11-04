"""#3777 — ``{% for g in u.groups.all %}{{ g.name }}`` over a QuerySet rendered empty.

On a LiveView (and ``DjustTemplateBackend``) a QuerySet of rows whose template
walks a related manager's ``.all`` lost every related row, while
``_rust.render_template`` and Django's own engine rendered the names.

**Root cause (measured, not the hypothesis in the issue's first comment).**
``extract_template_variables`` gives ``users: ['groups.all', 'groups.all.name']``
and the Rust queryset serializer (``serialize_object_with_paths``) walked it
with ``getattr``: ``groups`` is the manager, ``all`` is its BOUND METHOD, and a
method has no ``name`` — so it shipped ``{"groups": {"all": {}}}``. The loop ran
over an empty dict. The key-count fallback to the Python codegen never fires
because the ``groups`` key IS present (the issue's hypothesis, confirmed). The
codegen twin (``optimization/codegen.py``, ``attr_name == "all"``) has always
iterated ``.all()``; the Rust one never did, in any release since the
serializer was written (#1646). Only a QuerySet was affected: a ``list[Model]``
goes through the codegen per row, which is why the same template worked there.

Sibling spellings of the same missing step, all measured wrong before the fix:
``u.groups.all|length`` counted the characters of the QuerySet's ``repr``
(41, not 2), ``{% if u.groups.all %}`` was truthy for an EMPTY relation, and
``u.groups.all.0.name`` was empty on the LiveView.

Reproduction fidelity (#1650 / #1638): REAL ``User`` / ``Group`` rows and the
REAL related managers, through each entry a QuerySet reaches a template by —
``DjustTemplateBackend``, a LiveView's HTTP GET, its WebSocket mount — with
Django's engine as the control. Expected strings are DERIVED from Django, not
typed in (#2727).
"""

from __future__ import annotations

import html as html_lib
import re
from typing import Callable

import pytest
from django.contrib.auth.models import Group, User
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate

from djust import _rust
from djust.live_view import LiveView
from djust.testing import LiveViewTestClient


# ---------------------------------------------------------------------------
# The entries a template takes
# ---------------------------------------------------------------------------
def _backend_render(source: str, context: dict) -> str:
    from djust.template import DjustTemplateBackend

    backend = DjustTemplateBackend(
        params={"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
    )
    return str(backend.from_string(source).render(context=dict(context), request=None))


def _raw_render(source: str, context: dict) -> str:
    return _rust.render_template(source, dict(context))


def _body(html: str) -> str:
    match = re.search(r"<div dj-root[^>]*>(.*)</div>", html, re.S)
    assert match is not None, html
    return match.group(1)


def _liveview_render(source: str, context: dict) -> str:
    class _V(LiveView):
        def mount(self, request, **kwargs):
            for name, value in context.items():
                setattr(self, name, value)

    _V.template = f"<div dj-root>{source}</div>"
    client = LiveViewTestClient(_V)
    client.mount()
    return _body(client.render())


def _django_render(source: str, context: dict) -> str:
    return DjangoTemplate(source).render(DjangoContext(dict(context)))


ENGINES = [
    pytest.param(_backend_render, id="DjustTemplateBackend"),
    pytest.param(_liveview_render, id="LiveView"),
    pytest.param(_raw_render, id="render_template"),
]


# ---------------------------------------------------------------------------
# The world: two groups, three users — one of them in NO group
# ---------------------------------------------------------------------------
@pytest.fixture
def world(db):
    staff = Group.objects.create(name="staff")
    ops = Group.objects.create(name="ops")
    for name, groups in (("u0", (staff, ops)), ("u1", (staff,)), ("u2", ())):
        User.objects.create(username=name).groups.add(*groups)


def _users() -> dict:
    return {"users": User.objects.all().order_by("pk")}


def _groups() -> dict:
    return {"groups": Group.objects.all().order_by("pk")}


Case = tuple[str, str, Callable[[], dict]]
CASES: list[Case] = [
    # The issue's shape.
    (
        "for-all-name",
        "{% for u in users %}[{% for g in u.groups.all %}{{ g.name }},{% endfor %}]{% endfor %}",
        _users,
    ),
    (
        "for-all-two-attrs",
        "{% for u in users %}{{ u.username }}"
        "[{% for g in u.groups.all %}{{ g.name }}:{{ g.id }},{% endfor %}]{% endfor %}",
        _users,
    ),
    # The reverse manager, from the other side.
    (
        "reverse-user_set",
        "{% for g in groups %}{{ g.name }}"
        "[{% for u in g.user_set.all %}{{ u.username }},{% endfor %}]{% endfor %}",
        _groups,
    ),
    # `all` spelled with an index: the List arm of the same serializer.
    (
        "all-0-name",
        "{% for u in users %}[{{ u.groups.all.0.name }}]{% endfor %}",
        _users,
    ),
    # `all` named WHOLE: the leaf arm.
    (
        "all-length",
        "{% for u in users %}[{{ u.groups.all|length }}]{% endfor %}",
        _users,
    ),
    (
        "all-if-truthiness",
        "{% for u in users %}[{% if u.groups.all %}Y{% else %}N{% endif %}]{% endfor %}",
        _users,
    ),
    (
        "all-whole-row",
        "{% for u in users %}[{% for g in u.groups.all %}{{ g }},{% endfor %}]{% endfor %}",
        _users,
    ),
]
CASE_IDS = [c[0] for c in CASES]


@pytest.mark.django_db
class TestRelatedManagerAllOverAQuerySet3777:
    @pytest.mark.parametrize("render", ENGINES)
    @pytest.mark.parametrize(("name", "source", "make_ctx"), CASES, ids=CASE_IDS)
    def test_every_entry_agrees_with_django(self, render, name, source, make_ctx, world) -> None:
        expected = _django_render(source, make_ctx())
        got = render(source, make_ctx())
        assert html_lib.unescape(got) == expected, (
            f"{name!r} through {render.__name__}: Django renders {expected!r}, got {got!r}"
        )

    @pytest.mark.parametrize(("name", "source", "make_ctx"), CASES, ids=CASE_IDS)
    def test_the_control_is_not_vacuous(self, name, source, make_ctx, world) -> None:
        """An empty Django render would make every equality above pass for an
        engine that also renders nothing — the exact failure under test."""
        expected = _django_render(source, make_ctx())
        assert re.search(r"staff|ops|u0|[1-9]|Y", expected), expected

    def test_an_empty_relation_is_falsy_and_zero_long(self, world) -> None:
        """``u2`` is in no group: the leaf arm used to ship the QuerySet's
        ``repr`` string, which is truthy and 22 characters long."""
        source = "{% for u in users %}{{ u.username }}:{% if u.groups.all %}Y{% else %}N{% endif %}{{ u.groups.all|length }};{% endfor %}"
        expected = _django_render(source, _users())
        assert expected == "u0:Y2;u1:Y1;u2:N0;"
        assert _backend_render(source, _users()) == expected
        assert _liveview_render(source, _users()) == expected


# ---------------------------------------------------------------------------
# The serializer, directly — the shared chokepoint every entry above calls
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestSerializeQuerysetKeepsTheRows3777:
    def test_all_with_children_is_a_list_of_row_dicts(self, world) -> None:
        out = _rust.serialize_queryset(
            list(User.objects.order_by("pk")), ["groups.all", "groups.all.name"]
        )
        assert [[g["name"] for g in row["groups"]["all"]] for row in out] == [
            ["staff", "ops"],
            ["staff"],
            [],
        ]

    def test_an_indexed_all_path_is_a_list_of_row_dicts(self, world) -> None:
        """The List arm: ``serialize_queryset`` is public, and a caller's path
        may spell the step with an index. The template extractor never emits
        one for ``u.groups.all.0.name`` (it extracts ``groups.all.name``;
        measured), so only a direct call reaches this arm — which is why the
        template cases above cannot pin it (a gate-off of the arm left them
        green)."""
        out = _rust.serialize_queryset(list(User.objects.order_by("pk")), ["groups.all.0.name"])
        assert [[g["name"] for g in row["groups"]["all"]] for row in out] == [
            ["staff", "ops"],
            ["staff"],
            [],
        ]

    def test_rust_and_codegen_serialize_the_same_rows(self, world) -> None:
        """The #1646 parallel pair: the Rust serializer and the Python codegen
        it falls back to must agree on what ``.all`` yields."""
        from djust.optimization.codegen import compile_serializer, generate_serializer_code

        paths = ["groups.all.name", "groups.all.id"]
        code = generate_serializer_code("User", paths, "ser_3777")
        codegen = compile_serializer(code, "ser_3777")
        users = list(User.objects.order_by("pk"))
        assert _rust.serialize_queryset(users, paths) == [codegen(u) for u in users]


class _Rows:
    """A to-many attribute whose ``all`` really deletes every user."""

    def __init__(self, marker: str | None) -> None:
        def all(self=None):  # noqa: A001 — the name is the point
            User.objects.all().delete()
            return []

        if marker:
            setattr(all, marker, True)
        self.all = all


class _Holder:
    def __init__(self, marker: str | None) -> None:
        self.groups = _Rows(marker)


@pytest.mark.django_db
class TestACalledAllStillHonoursTheCallGuard3777:
    """The traversal CALLS ``all``, so it must ask the one guard the leaf arm
    and the sidecar walk ask (#3772): a destructive ``all`` is never run."""

    @pytest.mark.parametrize("marker", ["alters_data", "do_not_call_in_templates"])
    @pytest.mark.parametrize(
        "paths",
        [["groups.all.name"], ["groups.all.0.name"], ["groups.all"]],
        ids=["object", "indexed", "leaf"],
    )
    def test_a_marked_all_is_never_called(self, world, marker, paths) -> None:
        before = User.objects.count()
        out = _rust.serialize_queryset([_Holder(marker)], paths)
        assert User.objects.count() == before == 3, f"{paths} ran a marked `all`: {out}"

    def test_the_detector_is_live(self, world) -> None:
        """Non-vacuity: an UNMARKED destructive ``all`` IS run, so the count
        above moves exactly when the guard is absent."""
        _rust.serialize_queryset([_Holder(None)], ["groups.all.name"])
        assert User.objects.count() == 0


# ---------------------------------------------------------------------------
# The two entries that are not a template call: HTTP GET and WS mount
# ---------------------------------------------------------------------------
class UsersGroupsView(LiveView):
    login_required = False
    template = """<div dj-view="test_queryset_all_traversal_3777.UsersGroupsView">
    {% for u in users %}<p>{{ u.username }}[{% for g in u.groups.all %}{{ g.name }},{% endfor %}]</p>{% endfor %}</div>"""

    def mount(self, request, **kwargs):
        self.users = User.objects.all().order_by("pk")


def _expected_paragraphs() -> list[str]:
    return [
        f"<p>{u.username}[{''.join(g.name + ',' for g in u.groups.order_by('pk'))}]</p>"
        for u in User.objects.order_by("pk")
    ]


def _paragraphs(html: str) -> list[str]:
    return re.findall(r"<p>.*?</p>", re.sub(r' dj-id="[^"]*"', "", html))


def test_http_get_renders_the_related_rows(transactional_db) -> None:
    from django.contrib.sessions.middleware import SessionMiddleware
    from django.test import RequestFactory

    staff = Group.objects.create(name="staff")
    User.objects.create(username="solo").groups.add(staff)
    request = RequestFactory().get("/users-groups/")
    SessionMiddleware(lambda request: None).process_request(request)
    response = UsersGroupsView.as_view()(request)
    assert response.status_code == 200
    got = _paragraphs(response.content.decode())
    assert got == _expected_paragraphs() == ["<p>solo[staff,]</p>"]


@pytest.mark.asyncio
async def test_websocket_mount_renders_the_related_rows(transactional_db) -> None:
    from channels.db import database_sync_to_async
    from channels.testing import WebsocketCommunicator
    from django.test import override_settings

    from djust.websocket import LiveViewConsumer

    @database_sync_to_async
    def seed() -> list[str]:
        staff = Group.objects.create(name="staff")
        User.objects.create(username="solo").groups.add(staff)
        return _expected_paragraphs()

    expected = await seed()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        try:
            connected, _ = await communicator.connect()
            assert connected
            await communicator.receive_json_from()
            await communicator.send_json_to(
                {"type": "mount", "view": f"{__name__}.UsersGroupsView"}
            )
            message = await communicator.receive_json_from()
            assert message["type"] == "mount", message
            assert _paragraphs(message["html"]) == expected == ["<p>solo[staff,]</p>"]
        finally:
            await communicator.disconnect()
