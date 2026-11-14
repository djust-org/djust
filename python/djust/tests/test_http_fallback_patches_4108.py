"""Patches over the page-POST fallback: when, and what the extra render costs (#4108).

``mixins/request.py`` renders the restored view once before the handler
(``_http_fallback_baseline.prepare_baseline``). The answer is patches only when
that render is the page the client shows, which the client proves by echoing
the render's digest. These tests pin every condition that selects the patch
path, the hostile ways of supplying a digest, and what the pre-handler render
must not use up.
"""

from __future__ import annotations

import json
import re
from functools import cached_property

import pytest
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.paginator import Paginator
from django.test import Client, override_settings
from django.urls import path

from djust import LiveView
from djust.decorators import cache, event_handler, rate_limit, state
from djust.tests import http_fallback_matrix as matrix
from djust.tests.http_fallback_matrix import PAGE, answer_kind, meta_digest, post_event

pytestmark = [pytest.mark.django_db(transaction=True)]

#: Today a view's saved state is shared by every tab of a session on a route,
#: so one tab's event makes the other tab's digest stale. When saved state
#: becomes per page load (#4106) the tabs are independent: flip this, and
#: ``test_two_tabs_on_one_route`` expects patches for the second tab too.
TABS_SHARE_SAVED_STATE = True


class Notices(LiveView):
    """Django messages, a redirect, a skipped render, an ``@cache`` handler."""

    exposure_policy = "explicit"
    template = PAGE % (
        '\n<ul class="notices">{% for m in messages %}<li>{{ m }}</li>{% endfor %}</ul>\n'
        "<p>Value: <b>{{ count }}</b></p>\n"
    )
    count = state(0, persist="server")

    @event_handler
    def increment(self, **kwargs):
        self.count += 1

    @event_handler
    def leave(self, **kwargs):
        self.live_redirect("/m/counter/")

    @event_handler
    def quiet(self, **kwargs):
        self.count += 1
        self._skip_render = True

    @event_handler
    def announce(self, **kwargs):
        messages.info(self.request, "saved")
        self.count += 1

    @event_handler
    @cache(ttl=60)
    def cached(self, **kwargs):
        self.count += 1

    @event_handler
    @rate_limit(rate=1, burst=1)
    def limited(self, **kwargs):
        self.count += 1

    @event_handler
    def typed(self, amount: int = 0, **kwargs):
        self.count += amount


class Counting(LiveView):
    """Counts its own ``get_context_data`` calls, per request."""

    exposure_policy = "explicit"
    template = PAGE % "\n<p>Value: <b>{{ count }}</b></p>\n"
    count = state(0, persist="server")
    calls: list[int] = []

    def get_context_data(self, **kwargs):
        type(self).calls.append(1)
        return super().get_context_data(**kwargs)

    @event_handler
    def increment(self, **kwargs):
        self.count += 1


class OptedOut(LiveView):
    exposure_policy = "explicit"
    http_fallback_patches = False
    template = matrix.Counter.template
    count = state(0, persist="server")

    @event_handler
    def increment(self, **kwargs):
        self.count += 1


class Streamed(LiveView):
    exposure_policy = "explicit"
    template = PAGE % (
        '\n<ul id="rows" dj-update="append">{% for r in streams.rows %}'
        '<li id="{{ r.dom_id }}">{{ r.text }}</li>{% endfor %}</ul>\n<p id="n">{{ count }}</p>\n'
    )
    count = state(0, persist="server")

    def mount(self, request, **kwargs):
        self.stream("rows", [{"id": 1, "text": "one"}])

    @event_handler
    def increment(self, **kwargs):
        self.count += 1


class Ignored(LiveView):
    """A client-owned region: its DOM is not the server's render."""

    exposure_policy = "explicit"
    template = PAGE % (
        '\n<div id="widget" dj-update="ignore"><span>widget</span></div>\n'
        "<p>Value: <b>{{ count }}</b></p>\n"
    )
    count = state(0, persist="server")

    @event_handler
    def increment(self, **kwargs):
        self.count += 1


class Wide(LiveView):
    """One event changes more nodes than a patch answer may carry."""

    exposure_policy = "explicit"
    template = PAGE % "\n<ul>{% for n in numbers %}<li>{{ n }}</li>{% endfor %}</ul>\n"
    offset = state(0, persist="server")

    def get_context_data(self, **kwargs):
        return super().get_context_data(numbers=[self.offset + i for i in range(150)], **kwargs)

    @event_handler
    def shift(self, **kwargs):
        self.offset += 1000


