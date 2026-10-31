"""``params_form``: URL parameters declared, bound and cleaned before ``handle_params()`` (#3689).

``handle_params(params, uri)`` received the query string as raw strings. The
example in its own docstring, ``int(params.get("page", 1))``, answered 500 on
``?page=abc``, and every view that reads its URL wrote its own parsing,
defaults and idea of what an invalid value does.

A view that sets ``params_form`` to a Django form gets that form's
``cleaned_data`` in ``handle_params()``. Invalid input goes to
``handle_invalid_params(form, uri)`` (default: the declared initial values, with
the messages exposed to the template as ``params_errors``). The same decision
runs for the HTTP GET, the socket mount, ``url_change`` (``live_patch`` and
popstate) and the stateless HTTP fallback POST.

``params_errors`` and not ``params_form.errors``: a form instance placed in a
template context is rendered to ``{field: html}`` by
``serialization.render_form_value``, so it has no ``errors`` to read
(``test_a_form_instance_in_context_has_no_errors_key`` pins that).
"""

from __future__ import annotations

import json
import re
import sys

import pytest
from asgiref.sync import sync_to_async
from django import forms
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import ImproperlyConfigured
from django.http import Http404
from django.test import Client, RequestFactory, override_settings
from django.urls import path

from djust import LiveView
from djust.decorators import event_handler, state

from ._ws_frames import drain_extra, receive_until

pytestmark = pytest.mark.django_db

CALLS: list = []
INVALID_CALLS: list = []


class ListParams(forms.Form):
    category = forms.ChoiceField(
        choices=[("all", "All"), ("books", "Books")], required=False, initial="all"
    )
    page = forms.IntegerField(min_value=1, required=False, initial=1)


_TEMPLATE = (
    "<div dj-root>"
    '<p id="state">{{ category }}/{{ page }}</p>'
    '{% if params_errors.page %}<p class="err">PAGE:{{ params_errors.page.0 }}</p>{% endif %}'
    "{% if params_errors.category %}"
    '<p class="err">CATEGORY:{{ params_errors.category.0 }}</p>{% endif %}'
    '<p id="note">{{ note }}</p>'
    '<p id="count">ERRS:{{ params_errors|length }}</p>'
    "</div>"
)


class ListView(LiveView):
    """The docstring's example, with the form the issue proposes."""

    login_required = False
    params_form = ListParams
    template = _TEMPLATE

    def mount(self, request, **kwargs):
        self.category = "all"
        self.page = 1
        self.note = ""

    def handle_params(self, params, uri):
        CALLS.append((dict(params), uri))
        self.category = params["category"]
        self.page = params["page"]

    @event_handler()
    def mark(self, **kwargs):
        self.note = "%s/%s" % (self.category, self.page)


class RawListView(LiveView):
    """No ``params_form``: the raw dict, exactly as before."""

    login_required = False
    template = "<div dj-root>{{ page }}</div>"

    def mount(self, request, **kwargs):
        self.page = "none"

    def handle_params(self, params, uri):
        CALLS.append((dict(params), uri))
        self.page = params.get("page", "none")


class NotFoundOnInvalid(ListView):
    """A view may answer 404 for a bad URL."""

    def handle_invalid_params(self, form, uri):
        INVALID_CALLS.append((form, uri))
        raise Http404("No such page.")


class BadRequestOnInvalid(ListView):
    """A view may answer 400 and keep its own page."""

    bad = False

    def handle_invalid_params(self, form, uri):
        INVALID_CALLS.append((form, uri))
        self.bad = True
        super().handle_invalid_params(form, uri)

    def get_http_status(self):
        return 400 if self.bad else None


class RecordingInvalid(ListView):
    def handle_invalid_params(self, form, uri):
        INVALID_CALLS.append((form, uri))
        super().handle_invalid_params(form, uri)


