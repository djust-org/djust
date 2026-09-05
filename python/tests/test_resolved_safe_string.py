import pytest
from django.template import Context, Engine
from django.utils.safestring import mark_safe
from djust import _rust


class DynamicText:
    def __init__(self, safe):
        self._safe = safe

    def plain(self):
        return "<b>&</b>"

    def method(self):
        return mark_safe("<b>&</b>") if self._safe else "<b>&</b>"

    @property
    def property(self):
        return mark_safe("<b>&</b>") if self._safe else "<b>&</b>"


@pytest.mark.parametrize("safe", [False, True])
@pytest.mark.parametrize("path", ["p.method", "p.property"])
@pytest.mark.parametrize(
    "shape",
    [
        "{{ VALUE }}",
        "{{ VALUE|lower }}",
        "{{ VALUE|upper }}",
        "{{ VALUE|lower|add:p.plain }}",
        "{{ VALUE|lower|add:VALUE }}",
        "{{ missing|default:VALUE|lower }}",
        "{% with q=VALUE %}{{ q }}{% endwith %}",
        "{% with q=VALUE|lower %}{{ q }}{% endwith %}",
        "{% firstof VALUE|lower %}",
        "{% cycle VALUE|lower VALUE|lower %}",
    ],
)
def test_resolved_safe_string_keeps_its_marker(path, shape, safe):
    source = shape.replace("VALUE", path)
    context = {"p": DynamicText(safe)}
    expected = Engine().from_string(source).render(Context(context))
    assert _rust.render_template(source, context) == expected