class Fragile(LiveView):
    """Its next ``failures`` renders raise; later ones do not."""

    exposure_policy = "explicit"
    template = PAGE % "\n<p>Value: <b>{{ count }}</b></p>\n"
    count = state(0, persist="server")
    failures = 0

    def get_context_data(self, **kwargs):
        if type(self).failures > 0:
            type(self).failures -= 1
            raise RuntimeError("this render fails")
        return super().get_context_data(**kwargs)

    @event_handler
    def increment(self, **kwargs):
        self.count += 1


class Nested(LiveView):
    """Its handler makes another request of the same session before it returns."""

    exposure_policy = "explicit"
    template = PAGE % "\n<p>Value: <b>{{ count }}</b></p>\n"
    count = state(0, persist="server")
    inner: list = []

    @event_handler
    def increment(self, **kwargs):
        self.count += 1

    @event_handler
    def outer(self, **kwargs):
        for run in type(self).inner:
            run()
        self.count += 10


class Members(LiveView):
    login_required = True
    exposure_policy = "explicit"
    template = PAGE % "\n<p>Value: <b>{{ count }}</b></p>\n"
    count = state(0, persist="server")

    @event_handler
    def touch(self, **kwargs):
        pass


class Inner(LiveView):
    exposure_policy = "explicit"
    template = "<div><span>inner view</span></div>"


class Outer(LiveView):
    """A page that embeds another view."""

    exposure_policy = "explicit"
    template = "{% load live_tags %}" + PAGE % (
        '\n<p>Value: <b>{{ count }}</b></p>\n{% live_render "' + __name__ + '.Inner" %}\n'
    )
    count = state(0, persist="server")

    @event_handler
    def increment(self, **kwargs):
        self.count += 1


class Greeter(LiveView):
    """Renders the signed-in user, which a context processor supplies."""

    exposure_policy = "explicit"
    template = PAGE % '\n<p>Hello <b>{{ user.username|default:"stranger" }}</b></p>\n'

    @event_handler
    def sign_in(self, **kwargs):
        from django.contrib.auth import login

        login(
            self.request,
            get_user_model().objects.get(username="greeted"),
            backend="django.contrib.auth.backends.ModelBackend",
        )


def _rename_alice() -> None:
    get_user_model().objects.filter(username="alice").update(username="bob")


class _Roster(LiveView):
    """Shows users it read before the handler; the handler renames one in the database."""

    exposure_policy = "explicit"
    template = PAGE % (
        "\n<ul>{% for name in names %}<li>{{ name }}</li>{% endfor %}</ul>\n"
        "<p>Renames: <b>{{ count }}</b></p>\n"
    )
    count = state(0, persist="server")

    def names(self) -> list[str]:
        raise NotImplementedError

    def get_context_data(self, **kwargs):
        return super().get_context_data(names=self.names(), **kwargs)

    @event_handler
    def rename(self, **kwargs):
        _rename_alice()
        self.count += 1


class HeldQueryset(_Roster):
    """``mount()`` keeps a queryset; rendering it fills its result cache."""

    count = state(0, persist="server")

    def mount(self, request, **kwargs):
        self._rows = get_user_model().objects.order_by("pk")

    def names(self):
        return [user.username for user in self._rows]


class CachedProperty(_Roster):
    count = state(0, persist="server")

    @cached_property
    def roster(self):
        return list(get_user_model().objects.order_by("pk").values_list("username", flat=True))

    def names(self):
        return self.roster


class RelatedManager(_Roster):
    """Rows read through a related manager, kept unevaluated by ``mount()``."""

    count = state(0, persist="server")

    def mount(self, request, **kwargs):
        self._members = Group.objects.get(name="crew").user_set.order_by("pk")

    def names(self):
        return [user.username for user in self._members]


class Paginated(_Roster):
    count = state(0, persist="server")

    def mount(self, request, **kwargs):
        self._pages = Paginator(get_user_model().objects.order_by("pk"), 10)

    def names(self):
        return [user.username for user in self._pages.page(1)] + ["of %d" % self._pages.count]


def _member_names(request):
    """A context processor that queries on every call."""
    return {
        "member_names": ", ".join(
            get_user_model().objects.order_by("pk").values_list("username", flat=True)
        )
    }


class QueryingProcessor(LiveView):
    exposure_policy = "explicit"
    template = PAGE % '\n<p class="members">{{ member_names }}</p>\n<p><b>{{ count }}</b></p>\n'
    count = state(0, persist="server")

    def _get_context_processors(self):
        return [*super()._get_context_processors(), __name__ + "._member_names"]

    @event_handler
    def rename(self, **kwargs):
        _rename_alice()
        self.count += 1


STALE_DATA_VIEWS = {
    "queryset": HeldQueryset,
    "cached-property": CachedProperty,
    "related-manager": RelatedManager,
    "paginator": Paginated,
    "context-processor": QueryingProcessor,
}


