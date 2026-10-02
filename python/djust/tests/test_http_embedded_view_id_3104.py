"""#3104: the HTTP fallback routes an embedded child's event to the child.

Over WebSocket and SSE an event carrying an embedded child's ``view_id`` runs on
that child. The HTTP fallback used to ignore ``view_id`` and run it on the
parent (#3159 made it refuse instead). It now routes it: the page is rendered
once to register its children, the child's handler is validated, authorized and
run exactly as the page's own would be, and the answer is the child's own HTML
in an ``embedded_update`` (the frame the socket transports send).

This needs the child's id to be the same on the page GET and on the POST that
follows, and to name the same child even when the page changed in between.
Auto-numbered ids (``child_<N>``) came from a process-wide counter and never
repeated between requests; over HTTP they are now named for what the child is
(its view and its tag's arguments), so a position in the render never decides.

Every case drives the real view (``as_view()``) with a real session, as the
browser's fallback does.
"""

import json
import re

import pytest
from django.contrib.auth.models import AnonymousUser, Permission, User
from django.contrib.contenttypes.models import ContentType
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory

from djust import LiveView
from djust.decorators import permission_required
from djust.decorators import event_handler

MOD = __name__
PATH = "/page/"


class _Receiver:
    """The same handlers on the page and its children; each records what arrived."""

    received = ""

    def _record(self, name):
        self.received = (self.received + " " + name).strip()

    @event_handler
    def got_click(self, **kwargs):
        self._record("click")

    @event_handler
    def got_away(self, **kwargs):
        self._record("away")

    @event_handler
    def with_amount(self, amount: int = 0, **kwargs):
        self._record("amount=%d" % amount)

    @permission_required("auth.view_user")
    @event_handler
    def guarded(self, **kwargs):
        self._record("guarded")

    @event_handler
    def notify(self, **kwargs):
        self.flash("done", "info") if hasattr(self, "flash") else None
        self._record("notify")

    def not_a_handler(self, **kwargs):
        self._record("PRIVATE")


class Child(_Receiver, LiveView):
    exposure_policy = "legacy"
    template = (
        '<div><p>child <span id="r">{{ received }}</span></p>'
        '<button dj-click="got_click">c</button></div>'
    )

    def mount(self, request, **kwargs):
        self.received = ""


class StickyChild(_Receiver, LiveView):
    """A sticky child that opted into state persistence (ADR-018)."""

    exposure_policy = "legacy"
    sticky = True
    sticky_id = "dock"
    enable_state_snapshot = True
    template = '<div><p>dock <span id="r">{{ received }}</span></p></div>'

    def mount(self, request, **kwargs):
        self.received = ""


class DeniedChild(Child):
    """A child whose own permission the request lacks."""

    permission_required = "auth.change_user"


class ExplicitChild(LiveView):
    exposure_policy = "explicit"
    template = "<div><span>explicit</span></div>"


class Page(_Receiver, LiveView):
    exposure_policy = "legacy"
    enable_state_snapshot = True
    template = (
        "{% load live_tags %}"
        '<div dj-root dj-view="' + MOD + '.Page">'
        '<p>page <span id="page-r">{{ received }}</span></p>'
        '{% live_render "' + MOD + '.Child" %}'
        '{% live_render "' + MOD + '.Child" %}'
        '{% live_render "' + MOD + '.Child" view_id="pinned" %}'
        '{% live_render "' + MOD + '.StickyChild" sticky=True %}'
        "</div>"
    )

    def mount(self, request, **kwargs):
        self.received = ""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["view"] = self
        return context


class DeniedPage(Page):
    template = (
        "{% load live_tags %}"
        '<div dj-root dj-view="' + MOD + '.DeniedPage">'
        '{% live_render "' + MOD + '.DeniedChild" view_id="denied" %}'
        "</div>"
    )


