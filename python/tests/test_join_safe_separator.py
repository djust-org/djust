"""A join separator carries its own safety independently of the list items."""

import pytest
from django.template import Context, Engine
from django.utils.safestring import mark_safe

from djust.template import DjustTemplateBackend


@pytest.mark.parametrize("safe_separator", [False, True])
@pytest.mark.parametrize("autoescape", [False, True])
@pytest.mark.parametrize(
    "body",
    [
        "{{ items|join:separator }}",
        "{{ items|join:separator|lower }}",
        "{% with sep=separator %}{{ items|join:sep }}{% endwith %}",
        "{% with separator=unsafe %}{{ items|join:separator }}{% endwith %}",
        "{% for sep in separators %}{{ items|join:sep }}{% endfor %}",
        "{% with result=items|join:separator %}{{ result }}{% endwith %}",
        "{{ items|join:obj.separator }}",
    ],
)
def test_join_separator_safety_matches_django(safe_separator, autoescape, body):
    separator = mark_safe(" <b>&</b> ") if safe_separator else " <b>&</b> "
    values = {
        "items": ["Alpha", "Beta & me"],
        "separator": separator,
        "separators": [separator],
        "obj": {"separator": separator},
        "unsafe": "<script>unsafe</script>",
    }
    source = (
        "{% autoescape " + ("on" if autoescape else "off") + " %}" + body + "{% endautoescape %}"
    )
    expected = Engine().from_string(source).render(Context(values))
    backend = DjustTemplateBackend({"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})
    assert backend.from_string(source).render(values) == expected