class TagParams(forms.Form):
    tag = forms.MultipleChoiceField(choices=[("a", "A"), ("b", "B")], required=False, initial=["a"])
    q = forms.CharField(required=True)
    when = forms.IntegerField(required=False, initial=lambda: 7)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("q") == "forbidden":
            raise forms.ValidationError("That search is not allowed.")
        return cleaned


class TagView(LiveView):
    login_required = False
    params_form = TagParams
    template = "<div dj-root>{{ q }}|{{ tag }}|{{ when }}|{{ params_errors }}</div>"

    def mount(self, request, **kwargs):
        self.q = ""
        self.tag = []
        self.when = 0

    def handle_params(self, params, uri):
        CALLS.append((dict(params), uri))
        self.q = params["q"]
        self.tag = list(params["tag"])
        self.when = params["when"]


class AsyncListView(LiveView):
    """``handle_params`` may be ``async def``; the default fallback awaits it."""

    login_required = False
    params_form = ListParams
    template = _TEMPLATE

    def mount(self, request, **kwargs):
        self.category = "all"
        self.page = 1
        self.note = ""

    async def handle_params(self, params, uri):
        CALLS.append((dict(params), uri))
        self.category = params["category"]
        self.page = params["page"]


class ExplicitListView(LiveView):
    """``exposure_policy = "explicit"``: the view declares what it renders."""

    login_required = False
    exposure_policy = "explicit"
    params_form = ListParams
    template = (
        '<div dj-root><p id="state">{{ category }}/{{ page }}</p>'
        "{% if params_errors.page %}<p>PAGE:{{ params_errors.page.0 }}</p>{% endif %}</div>"
    )
    category = state("all")
    page = state(1)
    params_errors = state(default_factory=dict)

    def handle_params(self, params, uri):
        CALLS.append((dict(params), uri))
        self.category = params["category"]
        self.page = params["page"]

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            category=self.category, page=self.page, params_errors=self.params_errors, **kwargs
        )


class GroupParams(forms.Form):
    """A ``ModelChoiceField`` runs a query while it cleans."""

    from django.contrib.auth.models import Group

    group = forms.ModelChoiceField(queryset=Group.objects.all(), required=False)


class GroupView(LiveView):
    login_required = False
    params_form = GroupParams
    template = "<div dj-root>{{ picked }}|{{ params_errors|length }}</div>"

    def mount(self, request, **kwargs):
        self.picked = "none"

    def handle_params(self, params, uri):
        CALLS.append((dict(params), uri))
        group = params["group"]
        self.picked = group.name if group is not None else "none"


class ExplodingParams(forms.Form):
    q = forms.CharField(required=False)

    def clean(self):
        raise RuntimeError("a validator failed")


class ExplodingView(LiveView):
    login_required = False
    params_form = ExplodingParams
    template = "<div dj-root>never</div>"

    def handle_params(self, params, uri):
        CALLS.append((dict(params), uri))


class FlagParams(forms.Form):
    closed = forms.BooleanField(required=False, initial=True)
    n = forms.IntegerField(required=False, initial=5)


class FlagView(LiveView):
    login_required = False
    params_form = FlagParams
    template = "<div dj-root>{{ closed }}|{{ n }}</div>"

    def mount(self, request, **kwargs):
        self.closed = None
        self.n = None

    def handle_params(self, params, uri):
        CALLS.append((dict(params), uri))
        self.closed = params["closed"]
        self.n = params["n"]


class AsyncSuperView(AsyncListView):
    """A sync override calls ``super()`` WITHOUT ``return`` over an ``async def handle_params``."""

    def handle_invalid_params(self, form, uri):
        INVALID_CALLS.append((form, uri))
        super().handle_invalid_params(form, uri)


class BrokenDeclaration(LiveView):
    login_required = False
    params_form = "not a form class"
    template = "<div dj-root>x</div>"


class CleanedNotInitial(forms.Form):
    """A required field with no initial, to see what the default fallback hands over."""

    token = forms.CharField(required=True)


