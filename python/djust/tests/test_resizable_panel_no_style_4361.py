"""``{% resizable_panel %}`` can leave its size to the stylesheet, and writes no style under csp_strict (#4361)."""

from django.template import engines

from djust.components.components.resizable_panel import ResizablePanel


def _tag(args=""):
    return (
        engines["django"]
        .from_string(
            "{% load djust_components %}{% resizable_panel " + args + " %}x{% endresizable_panel %}"
        )
        .render({})
    )


def test_default_size_is_unchanged():
    assert 'style="width:50%;min-width:100px"' in _tag()
    assert 'style="width:50%;min-width:100px"' in ResizablePanel(content="x").render()


def test_empty_sizes_write_no_style_attribute():
    for html in (
        _tag('initial_size="" min_size=""'),
        ResizablePanel(content="x", initial_size="", min_size="").render(),
    ):
        assert "style=" not in html
        assert 'data-min-size="none"' in html
        assert 'dj-hook="ResizablePanel"' in html


def test_empty_initial_size_keeps_the_minimum():
    html = _tag('initial_size=""')
    assert 'style="min-width:100px"' in html


def test_csp_strict_writes_the_size_as_dj_style_attributes():
    from djust.config import config

    before = config.get("csp_strict", False)
    config.set("csp_strict", True)
    try:
        html = _tag()
    finally:
        config.set("csp_strict", before)
    assert ' style="' not in html
    assert 'data-dj-style-width="50%"' in html


def test_a_none_size_keeps_the_defaults():
    html = ResizablePanel(content="x", initial_size=None, min_size=None).render()
    assert "width:50%" in html and "min-width:100px" in html
