"""JSX-style ``<Rust…/>`` template tags warn that 2.0.0 removes them."""

import warnings

import pytest

from djust._rust import render_template

TAG = '<RustButton id="b" label="Go" />'


def test_a_rust_component_tag_warns_and_still_renders():
    with pytest.warns(DeprecationWarning) as record:
        html = render_template(TAG, {})
    assert "<button" in html, html
    messages = [str(w.message) for w in record if issubclass(w.category, DeprecationWarning)]
    assert any(
        "`<RustButton />` template tag is deprecated since djust 1.3" in m
        and "no earlier than 2.0.0" in m
        and "djust.components.ui" in m
        for m in messages
    ), messages


def test_warnings_as_errors_stop_the_render():
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        with pytest.raises(DeprecationWarning):
            render_template(TAG, {})


def test_an_ordinary_template_does_not_warn():
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        assert render_template("<b>{{ x }}</b>", {"x": 1}) == "<b>1</b>"
