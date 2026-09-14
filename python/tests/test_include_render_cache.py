"""Filesystem include selections expire at every render boundary."""

import os

import pytest
from django.template import Context, Engine

from djust import _rust


def test_include_reload_and_search_order_with_retained_view(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    card = second / "card.html"
    card.write_text("{{ item }}:old;")
    source = '{% for item in items %}{% include "card.html" %}{% endfor %}'
    view = _rust.RustLiveView(source)
    view.set_template_dirs([str(first), str(second)])
    view.set_state("items", ["<a>", "é", "&"])

    def check():
        engine = Engine(dirs=[str(first), str(second)])
        expected = engine.from_string(source).render(Context({"items": ["<a>", "é", "&"]}))
        assert view.render() == expected

    check()
    before = card.stat().st_mtime_ns
    card.write_text("new:{{ item }};")
    os.utime(card, ns=(before + 2_000_000_000, before + 2_000_000_000))
    check()
    (first / "card.html").write_text("first:{{ item }};")
    check()
    (first / "card.html").unlink()
    check()


def test_failed_render_does_not_retain_include_selection(tmp_path):
    card = tmp_path / "card.html"
    card.write_text("old")
    view = _rust.RustLiveView('{% include "card.html" %}{% include "missing.html" %}')
    view.set_template_dirs([str(tmp_path)])
    with pytest.raises(Exception, match="missing.html"):
        view.render()
    card.write_text("updated")
    (tmp_path / "missing.html").write_text("ok")
    assert view.render() == "updatedok"
