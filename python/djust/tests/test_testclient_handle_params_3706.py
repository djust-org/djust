"""``LiveViewTestClient`` drives ``handle_params`` the way the transports do (#3706).

The client called ``mount(request, **params)`` and nothing else, so a view that
declares ``params_form`` (#3689), or builds any state in ``handle_params()``,
could not be unit-tested through it: the state the page needs was never built.
The HTTP GET, the socket mount and ``url_change`` all call ``handle_params``
right after ``mount()`` (with an empty dict when the URL has no query string),
so the client does too, through the same ``djust._params_form`` decision.

* ``mount(query_params=...)`` gives the first ``handle_params`` a query string;
* ``url_change(query_params)`` is the ``live_patch`` / popstate frame.
"""

from __future__ import annotations

import pytest
from django import forms
from django.test import RequestFactory

from djust import LiveView
from djust.testing import LiveViewTestClient

pytestmark = pytest.mark.django_db

CALLS: list = []


@pytest.fixture(autouse=True)
def _clear_calls():
    CALLS.clear()
    yield
    CALLS.clear()


class ListParams(forms.Form):
    category = forms.ChoiceField(
        choices=[("all", "All"), ("books", "Books")], required=False, initial="all"
    )
    page = forms.IntegerField(min_value=1, required=False, initial=1)


class FormView(LiveView):
    """The #3689 example: a form-declared URL."""

    login_required = False
    params_form = ListParams
    template = "<div dj-root>{{ category }}/{{ page }}</div>"

    def mount(self, request, **kwargs):
        self.category = "unset"
        self.page = 0

    def handle_params(self, params, uri):
        CALLS.append((dict(params), uri))
        self.category = params["category"]
        self.page = params["page"]


class RawView(LiveView):
    """No ``params_form``: ``handle_params`` gets the raw dict."""

    login_required = False
    template = "<div dj-root>{{ tab }}</div>"

    def mount(self, request, **kwargs):
        self.tab = "none"

    def handle_params(self, params, uri):
        CALLS.append((dict(params), uri))
        self.tab = params.get("tab", "none")


class NoParamsHookView(LiveView):
    """Never defines ``handle_params``: the default hook must be harmless."""

    login_required = False
    template = "<div dj-root>{{ n }}</div>"

    def mount(self, request, **kwargs):
        self.n = 7


class TestMountRunsHandleParams:
    def test_a_view_that_builds_state_in_handle_params_has_it_after_mount(self):
        client = LiveViewTestClient(RawView).mount()
        assert CALLS == [({}, "/")]
        assert client.view_instance.tab == "none"

    def test_the_initial_values_reach_handle_params_when_the_url_is_bare(self):
        client = LiveViewTestClient(FormView).mount()
        assert client.view_instance.category == "all"
        assert client.view_instance.page == 1
        assert client.view_instance.params_errors == {}

    @pytest.mark.parametrize("via_websocket", [True, False])
    def test_both_mount_branches_run_it(self, via_websocket):
        client = LiveViewTestClient(FormView).mount(via_websocket=via_websocket)
        assert client.view_instance.page == 1

    def test_a_view_without_the_hook_still_mounts(self):
        client = LiveViewTestClient(NoParamsHookView).mount()
        assert client.get_state()["n"] == 7

    def test_a_query_string_is_cleaned_by_the_form(self):
        client = LiveViewTestClient(FormView).mount(query_params={"page": "3", "category": "books"})
        assert client.view_instance.page == 3  # an int: the form cleaned it
        assert client.view_instance.category == "books"
        assert CALLS == [({"category": "books", "page": 3}, "/?page=3&category=books")]

    def test_the_query_string_may_be_given_as_text(self):
        client = LiveViewTestClient(FormView).mount(query_params="page=4")
        assert client.view_instance.page == 4

    def test_an_invalid_value_is_a_message_in_the_page_not_an_exception(self):
        client = LiveViewTestClient(FormView).mount(query_params={"page": "abc"})
        assert client.view_instance.page == 1  # the default handle_invalid_params
        assert list(client.view_instance.params_errors) == ["page"]
        assert client.get_state()["params_errors"] == client.view_instance.params_errors

    def test_the_form_errors_reach_the_rendered_page(self):
        class Rendering(FormView):
            template = (
                "<div dj-root>{% if params_errors.page %}ERR:{{ params_errors.page.0 }}{% endif %}"
                "page={{ page }}</div>"
            )

        html = LiveViewTestClient(Rendering).mount(query_params={"page": "abc"}).render()
        assert "ERR:" in html
        assert "page=1" in html

    def test_a_repeated_key_stays_a_list_and_a_single_value_a_scalar(self):
        LiveViewTestClient(RawView).mount(query_params={"tab": ["a", "b"], "x": "1"})
        ((params, uri),) = CALLS
        assert params == {"tab": ["a", "b"], "x": "1"}
        assert uri == "/?tab=a&tab=b&x=1"

    def test_mount_kwargs_and_query_params_are_independent(self):
        class Both(RawView):
            def mount(self, request, **kwargs):
                self.kwargs_seen = dict(kwargs)
                self.tab = "none"

        client = LiveViewTestClient(Both).mount(item_id=5, query_params={"tab": "t"})
        assert client.view_instance.kwargs_seen == {"item_id": 5}
        assert client.view_instance.tab == "t"

    def test_the_request_carries_the_query_string(self):
        class Reads(RawView):
            def mount(self, request, **kwargs):
                self.tab = request.GET.get("tab", "none")

            def handle_params(self, params, uri):
                pass

        client = LiveViewTestClient(Reads).mount(query_params={"tab": "viaGET"})
        assert client.view_instance.tab == "viaGET"
        assert client.view_instance._websocket_query_string == "tab=viaGET"

    def test_handle_params_failures_are_not_swallowed(self):
        class Raises(RawView):
            def handle_params(self, params, uri):
                raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            LiveViewTestClient(Raises).mount()


