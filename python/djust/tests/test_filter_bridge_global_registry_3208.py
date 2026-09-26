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


def _count_registrations():
    """Patch ``register_django_filter`` with a counting pass-through."""
    return patch.object(tf, "register_django_filter", wraps=tf.register_django_filter)


def _fresh_bootstrap():
    """Re-run the one-time bootstrap from scratch (a new worker)."""
    tf._CUSTOM_FILTERS_BRIDGED = False
    tf._ensure_custom_filters_bridged()


def test_an_unchanged_registry_does_not_bootstrap_or_register_again():
    """The steady-state render path stays one generation read."""
    tf._ensure_custom_filters_bridged()
    with (
        patch.object(tf, "bootstrap_django_filters") as boot,
        _count_registrations() as register,
    ):
        for _ in range(5):
            tf._ensure_custom_filters_bridged()
    assert boot.call_count == 0
    assert register.call_count == 0


def test_unrelated_registrations_do_not_trigger_re_bridging():
    """#3213 review 3: any other registry change leaves the bridge alone."""
    tf._ensure_custom_filters_bridged()
    try:
        with (
            patch.object(tf, "bootstrap_django_filters") as boot,
            _count_registrations() as register,
        ):
            for i in range(10):
                _rust.register_custom_filter("probe_3208_%d" % i, lambda v, a=None: v, False, False)
                tf._ensure_custom_filters_bridged()
        assert boot.call_count == 0
        assert register.call_count == 0
    finally:
        for i in range(10):
            _rust.unregister_custom_filter("probe_3208_%d" % i)


def test_clearing_the_registry_restores_only_the_missing_names_once():
    tf._ensure_custom_filters_bridged()
    _rust.clear_custom_filters()
    with (
        patch.object(tf, "bootstrap_django_filters") as boot,
        _count_registrations() as register,
    ):
        tf._ensure_custom_filters_bridged()
        first = register.call_count
        tf._ensure_custom_filters_bridged()
    assert boot.call_count == 0, "restoring must not re-walk every library"
    assert first == len(tf._BRIDGED_FILTERS) > 0
    assert register.call_count == first, "a second call restored again"
    assert _rust.registry_entry_is_local("field_value", "filter")


def test_losing_one_filter_restores_that_filter_only():
    tf._ensure_custom_filters_bridged()
    _rust.unregister_custom_filter("field_value")
    with _count_registrations() as register:
        tf._ensure_custom_filters_bridged()
    assert [c.args[0] for c in register.call_args_list] == ["field_value"]
    assert _rust.registry_entry_is_local("field_value", "filter")


def test_restoring_never_overwrites_a_same_name_filter_a_load_registered():
    """#3213 review 1 (the reviewer's repro): two libraries define the same
    filter name. The bootstrap registered the second one's; a ``{% load %}``
    of the first then re-registered the name with its own. When an unrelated
    bridged filter goes missing, restoring it must leave that name alone:
    the loader's ``_still_bridged`` checks presence, not identity, so it
    would never correct a swap."""
    from django.template import Library, engines

    from djust import template_libraries as tl

    first, second = Library(), Library()
    first.filter("dup_3213", lambda value: "FIRST")
    second.filter("dup_3213", lambda value: "SECOND")
    engine = next(
        e.engine for e in engines.all() if hasattr(getattr(e, "engine", None), "template_libraries")
    )
    engine.template_libraries["dup_second_3213"] = second
    try:
        _fresh_bootstrap()
        assert "dup_3213" in tf._BRIDGED_FILTERS
        assert _rust.render_template("{{ x|dup_3213 }}", {"x": 1}) == "SECOND"

        tl._bridge_library("dup_first_3213", first)  # what {% load first %} does
        assert _rust.render_template("{{ x|dup_3213 }}", {"x": 1}) == "FIRST"

        _rust.unregister_custom_filter("field_value")
        tf._ensure_custom_filters_bridged()

        assert _rust.registry_entry_is_local("field_value", "filter")
        assert _rust.render_template("{{ x|dup_3213 }}", {"x": 1}) == "FIRST"
    finally:
        engine.template_libraries.pop("dup_second_3213", None)
        _rust.unregister_custom_filter("dup_3213")
        tl._filter_owner.pop("dup_3213", None)
        tl._loaded.pop("dup_first_3213", None)
        _fresh_bootstrap()


def test_a_filter_that_failed_to_register_is_not_retried_on_every_change():
    """#3213 review 3: a registration that raised is never recorded, so it
    cannot count as "missing" after every unrelated registry change."""
    real = tf.register_django_filter

    def refuse_field_value(name, *args, **kwargs):
        if name == "field_value":
            raise RuntimeError("boom")
        return real(name, *args, **kwargs)

    _rust.clear_custom_filters()
    try:
        with patch.object(tf, "register_django_filter", side_effect=refuse_field_value):
            _fresh_bootstrap()
        assert "field_value" not in tf._BRIDGED_FILTERS
        with _count_registrations() as register:
            for i in range(10):
                _rust.register_custom_filter("probe_3208_%d" % i, lambda v, a=None: v, False, False)
                tf._ensure_custom_filters_bridged()
        assert register.call_count == 0
    finally:
        for i in range(10):
            _rust.unregister_custom_filter("probe_3208_%d" % i)
        _fresh_bootstrap()
    assert _rust.registry_entry_is_local("field_value", "filter")


def test_a_failed_bootstrap_is_not_retried_on_every_render():
    try:
        with patch.object(tf, "bootstrap_django_filters", side_effect=RuntimeError("boom")) as boot:
            _fresh_bootstrap()
            tf._ensure_custom_filters_bridged()
        assert boot.call_count == 1
        assert tf._BRIDGED_FILTERS == {}
    finally:
        _fresh_bootstrap()  # leave the registry bridged
    assert _rust.registry_entry_is_local("field_value", "filter")


def test_a_clear_right_after_the_bootstrap_is_not_cached_as_intact():
    """#3213 review 2: the bootstrap must not record the generation it reads
    after its own work. A clear on another thread between its last
    registration check and that read would be cached as "intact" with the
    registry empty, and nothing would restore it until some unrelated
    registration moved the generation. Simulated deterministically: the clear
    runs right after the bootstrap's last probe."""
    real = tf._registered_globally
    probes = len(tf._library_filters())
    calls = {"n": 0}

    def probe_then_clear_after_last(name):
        result = real(name)
        calls["n"] += 1
        if calls["n"] == probes:
            _rust.clear_custom_filters()  # the concurrent clear
        return result

    try:
        with patch.object(tf, "_registered_globally", side_effect=probe_then_clear_after_last):
            _fresh_bootstrap()
        assert calls["n"] == probes
        assert "field_value" in tf._BRIDGED_FILTERS
        assert not _rust.registry_entry_is_local("field_value", "filter")
        tf._ensure_custom_filters_bridged()
        assert _rust.registry_entry_is_local("field_value", "filter")
    finally:
        _fresh_bootstrap()