class ExplicitParentPage(Page):
    exposure_policy = "explicit"
    template = (
        "{% load live_tags %}"
        '<div dj-root dj-view="' + MOD + '.ExplicitParentPage">'
        '{% live_render "' + MOD + '.Child" view_id="legacy-kid" %}'
        "</div>"
    )


class MixedPage(Page):
    template = (
        "{% load live_tags %}"
        '<div dj-root dj-view="' + MOD + '.MixedPage">'
        '{% live_render "' + MOD + '.ExplicitChild" view_id="ekid" %}'
        "</div>"
    )


def _request(method, session, body=None, event=None, user=None, path=PATH):
    factory = RequestFactory()
    if method == "get":
        request = factory.get(path)
    else:
        request = factory.post(
            path,
            data=json.dumps(body),
            content_type="application/json",
            HTTP_X_DJUST_EVENT=event,
        )
    request.user = user or AnonymousUser()
    request.tenant = None
    request.session = session
    return request


def _page(session, view_class=Page, user=None):
    """GET the page; return the child ids it rendered, in order."""
    response = view_class.as_view()(_request("get", session, user=user))
    html = response.content.decode()
    session.save()
    # Every event-bearing element of a child carries its id: once per child.
    return list(dict.fromkeys(re.findall(r'data-djust-embedded="([^"]+)"', html)))


def _post(session, body, event="got_click", view_class=Page, user=None):
    response = view_class.as_view()(_request("post", session, body, event, user=user))
    session.save()
    return response


def _parent_received(session):
    return session.get("liveview_" + PATH, {}).get("received", "").split()


def _html(response):
    return json.loads(response.content)["html"]


@pytest.fixture
def session():
    store = SessionStore()
    store.create()
    return store


@pytest.fixture
def user(db):
    return User.objects.create_user("u3104", password="x")


# --------------------------------------------------------------------------- #
# Ids: the same template yields the same ids on every request
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_the_ids_a_page_renders_are_the_same_on_every_request(session):
    first = _page(session)
    second = _page(SessionStore(session.session_key))
    # Two auto-named children (one view, one set of arguments: the second is a
    # repeat), a pinned one and a sticky one, in order.
    assert first == second
    assert re.fullmatch(r"child_[0-9a-f]{12}", first[0])
    assert first[1] == first[0] + "_2"
    assert first[2] == "pinned"
    assert "dock" in first


@pytest.mark.django_db
def test_the_ids_a_post_registers_match_the_page_s(session):
    ids = _page(session)
    response = _post(session, {"view_id": ids[1]})
    assert response.status_code == 200
    assert json.loads(response.content)["view_id"] == ids[1]


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
@pytest.mark.parametrize("flat", [True, False])
@pytest.mark.parametrize("which", [0, 1, 2])
def test_an_event_for_an_embedded_child_runs_on_that_child(session, flat, which):
    ids = _page(session)
    child_id = ids[which]
    body = {"view_id": child_id}
    response = _post(session, body if flat else {"event": "got_click", "params": body})
    assert response.status_code == 200, response.content
    data = json.loads(response.content)
    assert data["type"] == "embedded_update"
    assert data["view_id"] == child_id
    assert data["event_name"] == "got_click"
    assert re.search(r'<span id="r"[^>]*>click</span>', data["html"]), data["html"]
    # The page never ran the child's handler, and nothing of it was saved.
    assert _parent_received(session) == []
    follow = _post(session, {}, event="got_away")
    assert follow.status_code == 200
    assert _parent_received(session) == ["away"]


@pytest.mark.django_db
def test_a_child_event_is_not_answered_with_the_page(session):
    ids = _page(session)
    data = json.loads(_post(session, {"view_id": ids[0]}).content)
    assert "patches" not in data and "version" not in data
    assert "page-r" not in data["html"]


@pytest.mark.django_db
def test_a_parent_event_still_runs_on_the_parent(session):
    _page(session)
    response = _post(session, {})
    assert response.status_code == 200
    assert _parent_received(session) == ["click"]


