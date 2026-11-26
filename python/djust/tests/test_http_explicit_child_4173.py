"""#4173: an explicit ``{% live_render %}`` child takes events over the HTTP fallback.

Over WebSocket and SSE an event carrying an explicit child's ``view_id`` runs on
that child. The HTTP fallback refused it (``Embedded view not found``) because
only a legacy page's legacy child was routed, and an explicit page kept the
process-wide id counter, so the ids of the GET never came back on the POST.

Now an explicit page's explicit child is routed: its ids are named for the child
on both policies, the POST's render re-mounts it under the POST's own request
(view auth, object permission, identity), and the socket runtime's event checks
(``child_event_adapter``) pass against that request. A mixed-policy pair stays
refused (``test_http_embedded_view_id_3104.py``).

Every case drives the real view (``as_view()``) with a real session.
"""

import json
import re

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import AnonymousUser, User
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings
from django.utils.decorators import method_decorator

from djust import LiveView, event_handler, state
from djust.decorators import permission_required
from djust.tests._frames import legacy_type

MOD = __name__
PATH = "/page/"
_AUTO = r"child_[0-9a-f]{12}"


class Kid(LiveView):
    exposure_policy = "explicit"
    count = state(0)
    template = '<div><p>kid <span class="count">{{ count }}</span></p><b dj-click="inc">+</b></div>'

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1

    @permission_required("auth.view_user")
    @event_handler()
    def guarded(self, **kwargs):
        self.count += 100


class StickyKid(LiveView):
    exposure_policy = "explicit"
    sticky = True
    sticky_id = "dock"
    count = state(0, persist="server")
    template = (
        '<div><p>dock <span class="count">{{ count }}</span></p><b dj-click="inc">+</b></div>'
    )

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1


class DeniedKid(Kid):
    permission_required = "auth.change_user"


class ObjKid(Kid):
    """Its object permission is withdrawn by its own ``lock`` handler."""

    def get_object(self):
        return "the-object"

    def has_object_permission(self, request, obj):
        return not getattr(self, "_locked", False)

    @event_handler()
    def lock(self, **kwargs):
        self._locked = True


class PostGuardKid(Kid):
    """Only its ``post()`` is login-gated: the gate applies to events, not mounts."""

    @method_decorator(login_required)
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class LabelKid(LiveView):
    exposure_policy = "explicit"
    label = state("")
    template = "<span>KID {{ label }}</span>"
    seen: list = []

    def mount(self, request, label="", **kwargs):
        type(self).seen.append(type(label))
        self.label = label


class TemplateHost(LiveView):
    exposure_policy = "explicit"
    template_name = "host.html"


class NestedHost(LiveView):
    exposure_policy = "explicit"
    template = (
        "{% load live_tags %}"
        '<section class="host">{% live_render "' + MOD + '.Kid" label="inner" %}</section>'
    )


class Page(LiveView):
    exposure_policy = "explicit"
    show = state(True)
    template = (
        "{% load live_tags %}"
        '<div dj-root dj-view="' + MOD + '.Page"><p>page</p>'
        '{% live_render "' + MOD + '.Kid" label="a" %}'
        '{% live_render "' + MOD + '.Kid" label="b" %}'
        '{% live_render "' + MOD + '.Kid" view_id="pinned" %}'
        '{% live_render "' + MOD + '.StickyKid" sticky=True %}'
        '{% if show %}{% live_render "' + MOD + '.Kid" label="maybe" %}{% endif %}'
        "</div>"
    )


class PageWithoutMaybe(Page):
    template = Page.template.replace(
        '{% if show %}{% live_render "' + MOD + '.Kid" label="maybe" %}{% endif %}', ""
    ).replace(MOD + ".Page", MOD + ".PageWithoutMaybe")


class NestedPage(LiveView):
    exposure_policy = "explicit"
    template = (
        "{% load live_tags %}"
        '<div dj-root dj-view="' + MOD + '.NestedPage">'
        '{% live_render "' + MOD + '.NestedHost" %}</div>'
    )


class DeniedPage(LiveView):
    exposure_policy = "explicit"
    template = (
        "{% load live_tags %}"
        '<div dj-root dj-view="' + MOD + '.DeniedPage">'
        '{% live_render "' + MOD + '.Kid" view_id="kid" %}</div>'
    )


