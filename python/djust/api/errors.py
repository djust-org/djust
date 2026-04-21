"""Structured error responses for the HTTP API dispatch view."""

from __future__ import annotations

from typing import Any, Dict, Optional

from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse


def api_error(
    status: int,
    kind: str,
    message: str,
    details: Optional[Dict[str, Any]] = None,
) -> JsonResponse:
    """Build a structured API error response.

    Shape: ``{"error": "<kind>", "message": "<message>", "details": {...}}``.
    """
    body: Dict[str, Any] = {"error": kind, "message": message}
    if details is not None:
        body["details"] = details
    return JsonResponse(body, status=status, encoder=DjangoJSONEncoder)
