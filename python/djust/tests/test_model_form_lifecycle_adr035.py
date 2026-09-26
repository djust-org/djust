"""ADR-035 F1: the managed edit object — resolve, authorize, bind.

``ModelFormMixin`` edits one ``auth.Group`` the signed-in user belongs to.
Every test records the lifecycle order in ``EVENTS`` so a form built before
authorization, a second lookup inside one dispatch, or a hook that runs for a
denied object is visible. The route supplies the ``pk``; the transports are
the HTTP GET/POST paths, the shared runtime (WebSocket and SSE), a real
WebSocket session in normal and actor mode, and a real SSE session.
"""

import json
import uuid

import pytest
from asgiref.sync import sync_to_async
from django import forms
from django.contrib.auth.models import Group
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.test import RequestFactory, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import path

from djust import LiveView
from djust.forms import ModelFormMixin

EVENTS: list = []
REVOKED: set = set()


class GroupForm(forms.ModelForm):
    class Meta:
        model = Group
        fields = ["name"]

    def __init__(self, *args, **kwargs):
        EVENTS.append(("form", kwargs.get("instance") and kwargs["instance"].pk))
        super().__init__(*args, **kwargs)


class EditGroupView(ModelFormMixin[Group], LiveView):
    template = (
        '<div dj-root><h1>{{ object.name }}</h1><input name="name" '
        'value="{{ form_data.name }}"><p>{{ success_message }}</p></div>'
    )
    model = Group
    form_class = GroupForm
    login_required = True

    def get_queryset(self):
        EVENTS.append(("lookup",))
        return super().get_queryset().filter(user=self.request.user)

    def has_object_permission(self, request, obj):
        EVENTS.append(("permission", obj.pk))
        return obj.pk not in REVOKED

    def form_valid(self, form):
        EVENTS.append(("form_valid", form.instance.pk))
        self.object = form.save()
        self.success_message = "Saved " + self.object.name


class ActorEditGroupView(EditGroupView):
    use_actors = True


class AliasEditGroupView(EditGroupView):
    context_object_name = "group"
    template = "<div dj-root><h1>{{ group.name }}</h1><h2>{{ object.name }}</h2></div>"


class ExplicitEditGroupView(EditGroupView):
    exposure_policy = "explicit"


class SnapshotEditGroupView(EditGroupView):
    enable_state_snapshot = True


class OtherView(LiveView):
    template = "<div dj-root></div>"


urlpatterns = [
    path("groups/<int:pk>/edit/", EditGroupView.as_view()),
    path("actor-groups/<int:pk>/edit/", ActorEditGroupView.as_view()),
    path("alias-groups/<int:pk>/edit/", AliasEditGroupView.as_view()),
    path("explicit-groups/<int:pk>/edit/", ExplicitEditGroupView.as_view()),
    path("snapshot-groups/<int:pk>/edit/", SnapshotEditGroupView.as_view()),
    path("other/<int:pk>/", OtherView.as_view()),
    path("unrouted/", EditGroupView.as_view()),
]

ROUTES = {
    EditGroupView: "/groups/%s/edit/",
    ActorEditGroupView: "/actor-groups/%s/edit/",
    AliasEditGroupView: "/alias-groups/%s/edit/",
    ExplicitEditGroupView: "/explicit-groups/%s/edit/",
    SnapshotEditGroupView: "/snapshot-groups/%s/edit/",
}

DENIED = "Access denied for this object."


@pytest.fixture(autouse=True)
def _routes_and_records():
    EVENTS.clear()
    REVOKED.clear()
    with override_settings(ROOT_URLCONF=__name__, DJUST_TENANTS=None):
        yield
    EVENTS.clear()
    REVOKED.clear()


def _world():
    """alice belongs to ``mine``; ``theirs`` exists but is filtered out."""
    from django.contrib.auth import get_user_model

    tag = uuid.uuid4().hex[:8]
    alice = get_user_model().objects.create_user(username="alice-" + tag)
    mine = Group.objects.create(name="Mine " + tag)
    theirs = Group.objects.create(name="Theirs " + tag)
    alice.groups.add(mine)
    return alice, mine, theirs


def _request(user, url, method="get", body=None):
    from django.contrib.sessions.backends.db import SessionStore

    factory = RequestFactory()
    if method == "get":
        request = factory.get(url)
    else:
        request = factory.post(
            url, data=json.dumps(body), content_type="application/json", HTTP_X_DJUST_EVENT="x"
        )
    request.user = user
    request.tenant = None
    request.session = SessionStore()
    request.session.create()
    return request