class ObjPage(LiveView):
    exposure_policy = "explicit"
    template = (
        "{% load live_tags %}"
        '<div dj-root dj-view="' + MOD + '.ObjPage">'
        '{% live_render "' + MOD + '.ObjKid" view_id="kid" %}</div>'
    )


class PostGuardPage(LiveView):
    exposure_policy = "explicit"
    template = (
        "{% load live_tags %}"
        '<div dj-root dj-view="' + MOD + '.PostGuardPage">'
        '{% live_render "' + MOD + '.PostGuardKid" view_id="kid" %}</div>'
    )


class LazyPage(LiveView):
    exposure_policy = "explicit"
    template = (
        "{% load live_tags %}"
        '<div dj-root dj-view="' + MOD + '.LazyPage">'
        '{% live_render "' + MOD + '.Kid" lazy=True %}'
        '{% live_render "' + MOD + '.DeniedKid" lazy=True %}</div>'
    )


class LazyEagerPage(LiveView):
    """A lazy and an eager tag with one view and one set of arguments, and two
    identical lazy tags (#4173 review): every one gets its own id."""

    exposure_policy = "explicit"
    template = (
        "{% load live_tags %}"
        '<div dj-root dj-view="' + MOD + '.LazyEagerPage">'
        '{% live_render "' + MOD + '.Kid" lazy=True %}'
        '{% live_render "' + MOD + '.Kid" %}'
        '{% live_render "' + MOD + '.Kid" lazy=True %}</div>'
    )


def _request(method, session, body=None, event=None, user=None):
    factory = RequestFactory()
    if method == "get":
        request = factory.get(PATH)
    else:
        request = factory.post(
            PATH, data=json.dumps(body), content_type="application/json", HTTP_X_DJUST_EVENT=event
        )
    request.user = user or AnonymousUser()
    request.tenant = None
    request.session = session
    return request


def _page(session, view_class=Page, user=None):
    """GET the page; return (child ids in order, html)."""
    response = view_class.as_view()(_request("get", session, user=user))
    assert response.status_code == 200
    html = response.content.decode()
    session.save()
    return list(dict.fromkeys(re.findall(r'data-djust-embedded="([^"]+)"', html))), html


def _post(session, body, event="inc", view_class=Page, user=None):
    response = view_class.as_view()(_request("post", session, body, event, user=user))
    session.save()
    return response


def _count(response):
    data = json.loads(response.content)
    return int(re.search(r'class="count">(\d+)<', data["html"]).group(1))


@pytest.fixture
def session():
    store = SessionStore()
    store.create()
    return store


@pytest.fixture(autouse=True)
def _allowed():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust", MOD], DEBUG=False):
        yield


@pytest.mark.django_db
def test_auto_ids_repeat_between_get_and_post(session):
    first, _ = _page(session)
    second, _ = _page(SessionStore(session.session_key))
    assert first == second
    auto = [i for i in first if i not in ("pinned", "dock")]
    assert len(auto) == 3
    assert all(re.fullmatch(_AUTO, i) for i in auto)
    assert "pinned" in first and "dock" in first


@pytest.mark.django_db
@pytest.mark.parametrize("which", ["auto", "pinned", "sticky"])
def test_event_for_explicit_child_runs_on_it_over_http(session, which):
    ids, _ = _page(session)
    view_id = {"auto": ids[0], "pinned": "pinned", "sticky": "dock"}[which]
    response = _post(session, {"view_id": view_id})
    assert response.status_code == 200, response.content
    data = json.loads(response.content)
    assert legacy_type(data) == "embedded_update"
    assert data["view_id"] == view_id
    assert _count(response) == 1


@pytest.mark.django_db
def test_sticky_child_persisted_field_survives_two_posts(session):
    ids, _ = _page(session)
    assert [_count(_post(session, {"view_id": "dock"})) for _ in range(2)] == [1, 2]
    # A transient child (no persisted field, ADR-038 D-m) keeps nothing.
    assert [_count(_post(session, {"view_id": ids[0]})) for _ in range(2)] == [1, 1]


@pytest.mark.django_db
def test_nested_explicit_child_is_routed(session):
    ids, _ = _page(session, NestedPage)
    nested = [i for i in ids if re.fullmatch(_AUTO, i)]
    assert len(nested) == 2  # the host and the kid inside it
    inner = nested[-1]
    response = _post(session, {"view_id": inner}, view_class=NestedPage)
    assert response.status_code == 200, response.content
    data = json.loads(response.content)
    assert data["view_id"] == inner
    assert _count(response) == 1


