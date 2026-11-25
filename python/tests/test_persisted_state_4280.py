"""``persisted_state(view)`` is the public way to see what an explicit view would save (#4280).

``_get_private_state()`` raises on an explicit view, and ``get_state()`` is what the view holds,
not what it saves. ``persisted_state()`` splits the saved state by destination so a test can
assert that rows a page only read are in none of them.
"""

import pytest

from djust import LiveView, state
from djust import _automatic_state
from djust.testing import LiveViewTestClient, PersistedState, persisted_state


class Page(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>{{ shown }}{{ hidden }}</div>"
    shown = state("a", client=True)
    hidden = state("b")
    kept = state("k", persist="server")
    not_rendered = state("NR_SENTINEL", render=False)

    def mount(self, request, **kwargs):
        self.secret_rows = ["row"]

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "only_in_context": "CONTEXT_ONLY"}


class NoAutomatic(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>{{ hidden }}</div>"
    automatic_state = False
    hidden = state("b")
    kept = state("k", persist="server")

    def mount(self, request, **kwargs):
        self.secret_rows = ["row"]


def _mounted(view_class=Page):
    client = LiveViewTestClient(view_class)
    client.mount()
    return client


def test_persisted_state_splits_destinations():
    saved = _mounted().persisted_state()
    assert isinstance(saved, PersistedState)
    assert saved.automatic["secret_rows"] == ["row"]
    assert "kept" in saved.server and "kept" not in saved.automatic
    assert "shown" in saved.client
    assert saved.automatic["hidden"] == "b"
    # S2: declared fields without persist= are saved in automatic state, render=False too.
    assert saved.automatic["not_rendered"] == "NR_SENTINEL"
    assert {"secret_rows", "kept", "shown", "hidden"} <= saved.names


def test_render_false_and_get_context_data_values_are_not_in_automatic():
    client = _mounted()
    assert "only_in_context" in client.view_instance.get_context_data()
    saved = client.persisted_state()
    assert "only_in_context" not in saved.names
    assert "CONTEXT_ONLY" not in repr(saved)


def test_automatic_state_off_gives_empty_automatic():
    saved = _mounted(NoAutomatic).persisted_state()
    assert saved.automatic == {} and saved.automatic_private == {}
    assert saved.automatic_components == {} and saved.automatic_bytes == 0
    assert "kept" in saved.server  # a declared field is another store
    assert "secret_rows" not in saved.names


def test_client_method_matches_function():
    client = _mounted()
    assert client.persisted_state() == persisted_state(client.view_instance)


def test_client_method_needs_a_mounted_view():
    with pytest.raises(RuntimeError):
        LiveViewTestClient(Page).persisted_state()


def test_automatic_bytes_matches_saved_envelope_size():
    client = _mounted()
    saved = client.persisted_state()
    assert saved.automatic_bytes == len(
        _automatic_state.project(client.view_instance).text.encode()
    )
    assert saved.automatic_bytes > 0


def test_unsupplied_template_name_error_is_importable_from_the_public_module():
    """S3: the error a test catches is importable from djust.testing, not a private module."""
    from django.core.exceptions import ImproperlyConfigured

    import djust.testing as public

    assert "UnsuppliedTemplateNameError" in public.__all__
    assert issubclass(public.UnsuppliedTemplateNameError, ImproperlyConfigured)
