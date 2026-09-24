"""ADR-038 E1-4: the native (LiveView Native) renderer at its output.

``NativeRenderer`` renders the view's native template against
``get_context_data()`` and emits a widget VNode tree to the client. Under the
explicit policy that context holds only deliberate additions, so a template
that names an undeclared attribute renders it empty in the emitted tree. The
legacy control reaches it through the attribute walk.
"""

from unittest.mock import patch

import pytest
from django.template import Context, Template

from djust import LiveView

SENTINEL = "NATIVE_RENDER_SENTINEL"
NATIVE_TEMPLATE = "<Stack><Text>{{ secret_note }}</Text><Text>{{ title }}</Text></Stack>"


class NativeExposureView(LiveView):
    template_name = "native_exposure.html"

    def mount(self, request, **kwargs):
        self.secret_note = SENTINEL

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.exposure_policy == "explicit":
            context["title"] = "DECLARED_TITLE"
        return context


def _render_with_django(template_name, context):
    return Template(NATIVE_TEMPLATE).render(Context(context))


@pytest.mark.parametrize("policy", ["legacy", "explicit"])
def test_native_tree_carries_only_the_context_projection(rf, db, policy):
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore

    from djust.renderers import ComposeRenderer

    view_class = type("PolicyNativeView", (NativeExposureView,), {"exposure_policy": policy})
    request = rf.get("/native/")
    request.user = AnonymousUser()
    request.session = SessionStore()
    request.session.create()
    view = view_class()
    view.setup(request)
    view.request = request
    view.mount(request)

    renderer = ComposeRenderer(view)
    with (
        patch.object(renderer, "resolve_template", return_value="native_exposure.compose.html"),
        patch("djust.renderers.native.render_to_string", side_effect=_render_with_django),
    ):
        _html, tree_json, _version = renderer.render_with_diff()

    emitted = str(tree_json)
    assert '"tag": "text"' in emitted, "the native tree was not emitted"
    if policy == "legacy":
        assert SENTINEL in emitted
    else:
        assert SENTINEL not in emitted
        assert "DECLARED_TITLE" in emitted