urlpatterns = [
    *matrix.urlpatterns,
    path("p/notices/", Notices.as_view()),
    path("p/counting/", Counting.as_view()),
    path("p/opted-out/", OptedOut.as_view()),
    path("p/streamed/", Streamed.as_view()),
    path("p/ignored/", Ignored.as_view()),
    path("p/wide/", Wide.as_view()),
    path("p/fragile/", Fragile.as_view()),
    path("p/nested/", Nested.as_view()),
    path("p/members/", Members.as_view()),
    *(path("p/stale/%s/" % name, cls.as_view()) for name, cls in STALE_DATA_VIEWS.items()),
    path("p/outer/", Outer.as_view()),
    path("p/greeter/", Greeter.as_view()),
    # The same view class on a second route.
    path("p/counter-again/", matrix.Counter.as_view()),
]

SETTINGS = {
    "ROOT_URLCONF": __name__,
    "ALLOWED_HOSTS": ["*"],
    "DEBUG": False,
    "LIVEVIEW_ALLOWED_MODULES": ["djust"],
}


@pytest.fixture(autouse=True)
def _routes():
    with override_settings(**SETTINGS):
        yield


@pytest.fixture
def renders(monkeypatch):
    return matrix.RenderLog(monkeypatch)


#: Shaped like a token, and nobody's.
WRONG = "A" * 22 + "." + "0" * 16


def _open(url: str, client: Client | None = None) -> tuple[Client, str]:
    """GET ``url``; the client and the digest the page carries."""
    client = client or Client()
    response = client.get(url)
    assert response.status_code == 200, response.status_code
    digest = meta_digest(response.content.decode())
    assert digest
    return client, digest


def _send(
    client: Client, url: str, event: str, digest: str | None, params=None, status=200
) -> dict:
    response = post_event(client, url, event, params or {}, digest)
    assert response.status_code == status, (response.status_code, response.content[:300])
    return json.loads(response.content)


def _counted(renders, client, url, event, digest, params=None, status=200) -> tuple[dict, int]:
    before = len(renders.htmls)
    answer = _send(client, url, event, digest, params, status)
    return answer, len(renders.htmls) - before


# --------------------------------------------------------------------------- #
# The patch path
# --------------------------------------------------------------------------- #


def test_a_matching_digest_is_answered_with_path_addressed_patches_and_no_version(renders):
    client, digest = _open("/m/counter/")
    answer, count = _counted(renders, client, "/m/counter/", "increment", digest)
    assert count == 2  # the baseline render and the render after the handler
    assert "html" not in answer and "version" not in answer
    assert answer["patches"] == [{"type": "SetText", "path": [1, 0, 1, 0], "text": "1"}]
    # The next digest is the digest of the new render: echoing it gets patches again.
    assert answer["render_digest"] != digest
    again = _send(client, "/m/counter/", "increment", answer["render_digest"])
    assert answer_kind(again) == "patches"


def test_a_render_that_changes_nothing_is_an_empty_patch_list_with_the_same_digest(renders):
    user = get_user_model().objects.create_user("quiet-user", password="pw")
    client = Client()
    client.force_login(user)
    _client, digest = _open("/p/members/", client)
    answer, count = _counted(renders, client, "/p/members/", "touch", digest)
    assert count == 2
    assert answer["patches"] == [] and "version" not in answer and "html" not in answer
    assert answer["render_digest"] == digest


def test_no_digest_means_one_render_and_the_whole_page_exactly_as_before(renders):
    client, _digest = _open("/m/counter/")
    answer, count = _counted(renders, client, "/m/counter/", "increment", None)
    assert count == 1
    assert "patches" not in answer and answer["version"] == 1
    assert ">1</strong>" in answer["html"]


@pytest.mark.parametrize("where", ["setting", "view"])
def test_switched_off_the_answer_is_the_whole_page_from_one_render(renders, where):
    url = "/p/opted-out/" if where == "view" else "/m/counter/"
    overrides = {"LIVEVIEW_CONFIG": {"http_fallback_patches": False}} if where == "setting" else {}
    with override_settings(**overrides):
        from djust.config import config

        config.reload() if hasattr(config, "reload") else None
        client, digest = _open(url)
        answer, count = _counted(renders, client, url, "increment", digest)
    assert count == 1
    assert "patches" not in answer and answer["version"] == 1


