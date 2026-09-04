from datetime import date
from django.template import Engine, Context
from djust.template import DjustTemplateBackend
import pytest


@pytest.mark.parametrize(
    "source,values",
    [
        (
            '{% regroup data by at|date:"m" as grouped %}{% for group in grouped %}{{ group.grouper }}:{% for item in group.list %}{{ item.at|date:"d" }}{% endfor %},{% endfor %}',
            {
                "data": [
                    {"at": date(2012, 2, 14)},
                    {"at": date(2012, 2, 28)},
                    {"at": date(2012, 7, 4)},
                ]
            },
        ),
        (
            '{% regroup data by bar|join:"" as grouped %}{% for group in grouped %}{{ group.grouper }}:{% for item in group.list %}{{ item.foo|first }}{% endfor %},{% endfor %}',
            {
                "data": [
                    {"foo": "x", "bar": ["ab", "c"]},
                    {"foo": "y", "bar": ["a", "bc"]},
                    {"foo": "z", "bar": ["a", "d"]},
                ]
            },
        ),
    ],
)
def test_regroup(source, values):
    expected = Engine().from_string(source).render(Context(values))
    backend = DjustTemplateBackend({"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})
    assert backend.from_string(source).render(values) == expected