def _get(view_class, user, pk, session=None):
    request = _request(user, ROUTES[view_class] % pk)
    if session is not None:
        request.session = session
    return request, view_class.as_view()(request, pk=pk)


def _post(view_class, user, pk, session, event, params):
    request = RequestFactory().post(
        ROUTES[view_class] % pk,
        data=json.dumps({"event": event, "params": params}),
        content_type="application/json",
    )
    request.user, request.session, request.tenant = user, session, None
    return view_class.as_view()(request, pk=pk)


@pytest.fixture
def sql(monkeypatch):
    """Every SQL statement, from whichever thread and connection runs it."""
    from django.db.backends import utils

    statements: list = []
    original = utils.CursorWrapper._execute

    def record(self, query, params, *args):
        statements.append(query)
        return original(self, query, params, *args)

    monkeypatch.setattr(utils.CursorWrapper, "_execute", record)
    return statements


def _order(kind):
    return [event[0] for event in EVENTS if event[0] in kind]


def _lookups(queries):
    """The object lookups: get_queryset() joins the user's memberships."""
    return [
        q["sql"]
        for q in queries
        if q["sql"].startswith('SELECT "auth_group"') and 'JOIN "auth_user_groups"' in q["sql"]
    ]


# --------------------------------------------------------------------------
# HTTP GET: resolve -> authorize -> bind, one lookup, identical denials.
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_get_authorizes_before_the_form_and_looks_the_object_up_once():
    alice, mine, _ = _world()
    with CaptureQueriesContext(connection) as queries:
        _, response = _get(EditGroupView, alice, mine.pk)
    assert response.status_code == 200
    html = response.content.decode()
    assert mine.name in html
    assert _order({"lookup", "permission", "form"})[:3] == ["lookup", "permission", "form"]
    # The post-mount check reuses mount's verdict: one lookup, one permission call.
    assert _order({"lookup"}) == ["lookup"]
    assert _order({"permission"}) == ["permission"]
    assert len(_lookups(queries.captured_queries)) == 1


@pytest.mark.django_db
def test_missing_filtered_and_denied_are_indistinguishable():
    alice, mine, theirs = _world()
    REVOKED.add(mine.pk)
    missing = Group.objects.order_by("-pk").first().pk + 1000
    bodies = []
    for pk in (missing, theirs.pk, mine.pk):
        EVENTS.clear()
        _, response = _get(EditGroupView, alice, pk)
        bodies.append((response.status_code, response.content))
        assert "form" not in _order({"form"}), pk
    assert bodies == [(403, DENIED.encode())] * 3
    assert theirs.name.encode() not in b"".join(body for _, body in bodies)


@pytest.mark.django_db
def test_unrouted_view_never_uses_mount_parameters():
    alice, mine, _ = _world()
    request = _request(alice, "/unrouted/")
    view = EditGroupView()
    view.setup(request)
    # A pk that arrives as a mount parameter instead of a route kwarg.
    view.mount(request, pk=mine.pk)
    assert view.object is None
    assert "form" not in _order({"form"})


@pytest.mark.django_db
def test_object_and_configuration_never_reach_the_session():
    alice, mine, _ = _world()
    request, response = _get(EditGroupView, alice, mine.pk)
    assert response.status_code == 200
    saved = request.session["liveview_" + request.path]
    for name in ("object", "kwargs", "model", "queryset", "pk_url_kwarg", "slug_field"):
        assert name not in saved, name
    assert "form_data" in saved
    private = request.session.get("liveview_%s__private" % request.path, {})
    assert not any("authorized" in key or key == "_object" for key in private), private


@pytest.mark.django_db
def test_context_object_name_is_an_opt_in_alias():
    alice, mine, _ = _world()
    _, response = _get(AliasEditGroupView, alice, mine.pk)
    html = response.content.decode()
    assert "<h1>%s</h1>" % mine.name in html
    assert "<h2>%s</h2>" % mine.name in html


# --------------------------------------------------------------------------
# HTTP POST fallback: re-resolution per event, save, revocation.
# --------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("view_class", [EditGroupView, ExplicitEditGroupView])
def test_http_post_reauthorizes_every_event(view_class):
    alice, mine, _ = _world()
    request, response = _get(view_class, alice, mine.pk)
    assert response.status_code == 200
    EVENTS.clear()
    response = _post(
        view_class, alice, mine.pk, request.session, "submit_form", {"name": "Renamed"}
    )
    assert response.status_code == 200, response.content
    mine.refresh_from_db()
    assert mine.name == "Renamed"
    assert _order({"lookup", "permission", "form", "form_valid"}).count("form_valid") == 1
    first_permission = _order({"permission", "form"}).index("permission")
    assert first_permission < _order({"permission", "form"}).index("form")

    REVOKED.add(mine.pk)
    EVENTS.clear()
    response = _post(view_class, alice, mine.pk, request.session, "submit_form", {"name": "Again"})
    assert response.status_code == 403
    mine.refresh_from_db()
    assert mine.name == "Renamed"
    assert "form_valid" not in _order({"form_valid"})
    assert "form" not in _order({"form"})


