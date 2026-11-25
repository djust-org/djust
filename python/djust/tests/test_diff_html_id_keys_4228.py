"""``diff_html(..., id_keys=True)``: the opt-in ``id`` diff key (#4228).

HTML rendered without ``dj-if`` boundary markers gives the differ no anchor for
a conditional block, so a block that appears at the top is paired by position
with the old first sibling and every sibling after it is rewritten, wiping
typed form input. ``id_keys=True`` keys elements without ``dj-key`` by their
``id``. The default must stay byte-identical to the call without the keyword.
"""

import json

import pytest

import djust._rust as rust

FIELDS = (
    '<section id="a"><input name="x"></section>'
    '<section id="b"><textarea name="y"></textarea></section>'
    '<section id="c"><p>tail</p></section>'
)


def _page(banner):
    top = '<div id="banner">Saved</div>' if banner else ""
    return "<main>" + top + FIELDS + "</main>"


def _types(patches_json):
    return [p["type"] for p in json.loads(patches_json)]


def test_default_is_byte_identical_to_explicit_false():
    for old, new in [
        (_page(False), _page(True)),
        (_page(True), _page(False)),
        (
            '<ul><li id="1">a</li><li id="2">b</li></ul>',
            '<ul><li id="2">b</li><li id="1">a</li></ul>',
        ),
    ]:
        assert rust.diff_html(old, new) == rust.diff_html(old, new, id_keys=False)


def test_default_rewrites_siblings_after_an_unmarked_conditional():
    assert "Replace" in _types(rust.diff_html(_page(False), _page(True)))


def test_opt_in_inserts_the_conditional_alone():
    assert _types(rust.diff_html(_page(False), _page(True), id_keys=True)) == ["InsertChild"]
    assert _types(rust.diff_html(_page(True), _page(False), id_keys=True)) == ["RemoveChild"]


def test_id_keys_is_keyword_only():
    with pytest.raises(TypeError):
        rust.diff_html(_page(False), _page(True), True)