def test_a_refused_digest_gets_byte_for_byte_the_answer_of_a_request_that_sent_none(renders):
    """The whole-page answer after a mismatch is the answer this transport always gave."""
    refused_client, _ = _open("/m/list/")
    plain_client, _ = _open("/m/list/")
    for event, params in matrix.SCENARIOS["list"][1]:
        refused = _send(refused_client, "/m/list/", event, "A" * 22, params)
        plain = _send(plain_client, "/m/list/", event, None, params)
        # The digest is bound to the session; everything else is equal,
        # generated ids included.
        refused.pop("render_digest")
        plain.pop("render_digest")
        assert refused == plain
        assert "patches" not in refused


def test_more_patches_than_a_patch_answer_carries_is_the_whole_page(renders):
    client, digest = _open("/p/wide/")
    answer, count = _counted(renders, client, "/p/wide/", "shift", digest)
    assert count == 2
    assert "patches" not in answer and answer["version"] == 1
    assert "<li" in answer["html"] and ">1149</li>" in answer["html"]
    # Its digest is that page's: the next event is patched or not on its merits.
    assert answer["render_digest"]


# --------------------------------------------------------------------------- #
# Hostile and stale digests: each selects the whole page
# --------------------------------------------------------------------------- #


def _is_whole_page_of(answer: dict, text: str) -> bool:
    return "patches" not in answer and answer.get("version") == 1 and text in answer["html"]


def test_a_forged_digest_selects_the_whole_page(renders):
    client, digest = _open("/m/counter/")
    forged = ("B" if digest[0] != "B" else "C") + digest[1:]
    assert _is_whole_page_of(_send(client, "/m/counter/", "increment", forged), ">1</strong>")


def test_a_stale_digest_selects_the_whole_page(renders):
    client, digest = _open("/m/counter/")
    first = _send(client, "/m/counter/", "increment", digest)
    assert answer_kind(first) == "patches"
    # The page GET's digest again, after the state moved on.
    assert _is_whole_page_of(_send(client, "/m/counter/", "increment", digest), ">2</strong>")


def test_a_digest_from_another_session_selects_the_whole_page(renders):
    _other, foreign = _open("/m/counter/")
    client, own = _open("/m/counter/")
    # Both sessions show the same page, with different digests.
    assert foreign != own
    assert _is_whole_page_of(_send(client, "/m/counter/", "increment", foreign), ">1</strong>")


def test_a_digest_from_another_route_of_the_same_view_selects_the_whole_page(renders):
    client, elsewhere = _open("/p/counter-again/")
    _client, here = _open("/m/counter/", client)
    assert elsewhere != here
    assert _is_whole_page_of(_send(client, "/m/counter/", "increment", elsewhere), ">1</strong>")
    # The digest that is this route's would have been accepted.
    fresh, digest = _open("/m/counter/")
    assert answer_kind(_send(fresh, "/m/counter/", "increment", digest)) == "patches"


def test_a_digest_replayed_after_logout_and_login_selects_the_whole_page(renders):
    user = get_user_model().objects.create_user("digest-user", password="pw")
    client = Client()
    client.force_login(user)
    _client, before = _open("/p/members/", client)
    assert answer_kind(_send(client, "/p/members/", "touch", before)) == "patches"
    client.logout()
    assert post_event(client, "/p/members/", "touch", {}, before).status_code == 403
    client.force_login(user)
    # Same user, same page, same content: a new session, so the old digest is foreign.
    answer = _send(client, "/p/members/", "touch", before)
    assert _is_whole_page_of(answer, ">0</b>")
    assert answer["render_digest"] != before


def test_two_tabs_on_one_route(renders):
    """Tab 2's page is stale once tab 1's event changed the state both share."""
    session = Client()
    _c, tab1 = _open("/m/counter/", session)
    # The second tab's page load. (A page GET mounts the view again, so both
    # tabs show the same first page.)
    _c, tab2 = _open("/m/counter/", session)
    first = _send(session, "/m/counter/", "increment", tab1)
    assert answer_kind(first) == "patches"
    second = _send(session, "/m/counter/", "increment", tab2)
    if TABS_SHARE_SAVED_STATE:
        # Tab 2 shows 0, the saved state says 1: the whole page (2), which heals it.
        assert _is_whole_page_of(second, ">2</strong>")
        # Tab 1 is now the stale one.
        third = _send(session, "/m/counter/", "increment", first["render_digest"])
        assert _is_whole_page_of(third, ">3</strong>")
    else:
        assert second["patches"] == [{"type": "SetText", "path": [1, 0, 1, 0], "text": "1"}]


def test_a_template_changed_between_two_requests_selects_the_whole_page(renders, monkeypatch):
    """A deploy between the page GET and the event."""
    client, digest = _open("/m/counter/")
    monkeypatch.setattr(
        matrix.Counter, "template", matrix.Counter.template.replace("Value:", "Deployed value:")
    )
    answer = _send(client, "/m/counter/", "increment", digest)
    assert _is_whole_page_of(answer, "Deployed value: <strong")
    # From the new template on, the page is reproducible again.
    assert (
        answer_kind(_send(client, "/m/counter/", "increment", answer["render_digest"])) == "patches"
    )


