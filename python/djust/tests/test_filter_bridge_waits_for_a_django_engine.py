"""The filter bridge must not call itself done before a Django engine exists.

``_ensure_custom_filters_bridged`` is a one-shot: the first render bridges
every Django library filter (``live_tags``' ``field_value`` included) to the
Rust registry and sets ``_CUSTOM_FILTERS_BRIDGED``. When that first render ran
with no Django template engine configured (``TEMPLATES`` holding only the
djust backend, as ``tests/integration/test_dj_root_in_script_comment_2663.py``
does), there were no libraries to walk: the bootstrap registered nothing,
recorded nothing, and still set the flag. Nothing could ever restore a name it
had not recorded, so every later root-LiveView render in the process failed
with ``Invalid filter: 'field_value'``.

That is what turned ``main`` red on ``python-tests (shard 1/4)``: pytest-split's
shard composition put the 2663 test first on an xdist worker, ahead of
``test_filter_bridge_global_registry_3208.py``. The bridge is process state, so
the failure depended on which test happened to render first, not on either
test being wrong.
"""

from __future__ import annotations

import pytest
from django.template import loader
from django.test import override_settings

from djust import _rust
from djust import template_filters as tf

_DJUST_ONLY = [
    {
        "BACKEND": "djust.template.backend.DjustTemplateBackend",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {},
    }
]


@pytest.fixture
def fresh_worker():
    """A worker that has never bridged, restored to a bridged one afterwards."""
    _rust.clear_custom_filters()
    tf._CUSTOM_FILTERS_BRIDGED = False
    tf._BRIDGED_FILTERS = {}
    tf._BRIDGED_AT_GENERATION = None
    yield
    loader.engines._engines = {}
    tf._CUSTOM_FILTERS_BRIDGED = False
    tf._ensure_custom_filters_bridged()


def test_a_first_render_with_no_django_engine_does_not_end_the_bridge(fresh_worker):
    with override_settings(TEMPLATES=_DJUST_ONLY):
        loader.engines._engines = {}
        assert not list(tf._iter_django_libraries()), "the premise: no Django engine"
        tf._ensure_custom_filters_bridged()
    loader.engines._engines = {}  # the settings are back: the Django engine returns

    tf._ensure_custom_filters_bridged()

    assert _rust.registry_entry_is_local("field_value", "filter"), (
        "the engine-less first call set the one-shot flag with nothing bridged"
    )
    assert "field_value" in tf._BRIDGED_FILTERS
