"""``DJUST_UNSUPPLIED_NAMES`` makes an unsupplied template name loud under test (#4279).

An explicit view that sets ``self.count`` in ``mount()`` and reads ``{{ count }}`` renders
it empty. The notice that says so ran under ``DEBUG`` only, which a test run never has on.
``"warn"`` logs whatever ``DEBUG`` is; ``"error"`` raises on every such render.
"""

from __future__ import annotations

import logging
import warnings
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured

from djust import LiveView, state
from djust import _exposure_unsupplied
from djust._exposure_unsupplied import UnsuppliedTemplateNameError
from djust.testing import LiveViewTestClient

LOGGER = "djust.exposure"
SENTINEL = "VALUE_SENTINEL_4279"

pytestmark = pytest.mark.django_db


class Holds(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>[{{ held_name }}]</div>"

    def mount(self, request, **kwargs):
        self.held_name = SENTINEL


class HoldsList(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>{{ items_count }}</div>"

    def get_context_data(self, **kwargs: Any) -> dict:
        return {**super().get_context_data(**kwargs), "items": [1, 2]}


class Supplies(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>{{ shown }}</div>"
    shown = state("ok")

    def mount(self, request, **kwargs):
        self.other = 1


with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)

    class Legacy(LiveView):
        exposure_policy = "legacy"
        template = "<div dj-root>[{{ held_name }}]</div>"

        def mount(self, request, **kwargs):
            self.held_name = "x"


TAB_PARTIAL = "{{ panel_name }}"


class InTabInclude(LiveView):
    exposure_policy = "explicit"
    template = (
        "{% load djust_components %}<div dj-root>"
        '{% tabs id="t" %}{% tab id="a" label="A" %}{% include "t4279/_panel.html" %}{% endtab %}'
        "{% endtabs %}</div>"
    )

    def mount(self, request, **kwargs):
        self.panel_name = SENTINEL


@pytest.fixture(autouse=True)
def fresh_reports():
    saved = dict(_exposure_unsupplied._REPORTED)
    _exposure_unsupplied._REPORTED.clear()
    yield
    _exposure_unsupplied._REPORTED.clear()
    _exposure_unsupplied._REPORTED.update(saved)


def _render(view_class):
    client = LiveViewTestClient(view_class)
    client.mount()
    return client, client.render()


def _records(caplog, view_class):
    return [
        r for r in caplog.records if r.name == LOGGER and view_class.__qualname__ in r.getMessage()
    ]


def test_default_mode_is_silent_with_debug_off(settings, caplog):
    settings.DEBUG = False
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        _render(Holds)
    assert [r for r in caplog.records if r.name == LOGGER] == []


def test_warn_mode_logs_with_debug_off_once_per_class_and_name(settings, caplog):
    settings.DEBUG = False
    settings.DJUST_UNSUPPLIED_NAMES = "warn"
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        client, _html = _render(Holds)
        client.render()
    records = _records(caplog, Holds)
    assert len(records) == 1
    assert "{{ held_name }}" in records[0].getMessage()
    assert SENTINEL not in records[0].getMessage()


def test_error_mode_raises_naming_the_names_and_no_values(settings):
    settings.DJUST_UNSUPPLIED_NAMES = "error"
    with pytest.raises(UnsuppliedTemplateNameError) as excinfo:
        _render(Holds)
    message = str(excinfo.value)
    assert "held_name" in message and "Holds" in message
    assert "state()" in message and "DJUST_UNSUPPLIED_NAMES" in message
    assert SENTINEL not in message


def test_error_mode_raises_on_every_render(settings):
    settings.DJUST_UNSUPPLIED_NAMES = "error"
    client = LiveViewTestClient(Holds)
    for _ in range(2):
        with pytest.raises(UnsuppliedTemplateNameError):
            client.mount()
            client.render()


def test_error_mode_reports_count_names(settings):
    settings.DJUST_UNSUPPLIED_NAMES = "error"
    with pytest.raises(UnsuppliedTemplateNameError) as excinfo:
        _render(HoldsList)
    message = str(excinfo.value)
    assert "items_count" in message and "items|length" in message


def test_error_mode_is_quiet_for_a_supplying_view_and_a_legacy_view(settings):
    settings.DJUST_UNSUPPLIED_NAMES = "error"
    assert "ok" in _render(Supplies)[1]
    assert "[x]" in _render(Legacy)[1]


def test_invalid_mode_raises_improperly_configured(settings):
    settings.DJUST_UNSUPPLIED_NAMES = "erorr"
    with pytest.raises(ImproperlyConfigured) as excinfo:
        _render(Holds)
    assert "DJUST_UNSUPPLIED_NAMES" in str(excinfo.value) and '"error"' in str(excinfo.value)


def test_error_mode_sees_a_name_in_a_tab_include(settings, tmp_path):
    from djust.utils import clear_template_dirs_cache

    (tmp_path / "t4279").mkdir()
    (tmp_path / "t4279" / "_panel.html").write_text(TAB_PARTIAL)
    templates = [dict(t) for t in settings.TEMPLATES]
    templates[0] = {**templates[0], "DIRS": [str(tmp_path), *templates[0].get("DIRS", [])]}
    settings.TEMPLATES = templates
    clear_template_dirs_cache()
    settings.DJUST_UNSUPPLIED_NAMES = "error"
    try:
        with pytest.raises(UnsuppliedTemplateNameError) as excinfo:
            _render(InTabInclude)
    finally:
        clear_template_dirs_cache()
    assert "panel_name" in str(excinfo.value)