def test_a_request_made_while_another_runs_leaves_the_loser_with_a_whole_page(renders):
    """Two POSTs of one session overlap: the second starts and finishes inside the first."""
    client, digest = _open("/p/nested/")
    inner_answers = []
    Nested.inner = [lambda: inner_answers.append(_send(client, "/p/nested/", "increment", digest))]
    try:
        outer = _send(client, "/p/nested/", "outer", digest)
    finally:
        Nested.inner = []
    # Both saw the page the client showed, so both were answered with patches
    # computed from their own renders ...
    assert answer_kind(inner_answers[0]) == "patches" and answer_kind(outer) == "patches"
    # ... and the outer request saved last (0 + 10). A tab holding the inner
    # answer (1) is stale, and is told so with a whole page.
    stale = _send(client, "/p/nested/", "increment", inner_answers[0]["render_digest"])
    assert _is_whole_page_of(stale, ">11</b>")
    # The page that answer delivered is current again.
    current = _send(client, "/p/nested/", "increment", stale["render_digest"])
    assert answer_kind(current) == "patches"


def test_the_right_digest_with_a_dom_of_another_shape_selects_the_whole_page(renders):
    """The client's DOM is not laid out as the render: its paths are not the server's."""
    client, token = _open("/m/list/")
    digest, _, shape = token.partition(".")
    other_shape = ("0" if shape[0] != "0" else "1") + shape[1:]
    answer, count = _counted(renders, client, "/m/list/", "add", digest + "." + other_shape)
    assert count == 2
    assert "patches" not in answer and answer["version"] == 1
    # The same digest with the render's own shape would have been patched.
    fresh, token = _open("/m/list/")
    assert answer_kind(_send(fresh, "/m/list/", "add", token)) == "patches"


def test_a_token_names_the_shape_of_the_render_it_stands_for(renders):
    from djust import _render_digest

    client, token = _open("/m/list/")
    assert token.partition(".")[2] == _render_digest.dom_shape(renders.htmls[-1])
    answer = _send(client, "/m/list/", "add", token)
    # A row was added: another shape, named by the answer's token.
    assert answer["render_digest"].partition(".")[2] == _render_digest.dom_shape(renders.htmls[-1])
    assert answer["render_digest"].partition(".")[2] != token.partition(".")[2]


# --------------------------------------------------------------------------- #
# Resync: the page as the saved state renders it, with no event
# --------------------------------------------------------------------------- #


def _resync(client: Client, url: str, body: str = "{}", **headers):
    return client.post(
        url, data=body, content_type="application/json", HTTP_X_DJUST_RESYNC="1", **headers
    )


def test_a_resync_request_answers_the_whole_page_from_the_saved_state(renders):
    client, token = _open("/m/counter/")
    first = _send(client, "/m/counter/", "increment", token)
    second = _send(client, "/m/counter/", "increment", first["render_digest"])
    assert answer_kind(second) == "patches"
    before = len(renders.htmls)
    response = _resync(client, "/m/counter/")
    assert response.status_code == 200
    answer = json.loads(response.content)
    # One render, no handler: the state the two events left, not mount()'s.
    assert len(renders.htmls) - before == 1
    assert _is_whole_page_of(answer, ">2</strong>")
    # It names the token of that page, which is the one the last answer named.
    assert answer["render_digest"] == second["render_digest"]
    # The state is untouched: the next event counts on from it, with patches.
    third = _send(client, "/m/counter/", "increment", answer["render_digest"])
    assert third["patches"] == [{"type": "SetText", "path": [1, 0, 1, 0], "text": "3"}]


def test_a_page_get_is_not_a_resync(renders):
    """Why the request exists: the page GET mounts again and saves that."""
    client, token = _open("/m/counter/")
    _send(client, "/m/counter/", "increment", token)
    assert ">1</strong>" in json.loads(_resync(client, "/m/counter/").content)["html"]
    client.get("/m/counter/")
    assert ">0</strong>" in json.loads(_resync(client, "/m/counter/").content)["html"]


@pytest.mark.parametrize("body", ["{}", "", '{"event": "increment", "params": {}}'])
def test_a_resync_request_runs_no_handler_whatever_its_body_says(renders, body):
    client, token = _open("/m/counter/")
    _send(client, "/m/counter/", "increment", token)
    response = _resync(client, "/m/counter/", body, HTTP_X_DJUST_EVENT="increment")
    assert response.status_code == 200
    assert ">1</strong>" in json.loads(response.content)["html"]
    # Nothing was incremented and nothing was saved over the state.
    assert ">1</strong>" in json.loads(_resync(client, "/m/counter/").content)["html"]