class RequiredView(LiveView):
    login_required = False
    params_form = CleanedNotInitial
    template = "<div dj-root>{{ token }}|{{ params_errors.token.0 }}</div>"

    def mount(self, request, **kwargs):
        self.token = "unset"

    def handle_params(self, params, uri):
        CALLS.append((dict(params), uri))
        self.token = params["token"]


_VIEWS = (
    ListView,
    RawListView,
    NotFoundOnInvalid,
    BadRequestOnInvalid,
    RecordingInvalid,
    TagView,
    AsyncListView,
    ExplicitListView,
    BrokenDeclaration,
    RequiredView,
    GroupView,
    ExplodingView,
    FlagView,
    AsyncSuperView,
)
for _cls in _VIEWS:
    setattr(sys.modules[__name__], _cls.__name__, _cls)

urlpatterns = [
    path("list/", ListView.as_view()),
    path("raw/", RawListView.as_view()),
    path("notfound/", NotFoundOnInvalid.as_view()),
    path("badrequest/", BadRequestOnInvalid.as_view()),
    path("recording/", RecordingInvalid.as_view()),
    path("tags/", TagView.as_view()),
    path("async/", AsyncListView.as_view()),
    path("explicit/", ExplicitListView.as_view()),
    path("broken/", BrokenDeclaration.as_view()),
    path("required/", RequiredView.as_view()),
    path("group/", GroupView.as_view()),
    path("exploding/", ExplodingView.as_view()),
    path("flags/", FlagView.as_view()),
    path("asyncsuper/", AsyncSuperView.as_view()),
]


@pytest.fixture(autouse=True)
def _urls(settings):
    settings.ROOT_URLCONF = __name__
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.LIVEVIEW_ALLOWED_MODULES = [__name__]
    CALLS.clear()
    INVALID_CALLS.clear()
    yield
    CALLS.clear()
    INVALID_CALLS.clear()


def _state(body: bytes | str) -> str:
    text = body.decode() if isinstance(body, bytes) else body
    match = re.search(r'<p[^>]*id="state"[^>]*>([^<]*)</p>', text)
    assert match, text
    return match.group(1)


# --------------------------------------------------------------------------- #
# The HTTP GET
# --------------------------------------------------------------------------- #


class TestTheIssuesRepro:
    def test_the_docstring_example_no_longer_500s_on_a_bad_page(self):
        """The reproducer: ``?page=abc`` answered 500 with ``int('abc')``."""
        response = Client().get("/list/?page=abc")
        assert response.status_code == 200, response.content[:300]
        assert _state(response.content) == "all/1"

    def test_the_error_is_reported_to_the_template(self):
        body = Client().get("/list/?page=abc").content.decode()
        assert 'class="err">PAGE:Enter a whole number.' in body

    def test_a_valid_page_arrives_cleaned_not_as_a_string(self):
        response = Client().get("/list/?page=2&category=books")
        assert response.status_code == 200
        assert _state(response.content) == "books/2"
        assert CALLS == [({"category": "books", "page": 2}, "/list/?page=2&category=books")]
        assert type(CALLS[0][0]["page"]) is int


