"""Engine-detected runtime failures use Django's public exception classes."""

import pytest
from django.template import Context, Engine, TemplateSyntaxError

from djust import _rust
from djust.template import DjustTemplateBackend


@pytest.mark.parametrize(
    "source,context,error_type",
    [
        ("{% for a,b in rows %}{{ a }}{{ b }}{% endfor %}", {"rows": [[1, 2, 3]]}, ValueError),
        ("{% for a,b in rows %}{{ a }}{{ b }}{% endfor %}", {"rows": [[1]]}, ValueError),
        ("{% for a,b in rows %}{{ a }}{{ b }}{% endfor %}", {"rows": [None]}, ValueError),
        ("{% widthratio a b c %}", {"a": 1, "b": 2, "c": "bad"}, TemplateSyntaxError),
        (
            "{% widthratio a b c as result %}{{ result }}",
            {"a": 1, "b": 2, "c": None},
            TemplateSyntaxError,
        ),
    ],
)
def test_runtime_error_class_and_message(source, context, error_type):
    django = Engine().from_string(source)
    backend = DjustTemplateBackend({"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})
    template = backend.from_string(source)
    with pytest.raises(error_type) as reference:
        django.render(Context(context))
    for render in [
        lambda: template.render(context),
        lambda: _rust.render_template(source, context),
    ]:
        with pytest.raises(error_type) as actual:
            render()
        assert type(actual.value) is type(reference.value)
        assert str(actual.value) == str(reference.value)