# --------------------------------------------------------------------------
# The managed object API.
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_object_setter_accepts_only_the_same_record():
    alice, mine, theirs = _world()
    request, _ = _get(EditGroupView, alice, mine.pk)
    view = EditGroupView()
    view.setup(request, pk=mine.pk)
    view._djust_bind_route_kwargs({"pk": mine.pk})
    view.request = request
    view.mount(request, pk=mine.pk)
    fresh = Group.objects.get(pk=mine.pk)
    view.object = fresh
    assert view.object is fresh
    for value in (theirs, None, Group(name="unsaved"), alice):
        with pytest.raises(ValueError, match="navigate"):
            view.object = value
    assert view.object is fresh


def test_unbound_object_cannot_be_assigned():
    view = EditGroupView()
    assert view.object is None
    with pytest.raises(ValueError):
        view.object = Group(pk=1, name="x")


def test_mixing_model_instance_is_a_configuration_error():
    with pytest.raises(ImproperlyConfigured, match="_model_instance"):

        class Mixed(ModelFormMixin, LiveView):
            model = Group
            form_class = GroupForm
            _model_instance = None


@pytest.mark.parametrize("name", ["object", "form_data", "_private", "not a name"])
def test_context_object_name_must_be_a_free_template_name(name):
    with pytest.raises(ImproperlyConfigured, match="context_object_name"):

        class Aliased(ModelFormMixin, LiveView):
            model = Group
            form_class = GroupForm
            context_object_name = name


@pytest.mark.django_db
def test_plain_form_class_is_refused_and_denied():
    class PlainForm(forms.Form):
        name = forms.CharField()

    class PlainView(EditGroupView):
        form_class = PlainForm

    alice, mine, _ = _world()
    request = _request(alice, "/groups/%s/edit/" % mine.pk)
    view = PlainView()
    view.setup(request, pk=mine.pk)
    view._djust_bind_route_kwargs({"pk": mine.pk})
    with pytest.raises(ImproperlyConfigured, match="ModelForm"):
        view.mount(request, pk=mine.pk)


def test_generic_subscription_is_optional_at_runtime():
    class Bare(ModelFormMixin, LiveView):
        model = Group
        form_class = GroupForm

    class Typed(ModelFormMixin[Group], LiveView):
        model = Group
        form_class = GroupForm

    assert issubclass(Bare, ModelFormMixin) and issubclass(Typed, ModelFormMixin)


# --------------------------------------------------------------------------
# Socket transports: WS (normal + actor), SSE, via the real entry points.
# --------------------------------------------------------------------------


async def _ws_session(view_class, user, url, params=None):
    from channels.testing import WebsocketCommunicator
    from djust.tests.test_exposure_runtime import make_request
    from djust.websocket import LiveViewConsumer

    request = await sync_to_async(make_request)()
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=request.session, user=user, tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from(timeout=3)
    await socket.send_json_to(
        {
            "type": "mount",
            "view": __name__ + "." + view_class.__name__,
            "url": url,
            "params": params or {},
        }
    )
    return socket, await socket.receive_json_from(timeout=5)


async def _ws_event(socket, ref, event, params):
    await socket.send_json_to({"type": "event", "event": event, "params": params, "ref": ref})
    frames = []
    for _ in range(6):
        frame = await socket.receive_json_from(timeout=5)
        frames.append(frame)
        if frame.get("type") in {"patch", "html_update", "error", "noop"}:
            break
    return frames


