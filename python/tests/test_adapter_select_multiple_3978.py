"""The form adapters render a ``SelectMultiple`` field as a multiple select (#3978).

``as_live_field`` / ``{% live_field %}`` drew a ``MultipleChoiceField`` with the
default widget as a single ``<select>``, so a list value could show one item at
most and the browser submitted one.
"""

import re

import pytest
from django import forms

from djust.frameworks import get_adapter

CHOICES = [("x", "X"), ("y", "Y"), ("z", "Z")]


class TagsForm(forms.Form):
    tags = forms.MultipleChoiceField(choices=CHOICES, required=False)
    pick = forms.ChoiceField(choices=CHOICES, required=False)


@pytest.mark.parametrize("framework", ["plain", "bootstrap4", "bootstrap5", "tailwind"])
def test_a_select_multiple_field_is_a_multiple_select_with_each_value_selected(framework):
    field = TagsForm().fields["tags"]
    html = get_adapter(framework).render_field(field, "tags", ["x", "z"], [])
    select = re.search(r"<select[^>]*>", html).group(0)
    assert 'multiple="multiple"' in select
    assert re.findall(r'<option value="(\w*)" selected>', html) == ["x", "z"]
    assert 'value=""' not in html  # no blank option: selecting nothing already says "none"


@pytest.mark.parametrize("framework", ["plain", "bootstrap5"])
def test_a_single_select_is_unchanged(framework):
    field = TagsForm().fields["pick"]
    html = get_adapter(framework).render_field(field, "pick", "y", [])
    assert "multiple" not in re.search(r"<select[^>]*>", html).group(0)
    assert '<option value="">---------</option>' in html