@pytest.mark.django_db
def test_a_sticky_child_that_opted_in_keeps_its_state_across_events(session):
    """Persistence as the socket does it: under the sticky key, restored on the
    next render."""
    ids = _page(session)
    assert "dock" in ids
    first = json.loads(_post(session, {"view_id": "dock"}).content)
    assert re.search(r'<span id="r"[^>]*>click</span>', first["html"])
    second = json.loads(_post(session, {"view_id": "dock"}, event="got_away").content)
    assert re.search(r'<span id="r"[^>]*>click away</span>', second["html"]), second["html"]


@pytest.mark.django_db
def test_a_child_that_did_not_opt_in_starts_fresh_each_request(session):
    """A non-sticky child's state is not persisted over HTTP: each request
    re-creates it, as every parent render always did."""
    ids = _page(session)
    _post(session, {"view_id": ids[0]})
    second = json.loads(_post(session, {"view_id": ids[0]}, event="got_away").content)
    assert re.search(r'<span id="r"[^>]*>away</span>', second["html"])


@pytest.mark.django_db
def test_a_child_event_carries_the_pages_parameter_contract_fields(session):
    ids = _page(session)
    request = _request("post", session, {"view_id": ids[0]}, "got_click")
    request.META["HTTP_X_DJUST_PARAMETER_CONTRACTS"] = "1"
    response = Page.as_view()(request)
    data = json.loads(response.content)
    assert data["parameter_contract_view"] == MOD + ".Page"
    assert "parameter_contracts" in data


# --------------------------------------------------------------------------- #
# Authorization and parameter policy: the page's own layers apply to the child
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_an_undecorated_method_of_the_child_is_not_an_event(session):
    ids = _page(session)
    response = _post(session, {"view_id": ids[0]}, event="not_a_handler")
    assert response.status_code == 400
    assert json.loads(response.content) == {"error": "Event handler not found"}


@pytest.mark.django_db
@pytest.mark.parametrize("event", ["__init__", "_private", "mount"])
def test_an_unsafe_or_unknown_event_name_is_refused_for_a_child(session, event):
    ids = _page(session)
    response = _post(session, {"view_id": ids[0]}, event=event)
    assert response.status_code == 400


@pytest.mark.django_db
def test_a_handler_permission_the_caller_lacks_is_refused_for_a_child(session, user):
    ids = _page(session, user=user)
    response = _post(session, {"view_id": ids[0]}, event="guarded", user=user)
    assert response.status_code == 403
    assert json.loads(response.content)["code"] == "permission_denied"


@pytest.mark.django_db
def test_a_handler_permission_the_caller_holds_runs_on_the_child(session, user):
    ct = ContentType.objects.get_for_model(User)
    user.user_permissions.add(Permission.objects.get(content_type=ct, codename="view_user"))
    user = User.objects.get(pk=user.pk)
    ids = _page(session, user=user)
    response = _post(session, {"view_id": ids[0]}, event="guarded", user=user)
    assert response.status_code == 200, response.content
    assert re.search(r'<span id="r"[^>]*>guarded</span>', _html(response))


@pytest.mark.django_db
def test_a_child_whose_own_permission_the_caller_lacks_is_refused(session, user):
    """The child checks its own view-level auth when the page registers it."""
    with pytest.raises(Exception):
        # The page GET itself refuses to render it (as it always did).
        _page(session, DeniedPage, user=user)
    ids_page = Page  # noqa: F841 - the POST below targets the denied child
    response = DeniedPage.as_view()(
        _request("post", session, {"view_id": "denied"}, "got_click", user=user)
    )
    assert response.status_code == 403
    assert json.loads(response.content)["code"] == "permission_denied"


