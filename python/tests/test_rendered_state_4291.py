"""``rendered_state(view)`` is the public reader of what a render handed the template engine (#4291).

``persisted_state()`` answers what a save keeps and ``get_state()`` (explicit) only the
``client=True`` fields; neither shows the renderer's state, which a data-minimization test needs.
"""

import pytest

from djust import LiveView, state
from djust.testing import LiveViewTestClient, rendered_state


class Page(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>{{ shown }} {{ extra }}</div>"
    shown = state("a")

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "extra": {"k": "v"}}


def test_rendered_state_reads_the_renderers_state():
    client = LiveViewTestClient(Page)
    client.mount()
    client.render()
    got = client.rendered_state()
    assert got["shown"] == "a"
    assert got["extra"] == {"k": "v"}
    assert rendered_state(client.view_instance) == got


def test_rendered_state_is_detached():
    client = LiveViewTestClient(Page)
    client.mount()
    client.render()
    got = client.rendered_state()
    got["extra"]["k"] = "changed"
    got["shown"] = "changed"
    again = client.rendered_state()
    assert again["shown"] == "a"
    assert again["extra"] == {"k": "v"}


def test_rendered_state_before_mount_raises():
    with pytest.raises(RuntimeError):
        LiveViewTestClient(Page).rendered_state()


def test_rendered_state_without_a_renderer_raises():
    view = Page()
    view.__dict__["_rust_view"] = None
    with pytest.raises(RuntimeError, match="not rendered"):
        rendered_state(view)
