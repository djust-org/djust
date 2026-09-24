"""Fresh page requests for SSE navigation; no browser identity is trusted."""

from copy import copy
from io import BytesIO
from typing import Any
from urllib.parse import unquote, urlencode, urlsplit

from django.http import HttpRequest, QueryDict
from django.urls import Resolver404, resolve
from django.utils.datastructures import MultiValueDict

from .security.mount import validate_mount_url


def page_request(request: HttpRequest, url: str, params: dict[str, Any]) -> HttpRequest:
    """Copy current middleware/auth state, replacing only page-routing inputs."""
    parsed = urlsplit(validate_mount_url(url))
    target = copy(request)
    target.path = unquote(parsed.path)
    script_name = request.META.get("SCRIPT_NAME", "").rstrip("/")
    target.path_info = (
        target.path[len(script_name) :] or "/"
        if script_name and target.path.startswith(script_name + "/")
        else target.path
    )
    query = QueryDict(parsed.query, mutable=True)
    query.update(QueryDict(urlencode(params, doseq=True)))
    target.GET = query
    target.POST = QueryDict()
    target._files = MultiValueDict()
    if "FILES" in target.__dict__:
        target.__dict__["FILES"] = target._files
    target.method = "GET"
    target.META = {
        **request.META,
        "PATH_INFO": target.path_info,
        "QUERY_STRING": query.urlencode(),
        "REQUEST_METHOD": "GET",
        "CONTENT_LENGTH": "0",
    }
    target.META.pop("CONTENT_TYPE", None)
    target._body = b""
    target._stream = BytesIO()
    try:
        target.resolver_match = resolve(target.path_info, urlconf=getattr(request, "urlconf", None))
    except Resolver404:
        target.resolver_match = None
    return target
