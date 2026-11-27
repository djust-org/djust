"""A new view of a cached template does not re-parse its includes for the dj-model gate (#4363).

Every page GET and socket mount builds a new view, and the per-view memo of
"does this template record its dj-model bindings" was cold on each one: the
walk re-parsed the page and every literal ``{% include %}`` site, each running
the ``{% load %}`` hook (~0.2 ms for ``djust_components``). With render caches
on (``DEBUG`` off) the gate now reads the split ``writable_dj_model_fields``
stores, so a warm render runs no ``{% load %}`` hook. Also: an already-bridged
library's ``{% load %}`` answers from the registry generation, not 206 probes.
"""

from __future__ import annotations

import pytest

from djust import _render_caches, template_libraries
from djust._rust import RustLiveView, clear_dj_model_fields_cache, set_dj_model_gate_shared

pytestmark = pytest.mark.django_db

SITES = 6


@pytest.fixture
def shared():
    """Render caches on (``DEBUG`` off): the gate shares its walk."""
    set_dj_model_gate_shared(True)
    yield
    _render_caches._gate_shared[0] = None
    _render_caches.render_caches_enabled()


@pytest.fixture
def page(tmp_path):
    (tmp_path / "part.html").write_text(
        '{% load djust_components %}{% badge label="x" status="online" %}'
        '{% if on %}<input dj-model="name">{% endif %}'
    )
    clear_dj_model_fields_cache()
    yield "<div dj-root>" + '{% include "part.html" %}' * SITES + "</div>", [str(tmp_path)]
    clear_dj_model_fields_cache()


@pytest.fixture
def loads(monkeypatch):
    calls = []
    real = template_libraries._find_library

    def counting(name):
        calls.append(name)
        return real(name)

    monkeypatch.setattr(template_libraries, "_find_library", counting)
    return calls


def _request(source, dirs, *, cache):
    view = RustLiveView(source, dirs)
    view.update_state({"on": True})
    view.render()
    return view.writable_dj_model_fields(cache=cache)


def test_a_warm_view_runs_no_load_hook(shared, page, loads):
    source, dirs = page
    assert _request(source, dirs, cache=True) == ["name"]
    loads.clear()
    assert _request(source, dirs, cache=True) == ["name"]
    assert loads == []


def test_a_page_shell_view_shares_the_walk_without_reading_the_allowlist(shared, page, loads):
    """A GET's shell view renders and is dropped: its gate alone fills the cache."""
    source, dirs = page
    for _ in range(2):
        view = RustLiveView(source, dirs)
        view.update_state({"on": True})
        view.render()
        if _ == 0:
            loads.clear()
    assert loads == []


def test_without_render_caches_each_view_walks_again(page, loads):
    set_dj_model_gate_shared(False)
    try:
        source, dirs = page
        _request(source, dirs, cache=False)
        loads.clear()
        _request(source, dirs, cache=False)
    finally:
        _render_caches._gate_shared[0] = None
        _render_caches.render_caches_enabled()
    assert len(loads) >= SITES  # DEBUG on: templates may change, so the walk runs


def test_render_caches_enabled_tells_the_gate_once(monkeypatch):
    import djust._rust as rust

    calls = []
    monkeypatch.setattr(rust, "set_dj_model_gate_shared", calls.append)
    monkeypatch.setattr(_render_caches, "_gate_shared", [None])
    enabled = _render_caches.render_caches_enabled()
    _render_caches.render_caches_enabled()
    assert calls == [enabled]


def test_clearing_the_caches_walks_again(shared, page, loads):
    source, dirs = page
    _request(source, dirs, cache=True)
    clear_dj_model_fields_cache()
    loads.clear()
    _request(source, dirs, cache=True)
    assert len(loads) >= SITES


def test_an_already_bridged_library_is_not_probed_again(monkeypatch):
    template_libraries.load_libraries(["djust_components"])
    probes = []
    real = template_libraries._probe_still_bridged

    def counting(*args):
        probes.append(args[0])
        return real(*args)

    monkeypatch.setattr(template_libraries, "_probe_still_bridged", counting)
    for _ in range(5):
        template_libraries.load_libraries(["djust_components"])
    assert len(probes) <= 1


def test_a_registry_change_probes_again(monkeypatch):
    from djust._rust import register_tag_handler

    template_libraries.load_libraries(["djust_components"])
    probes = []
    real = template_libraries._probe_still_bridged

    def counting(*args):
        probes.append(args[0])
        return real(*args)

    monkeypatch.setattr(template_libraries, "_probe_still_bridged", counting)

    class _Tag:
        def render(self, args, context):
            return ""

    register_tag_handler("gate_4363_unrelated", _Tag())
    template_libraries.load_libraries(["djust_components"])
    assert probes  # the generation moved: the memo does not answer


def test_a_subset_load_is_not_probed_again(monkeypatch):
    template_libraries.load_libraries(["badge", "from", "djust_components"])
    probes = []
    real = template_libraries._probe_still_bridged

    def counting(*args):
        probes.append(args[0])
        return real(*args)

    monkeypatch.setattr(template_libraries, "_probe_still_bridged", counting)
    for _ in range(3):
        template_libraries.load_libraries(["badge", "from", "djust_components"])
        template_libraries.load_libraries(["djust_components"])
    assert len(probes) <= 2
