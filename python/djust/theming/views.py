from django.http import HttpResponse
from django.views.decorators.cache import cache_control
from django.views.decorators.http import etag
from django.utils.cache import patch_vary_headers

from .manager import (
    generate_css_for_state,
    generate_deferred_css_for_state,
    get_css_prefix,
    get_theme_manager,
)


def _generate_css_content(request):
    """Generate the CSS content based on the request."""
    manager = get_theme_manager(request)
    state = manager.get_state()
    return generate_css_for_state(state, css_prefix=get_css_prefix())


def _css_etag(request, *args, **kwargs):
    """Generate ETag based on theme state."""
    manager = get_theme_manager(request)
    state = manager.get_state()
    return f"{state.theme}-{state.preset}-{state.mode}-{state.pack}"


@cache_control(max_age=3600, private=True)  # Cache for 1 hour, private (vary by user)
@etag(_css_etag)
def theme_css_view(request):
    """
    Serve dynamic theme CSS.

    This view generates the CSS for the current theme configuration.
    It uses ETag and Cache-Control headers to ensure efficient caching
    while respecting user-specific theme settings.
    """
    css = _generate_css_content(request)
    response = HttpResponse(css, content_type="text/css")
    patch_vary_headers(response, ["Cookie"])
    return response


def _deferred_css_etag(request, *args, **kwargs):
    """Generate ETag for deferred CSS based on theme state."""
    manager = get_theme_manager(request)
    state = manager.get_state()
    return f"deferred-{state.theme}-{state.preset}-{state.mode}-{state.pack}"


@cache_control(max_age=3600, private=True)
@etag(_deferred_css_etag)
def deferred_theme_css_view(request):
    """
    Serve deferred theme CSS (base styles, utilities, component styles).

    This endpoint is loaded asynchronously via ``<link rel="preload">``
    when the ``critical_css`` config option is enabled. Contains styles
    that are not needed for first paint.
    """
    manager = get_theme_manager(request)
    state = manager.get_state()
    css = generate_deferred_css_for_state(state, css_prefix=get_css_prefix())
    response = HttpResponse(css, content_type="text/css")
    patch_vary_headers(response, ["Cookie"])
    return response