class TestTheDocumentedExample:
    def test_the_example_in_the_handle_params_docstring_runs_and_survives_a_bad_page(self):
        """The issue's complaint: the docstring's own example 500ed on ``?page=abc``."""
        import textwrap

        from djust._params_form import run_handle_params_sync
        from djust.mixins.navigation import NavigationMixin

        doc = NavigationMixin.handle_params.__doc__
        block = doc.split("Example::", 1)[1].split("Invalid input goes to", 1)[0]
        namespace = {"forms": forms, "LiveView": LiveView}
        exec(textwrap.dedent(block), namespace)  # noqa: S102 - the documented snippet itself

        view = namespace["ListView"]()
        run_handle_params_sync(view, {"page": "abc"}, "/list/?page=abc")
        assert (view.category, view.page) == ("all", 1)
        run_handle_params_sync(view, {"page": "2", "category": "books"}, "/list/?page=2")
        assert (view.category, view.page) == ("books", 2)

    def test_the_example_in_the_navigation_guide_runs_and_survives_a_bad_page(self):
        """The guide's ``params_form`` snippet is executed, not just parsed."""
        import pathlib

        from djust._params_form import run_handle_params_sync

        guide = pathlib.Path(__file__).resolve().parents[3] / "docs/website/guides/navigation.md"
        blocks = re.findall(r"```python\n(.*?)```", guide.read_text(), re.S)
        [snippet] = [
            b for b in blocks if "class ListParams" in b and "params_form = ListParams" in b
        ]
        namespace: dict = {}
        exec(snippet, namespace)  # noqa: S102 - the documented snippet itself

        view = namespace["ListView"]()
        view.mount(None)
        run_handle_params_sync(view, {"page": "abc"}, "/list/?page=abc")
        assert (view.category, view.page) == ("all", 1)
        assert view.params_errors == {"page": ["Enter a whole number."]}
        run_handle_params_sync(view, {"page": "2", "category": "books"}, "/list/?page=2")
        assert (view.category, view.page) == ("books", 2)
        assert view.params_errors == {}


class TestValidInput:
    def test_missing_fields_take_their_initial_values(self):
        Client().get("/list/")
        assert CALLS == [({"category": "all", "page": 1}, "/list/")]

    def test_empty_fields_take_their_initial_values(self):
        Client().get("/list/?page=&category=")
        assert CALLS[0][0] == {"category": "all", "page": 1}

    def test_a_valid_request_has_no_errors(self):
        body = Client().get("/list/?page=3").content.decode()
        assert 'class="err"' not in body

    def test_a_parameter_the_form_does_not_declare_is_dropped(self):
        Client().get("/list/?page=2&utm_source=x")
        assert CALLS[0][0] == {"category": "all", "page": 2}

    def test_a_repeated_key_binds_a_multiple_choice_field(self):
        Client().get("/tags/?q=hi&tag=a&tag=b")
        assert CALLS[0][0] == {"tag": ["a", "b"], "q": "hi", "when": 7}

    def test_a_callable_initial_is_evaluated(self):
        Client().get("/tags/?q=hi")
        assert CALLS[0][0]["when"] == 7
        assert CALLS[0][0]["tag"] == ["a"]


class TestInitialFillsWhatTheUrlLeftOut:
    """The contract is "a field the URL left empty takes its initial value", for every field type."""

    def test_an_absent_boolean_takes_its_initial_value(self):
        """``BooleanField`` cleans an absent key to ``False``: that is not "empty" to Django."""
        Client().get("/flags/")
        assert CALLS == [({"closed": True, "n": 5}, "/flags/")]

    def test_an_empty_boolean_takes_its_initial_value(self):
        Client().get("/flags/?closed=")
        assert CALLS[0][0]["closed"] is True

    @pytest.mark.parametrize("value", ["false", "False"])
    def test_a_boolean_the_url_supplies_is_kept(self, value):
        Client().get("/flags/?closed=%s&n=7" % value)
        assert CALLS[0][0] == {"closed": False, "n": 7}

    def test_a_valid_url_and_the_invalid_fallback_agree_on_an_absent_field(self):
        Client().get("/flags/")
        valid = CALLS[0][0]["closed"]
        form = FlagParams(data={"n": "abc"})
        form.is_valid()
        from djust._params_form import initial_values

        assert initial_values(form)["closed"] is valid is True


