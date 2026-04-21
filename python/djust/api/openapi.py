"""OpenAPI 3.1 schema generator for the djust HTTP API (ADR-008).

Walks the registry of ``@event_handler(expose_api=True)`` handlers and emits
an OpenAPI 3.1 document. Each handler becomes one ``POST`` path under
``/djust/api/<view_slug>/<handler_name>/``. Type mapping is derived from the
handler's type hints via the existing ``get_handler_signature_info`` output.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpRequest, JsonResponse
from django.views import View

from djust.api.registry import iter_exposed_handlers
from djust.validation import get_handler_signature_info

PYTHON_TO_OPENAPI: Dict[str, Dict[str, Any]] = {
    "int": {"type": "integer"},
    "integer": {"type": "integer"},
    "float": {"type": "number"},
    "number": {"type": "number"},
    "bool": {"type": "boolean"},
    "boolean": {"type": "boolean"},
    "str": {"type": "string"},
    "string": {"type": "string"},
    "Decimal": {"type": "string", "format": "decimal"},
    "UUID": {"type": "string", "format": "uuid"},
    "datetime": {"type": "string", "format": "date-time"},
    "date": {"type": "string", "format": "date"},
    "time": {"type": "string", "format": "time"},
}


def _map_param_type(type_name: str) -> Dict[str, Any]:
    """Map a Python type name from ``get_handler_signature_info`` to an OpenAPI schema fragment."""
    if not type_name or type_name == "Any":
        return {}
    # list[T] / List[T] handling — the signature info emits names like "list[int]".
    lowered = type_name.lower()
    if lowered.startswith("list[") and lowered.endswith("]"):
        inner = type_name[5:-1].strip()
        return {"type": "array", "items": _map_param_type(inner) or {}}
    # Optional[T] / Union[T, None] becomes the inner type + nullable.
    if lowered.startswith("optional[") and lowered.endswith("]"):
        inner = type_name[9:-1].strip()
        base = _map_param_type(inner) or {"type": "string"}
        return {**base, "nullable": True}
    # Direct mapping table.
    base_name = type_name.split(".")[-1]
    return PYTHON_TO_OPENAPI.get(base_name, {"type": "string"})


def _build_request_body_schema(sig_info: Dict[str, Any]) -> Dict[str, Any]:
    properties: Dict[str, Any] = {}
    required = []
    for param in sig_info["params"]:
        name = param["name"]
        if name == "self":
            continue
        properties[name] = _map_param_type(param.get("type", "Any"))
        if param.get("required"):
            required.append(name)
    schema: Dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    schema["additionalProperties"] = bool(sig_info.get("accepts_kwargs"))
    return schema


def _response_envelope_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "result": {
                "description": "Handler return value (may be null).",
                "nullable": True,
            },
            "assigns": {
                "type": "object",
                "description": "Public view attributes that changed during the handler.",
                "additionalProperties": True,
            },
        },
        "required": ["result", "assigns"],
    }


def _error_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "error": {"type": "string"},
            "message": {"type": "string"},
            "details": {"type": "object", "additionalProperties": True},
        },
        "required": ["error", "message"],
    }


def _build_operation(view_cls, handler_name: str, handler) -> Dict[str, Any]:
    sig_info = get_handler_signature_info(handler)
    body_schema = _build_request_body_schema(sig_info)
    summary = (
        (sig_info.get("description") or "").splitlines()[0] if sig_info.get("description") else ""
    )
    op: Dict[str, Any] = {
        "operationId": f"{view_cls.__name__}_{handler_name}",
        "tags": [view_cls.__name__],
        "requestBody": {
            "required": bool(body_schema.get("required")),
            "content": {"application/json": {"schema": body_schema}},
        },
        "responses": {
            "200": {
                "description": "Handler executed successfully.",
                "content": {"application/json": {"schema": _response_envelope_schema()}},
            },
            "400": {
                "description": "Validation or JSON parse error.",
                "content": {"application/json": {"schema": _error_schema()}},
            },
            "401": {
                "description": "Authentication required.",
                "content": {"application/json": {"schema": _error_schema()}},
            },
            "403": {
                "description": "Permission denied or CSRF failure.",
                "content": {"application/json": {"schema": _error_schema()}},
            },
            "404": {
                "description": "Unknown view or handler, or handler not exposed.",
                "content": {"application/json": {"schema": _error_schema()}},
            },
            "429": {
                "description": "Rate limit exceeded.",
                "content": {"application/json": {"schema": _error_schema()}},
            },
            "500": {
                "description": "Handler raised an unexpected error.",
                "content": {"application/json": {"schema": _error_schema()}},
            },
        },
    }
    if summary:
        op["summary"] = summary
    description = sig_info.get("description")
    if description and description != summary:
        op["description"] = description
    return op


def build_schema(title: str = "djust API", version: Optional[str] = None) -> Dict[str, Any]:
    """Walk the registry and emit an OpenAPI 3.1 document."""
    try:
        from djust import __version__ as djust_version
    except ImportError:  # pragma: no cover
        djust_version = "unknown"
    paths: Dict[str, Any] = {}
    for slug, view_cls, handler_name, handler in iter_exposed_handlers():
        url = f"/djust/api/{slug}/{handler_name}/"
        paths[url] = {"post": _build_operation(view_cls, handler_name, handler)}
    return {
        "openapi": "3.1.0",
        "info": {
            "title": title,
            "version": version or djust_version,
            "description": (
                "Auto-generated HTTP API for djust @event_handler methods marked "
                "with ``expose_api=True``."
            ),
        },
        "paths": paths,
        "components": {
            "schemas": {
                "ResponseEnvelope": _response_envelope_schema(),
                "Error": _error_schema(),
            },
        },
    }


class OpenAPISchemaView(View):
    """Serve the OpenAPI JSON document at ``/djust/api/openapi.json``."""

    http_method_names = ["get", "options"]

    def get(self, request: HttpRequest) -> JsonResponse:
        schema = build_schema()
        return JsonResponse(schema, encoder=DjangoJSONEncoder, json_dumps_params={"indent": 2})
