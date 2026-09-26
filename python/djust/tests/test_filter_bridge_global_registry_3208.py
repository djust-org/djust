"""djust's own filters must stay visible to every render (#3208).

``_ensure_custom_filters_bridged`` forwards every Django library filter
(``live_tags``' ``field_value`` included) to the Rust registry once per
process, lazily, on the first render. Two ways it went wrong, both leaving
the one-shot flag saying "bridged" while root-LiveView renders failed with
``Invalid filter: 'field_value'`` for the rest of the process:

1. **The first render was inside another backend's registry namespace.**
   ``DjustTemplateBackend.from_string`` renders under
   ``rendering_with_backend(backend)``. When that was the process's first
   render, the bootstrap registered into that backend's namespace. This is
   what turned the pre-push hook red: on its xdist worker,
   ``test_catalogue_ux.py::TestUsageWithEvents::...[...-rust]`` ran first and
   ``test_rust_renderer_v1_2_1_9.py::test_form_tags_and_filters_work_in_a_real_root_liveview``
   failed after it. Fix: the bootstrap always writes the global namespace.
2. **Something emptied the global registry after the bootstrap**
   (``clear_custom_filters()``). Fix: the guard re-verifies the bridged names
   whenever the registry generation moved.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django import forms

from djust import LiveView, _rust
from djust import template_filters as tf
from djust.forms import FormMixin


class _NameForm(forms.Form):
    name = forms.CharField(max_length=10)


def _render_field_value() -> str:
    class V(FormMixin, LiveView):
        form_class = _NameForm
        template = (
            "{% load live_tags %}<div dj-root>"
            '<u>[{{ view|field_value:"name" }}]'
            '{% if view|has_errors:"name" %}bad{% endif %}</u>'
            "</div>"
        )

    v = V()
    v.mount(None)
    v.submit_form(name="far too long a name")
    html, _patches, _version = v.render_with_diff(None)
    return html


@pytest.mark.django_db
def test_first_bootstrap_inside_another_backends_render_serves_every_render():
    """The hook's failing sequence, in-process: the process's first bridge
    happens inside a separate ``DjustTemplateBackend`` render (what
    ``test_catalogue_ux``'s rust-backend case does), then a root LiveView
    renders ``{{ view|field_value:"name" }}``."""
    from djust.template_backend import DjustTemplateBackend

    _rust.clear_custom_filters()
    tf._CUSTOM_FILTERS_BRIDGED = False  # a fresh worker: nothing bridged yet
    assert not _rust.registry_entry_is_local("field_value", "filter")

    DjustTemplateBackend(
        {"NAME": "usage-test-3208", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}}
    ).from_string("{% load live_tags %}<p>{{ x }}</p>")

    assert tf._CUSTOM_FILTERS_BRIDGED
    assert _rust.current_registry_namespace() == 0
    assert _rust.registry_entry_is_local("field_value", "filter"), (
        "the bootstrap registered into the other backend's namespace"
    )
    html = _render_field_value()
    assert "[far too long a name]" in html
    assert "bad</u>" in html


@pytest.mark.django_db
def test_root_liveview_filters_render_after_the_registry_was_cleared():
    """The polluting sequence, in-process: bridge, clear, then the renderer test."""
    tf._ensure_custom_filters_bridged()
    assert _rust.registry_entry_is_local("field_value", "filter")
    _rust.clear_custom_filters()  # what test_rust_custom_filters_1121's teardown does
    assert not _rust.registry_entry_is_local("field_value", "filter")

    html = _render_field_value()

    assert "[far too long a name]" in html
    assert "bad</u>" in html


def test_an_unchanged_registry_does_not_bootstrap_again():
    """The steady-state render path stays one generation read, no re-walk."""
    tf._ensure_custom_filters_bridged()
    with patch.object(tf, "bootstrap_django_filters", wraps=tf.bootstrap_django_filters) as boot:
        for _ in range(5):
            tf._ensure_custom_filters_bridged()
    assert boot.call_count == 0


def test_a_registry_change_that_keeps_the_bridged_filters_does_not_bootstrap():
    tf._ensure_custom_filters_bridged()
    before = _rust.registry_generation()
    tf.register_django_filter("probe_3208", lambda value, arg=None: value)
    try:
        assert _rust.registry_generation() != before
        with patch.object(tf, "bootstrap_django_filters") as boot:
            tf._ensure_custom_filters_bridged()
        assert boot.call_count == 0
    finally:
        _rust.unregister_custom_filter("probe_3208")


def test_clearing_the_registry_bootstraps_exactly_once():
    tf._ensure_custom_filters_bridged()
    _rust.clear_custom_filters()
    with patch.object(tf, "bootstrap_django_filters", wraps=tf.bootstrap_django_filters) as boot:
        tf._ensure_custom_filters_bridged()
        tf._ensure_custom_filters_bridged()
    assert boot.call_count == 1
    assert _rust.registry_entry_is_local("field_value", "filter")


def test_a_failed_bootstrap_is_not_retried_on_every_render():
    tf._ensure_custom_filters_bridged()
    _rust.clear_custom_filters()
    try:
        with patch.object(tf, "bootstrap_django_filters", side_effect=RuntimeError("boom")) as boot:
            tf._ensure_custom_filters_bridged()
            tf._ensure_custom_filters_bridged()
        assert boot.call_count == 1
    finally:
        tf._CUSTOM_FILTERS_BRIDGED = False
        tf._ensure_custom_filters_bridged()  # leave the registry bridged
    assert _rust.registry_entry_is_local("field_value", "filter")