def _allow():
    return override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__], ROOT_URLCONF=__name__, DJUST_TENANTS=None
    )


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("view_class", [EditGroupView, ActorEditGroupView])
async def test_websocket_lifecycle(view_class, sql, monkeypatch):
    from djust.runtime import WSConsumerTransport

    actor_turns = []
    original = WSConsumerTransport._dispatch_actor_event_locked

    async def through_actor(self, *args, **kwargs):
        actor_turns.append(args)
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(WSConsumerTransport, "_dispatch_actor_event_locked", through_actor)
    alice, mine, theirs = await sync_to_async(_world)()
    with _allow():
        socket, mounted = await _ws_session(view_class, alice, ROUTES[view_class] % mine.pk)
        try:
            assert mounted["type"] == "mount", mounted
            assert mine.name in json.dumps(mounted)
            order = _order({"lookup", "permission", "form"})
            assert order[:3] == ["lookup", "permission", "form"], EVENTS
            assert order.count("lookup") == 1, EVENTS

            EVENTS.clear()
            sql.clear()
            frames = await _ws_event(socket, 1, "submit_form", {"name": "Socket name"})
            assert not any(f.get("type") == "error" for f in frames), frames
            assert "Saved Socket name" in json.dumps(frames)
            # One lookup for the event; the handler and form reuse it.
            assert _order({"lookup"}) == ["lookup"], EVENTS
            assert len(_lookups({"sql": q} for q in sql)) == 1, sql
            assert _order({"form_valid"}) == ["form_valid"]
            assert bool(actor_turns) is (view_class is ActorEditGroupView)

            # Access revoked between events: denied before any form or hook.
            REVOKED.add(mine.pk)
            EVENTS.clear()
            frames = await _ws_event(socket, 2, "submit_form", {"name": "Too late"})
            assert frames[-1]["type"] == "error", frames
            assert _order({"form", "form_valid"}) == [], EVENTS
            await sync_to_async(mine.refresh_from_db)()
            assert mine.name == "Socket name"
        finally:
            await socket.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "case",
    ["missing", "filtered", "revoked", "forged_param", "other_route", "unrouted"],
)
async def test_websocket_denials_are_indistinguishable(case):
    alice, mine, theirs = await sync_to_async(_world)()
    url, params = "/groups/%s/edit/" % mine.pk, {}
    if case == "missing":
        url = "/groups/%s/edit/" % (theirs.pk + 1000)
    elif case == "filtered":
        url = "/groups/%s/edit/" % theirs.pk
    elif case == "revoked":
        REVOKED.add(mine.pk)
    elif case == "forged_param":
        # The route names mine; a client parameter names theirs.
        url, params = "/groups/%s/edit/" % theirs.pk, {"pk": mine.pk}
    elif case == "other_route":
        # A URL routed to another view cannot select this view's object.
        url = "/other/%s/" % mine.pk
    elif case == "unrouted":
        url, params = "/unrouted/", {"pk": mine.pk}
    with _allow():
        socket, frame = await _ws_session(EditGroupView, alice, url, params)
        try:
            assert frame["type"] == "error", frame
            assert frame.get("code") == "permission_denied", frame
            assert frame.get("error") == DENIED, frame
            assert mine.name not in json.dumps(frame) and theirs.name not in json.dumps(frame)
            assert _order({"form"}) == [], EVENTS
        finally:
            await socket.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_sse_lifecycle():
    from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions
    from djust.tests.test_exposure_sse_navigation import drain, request_for

    alice, mine, _ = await sync_to_async(_world)()
    sid = str(uuid.uuid4())
    with _allow():
        request = await sync_to_async(request_for)(
            "GET",
            f"/djust/sse/{sid}/",
            {"view": __name__ + ".EditGroupView", "_djust_url": "/groups/%s/edit/" % mine.pk},
        )
        request.user = alice
        assert (await DjustSSEStreamView().get(request, session_id=sid)).status_code == 200
        session = _sse_sessions[sid]
        try:
            mounted = drain(session)
            assert any(f["type"] == "mount" for f in mounted), mounted
            assert mine.name in json.dumps(mounted)

            async def send(ref, name):
                post = await sync_to_async(request_for)(
                    "POST",
                    f"/djust/sse/{sid}/message/",
                    {"type": "event", "event": "submit_form", "params": {"name": name}, "ref": ref},
                    request.session.session_key,
                )
                post.user = alice
                assert (await DjustSSEMessageView().post(post, session_id=sid)).status_code == 200
                return drain(session)

            EVENTS.clear()
            frames = await send(1, "SSE name")
            assert "Saved SSE name" in json.dumps(frames), frames
            assert _order({"lookup"}) == ["lookup"]

            REVOKED.add(mine.pk)
            EVENTS.clear()
            frames = await send(2, "Too late")
            assert any(f["type"] == "error" for f in frames), frames
            assert _order({"form", "form_valid"}) == []
        finally:
            _sse_sessions.pop(sid, None)


