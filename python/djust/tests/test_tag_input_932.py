"""Regression test for bug #932 — TagInput rendered no `name=` attribute.

Before the fix: ``TagInput._render_custom`` produced an ``<input
type='text' class='tag-input-field' placeholder='...'>`` but omitted
``name=``. When the enclosing ``<form>`` submitted, the field's value was
silently dropped from the POST data — the server received no ``name`` key
for the tag list.

After the fix: a hidden ``<input type="hidden" name="<self.name>"
value="<csv of tags>">`` is emitted alongside the transient "type to add"
text input, so form submissions POST the current tag list under the
component's ``name``.
"""

import tests.conftest  # noqa: F401  -- configure Django settings

from djust.components.components.tag_input import TagInput


def test_tag_input_renders_name_attribute():
    """Bug #932: TagInput output must include ``name="<self.name>"``."""
    comp = TagInput(name="skills", tags=["python", "rust"])
    html = comp._render_custom()

    assert (
        'name="skills"' in html
    ), "TagInput output must carry a `name=` attribute so form POSTs include the field value"


def test_tag_input_hidden_input_carries_csv_of_tags():
    """The hidden input's value must be a csv of the current tag list."""
    comp = TagInput(name="topics", tags=["a", "b", "c"])
    html = comp._render_custom()

    assert 'type="hidden"' in html
    assert 'name="topics"' in html
    # The serialized value carries all three tags
    assert 'value="a,b,c"' in html


def test_tag_input_no_name_emits_no_hidden_input():
    """With an empty name we omit the hidden input entirely."""
    comp = TagInput(name="", tags=["x"])
    html = comp._render_custom()

    # No stray hidden input when the component has no name
    assert 'type="hidden"' not in html


def test_tag_input_hidden_input_value_is_html_escaped():
    """Tag values containing HTML-special chars must be escaped inside the hidden input."""
    comp = TagInput(name="tags", tags=['<script>alert("x")</script>'])
    html = comp._render_custom()

    assert "<script>" not in html.split('type="hidden"', 1)[-1].split(">", 1)[0] + ">"
    assert "&lt;script&gt;" in html
