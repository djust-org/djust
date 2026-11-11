"""Django fetch modes through djust's queryset serializer and Rust renderer.

Run timing measurements with pytest-benchmark in a Django 6.1 environment;
no latency thresholds are asserted. SQL counts include queryset evaluation and
JIT serialization, while table and fixture construction are outside the run.
"""

from __future__ import annotations

import django
import pytest
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.db import connection, models
from django.test.utils import CaptureQueriesContext

from djust import LiveView
from djust._rust import render_template


ROWS = 50
PROPERTY_TEMPLATE = "{% for row in rows %}[{{ row.fetch_peers_label }}]{% endfor %}"
DIRECT_TEMPLATE = "{% for row in rows %}[{{ row.content_type.model }}]{% endfor %}"
CONDITIONAL_TEMPLATE = "{% for row in rows %}[{{ row.fetch_peers_conditional }}]{% endfor %}"
HAS_FETCH_PEERS = django.VERSION >= (6, 1) and hasattr(models, "FETCH_PEERS")
PEER_PARAM = pytest.param(
    "peers",
    marks=pytest.mark.skipif(not HAS_FETCH_PEERS, reason="Django 6.1 fetch modes required"),
)


@pytest.fixture
def permissions(monkeypatch, db):
    """Use distinct related objects to expose the cost of speculative fetching."""
    types = ContentType.objects.bulk_create(
        [ContentType(app_label="djust_peers_bench", model=f"type{i}") for i in range(ROWS)]
    )
    entries = Permission.objects.bulk_create(
        [
            Permission(
                name=f"Fetch peer {i}", codename=f"djust_fetch_peers_{i}", content_type=types[i]
            )
            for i in range(ROWS)
        ]
    )
    monkeypatch.setattr(
        Permission,
        "fetch_peers_label",
        property(lambda permission: permission.content_type.model),
        raising=False,
    )
    monkeypatch.setattr(
        Permission,
        "fetch_peers_conditional",
        property(
            lambda permission: (
                permission.content_type.model
                if permission.codename == "djust_fetch_peers_0"
                else "skipped"
            )
        ),
        raising=False,
    )
    return [entry.pk for entry in entries]


def render_rows(ids, mode, template=PROPERTY_TEMPLATE):
    """Render a fresh queryset through the real JIT and Rust paths."""
    queryset = Permission.objects.filter(pk__in=ids).order_by("pk")
    if mode == "peers":
        queryset = queryset.fetch_mode(models.FETCH_PEERS)
    elif mode == "select":
        queryset = queryset.select_related("content_type")
    serialized = LiveView()._jit_serialize_queryset(queryset, template, "rows")
    return render_template(template, {"rows": serialized})


def expected_html(count):
    return "".join(f"[type{i}]" for i in range(count))


@pytest.mark.django_db
@pytest.mark.parametrize(
    "mode,max_queries",
    [
        ("default", ROWS + 1),
        pytest.param(
            "peers",
            2,
            marks=pytest.mark.skipif(not HAS_FETCH_PEERS, reason="Django 6.1 fetch modes required"),
        ),
        ("select", 1),
    ],
)
def test_dynamic_property_modes_preserve_output_and_bound_queries(permissions, mode, max_queries):
    with CaptureQueriesContext(connection) as queries:
        html = render_rows(permissions, mode)
    assert html == expected_html(ROWS)
    assert len(queries) <= max_queries


@pytest.mark.django_db
@pytest.mark.parametrize("mode", ["default", PEER_PARAM])
def test_known_template_relation_is_already_joined(permissions, mode):
    with CaptureQueriesContext(connection) as queries:
        html = render_rows(permissions, mode, DIRECT_TEMPLATE)
    assert html == expected_html(ROWS)
    assert len(queries) == 1


@pytest.mark.django_db
@pytest.mark.skipif(not HAS_FETCH_PEERS, reason="Django 6.1 fetch modes required")
def test_fetch_mode_survives_queryset_slice_and_jit_optimization(permissions):
    queryset = (
        Permission.objects.filter(pk__in=permissions)
        .order_by("pk")
        .fetch_mode(models.FETCH_PEERS)[:17]
    )
    with CaptureQueriesContext(connection) as queries:
        serialized = LiveView()._jit_serialize_queryset(queryset, PROPERTY_TEMPLATE, "rows")
        html = render_template(PROPERTY_TEMPLATE, {"rows": serialized})
    assert html == expected_html(17)
    assert len(queries) <= 2


@pytest.mark.django_db
@pytest.mark.parametrize("mode", ["default", PEER_PARAM, "select"])
def test_benchmark_dynamic_property_render(benchmark, permissions, mode):
    result = benchmark(render_rows, permissions, mode)
    assert result == expected_html(ROWS)


@pytest.mark.django_db
@pytest.mark.parametrize("mode", ["default", PEER_PARAM])
def test_conditional_property_does_not_reduce_query_count(permissions, mode):
    # Only the first row needs its related object. Peer fetching still makes
    # a batch query; it cannot improve on a single on-demand query here.
    with CaptureQueriesContext(connection) as queries:
        html = render_rows(permissions, mode, CONDITIONAL_TEMPLATE)
    assert html == "[type0]" + "[skipped]" * (ROWS - 1)
    assert len(queries) <= 2


@pytest.mark.django_db
@pytest.mark.parametrize("mode", ["default", PEER_PARAM])
def test_benchmark_conditional_property_render(benchmark, permissions, mode):
    result = benchmark(render_rows, permissions, mode, CONDITIONAL_TEMPLATE)
    assert result == "[type0]" + "[skipped]" * (ROWS - 1)
