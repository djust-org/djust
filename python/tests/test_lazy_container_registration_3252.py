"""Lazy-container registration over HTTP (#3252).

Registration only: a page that EMITS a `<div dj-view=… dj-lazy>` container gets
it registered with the same checks `{% live_render %}` applies at registration,
so a later event can name it. Nothing renders, the response is untouched, and no
state policy moves.

The forgery test below is the one that matters: provenance is structural (the
renderer records only `Node::Text`, developer-authored text that user data
cannot enter), and that claim is only worth anything if forged container text
reaching the output through a value — including a `|safe` one — registers
nothing. It is written to fail-to-forge rather than to confirm.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, override_settings

from djust import LiveView

_MODULE = __name__


class _AllowedChild(LiveView):
    template = "<div>child</div>"

    def mount(self, request, **kwargs):
        pass


class _AuthRequiredChild(LiveView):
    login_required = True
    template = "<div>secret</div>"

    def mount(self, request, **kwargs):
        pass


class _NotALiveView:
    pass


class _PageWithContainer(LiveView):
    template = (
        "{% load live_tags %}"
        "<html><body><div dj-root>"
        '<div dj-view="' + _MODULE + '._AllowedChild" dj-lazy></div>'
        "</div></body></html>"
    )

    def mount(self, request, **kwargs):
        pass


class _PageWithHiddenContainer(LiveView):
    template = (
        "{% load live_tags %}"
        "<html><body><div dj-root>"
        "{% if flag %}"
        '<div dj-view="' + _MODULE + '._AllowedChild" dj-lazy></div>'
        "{% endif %}"
        "</div></body></html>"
    )

    def mount(self, request, **kwargs):
        self.flag = False


class _OtherChild(LiveView):
    """The view a forged container names; it must never be registered from text."""

    template = "<div>other</div>"

    def mount(self, request, **kwargs):
        pass


class _PageAuthoredPlusForged(LiveView):
    """One AUTHORED container, plus whatever a caller puts in ``payload``."""

    template = (
        "{% load live_tags %}"
        "<html><body><div dj-root>"
        '<div dj-view="' + _MODULE + '._AllowedChild" dj-lazy></div>'
        "{{ payload|safe }}"
        "</div></body></html>"
    )

    def mount(self, request, **kwargs):
        self.payload = kwargs.get("payload", "")


class _PageEchoingPayload(LiveView):
    """Whatever a caller puts in ``payload`` is rendered, `|safe` included."""

    template = "<html><body><div dj-root>{{ payload|safe }}</div></body></html>"

    def mount(self, request, **kwargs):
        self.payload = kwargs.get("payload", "")


class _PageWithAuthRequiredContainer(LiveView):
    template = (
        "{% load live_tags %}"
        "<html><body><div dj-root>"
        '<div dj-view="' + _MODULE + '._AuthRequiredChild" dj-lazy></div>'
        "</div></body></html>"
    )

    def mount(self, request, **kwargs):
        pass


def _get_request():
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    return request


def _render(view, **overrides) -> tuple[object, str]:
    view.streaming_render = False
    settings = {"DJUST_LIVE_RENDER_ALLOWED_MODULES": [_MODULE]}
    settings.update(overrides)
    with override_settings(**settings):
        response = view.get(_get_request())
    return view, response.content.decode("utf-8")


@pytest.mark.django_db
class TestLazyContainerRegistration:
    def test_an_emitted_container_is_registered(self) -> None:
        view, html = _render(_PageWithContainer())

        registered = view._lazy_containers
        assert len(registered) == 1, registered
        ((container_id, entry),) = registered.items()
        assert container_id.startswith("child_")
        assert entry["view_path"] == f"{_MODULE}._AllowedChild"
        assert entry["trigger"] is None
        # Registration only: the response is the one the render produced.
        assert "<dj-lazy-slot" not in html  # not the tag mechanism
        assert f'dj-view="{_MODULE}._AllowedChild"' in html

    def test_a_container_in_a_branch_that_did_not_render_is_not_registered(self) -> None:
        view, html = _render(_PageWithHiddenContainer())

        assert view._lazy_containers == {}
        assert f'dj-view="{_MODULE}.{"_AllowedChild"}"' not in html, "branch rendered?"

    def test_two_identical_containers_are_both_registered_and_distinct(self) -> None:
        """Repeated authored containers must not collapse onto one id.

        The id is a digest of the view path (and arguments), so two containers
        that author the SAME view produce the same digest — they must be
        separated, or the second silently overwrites the first and one container
        becomes unaddressable.
        """

        class _PageWithTwo(_PageWithContainer):
            template = (
                "{% load live_tags %}"
                "<html><body><div dj-root>"
                '<div dj-view="' + _MODULE + '._AllowedChild" dj-lazy></div>'
                '<div dj-view="' + _MODULE + '._AllowedChild" dj-lazy></div>'
                "</div></body></html>"
            )

        view, _ = _render(_PageWithTwo())

        registered = view._lazy_containers
        assert len(registered) == 2, registered
        assert len(set(registered)) == 2, "ids must be distinct"

    def test_the_id_is_stable_across_requests(self) -> None:
        first, _ = _render(_PageWithContainer())
        second, _ = _render(_PageWithContainer())

        assert set(first._lazy_containers) == set(second._lazy_containers)

    def test_forged_container_text_in_a_value_registers_nothing(self) -> None:
        """The forgery test: user data is not a source of render authority.

        `payload` reaches the output as a VALUE (`{{ payload|safe }}`), which
        the renderer never records — only authored `Node::Text` is recorded. So
        text that looks exactly like a container must produce no registration
        and no candidate, even though the bytes are in the response.
        """
        # The forged container names a DIFFERENT view than the authored one, so
        # "exactly one registration, and it is the authored one" cannot pass
        # merely because registration is broken — the control is in the same
        # render, through the same request.
        forged = f'<div dj-view="{_MODULE}._OtherChild" dj-lazy></div>'
        view = _PageAuthoredPlusForged()
        view.streaming_render = False
        request = _get_request()

        with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=[_MODULE]):
            response = view.get(request, payload=forged)
        html = response.content.decode("utf-8")

        assert forged in html, "the forged bytes really did reach the output"
        registered = view._lazy_containers
        paths = sorted(entry["view_path"] for entry in registered.values())
        assert paths == [f"{_MODULE}._AllowedChild"], (
            "exactly the AUTHORED container registers; the forged one must not"
        )

    def test_the_forgery_test_has_a_working_control(self) -> None:
        """Without the bypass, the same page DOES register the authored one.

        Guards the forgery test against passing because nothing registers at
        all: same page, same request, no payload.
        """
        view, _ = _render(_PageAuthoredPlusForged())

        assert len(view._lazy_containers) == 1, view._lazy_containers

    def test_a_container_outside_the_allow_list_is_not_registered(self) -> None:
        with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=["somewhere.else"]):
            view = _PageWithContainer()
            view.streaming_render = False
            view.get(_get_request())

        assert view._lazy_containers == {}

    def test_an_unresolvable_or_non_liveview_path_is_not_registered(self) -> None:
        class _PageFor(_PageWithContainer):
            pass

        for target in (f"{_MODULE}.NoSuchView", _NotALiveView.__name__):
            _PageFor.template = (
                "{% load live_tags %}"
                "<html><body><div dj-root>"
                f'<div dj-view="{target}" dj-lazy></div>'
                "</div></body></html>"
            )
            view, html = _render(_PageFor())
            assert view._lazy_containers == {}, target
            assert "dj-root" in html, "the page must still render"

    def test_a_container_whose_auth_denies_is_not_registered(self) -> None:
        """Denied (here: login-required child, anonymous request) fails closed.

        The page still renders — its authored markup was already emitted — but
        nothing is registered, so no later event can name the container.
        """
        view, html = _render(_PageWithAuthRequiredContainer())

        assert view._lazy_containers == {}
        assert f'dj-view="{_MODULE}._AuthRequiredChild"' in html