class TestUrlChange:
    def test_url_change_runs_handle_params_with_the_new_query(self):
        client = LiveViewTestClient(FormView).mount()
        CALLS.clear()
        client.url_change({"page": "2"})
        assert client.view_instance.page == 2
        assert CALLS == [({"category": "all", "page": 2}, "/?page=2")]

    def test_an_invalid_url_change_sets_errors_and_a_valid_one_clears_them(self):
        client = LiveViewTestClient(FormView).mount()
        client.url_change({"page": "0"})
        assert "page" in client.view_instance.params_errors
        client.url_change({"page": "5"})
        assert client.view_instance.params_errors == {}
        assert client.view_instance.page == 5

    def test_it_requires_a_mounted_view(self):
        with pytest.raises(RuntimeError, match="mount"):
            LiveViewTestClient(FormView).url_change({"page": "2"})

    def test_it_is_recorded_in_the_event_history(self):
        client = LiveViewTestClient(FormView).mount()
        client.url_change({"page": "2"})
        assert client.get_event_history()[-1]["type"] == "url_change"
        assert client.get_event_history()[-1]["params"] == {"page": "2"}

    def test_it_returns_the_client_for_chaining(self):
        client = LiveViewTestClient(FormView).mount()
        assert client.url_change({"page": "2"}) is client


class TestParityWithTheHttpGet:
    """What the client hands ``handle_params`` is what a real GET hands it."""

    QUERY = "page=3&category=books&category=all&extra=1"

    def _real_get(self, view_class):
        from django.contrib.auth.models import AnonymousUser

        from djust.utils import build_session_for_request

        request = RequestFactory().get("/?" + self.QUERY)
        request.user = AnonymousUser()
        request.session = build_session_for_request()
        view_class.as_view()(request)
        recorded = list(CALLS)
        CALLS.clear()
        assert recorded, "the GET did not call handle_params"
        return recorded

    def test_the_params_and_the_uri_are_the_ones_the_get_passes(self):
        via_get = self._real_get(FormView)
        LiveViewTestClient(FormView).mount(query_params=self.QUERY)
        assert CALLS == via_get

    def test_a_raw_view_gets_the_same_dict_as_the_get(self):
        via_get = self._real_get(RawView)
        LiveViewTestClient(RawView).mount(query_params=self.QUERY)
        assert CALLS == via_get


class TestParityHelperAndCreateTestView:
    def test_the_djid_parity_helper_runs_handle_params_on_both_instances(self):
        """``_build_mounted_instance`` is a second mount path in this module."""
        client = LiveViewTestClient(RawView)
        client.assert_http_ws_djid_parity()
        # one call per built instance (HTTP-shaped and WS-shaped)
        assert len(CALLS) == 2

    def test_create_test_view_gets_handle_params_too(self):
        from djust.testing import create_test_view

        view = create_test_view(RawView)
        assert view.tab == "none"
        assert CALLS == [({}, "/")]