@pytest.mark.django_db
def test_mount_verdict_is_one_shot():
    from djust.auth.core import check_object_permission

    alice, mine, _ = _world()
    request = _request(alice, "/groups/%s/edit/" % mine.pk)
    view = EditGroupView()
    view.setup(request, pk=mine.pk)
    view._djust_bind_route_kwargs({"pk": mine.pk})
    view.mount(request, pk=mine.pk)
    check_object_permission(view, request)  # the post-mount check: reused
    assert _order({"lookup"}) == ["lookup"]
    check_object_permission(view, request)  # the next dispatch: resolved again
    assert _order({"lookup"}) == ["lookup", "lookup"]
    # A verdict left for one request is never honoured for another.
    view._djust_authorized_object = (request, view.object)
    check_object_permission(view, _request(alice, request.path))
    assert _order({"lookup"}) == ["lookup"] * 3


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("revoke", [False, True])
async def test_websocket_restore_after_http_get_reauthorizes(revoke, monkeypatch):
    """The WS mount restores the GET's saved state instead of calling mount();
    the route still binds and the object is resolved and authorized again."""
    from channels.testing import WebsocketCommunicator
    from djust.websocket import LiveViewConsumer

    alice, mine, _ = await sync_to_async(_world)()
    request, response = await sync_to_async(_get)(SnapshotEditGroupView, alice, mine.pk)
    assert response.status_code == 200
    await sync_to_async(request.session.save)()
    if revoke:
        REVOKED.add(mine.pk)
    EVENTS.clear()
    mounted = []
    original_mount = ModelFormMixin.mount

    def recording_mount(self, *args, **kwargs):
        mounted.append(True)
        return original_mount(self, *args, **kwargs)

    monkeypatch.setattr(ModelFormMixin, "mount", recording_mount)
    with _allow():
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope.update(session=request.session, user=alice, tenant=None)
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=3)
        try:
            await socket.send_json_to(
                {
                    "type": "mount",
                    "view": __name__ + ".SnapshotEditGroupView",
                    "url": request.path,
                    "has_prerendered": True,
                }
            )
            frame = await socket.receive_json_from(timeout=5)
            if revoke:
                assert frame["type"] == "error" and frame["error"] == DENIED, frame
                return
            assert frame["type"] == "mount", frame
            assert mounted == [], "the restore path must not call mount()"
            assert _order({"lookup"}) == ["lookup"], EVENTS
            frames = await _ws_event(socket, 1, "submit_form", {"name": "Restored"})
            assert "Saved Restored" in json.dumps(frames), frames
        finally:
            await socket.disconnect()


class SelfForm(forms.ModelForm):
    class Meta:
        from django.contrib.auth import get_user_model

        model = get_user_model()
        fields = ["first_name"]


class EditSelfView(ModelFormMixin, LiveView):
    template = "<div dj-root><p>{{ object.username }}|{{ object.password }}|</p></div>"
    form_class = SelfForm

    def get_queryset(self):
        from django.contrib.auth import get_user_model

        return get_user_model().objects.filter(pk=self.request.user.pk)


class ExplicitEditSelfView(EditSelfView):
    exposure_policy = "explicit"


@pytest.mark.django_db
@pytest.mark.parametrize("view_class", [EditSelfView, ExplicitEditSelfView])
def test_rendering_the_object_honours_sensitive_fields(view_class):
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user(username="self-" + uuid.uuid4().hex[:8])
    user.set_password("correct horse")
    user.save()
    request = _request(user, "/me/%s/" % user.pk)
    response = view_class.as_view()(request, pk=user.pk)
    html = response.content.decode()
    assert response.status_code == 200
    assert user.username in html
    assert user.password not in html
    assert "pbkdf2" not in html


def test_s013_flags_an_adapter_that_neither_scopes_nor_authorizes():
    import gc

    from djust.checks.security import check_model_form_object_policy

    class UnscopedEditView(ModelFormMixin, LiveView):
        model = Group
        form_class = GroupForm

    class AuthorizedEditView(UnscopedEditView):
        def has_object_permission(self, request, obj):
            return False

    class AcknowledgedEditView(ModelFormMixin, LiveView):  # noqa: S013
        model = Group
        form_class = GroupForm

    try:
        found = {
            message.msg.split(" ")[0].rsplit(".", 1)[-1]
            for message in check_model_form_object_policy(None)
            if message.id == "djust.S013"
        }
        assert "UnscopedEditView" in found
        assert not found & {
            "AuthorizedEditView",
            "AcknowledgedEditView",
            "EditGroupView",
            "EditSelfView",
        }
        with override_settings(DJUST_CONFIG={"suppress_checks": ["S013"]}):
            assert check_model_form_object_policy(None) == []
    finally:
        del UnscopedEditView, AuthorizedEditView, AcknowledgedEditView
        gc.collect()