def test_a_resync_request_passes_the_views_own_checks(renders):
    user = get_user_model().objects.create_user("resync-user", password="pw")
    anonymous = Client()
    before = len(renders.htmls)
    response = _resync(anonymous, "/p/members/")
    assert response.status_code == 403 and "redirect" in json.loads(response.content)
    assert len(renders.htmls) == before
    client = Client()
    client.force_login(user)
    assert _resync(client, "/p/members/").status_code == 200


def test_only_the_exact_header_value_is_a_resync(renders):
    client, _token = _open("/m/counter/")
    response = client.post(
        "/m/counter/", data="{}", content_type="application/json", HTTP_X_DJUST_RESYNC="yes"
    )
    assert response.status_code == 400  # an event request with no event name


# --------------------------------------------------------------------------- #
# What the pre-handler render must not use up
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", list(STALE_DATA_VIEWS))
@pytest.mark.parametrize("echo", [True, False], ids=["patches", "whole-page"])
def test_a_row_the_handler_changed_in_the_database_is_in_the_answer(renders, name, echo):
    """The pre-handler render must leave nothing behind that the next render reuses.

    It evaluates querysets, fills ``cached_property`` values and prefetched
    relations. Rendered on the view itself, those caches would serve the old
    row to the render after the handler, and the patch answer would miss a
    change the whole-page answer shows.
    """
    users = get_user_model().objects
    crew = Group.objects.create(name="crew")
    for username in ("alice", "carol"):
        crew.user_set.add(users.create_user(username, password="pw"))
    url = "/p/stale/%s/" % name
    client, digest = _open(url)
    assert "alice" in client.get(url).content.decode()
    client, digest = _open(url)
    answer = _send(client, url, "rename", digest if echo else None)
    assert answer_kind(answer) == ("patches" if echo else "html")
    body = json.dumps(answer)
    assert "bob" in body and "alice" not in body
    # What the server holds now is the page with the new name.
    assert "bob" in renders.htmls[-1] and "alice" not in renders.htmls[-1]


def test_a_refused_request_renders_nothing(renders):
    client, digest = _open("/p/notices/")
    for event, params, status in [
        ("no_such_handler", {}, 400),
        ("typed", {"amount": "not-a-number"}, 400),
        ("mount", {}, 400),
    ]:
        _answer, count = _counted(renders, client, "/p/notices/", event, digest, params, status)
        assert count == 0, event
    # A rate-limited handler: the first call passes, the second is refused.
    _answer, count = _counted(renders, client, "/p/notices/", "limited", digest)
    assert count == 2
    _answer, count = _counted(renders, client, "/p/notices/", "limited", digest, status=429)
    assert count == 0


def test_get_context_data_runs_once_without_a_digest_and_twice_with_one(renders):
    client, digest = _open("/p/counting/")
    Counting.calls.clear()
    _send(client, "/p/counting/", "increment", None)
    assert len(Counting.calls) == 1
    Counting.calls.clear()
    client, digest = _open("/p/counting/")
    Counting.calls.clear()
    assert answer_kind(_send(client, "/p/counting/", "increment", digest)) == "patches"
    # The documented cost of the patch path.
    assert len(Counting.calls) == 2


def _pending(client: Client) -> list[str]:
    """Messages waiting for this session (session storage), read without consuming them."""
    from django.contrib.messages.storage.base import Message
    from django.contrib.messages.storage.cookie import MessageDecoder
    from django.contrib.messages.storage.session import SessionStorage

    stored = client.session.get(SessionStorage.session_key)
    if not stored:
        return []
    return [str(m) for m in json.loads(stored, cls=MessageDecoder) if isinstance(m, Message)]


@override_settings(MESSAGE_STORAGE="django.contrib.messages.storage.session.SessionStorage")
def test_a_message_added_by_the_handler_is_rendered_once_on_the_patch_path(renders):
    client, digest = _open("/p/notices/")
    answer = _send(client, "/p/notices/", "announce", digest)
    assert answer_kind(answer) == "patches"
    assert "saved" in json.dumps(answer["patches"])
    # Rendered, so consumed: the next request's page has no message, which is
    # not the page the client shows (it still shows "saved").
    assert _pending(client) == []
    after = _send(client, "/p/notices/", "increment", answer["render_digest"])
    assert _is_whole_page_of(after, ">2</b>") and "saved" not in after["html"]