class TestInvalidInput:
    @pytest.mark.parametrize(
        "query, message",
        [
            ("page=abc", "PAGE:Enter a whole number."),
            ("page=0", "PAGE:Ensure this value is greater than or equal to 1."),
            ("category=zzz", "CATEGORY:Select a valid choice."),
        ],
    )
    def test_each_error_is_reported_and_the_page_still_renders(self, query, message):
        response = Client().get("/list/?" + query)
        assert response.status_code == 200
        assert message in response.content.decode()
        assert _state(response.content) == "all/1"

    def test_the_default_hands_handle_params_the_initial_values_for_every_field(self):
        """One bad value means the declared defaults, not a half-applied URL."""
        Client().get("/list/?category=books&page=abc")
        assert CALLS == [({"category": "all", "page": 1}, "/list/?category=books&page=abc")]

    def test_a_required_field_that_is_missing_is_an_error_with_no_initial(self):
        body = Client().get("/required/").content.decode()
        assert "unset|" not in body  # handle_params ran with the (None) fallback
        assert "This field is required." in body
        assert CALLS == [({"token": None}, "/required/")]

    def test_a_form_level_error_is_reported_under___all__(self):
        body = Client().get("/tags/?q=forbidden").content.decode()
        assert "That search is not allowed." in body
        assert "__all__" in body

    def test_stale_errors_do_not_survive_a_valid_request(self):
        view = ListView()
        request = RequestFactory().get("/list/?page=abc")
        request.user = AnonymousUser()
        request.session = SessionStore()
        request.session.create()
        view.get(request)
        assert view.params_errors != {}
        request2 = RequestFactory().get("/list/?page=2")
        request2.user, request2.session = request.user, request.session
        view.get(request2)
        assert view.params_errors == {}


class TestOverridingInvalidParams:
    def test_the_view_receives_the_bound_form_and_the_uri(self):
        Client().get("/recording/?page=abc")
        [(form, uri)] = INVALID_CALLS
        assert isinstance(form, ListParams)
        assert form.is_bound and list(form.errors) == ["page"]
        assert uri == "/recording/?page=abc"

    def test_valid_input_never_calls_it(self):
        Client().get("/recording/?page=2")
        assert INVALID_CALLS == []

    def test_raising_http404_answers_404(self):
        assert Client().get("/notfound/?page=abc").status_code == 404
        assert Client().get("/notfound/?page=2").status_code == 200

    def test_a_view_can_answer_400_and_keep_its_page(self):
        response = Client().get("/badrequest/?page=abc")
        assert response.status_code == 400
        assert 'class="err">PAGE:Enter a whole number.' in response.content.decode()
        assert Client().get("/badrequest/?page=2").status_code == 200


class TestWithoutParamsForm:
    def test_a_view_without_params_form_gets_the_raw_dict(self):
        """Opt-in: the strings, the unknown keys and the lack of ``params_errors``."""
        response = Client().get("/raw/?page=abc&utm=x")
        assert response.status_code == 200
        assert CALLS == [({"page": "abc", "utm": "x"}, "/raw/?page=abc&utm=x")]
        assert not hasattr(RawListView(), "params_errors")

    def test_the_default_declaration_is_none(self):
        assert LiveView.params_form is None


