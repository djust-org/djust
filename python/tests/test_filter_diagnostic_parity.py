"""Filter names and refusal precedence follow Django's match-by-match parser."""

import pytest
from django.template import Engine, TemplateSyntaxError
from djust.template import DjustTemplateBackend


@pytest.mark.parametrize(
    "expression",
    [
        "value|does_not_exist",
        "value|fil(ter)",
        "value|upper(ter)",
        'value|cut(ter):"x"',
        'value|nosuch:"a":"b"',
        "value|nosuch:_private",
        'value|upper:"a"|cut:_private',
        'value|cut:"a":"b"',
        "value|nosuch:",
    ],
)
def test_filter_errors_match_django(expression):
    source = "{{ " + expression + " }}"
    with pytest.raises(TemplateSyntaxError) as expected:
        Engine().from_string(source)
    backend = DjustTemplateBackend({"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})
    with pytest.raises(TemplateSyntaxError) as actual:
        backend.from_string(source)
    assert str(expected.value) in str(actual.value)
