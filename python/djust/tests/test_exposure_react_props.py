"""ADR-038 E1-4: React component props at the rendered-HTML destination.

``_hydrate_react_components`` resolves ``{{ name }}`` props from
``get_context_data()`` and writes them into the page as ``data-react-props``.
Under the explicit policy that context holds only deliberate additions, so a
prop naming an undeclared attribute stays unresolved: the value never reaches
the HTML. The legacy control resolves it from the attribute walk.
"""

import pytest

from djust import LiveView
from djust.react import react_components

SENTINEL = "REACT_PROP_SENTINEL"


class ReactPropView(LiveView):
    template = (
        '<div dj-root><div data-react-component="ExposureCard" '
        'data-react-props=\'{"note": "{{ secret_note }}", "title": "{{ title }}"}\'>'
        "</div></div>"
    )

    def mount(self, request, **kwargs):
        self.secret_note = SENTINEL

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.exposure_policy == "explicit":
            context["title"] = "DECLARED_TITLE"
        return context


@pytest.fixture(autouse=True)
def card():
    react_components.register("ExposureCard")(lambda props, children: f"<b>{props}</b>")
    yield
    react_components._components.pop("ExposureCard", None)


@pytest.mark.parametrize("policy", ["legacy", "explicit"])
def test_react_props_resolve_only_from_the_context_projection(rf, db, policy):
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore

    view_class = type("PolicyReactView", (ReactPropView,), {"exposure_policy": policy})
    request = rf.get("/react/")
    request.user = AnonymousUser()
    request.session = SessionStore()
    request.session.create()
    response = view_class.as_view()(request)
    body = response.content.decode()
    assert response.status_code == 200, body[:300]
    assert "data-react-component" in body, "the placeholder was not rendered"
    if policy == "legacy":
        assert SENTINEL in body
    else:
        assert SENTINEL not in body
        assert "DECLARED_TITLE" in body


@pytest.mark.parametrize("policy", ["legacy", "explicit"])
def test_hydration_resolves_props_only_from_the_context_projection(rf, db, policy):
    """Hydration itself: ``{{ name }}`` props that survive template rendering
    (a placeholder built outside the template engine) resolve against
    ``get_context_data()``, never against arbitrary attributes."""
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore

    view_class = type("HydrateReactView", (ReactPropView,), {"exposure_policy": policy})
    request = rf.get("/react/")
    request.user = AnonymousUser()
    request.session = SessionStore()
    request.session.create()
    view = view_class()
    view.setup(request)
    view.request = request
    view.mount(request)
    placeholder = (
        '<div data-react-component="ExposureCard" '
        'data-react-props=\'{"note": "{{ secret_note }}", "title": "{{ title }}"}\'>'
        "</div>"
    )
    html = view._hydrate_react_components(placeholder)
    assert "<b>" in html, "the registered renderer did not run"
    if policy == "legacy":
        assert SENTINEL in html
    else:
        assert SENTINEL not in html
        assert "DECLARED_TITLE" in html