@override_settings(MESSAGE_STORAGE="django.contrib.messages.storage.session.SessionStorage")
def test_a_message_added_by_the_handler_is_in_the_whole_page_after_a_refused_digest(renders):
    client, _digest = _open("/p/notices/")
    answer, count = _counted(renders, client, "/p/notices/", "announce", WRONG)
    assert count == 2
    assert _is_whole_page_of(answer, "<li") and "saved" in answer["html"]
    assert _pending(client) == []


@override_settings(MESSAGE_STORAGE="django.contrib.messages.storage.session.SessionStorage")
def test_a_pending_message_survives_a_request_that_answers_without_a_render(renders, monkeypatch):
    """The pre-render iterates the messages; a redirect must still leave them."""
    client, digest = _open("/p/notices/")

    class Leaving(Notices):
        pass

    # Queue a message without rendering it: a handler that adds one and redirects.
    def add_and_leave(self, **kwargs):
        messages.info(self.request, "for the next page")
        self.live_redirect("/m/counter/")

    monkeypatch.setattr(Notices, "leave", event_handler(add_and_leave))
    first = _send(client, "/p/notices/", "leave", None)
    assert first["_navigation"][0]["action"] == "live_redirect"
    assert _pending(client) == ["for the next page"]

    # Now a request that echoes a digest, pre-renders (iterating the pending
    # message) and then redirects without a render.
    monkeypatch.setattr(
        Notices, "leave", event_handler(lambda self, **kwargs: self.live_redirect("/m/counter/"))
    )
    before = len(renders.htmls)
    second = _send(client, "/p/notices/", "leave", digest)
    assert len(renders.htmls) - before == 1  # the pre-handler render ran
    assert second["_navigation"][0]["action"] == "live_redirect"
    assert _pending(client) == ["for the next page"]


def test_a_skipped_render_after_the_pre_render_answers_as_before(renders):
    client, digest = _open("/p/notices/")
    answer, count = _counted(renders, client, "/p/notices/", "quiet", digest)
    assert count == 1  # only the pre-handler render
    assert answer["patches"] == [] and "version" not in answer and "render_digest" not in answer
    # The state moved without a render: the client's page is stale, and the
    # digest it still holds says so.
    assert _is_whole_page_of(_send(client, "/p/notices/", "increment", digest), ">2</b>")


def test_an_at_cache_request_is_not_pre_rendered(renders):
    client, digest = _open("/p/notices/")
    answer, count = _counted(
        renders, client, "/p/notices/", "cached", digest, {"_cacheRequestId": "r1"}
    )
    assert count == 1
    assert "patches" not in answer and answer["cache_request_id"] == "r1"


@pytest.mark.parametrize(
    "url, event",
    [("/m/feed/", "post_entry"), ("/p/streamed/", "increment")],
    ids=["temporary_assigns", "streams"],
)
def test_a_view_whose_render_consumes_its_rows_is_not_pre_rendered(renders, url, event):
    client, digest = _open(url)
    answer, count = _counted(renders, client, url, event, digest)
    assert count == 1
    assert "patches" not in answer and answer["version"] == 1


def test_a_client_owned_region_in_the_render_selects_the_whole_page(renders):
    client, digest = _open("/p/ignored/")
    answer, count = _counted(renders, client, "/p/ignored/", "increment", digest)
    assert count == 2
    assert _is_whole_page_of(answer, ">1</b>")


def _without_child_numbers(answer: dict) -> dict:
    """An explicit page numbers its children from a process-wide counter."""
    answer = dict(answer)
    answer.pop("render_digest")
    answer["html"] = re.sub(r"child_\d+", "child_N", answer["html"])
    if "owned_children" in answer:
        answer["owned_children"] = [["child_N", node] for _c, node in answer["owned_children"]]
    return answer


def test_a_page_whose_template_embeds_another_view_is_not_pre_rendered(renders):
    client, digest = _open("/p/outer/")
    plain, _ = _open("/p/outer/")
    answer, count = _counted(renders, client, "/p/outer/", "increment", digest)
    assert count == 1
    assert _is_whole_page_of(answer, ">1</b>") and "inner view" in answer["html"]
    reference = _send(plain, "/p/outer/", "increment", None)
    assert _without_child_numbers(answer) == _without_child_numbers(reference)


def test_a_child_the_template_source_does_not_show_still_selects_the_whole_page(
    renders, monkeypatch
):
    """An ``{% include %}`` can bring a child in: found after the render, not before."""
    from djust import _http_fallback_baseline

    monkeypatch.setattr(_http_fallback_baseline, "_embeds_views", lambda view: False)
    # Even a digest that matches: a page holding another view is not patched.
    monkeypatch.setattr(_http_fallback_baseline, "tokens_match", lambda *args: True)
    client, digest = _open("/p/outer/")
    plain, _ = _open("/p/outer/")
    answer, count = _counted(renders, client, "/p/outer/", "increment", digest)
    assert count == 2
    assert _is_whole_page_of(answer, ">1</b>")
    # It is the answer a request that sent no digest gets.
    reference = _send(plain, "/p/outer/", "increment", None)
    assert _without_child_numbers(answer) == _without_child_numbers(reference)


