"""Theme block arguments follow the installed Django tag-helper signature."""

import pytest
from django.template import TemplateSyntaxError, engines


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('{% theme_card_block title="Welcome" %}Body{% end_theme_card_block %}', "Welcome"),
        ('{% theme_card_block "Title" "Footer" %}Body{% end_theme_card_block %}', "Footer"),
        (
            '{% theme_alert_block variant="info" title="Warning" %}Body{% end_theme_alert_block %}',
            "Warning",
        ),
        ('{% theme_tooltip_block "Help" %}Body{% end_theme_tooltip_block %}', "Help"),
    ],
)
def test_theme_block_arguments_render_with_django(source, expected):
    template = engines["django"].from_string("{% load theme_components %}" + source)
    html = template.render({})
    assert expected in html
    assert "Body" in html


def test_theme_block_rejects_explicit_body():
    with pytest.raises(TemplateSyntaxError, match="cannot also be given"):
        engines["django"].from_string(
            '{% load theme_components %}{% theme_card_block body="duplicate" %}'
            "Body{% end_theme_card_block %}"
        )