@pytest.mark.django_db
def test_parameters_are_validated_against_the_childs_handler(session):
    ids = _page(session)
    ok = _post(session, {"view_id": ids[0], "amount": "7"}, event="with_amount")
    assert ok.status_code == 200
    assert re.search(r'<span id="r"[^>]*>amount=7</span>', _html(ok))
    bad = _post(session, {"view_id": ids[0], "amount": "seven"}, event="with_amount")
    assert bad.status_code == 400
    assert json.loads(bad.content)["type"] == "error"


# --------------------------------------------------------------------------- #
# Fail closed: an id that names no routable child is refused, never run elsewhere
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
@pytest.mark.parametrize(
    "forged", ["child_999999", "__root", ["child_1"], 1, "dock ", "child_000000000000"]
)
def test_every_truthy_foreign_view_id_is_refused(session, forged):
    _page(session)
    response = _post(session, {"view_id": forged})
    assert response.status_code == 400
    assert json.loads(response.content) == {"error": "Embedded view not found"}
    assert _parent_received(session) == []


@pytest.mark.django_db
@pytest.mark.parametrize("empty", [None, "", 0, False, []])
@pytest.mark.parametrize("flat", [True, False])
def test_an_empty_view_id_is_the_parent_as_on_the_socket_runtime(session, flat, empty):
    """The runtime pops ``view_id`` and routes a falsy one to the root view."""
    _page(session)
    body = {"view_id": empty}
    response = _post(session, body if flat else {"event": "got_click", "params": body})
    assert response.status_code == 200, response.content
    assert _parent_received(session) == ["click"]


@pytest.mark.django_db
def test_an_explicit_exposure_child_is_not_routed_over_http(session):
    """An explicit child's turn is authorized against a mount binding a
    stateless request has not: it stays refused (fail closed)."""
    _page(session, MixedPage)
    response = _post(session, {"view_id": "ekid"}, view_class=MixedPage)
    assert response.status_code == 400
    assert json.loads(response.content) == {"error": "Embedded view not found"}


@pytest.mark.django_db
def test_nothing_is_routed_for_an_explicit_exposure_page(session):
    _page(session, ExplicitParentPage)
    response = _post(session, {"view_id": "legacy-kid"}, view_class=ExplicitParentPage)
    assert response.status_code == 400
    assert json.loads(response.content) == {"error": "Embedded view not found"}


# --------------------------------------------------------------------------- #
# A page that changes between the GET and the POST (review of #3333, I3)
# --------------------------------------------------------------------------- #


class Row(LiveView):
    exposure_policy = "legacy"
    template = '<div><span id="row">row={{ item_id }}</span></div>'

    def mount(self, request, item_id=None, **kwargs):
        self.item_id = item_id

    @event_handler
    def remove(self, **kwargs):
        self.removed = self.item_id


class Unstable:
    """A kwarg whose repr differs on every request (it carries an address)."""


class ListPage(LiveView):
    """A for-loop of rows; the list is the page's saved state."""

    exposure_policy = "legacy"
    enable_state_snapshot = True
    template = (
        "{% load live_tags %}"
        '<div dj-root dj-view="' + MOD + '.ListPage">'
        "{% for i in items %}"
        '{% live_render "' + MOD + '.Row" item_id=i %}'
        "{% endfor %}"
        "</div>"
    )

    def mount(self, request, **kwargs):
        self.items = [1, 2, 3]

    @event_handler
    def prepend(self, **kwargs):
        self.items = [0] + list(self.items)

    @event_handler
    def drop_first(self, **kwargs):
        self.items = list(self.items)[1:]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["view"] = self
        return context


def _row_of(response):
    return re.search(r"row=(\d+)", json.loads(response.content)["html"]).group(1)


