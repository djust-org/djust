"""ADR-038: a non-sticky child's failing context must not fall back or log values.

The sticky helper (``_render_sticky_child_html``) already raises a value-free
``ExposureError`` when an explicit child's ``get_context_data`` fails. Its
non-sticky twin in ``live_render`` logged the exception with its traceback and
rendered the child with an empty context instead — for any policy.
"""

import logging

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.template import Context, Template

from djust import LiveView
from djust._exposure import ExposureError
from djust.decorators import state

CALLS = []


class ChildParent(LiveView):
    template = "<div dj-root></div>"


class ContextFailChild(LiveView):
    exposure_policy = "legacy"
    count = state(1)
    template = "<div>{{ count }}</div>"

    def get_context_data(self, **kwargs):
        CALLS.append(True)
        raise ValueError("CHILD_CONTEXT_SENTINEL")


@pytest.fixture(autouse=True)
def staged(monkeypatch, settings, db):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    settings.DJUST_LIVE_RENDER_ALLOWED_MODULES = [__name__]


def _render(rf):
    request = rf.get("/page/")
    request.user = AnonymousUser()
    request.tenant = None
    request.session = SessionStore()
    parent = ChildParent()
    parent.request = request
    return Template("{% load live_tags %}{% live_render target %}").render(
        Context(
            {
                "view": parent,
                "request": request,
                "target": __name__ + ".ContextFailChild",
            }
        )
    )


@pytest.mark.parametrize("policy", ["legacy", "explicit"])
def test_nonsticky_child_context_failure(monkeypatch, rf, caplog, policy):
    CALLS.clear()
    monkeypatch.setattr(ContextFailChild, "exposure_policy", policy)
    with caplog.at_level(logging.DEBUG):
        if policy == "legacy":
            html = _render(rf)
            # Unchanged legacy behaviour: logged, then rendered with no context.
            assert "CHILD_CONTEXT_SENTINEL" in caplog.text
            assert "<div>" in html
        else:
            with pytest.raises(ExposureError, match="Explicit child rendering context unavailable"):
                _render(rf)
            assert "CHILD_CONTEXT_SENTINEL" not in caplog.text
    assert CALLS, "the child's get_context_data never ran; the test would be vacuous"