@pytest.mark.django_db
def test_post_for_a_child_the_page_did_not_render_is_refused(session):
    ids, _ = _page(session)
    response = _post(session, {"view_id": "child_000000000000"})
    assert response.status_code == 400
    assert json.loads(response.content) == {"error": "Embedded view not found"}

    # The {% if show %} kid: rendered by the GET, not by a page without it.
    without, _ = _page(SessionStore(session.session_key), PageWithoutMaybe)
    (maybe,) = [i for i in ids if i not in without]
    assert _post(session, {"view_id": maybe}).status_code == 200
    response = _post(session, {"view_id": maybe}, view_class=PageWithoutMaybe)
    assert response.status_code == 400
    assert json.loads(response.content) == {"error": "Embedded view not found"}


@pytest.mark.django_db
def test_other_session_cannot_reach_the_child(session):
    """An id names what the child is, not whose: session B's POST with session
    A's ids reaches B's own child, never A's state (the identity, and the state
    binding, include the session)."""
    ids, _ = _page(session)
    assert [_count(_post(session, {"view_id": "dock"})) for _ in range(2)] == [1, 2]
    other = SessionStore()
    other.create()
    # B never loaded the page: its POST mounts B's own children.
    assert _count(_post(other, {"view_id": "dock"})) == 1
    assert _count(_post(session, {"view_id": "dock"})) == 3
    response = _post(other, {"view_id": "child_000000000000"})
    assert response.status_code == 400


@pytest.mark.django_db
def test_child_permission_and_handler_permission_are_enforced(session, monkeypatch):
    user = User.objects.create_user("u4173", password="x")
    ids, _ = _page(session, DeniedPage, user=user)
    # The handler needs a permission the caller lacks.
    response = _post(session, {"view_id": "kid"}, event="guarded", view_class=DeniedPage, user=user)
    assert response.status_code == 403
    # The child itself needs a permission the caller lacks.
    monkeypatch.setattr(Kid, "permission_required", "auth.change_user")
    response = _post(session, {"view_id": "kid"}, view_class=DeniedPage, user=user)
    assert response.status_code == 403


@pytest.mark.django_db
def test_object_permission_is_checked_again_after_the_handler(session):
    """As the socket runtime re-checks an explicit child's object after its handler."""
    _page(session, ObjPage)
    assert _post(session, {"view_id": "kid"}, view_class=ObjPage).status_code == 200
    response = _post(session, {"view_id": "kid"}, event="lock", view_class=ObjPage)
    assert response.status_code == 403
    assert json.loads(response.content)["code"] == "permission_denied"


@pytest.mark.django_db
def test_child_post_only_gate_applies_to_its_events(session):
    """The child's view auth is checked for an event (``event=True``), which
    also applies what only its ``post()`` runs; the mount did not."""
    ids, html = _page(session, PostGuardPage)
    assert ids == ["kid"]
    response = _post(session, {"view_id": "kid"}, view_class=PostGuardPage)
    assert response.status_code == 403


@pytest.mark.django_db
def test_lazy_tag_child_takes_events_over_http(session):
    """A ``{% live_render lazy %}`` child: its fill runs only on the GET, so the
    POST mounts that one slot's child to route its event (#4173 follow-up)."""
    ids, html = _page(session, LazyPage)
    slots = re.findall(r'<dj-lazy-slot[^>]*data-id="([^"]+)"', html)
    assert len(slots) == 2 and all(re.fullmatch(_AUTO, i) for i in slots)
    kid, denied = slots
    assert kid in ids  # filled
    assert [_count(_post(session, {"view_id": kid}, view_class=LazyPage)) for _ in "ab"] == [1, 1]
    # The denied slot's fill refuses on the POST too: nothing is registered.
    response = _post(session, {"view_id": denied}, view_class=LazyPage)
    assert response.status_code == 400
    assert json.loads(response.content) == {"error": "Embedded view not found"}


