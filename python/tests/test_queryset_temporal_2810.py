"""Queryset values must retain the temporal types consumed by Django filters."""

from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest
from django.db import models
from django.template import Context, Engine

from djust._rust import serialize_queryset
from djust.optimization.codegen import compile_serializer, generate_serializer_code


class TemporalEntry(models.Model):
    created_at = models.DateTimeField(null=True)

    class Meta:
        app_label = "queryset_temporal_2810"


def test_queryset_utc_filter_matches_model_serializer():
    entry = TemporalEntry(
        created_at=datetime(2024, 1, 2, 14, 34, tzinfo=timezone(timedelta(hours=2)))
    )
    serializer = compile_serializer(
        generate_serializer_code("TemporalEntry", ["created_at"], "serialize_entry"),
        "serialize_entry",
    )
    template = Engine(libraries={"tz": "django.templatetags.tz"}).from_string(
        '{% load tz %}{{ entry.created_at|utc|date:"M d, Y · H:i" }} UTC'
    )
    values = [serialize_queryset([entry], ["created_at"])[0], serializer(entry)]
    assert [template.render(Context({"entry": value})) for value in values] == [
        "Jan 02, 2024 · 12:34 UTC",
        "Jan 02, 2024 · 12:34 UTC",
    ]


@pytest.mark.parametrize(
    "value",
    [
        datetime(2024, 1, 2, 12, 34),
        datetime(2024, 1, 2, 12, 34, tzinfo=timezone.utc),
        date(2024, 1, 2),
        time(12, 34),
        timedelta(days=2),
        None,
    ],
)
def test_temporal_values_survive_all_queryset_paths(value):
    entry = SimpleNamespace(
        direct=value,
        nested=SimpleNamespace(value=value),
        children=[SimpleNamespace(value=value)],
        payload={"values": [value]},
        method=lambda: value,
    )
    result = serialize_queryset(
        [entry], ["direct", "nested.value", "children.0.value", "payload", "method"]
    )[0]
    assert result["direct"] is value
    assert result["nested"]["value"] is value
    assert result["children"][0]["value"] is value
    assert result["payload"]["values"][0] is value
    assert result["method"] is value
