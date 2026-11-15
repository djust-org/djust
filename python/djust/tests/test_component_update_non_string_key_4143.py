"""``Component.update`` skips a ``__dict__`` key that is not a string (#4143 follow-up).

#4143 made every walk over a view's ``__dict__`` skip a key that is not a ``str``: such a key
is not an attribute name, and ``key.startswith`` raised on it. ``Component.update`` walks the
component's own ``__dict__`` to rebuild its Rust instance and still called ``startswith`` on
every key. It now skips a non-string key the same way.
"""

from __future__ import annotations

from djust.components.ui.divider_simple import Divider


def _divider_with_a_non_string_key() -> Divider:
    divider = Divider(text="before")
    vars(divider)[1] = "one"  # not an attribute name
    return divider


def test_update_with_a_non_string_key_in_dict_rebuilds_the_component():
    divider = _divider_with_a_non_string_key()
    assert divider._rust_impl_class is not None  # the path that walks __dict__
    assert divider.update(text="after") is divider
    html = str(divider.render())
    assert "after" in html and "before" not in html, html


def test_the_non_string_key_is_left_where_it_was():
    divider = _divider_with_a_non_string_key()
    divider.update(text="after")
    assert vars(divider)[1] == "one"


def test_the_control_updates_the_same_way():
    divider = Divider(text="before")
    divider.update(text="after")
    assert "after" in str(divider.render())
