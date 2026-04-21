"""Tests for djust.api.openapi (ADR-008 OpenAPI 3.1 schema generator)."""

from __future__ import annotations

import json

import pytest

from djust.api.openapi import _map_param_type, build_schema
from djust.api.registry import register_api_view, reset_registry
from djust.decorators import event_handler
from djust.live_view import LiveView


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    yield
    reset_registry()


def test_type_mapping_primitives():
    assert _map_param_type("int")["type"] == "integer"
    assert _map_param_type("float")["type"] == "number"
    assert _map_param_type("bool")["type"] == "boolean"
    assert _map_param_type("str")["type"] == "string"


def test_type_mapping_uuid_and_decimal():
    assert _map_param_type("UUID") == {"type": "string", "format": "uuid"}
    assert _map_param_type("Decimal") == {"type": "string", "format": "decimal"}


def test_type_mapping_list_items():
    schema = _map_param_type("list[int]")
    assert schema == {"type": "array", "items": {"type": "integer"}}


def test_type_mapping_optional_is_nullable():
    schema = _map_param_type("Optional[str]")
    assert schema["nullable"] is True
    assert schema["type"] == "string"


def test_type_mapping_unknown_falls_back_to_string():
    assert _map_param_type("SomeCustomType")["type"] == "string"
    assert _map_param_type("Any") == {}


def test_build_schema_is_openapi_3_1():
    class V(LiveView):
        api_name = "openapi.simple"

        @event_handler(expose_api=True)
        def ping(self, **kwargs):
            """Return pong."""
            return "pong"

    register_api_view("openapi.simple", V)
    schema = build_schema()
    assert schema["openapi"] == "3.1.0"
    assert schema["info"]["title"] == "djust API"
    assert "/djust/api/openapi.simple/ping/" in schema["paths"]


def test_schema_excludes_non_exposed_handlers():
    class V(LiveView):
        api_name = "openapi.mixed"

        @event_handler(expose_api=True)
        def public(self, **kwargs):
            return "p"

        @event_handler  # NOT exposed
        def private(self, **kwargs):
            return "q"

    register_api_view("openapi.mixed", V)
    schema = build_schema()
    paths = list(schema["paths"].keys())
    assert any("public" in p for p in paths)
    assert not any("private" in p for p in paths)


def test_schema_request_body_has_types_and_required():
    class V(LiveView):
        api_name = "openapi.params"

        @event_handler(expose_api=True)
        def update(self, item_id: int, quantity: int = 1, **kwargs):
            """Update item quantity."""
            return None

    register_api_view("openapi.params", V)
    schema = build_schema()
    op = schema["paths"]["/djust/api/openapi.params/update/"]["post"]
    body_schema = op["requestBody"]["content"]["application/json"]["schema"]
    props = body_schema["properties"]
    assert props["item_id"]["type"] == "integer"
    assert props["quantity"]["type"] == "integer"
    # required is only the params without a default, not kwargs.
    assert body_schema["required"] == ["item_id"]


def test_schema_responses_include_error_envelope():
    class V(LiveView):
        api_name = "openapi.errors"

        @event_handler(expose_api=True)
        def go(self, **kwargs):
            return "x"

    register_api_view("openapi.errors", V)
    schema = build_schema()
    op = schema["paths"]["/djust/api/openapi.errors/go/"]["post"]
    for code in ("200", "400", "401", "403", "404", "429", "500"):
        assert code in op["responses"], f"missing response {code}"


def test_schema_summary_from_docstring():
    class V(LiveView):
        api_name = "openapi.doc"

        @event_handler(expose_api=True)
        def documented(self, **kwargs):
            """One-line summary for docs."""
            return None

    register_api_view("openapi.doc", V)
    schema = build_schema()
    op = schema["paths"]["/djust/api/openapi.doc/documented/"]["post"]
    assert op["summary"] == "One-line summary for docs."


def test_schema_is_json_serializable():
    class V(LiveView):
        api_name = "openapi.json"

        @event_handler(expose_api=True)
        def go(self, q: str = "", **kwargs):
            return q

    register_api_view("openapi.json", V)
    schema = build_schema()
    serialized = json.dumps(schema)
    assert "openapi.json" in serialized


def test_schema_accepts_kwargs_sets_additional_properties():
    class V(LiveView):
        api_name = "openapi.kwargs"

        @event_handler(expose_api=True)
        def flex(self, **kwargs):
            return None

    register_api_view("openapi.kwargs", V)
    schema = build_schema()
    body = schema["paths"]["/djust/api/openapi.kwargs/flex/"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    assert body["additionalProperties"] is True