@pytest.mark.django_db
def test_an_id_names_the_same_row_after_another_tab_prepends_one(session):
    """The reviewer's repro: the third row's id, echoed after a row was added
    in front of it, still reaches the third row (it ran on the second when the
    id was the row's position)."""
    ids = _page(session, ListPage)
    assert len(set(ids)) == 3
    response = _post(session, {}, event="prepend", view_class=ListPage)  # tab B
    assert response.status_code == 200
    response = _post(session, {"view_id": ids[2]}, event="remove", view_class=ListPage)
    assert response.status_code == 200
    assert _row_of(response) == "3"
    response = _post(session, {"view_id": ids[0]}, event="remove", view_class=ListPage)
    assert _row_of(response) == "1"


@pytest.mark.django_db
def test_an_id_whose_row_is_gone_is_refused_not_run_on_another(session):
    ids = _page(session, ListPage)
    _post(session, {}, event="drop_first", view_class=ListPage)  # row 1 leaves
    response = _post(session, {"view_id": ids[0]}, event="remove", view_class=ListPage)
    assert response.status_code == 400
    assert json.loads(response.content) == {"error": "Embedded view not found"}
    # The rows that remain are still reachable by their own ids.
    response = _post(session, {"view_id": ids[1]}, event="remove", view_class=ListPage)
    assert response.status_code == 200
    assert _row_of(response) == "2"


@pytest.mark.django_db
def test_a_child_is_named_for_its_view_and_its_arguments():
    from djust.mixins.sticky import _child_identity

    same = _child_identity("a.B", {"x": 1, "y": [1, 2], "z": {"k": "v"}})
    assert same == _child_identity("a.B", {"z": {"k": "v"}, "y": [1, 2], "x": 1})
    assert same != _child_identity("a.C", {"x": 1, "y": [1, 2], "z": {"k": "v"}})
    assert same != _child_identity("a.B", {"x": 2, "y": [1, 2], "z": {"k": "v"}})
    # An object is told by its type, never by a repr that carries an address.
    assert _child_identity("a.B", {"o": Unstable()}) == _child_identity("a.B", {"o": Unstable()})


@pytest.mark.django_db
def test_a_model_argument_names_the_row_not_the_python_object(user):
    from djust.mixins.sticky import _child_identity

    same_row = User.objects.get(pk=user.pk)
    assert _child_identity("a.B", {"u": user}) == _child_identity("a.B", {"u": same_row})


@pytest.mark.django_db
def test_the_identity_is_keyed_and_tells_decimals_and_dates_apart(settings):
    import datetime
    import decimal

    from djust.mixins.sticky import _child_identity

    assert _child_identity("a.B", {"d": decimal.Decimal(1)}) != _child_identity(
        "a.B", {"d": decimal.Decimal(2)}
    )
    assert _child_identity("a.B", {"d": datetime.date(2026, 1, 1)}) != _child_identity(
        "a.B", {"d": datetime.date(2026, 1, 2)}
    )
    assert _child_identity("a.B", {"k": {1: "a"}}) != _child_identity("a.B", {"k": {"1": "a"}})
    # Keyed: another SECRET_KEY names the same child differently, so a client
    # cannot confirm a guessed argument by hashing it.
    settings.SECRET_KEY = "one-key"
    first = _child_identity("a.B", {"pk": 5})
    assert first == _child_identity("a.B", {"pk": 5})
    settings.SECRET_KEY = "another-key"
    assert _child_identity("a.B", {"pk": 5}) != first


@pytest.mark.django_db
def test_the_identity_is_not_computed_when_ids_come_from_the_counter(monkeypatch):
    """Over a socket the id is the counter's: the tag's arguments are not hashed."""
    from djust.mixins import sticky

    calls = []
    monkeypatch.setattr(sticky, "_child_identity", lambda *a: calls.append(a) or "x" * 12)
    parent = Page()
    assert parent._assign_view_id(None, lambda: sticky._child_identity("a", {})).startswith(
        "child_"
    )
    assert calls == []
    parent.__dict__["_render_child_ids"] = __import__("itertools").count(1)
    assert (
        parent._assign_view_id(None, lambda: sticky._child_identity("a", {})) == "child_" + "x" * 12
    )
    assert len(calls) == 1
