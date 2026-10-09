"""The theme editor's inline JSON must not break out of its ``<script>``.

``editor_view`` serializes every registered preset and design system and the
editor template emits both blobs inside an inline ``<script>`` with ``|safe``.
``json.dumps`` leaves ``<``, ``>`` and ``&`` raw, so a registered value holding
``</script>`` closed the element and the rest of the string ran as markup.
"""

# Import conftest first to configure Django settings before we add ours.
import tests.conftest  # noqa: F401

import json
import re

import pytest

from django.test import RequestFactory, override_settings

from djust.theming.gallery import views as gallery_views

pytestmark = pytest.mark.theming

_PAYLOAD = "</script><script>alert(1)</script><!--<script>&amp;  "

_PRESETS = {
    "evil": {
        "display_name": _PAYLOAD,
        "light": {"primary": {"h": 1, "s": 2, "l": 3}},
        "radius": 0.5,
    }
}
_DESIGN_SYSTEMS = {
    "evil_ds": {
        "name": "evil_ds",
        "display_name": _PAYLOAD,
        "typography": {"heading_font": _PAYLOAD},
    }
}

_DATA_BLOCK = re.compile(
    r"<script>\s*var PRESET_DATA = (?P<presets>.*?);\s*"
    r"var DESIGN_SYSTEMS = (?P<ds>.*?);\s*</script>",
    re.S,
)


@pytest.fixture
def rendered(monkeypatch):
    monkeypatch.setattr(gallery_views, "serialize_all_presets", lambda: _PRESETS)
    monkeypatch.setattr(gallery_views, "serialize_all_design_systems", lambda: _DESIGN_SYSTEMS)
    request = RequestFactory().get("/theming/themes/editor/")
    request.session = {}
    with override_settings(DEBUG=True, ROOT_URLCONF="tests.gallery_test_urls"):
        response = gallery_views.editor_view(request)
    assert response.status_code == 200
    return response.content.decode()


def test_payload_cannot_close_the_data_script(rendered):
    assert "<script>alert(1)" not in rendered
    match = _DATA_BLOCK.search(rendered)
    assert match is not None, "data <script> block was split by the payload"
    for blob in (match["presets"], match["ds"]):
        # Nothing the HTML tokenizer reacts to inside script data.
        assert "<" not in blob
        assert ">" not in blob
        assert "&" not in blob
        assert " " not in blob and " " not in blob


def test_escaped_json_parses_back_to_the_original_data(rendered):
    match = _DATA_BLOCK.search(rendered)
    assert match is not None
    # \uXXXX escapes are valid JSON (and JS) string escapes, so the browser sees
    # exactly the registered values.
    assert json.loads(match["presets"]) == _PRESETS
    assert json.loads(match["ds"]) == _DESIGN_SYSTEMS


@override_settings(DEBUG=True, ROOT_URLCONF="tests.gallery_test_urls")
def test_real_registries_still_round_trip():
    from djust.theming.gallery.context import (
        serialize_all_design_systems,
        serialize_all_presets,
    )

    request = RequestFactory().get("/theming/themes/editor/")
    request.session = {}
    content = gallery_views.editor_view(request).content.decode()
    match = _DATA_BLOCK.search(content)
    assert match is not None
    assert json.loads(match["presets"]) == serialize_all_presets()
    assert json.loads(match["ds"]) == serialize_all_design_systems()