def test_a_user_signed_in_by_the_handler_is_rendered_by_the_patch_answer(renders):
    """A context processor's object value (``user``) is rendered again after the handler."""
    get_user_model().objects.create_user("greeted", password="pw")
    client, digest = _open("/p/greeter/")
    answer = _send(client, "/p/greeter/", "sign_in", digest)
    assert answer["patches"] == [{"type": "SetText", "path": [0, 1, 0], "text": "greeted"}]
    # login() gave the request a new session: the answer's digest is bound to
    # it, and the next request (on the new session cookie) is patched.
    assert (
        answer_kind(_send(client, "/p/greeter/", "sign_in", answer["render_digest"])) == "patches"
    )


def test_a_pre_render_that_raises_does_not_fail_the_request(renders):
    client, digest = _open("/p/fragile/")
    Fragile.failures = 1
    try:
        answer = _send(client, "/p/fragile/", "increment", digest)
    finally:
        Fragile.failures = 0
    assert _is_whole_page_of(answer, ">1</b>")
    # Without a digest the same failure is the render's own, and is reported.
    Fragile.failures = 1
    try:
        _send(client, "/p/fragile/", "increment", None, status=500)
    finally:
        Fragile.failures = 0


def test_the_pre_render_goes_through_the_sync_turn_wrapper(monkeypatch):
    """``get_context_data`` of the baseline render runs as a ``render`` step."""
    from djust import sync_turn

    steps = []
    real = sync_turn.run_sync_step

    def recording(view, step, fn, *args, **kwargs):
        steps.append((step, getattr(fn, "__name__", "")))
        return real(view, step, fn, *args, **kwargs)

    monkeypatch.setattr(sync_turn, "run_sync_step", recording)
    client, digest = _open("/p/counting/")
    steps.clear()
    Counting.calls.clear()
    assert answer_kind(_send(client, "/p/counting/", "increment", digest)) == "patches"
    renders_through_wrapper = [s for s in steps if s == ("render", "get_context_data")]
    assert len(renders_through_wrapper) == len(Counting.calls) == 2


# --------------------------------------------------------------------------- #
# Compression of the answer
# --------------------------------------------------------------------------- #


def _post_accepting_gzip(client: Client, url: str, event: str, digest: str | None):
    return post_event(client, url, event, {}, digest, HTTP_ACCEPT_ENCODING="gzip, br")


def test_a_whole_page_answer_is_gzipped_for_a_client_that_accepts_it(renders):
    import gzip

    client, _digest = _open("/p/wide/")
    response = _post_accepting_gzip(client, "/p/wide/", "shift", None)
    assert response["Content-Encoding"] == "gzip"
    assert "Accept-Encoding" in response["Vary"]
    answer = json.loads(gzip.decompress(response.content))
    assert ">1149</li>" in answer["html"]
    plain = post_event(_open("/p/wide/")[0], "/p/wide/", "shift", {}, None)
    assert not plain.has_header("Content-Encoding")
    # The point of it: the page with 150 rows goes out several times smaller.
    assert len(response.content) * 3 < len(plain.content)


def test_a_small_patch_answer_is_sent_as_it_is(renders):
    client, digest = _open("/m/counter/")
    response = _post_accepting_gzip(client, "/m/counter/", "increment", digest)
    assert not response.has_header("Content-Encoding")
    assert answer_kind(json.loads(response.content)) == "patches"


def test_compression_can_be_left_to_the_deployment(renders):
    client, _digest = _open("/p/wide/")
    with override_settings(LIVEVIEW_CONFIG={"http_fallback_compress": False}):
        response = _post_accepting_gzip(client, "/p/wide/", "shift", None)
    assert not response.has_header("Content-Encoding")
    assert ">1149</li>" in json.loads(response.content)["html"]


def test_a_projects_own_gzip_middleware_does_not_compress_it_twice(renders, settings):
    import gzip

    settings.MIDDLEWARE = ["django.middleware.gzip.GZipMiddleware", *settings.MIDDLEWARE]
    client, _digest = _open("/p/wide/")
    response = _post_accepting_gzip(client, "/p/wide/", "shift", None)
    assert response["Content-Encoding"] == "gzip"
    assert ">1149</li>" in json.loads(gzip.decompress(response.content))["html"]