class TestDeclarationAndExposure:
    def test_a_params_form_that_is_not_a_form_class_is_refused_loudly(self):
        with pytest.raises(ImproperlyConfigured, match="params_form"):
            Client(raise_request_exception=True).get("/broken/")

    def test_an_async_handle_params_gets_the_cleaned_values(self):
        response = Client().get("/async/?page=4")
        assert _state(response.content) == "all/4"
        assert CALLS[0][0] == {"category": "all", "page": 4}

    def test_the_default_fallback_awaits_an_async_handle_params(self):
        response = Client().get("/async/?page=abc")
        assert response.status_code == 200
        assert _state(response.content) == "all/1"
        assert 'class="err">PAGE:' in response.content.decode()

    def test_a_sync_override_that_calls_super_without_return_still_runs_an_async_handle_params(
        self,
    ):
        """The documented ``super().handle_invalid_params(form, uri)`` pattern, no ``return``."""
        response = Client().get("/asyncsuper/?page=abc")
        assert response.status_code == 200
        assert CALLS == [({"category": "all", "page": 1}, "/asyncsuper/?page=abc")]
        assert len(INVALID_CALLS) == 1

    def test_an_over_long_integer_binds_as_absent_instead_of_raising(self):
        from djust._params_form import _bindable

        assert dict(_bindable({"page": 10**5000, "ok": 1}).lists()) == {"ok": ["1"]}

    def test_an_explicit_exposure_view_cleans_its_params_too(self):
        response = Client().get("/explicit/?page=2&category=books")
        assert response.status_code == 200
        assert _state(response.content) == "books/2"
        assert CALLS == [({"category": "books", "page": 2}, "/explicit/?page=2&category=books")]

    def test_an_explicit_exposure_view_with_a_bad_value_renders_when_it_declares_the_errors(self):
        response = Client().get("/explicit/?page=abc")
        assert response.status_code == 200
        assert _state(response.content) == "all/1"
        assert "PAGE:Enter a whole number." in response.content.decode()

    def test_a_form_instance_in_context_has_no_errors_key(self):
        """Why the errors are exposed as ``params_errors``, not ``params_form.errors``."""
        from djust.serialization import render_form_value

        form = ListParams(data={"page": "abc"})
        form.is_valid()
        rendered = render_form_value(form)
        assert set(rendered) == {"category", "page"}
        assert "errors" not in rendered


# --------------------------------------------------------------------------- #
# The socket: mount, url_change
# --------------------------------------------------------------------------- #


class _ScopeSession:
    def __init__(self, key):
        self.session_key = key


async def _connect(view_name, *, params=None, url="/list/"):
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    def _create_session():
        store = SessionStore()
        store.create()
        return store.session_key

    key = await sync_to_async(_create_session)()
    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession(key)
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=2)  # connect frame
    await communicator.send_json_to(
        {"type": "mount", "view": f"{__name__}.{view_name}", "url": url, "params": params or {}}
    )
    frames = await receive_until(
        communicator, lambda fs: any(f.get("type") in ("mount", "error") for f in fs)
    )
    return communicator, frames[-1], frames


def _text(frame) -> str:
    return repr(frame.get("html") or frame.get("patches") or "")


async def _url_change(communicator, params, uri):
    await communicator.send_json_to({"type": "url_change", "params": params, "uri": uri})
    frames = await receive_until(
        communicator,
        lambda fs: any(f.get("type") in ("patch", "html_update", "error") for f in fs),
    )
    return frames[-1]


@pytest.mark.asyncio
class TestTheSocket:
    async def test_the_socket_mount_cleans_the_frames_params(self):
        communicator, mount, _ = await _connect("ListView", params={"page": "2"})
        assert mount["type"] == "mount", mount
        assert _state(mount["html"]) == "all/2"
        assert CALLS[-1][0] == {"category": "all", "page": 2}
        await communicator.disconnect()

    async def test_a_bad_page_on_the_socket_mount_is_an_error_in_the_page_not_a_failed_mount(self):
        communicator, mount, frames = await _connect("ListView", params={"page": "abc"})
        assert mount["type"] == "mount", frames
        assert _state(mount["html"]) == "all/1"
        assert "PAGE:Enter a whole number." in mount["html"]
        await communicator.disconnect()

    async def test_url_change_cleans_and_reports(self):
        communicator, mount, _ = await _connect("ListView")
        await drain_extra(communicator)
        CALLS.clear()

        frame = await _url_change(communicator, {"page": "3"}, "/list/?page=3")
        assert frame["type"] in ("patch", "html_update"), frame
        assert "3" in _text(frame)
        assert CALLS == [({"category": "all", "page": 3}, "/list/?page=3")]

        frame = await _url_change(communicator, {"page": "abc"}, "/list/?page=abc")
        assert frame["type"] in ("patch", "html_update"), frame
        assert "Enter a whole number." in _text(frame), frame

        assert "ERRS:1" in _text(frame), frame

        # Back to a valid URL: the error leaves the page.
        frame = await _url_change(communicator, {"page": "2"}, "/list/?page=2")
        assert frame["type"] in ("patch", "html_update"), frame
        assert "ERRS:0" in _text(frame), frame
        await communicator.disconnect()

    @pytest.mark.parametrize("params", [None, "page=2", ["page", "2"], 7])
    async def test_url_change_with_params_that_are_not_a_mapping_does_not_crash(self, params):
        communicator, mount, _ = await _connect("ListView")
        await drain_extra(communicator)
        CALLS.clear()
        frame = await _url_change(communicator, params, "/list/")
        assert frame["type"] != "error", frame
        assert CALLS == [({"category": "all", "page": 1}, "/list/")]
        await communicator.disconnect()

    async def test_raising_http404_from_handle_invalid_params_fails_the_socket_mount(self):
        communicator, frame, frames = await _connect("NotFoundOnInvalid", params={"page": "abc"})
        assert frame["type"] == "error", frames
        assert "No such page" not in json.dumps(frame)  # no raw exception text on the wire
        await communicator.disconnect()


