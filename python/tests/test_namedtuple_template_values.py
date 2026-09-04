"""Named tuples retain fields as well as ordinary tuple semantics in bindings."""

from collections import namedtuple
import pytest
from django.template import Context, Engine, Library
from djust.template import DjustTemplateBackend

register = Library()
Row = namedtuple("Row", "label items")


@register.simple_tag
def make_row():
    return Row("label", ["one", "two"])


@pytest.mark.parametrize(
    "body",
    [
        '{{ row.label }}:{{ row.items|join:"," }}',
        '{{ row.0 }}:{{ row.1|join:"," }}',
        "{{ row }}",
        "{{ row|length }}",
        '{{ row|slice:":1" }}',
        '{{ row|json_script:"row" }}',
        "{{ row|pprint }}",
        "{% for value in row %}{{ value }};{% endfor %}",
        '{% for key, values in rows %}{{ key }}:{{ values|join:"," }}{% endfor %}',
        "{% if row == equivalent %}equal{% endif %}",
    ],
)
def test_namedtuple_bindings_match_django(body):
    libraries = {"tuple_fixture": __name__}
    source = "{% load tuple_fixture %}{% make_row as row %}" + body
    values = {"rows": [make_row()], "equivalent": ("label", ["one", "two"])}
    expected = Engine(libraries=libraries).from_string(source).render(Context(values))
    backend = DjustTemplateBackend(
        {"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {"libraries": libraries}}
    )
    assert backend.from_string(source).render(values) == expected


def test_namedtuple_fields_survive_liveview_state_roundtrip():
    from djust._rust import RustLiveView

    source = '{{ row.label }}:{{ row.items|join:"," }}|{{ row }}'
    view = RustLiveView(source)
    view.set_state("row", make_row())
    restored = RustLiveView.deserialize_msgpack(view.serialize_msgpack())
    expected = Engine().from_string(source).render(Context({"row": make_row()}))
    assert restored.render() == expected