@pytest.mark.django_db
def test_lazy_and_eager_twins_get_distinct_ids_and_each_is_routed(session):
    ids, html = _page(session, LazyEagerPage)
    slots = re.findall(r'<dj-lazy-slot[^>]*data-id="([^"]+)"', html)
    assert len(slots) == 2 and len(set(slots)) == 2
    # Both slots filled, and the eager twin has an id of its own.
    assert set(slots) <= set(ids) and len(ids) == 3, (ids, slots)
    assert 'data-status="error"' not in html
    for view_id in ids:
        response = _post(session, {"view_id": view_id}, view_class=LazyEagerPage)
        assert response.status_code == 200, (view_id, response.content)
        data = json.loads(response.content)
        assert data["view_id"] == view_id and _count(response) == 1


@pytest.mark.django_db
@pytest.mark.parametrize("where", ["page", "nested"])
def test_template_literal_kwargs_reach_an_explicit_child_as_str(tmp_path, where):
    """Django marks a tag's string literal safe; an explicit child's inputs are
    exact-str JSON identities, so the tag normalises them (#4173 review)."""
    from django.contrib.sessions.backends.signed_cookies import SessionStore as Signed

    tag = '{% live_render "' + MOD + '.LabelKid" label="inner" %}'
    (tmp_path / "host.html").write_text("{% load live_tags %}<section>" + tag + "</section>")
    if where == "page":
        page_tag = tag
    else:
        page_tag = '{% live_render "' + MOD + '.TemplateHost" %}'
    (tmp_path / "page.html").write_text("{% load live_tags %}<main dj-root>" + page_tag + "</main>")

    class TemplatePage(LiveView):
        exposure_policy = "explicit"
        template_name = "page.html"

    LabelKid.seen.clear()
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    request.session = Signed()
    request.tenant = None
    templates = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": True,
        }
    ]
    with override_settings(TEMPLATES=templates, DJUST_LIVE_RENDER_ALLOWED_MODULES=[MOD]):
        page = TemplatePage()
        page.request = request
        html, _, _ = page.render_with_diff(request)
    assert "KID inner" in html
    assert LabelKid.seen == [str]


def _strip(html):
    html = re.sub(r'\s*data-djust-embedded="[^"]*"', "", html)
    return re.sub(r'\s*dj-id="[^"]*"', "", html)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_same_event_over_ws_sse_and_http_agrees(monkeypatch):
    from djust import sse
    from djust.sse import _sse_sessions
    from djust.websocket import LiveViewConsumer

    from . import test_sse_target_view_3252 as sse_helpers
    from ._ws_frames import has_type, receive_until
    from .test_exposure_runtime import make_request

    htmls = {}

    # WebSocket
    request = await sync_to_async(make_request)()
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=request.session, user=request.user, tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from()
    try:
        await socket.send_json_to({"type": "mount", "view": MOD + ".Page", "url": PATH})
        mount = await socket.receive_json_from()
        assert mount["type"] == "mount"
        kid = re.search(r'data-djust-embedded="(child_[^"]+)"', mount["html"]).group(1)
        await socket.send_json_to({"type": "event", "event": "inc", "params": {"view_id": kid}})
        frames = await receive_until(socket, has_type("embedded_update"))
        htmls["ws"] = [f for f in frames if legacy_type(f) == "embedded_update"][0]["html"]
    finally:
        await socket.disconnect()

    # SSE: the helpers' page is their own; point them at ours.
    monkeypatch.setattr(sse, "_SESSION_LINGER_S", 0)
    monkeypatch.setattr(sse_helpers, "MOD", MOD)
    _sse_sessions.clear()
    try:
        sse_session, key, stream = await sse_helpers._open()
        view = sse_session.view_instance
        kid = next(i for i in view._get_all_child_views() if re.fullmatch(r"child_\d+", i))
        await sse_helpers._post(
            sse_session, key, {"type": "event", "event": "inc", "params": {"view_id": kid}}
        )
        htmls["sse"] = (await sse_helpers._frame(stream, "embedded_update"))["html"]
    finally:
        _sse_sessions.clear()

    # HTTP
    def http():
        store = SessionStore()
        store.create()
        ids, _ = _page(store)
        return json.loads(_post(store, {"view_id": ids[0]}).content)["html"]

    htmls["http"] = await sync_to_async(http)()

    assert "1" in htmls["http"]
    assert _strip(htmls["ws"]) == _strip(htmls["sse"]) == _strip(htmls["http"])