# --------------------------------------------------------------------------- #
# The stateless HTTP fallback POST
# --------------------------------------------------------------------------- #


STATELESS = override_settings(LIVEVIEW_CONFIG={"view_state_in_session": False})


def _get_request(url):
    request = RequestFactory().get(url)
    request.user = AnonymousUser()
    request.tenant = None
    request.session = SessionStore()
    request.session.create()
    return request


def _post(initial, view, event, url):
    request = RequestFactory().post(
        url, data=json.dumps({}), content_type="application/json", HTTP_X_DJUST_EVENT=event
    )
    request.user, request.session, request.tenant = initial.user, initial.session, None
    return view.post(request)


class TestTheFallbackPost:
    def test_the_event_meets_the_cleaned_params(self):
        with STATELESS:
            url = "/list/?page=2&category=books"
            initial = _get_request(url)
            ListView().get(initial)
            CALLS.clear()
            view = ListView()
            response = _post(initial, view, "mark", url)
        assert response.status_code == 200, response.content
        assert view.note == "books/2"
        assert CALLS == [({"category": "books", "page": 2}, url)]

    def test_a_bad_value_does_not_500_the_event(self):
        with STATELESS:
            url = "/list/?page=abc"
            initial = _get_request(url)
            ListView().get(initial)
            CALLS.clear()
            view = ListView()
            response = _post(initial, view, "mark", url)
        assert response.status_code == 200, response.content
        assert view.note == "all/1"
        assert view.params_errors == {"page": ["Enter a whole number."]}
        assert CALLS == [({"category": "all", "page": 1}, url)]


# --------------------------------------------------------------------------- #
# Validation runs application code
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestValidationRunsOffTheEventLoop:
    async def test_a_model_choice_field_queries_on_the_socket_mount(self):
        """Cleaning ``?group=<pk>`` queries the database: not on the event loop."""
        from django.contrib.auth.models import Group

        group = await sync_to_async(Group.objects.create)(name="staff")
        communicator, mount, frames = await _connect(
            "GroupView", params={"group": str(group.pk)}, url="/group/"
        )
        assert mount["type"] == "mount", frames
        assert "staff|0" in mount["html"]
        await communicator.disconnect()

    async def test_a_model_choice_field_rejects_an_unknown_pk_on_url_change(self):
        communicator, mount, _ = await _connect("GroupView", url="/group/")
        await drain_extra(communicator)
        frame = await _url_change(communicator, {"group": "999999"}, "/group/?group=999999")
        assert frame["type"] != "error", frame
        assert "none|1" in _text(frame), frame
        await communicator.disconnect()


class TestModelChoiceFieldOverHttp:
    def test_the_http_get_resolves_the_model_choice_field_too(self):
        from django.contrib.auth.models import Group

        Group.objects.create(name="editors")
        pk = Group.objects.get(name="editors").pk
        response = Client().get("/group/?group=%d" % pk)
        assert response.status_code == 200
        assert b"editors|0" in response.content


