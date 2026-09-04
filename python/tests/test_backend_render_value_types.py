"""The backend's native render boundary retains values rather than JSON spellings."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from django.template import Context, Engine
from django.utils.translation import gettext_lazy

from djust.template import DjustTemplateBackend
from djust.template.serialization import serialize_context


@pytest.mark.parametrize(
    "source,context",
    [
        ("{{ a|add:b }}", {"a": (1, 2), "b": (3,)}),
        ("{{ obj.items|add:tail }}", {"obj": {"items": (1, 2)}, "tail": (3,)}),
        ("{{ a|add:b }}", {"a": "word", "b": gettext_lazy("lazy")}),
        ("{{ a|add:b }}", {"a": gettext_lazy("word"), "b": gettext_lazy("lazy")}),
        ("{{ value|add:1 }}", {"value": Decimal("9007199254740993")}),
        ("{{ value|pprint }}", {"value": Decimal("1.000000000000000001")}),
        ('{{ value|date:"Y-m-d H:i:s.u" }}', {"value": datetime(2024, 6, 15, 14, 30, 45, 123456)}),
        ('{{ value|date:"Y-m-d" }}', {"value": date(2024, 6, 15)}),
        ('{{ value|time:"H:i:s.u" }}', {"value": time(14, 30, 45, 123456)}),
        ("{{ value|length }}|{{ value|pprint }}", {"value": (1, "<x>")}),
    ],
)
def test_backend_preserves_django_value_semantics(source, context):
    expected = Engine().from_string(source).render(Context(context))
    backend = DjustTemplateBackend({"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})
    assert backend.from_string(source).render(context) == expected


def test_render_preparation_retains_nested_types_without_mutating_context():
    values = [
        date(2024, 1, 1),
        datetime(2024, 1, 1),
        time(12),
        timedelta(days=2),
        Decimal("1.23"),
        UUID(int=1),
    ]
    original = {"rows": [{"values": tuple(values), "lazy": gettext_lazy("text")}]}
    prepared = serialize_context(original, for_render=True)
    assert prepared is not original
    assert isinstance(prepared["rows"][0]["values"], tuple)
    assert all(a is b for a, b in zip(prepared["rows"][0]["values"], values))
    assert prepared["rows"][0]["lazy"] == "text"
    assert not isinstance(original["rows"][0]["lazy"], str)