class TestAWrapperThatSwallowsTheFormStepFailsClosed:
    """``sync_turn_wrapper`` may suppress an exception; a failed form must not mean "no form"."""

    @pytest.fixture
    def swallowing_wrapper(self):
        import contextlib

        from djust.config import config as djust_config

        @contextlib.contextmanager
        def scope_(view, step, turn):
            try:
                yield
            except Exception:  # noqa: BLE001 - the wrapper under test swallows it
                return

        previous = djust_config.get("sync_turn_wrapper")
        djust_config.set("sync_turn_wrapper", scope_)
        yield
        djust_config.set("sync_turn_wrapper", previous)

    def test_the_http_get_is_refused_not_handed_the_raw_params(self, swallowing_wrapper):
        response = Client(raise_request_exception=False).get("/exploding/?q=x")
        assert CALLS == [], "handle_params ran on a URL no form accepted"
        assert response.status_code == 403, response.status_code

    @pytest.mark.asyncio
    async def test_the_socket_mount_fails_rather_than_handing_over_the_raw_params(
        self, swallowing_wrapper
    ):
        communicator, frame, frames = await _connect(
            "ExplodingView", params={"q": "x"}, url="/exploding/"
        )
        assert frame["type"] == "error", frames
        assert CALLS == []
        await communicator.disconnect()


class TestErrorsAreEscaped:
    def test_a_value_echoed_by_an_error_message_is_escaped(self):
        body = Client().get("/list/?category=%3Cscript%3Ealert(1)%3C/script%3E").content.decode()
        assert "<script>alert(1)</script>" not in body
        assert "CATEGORY:" in body


# --------------------------------------------------------------------------- #
# One decision for every transport
# --------------------------------------------------------------------------- #


def _direct_hook_references(source: str) -> list:
    """``(line, kind)`` for each way a module names ``handle_params`` on some object."""
    import ast

    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and node.attr == "handle_params":
            found.append((node.lineno, "attribute"))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"getattr", "hasattr"}
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "handle_params"
        ):
            found.append((node.lineno, node.func.id))
    return found


class TestNoPathCallsHandleParamsDirectly:
    """A pin on the CALLER SET (#1125): a new path must go through the helper.

    A direct ``handle_params`` call would skip the form on that transport, and
    only that transport, which is the parallel-path drift (#1646) this feature
    exists to avoid. The scan is over the AST of every framework module, so a
    renamed receiver (``target_view.handle_params``) or a
    ``getattr(view, "handle_params")`` is found as well as ``view.handle_params``.
    """

    #: The helper itself, and the default ``handle_invalid_params`` that hands
    #: ``handle_params`` the initial values.
    ALLOWED = {"_params_form.py", "mixins/navigation.py"}

    def test_only_the_shared_helper_names_the_hook(self):
        import pathlib

        import djust

        root = pathlib.Path(djust.__file__).parent
        offenders = []
        scanned = 0
        for module in sorted(root.rglob("*.py")):
            rel = module.relative_to(root).as_posix()
            if rel.startswith("tests/") or "/tests/" in rel or rel in self.ALLOWED:
                continue
            scanned += 1
            for line, kind in _direct_hook_references(module.read_text()):
                offenders.append("%s:%d %s" % (rel, line, kind))
        assert scanned > 100, "the scan found no framework modules"
        assert offenders == [], offenders

    def test_the_scan_flags_each_shape_a_new_path_could_use(self):
        """Not vacuous: renamed receivers and the getattr/hasattr forms are all found."""
        source = (
            "async def f(target_view, other):\n"
            "    await call_hook(target_view.handle_params, {}, '')\n"
            "    hook = getattr(other, 'handle_params')\n"
            "    return hasattr(other, 'handle_params')\n"
        )
        assert sorted(_direct_hook_references(source)) == [
            (2, "attribute"),
            (3, "getattr"),
            (4, "hasattr"),
        ]
        assert _direct_hook_references("def f(view):\n    return view.handle_other\n") == []
