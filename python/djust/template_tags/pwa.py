"""
PWA template tag handlers for djust's Rust template engine.

Registers handlers for the four PWA template tags so the Rust VDOM engine
renders them as real HTML instead of ``<!-- djust: unsupported tag -->`` comments.

Each handler delegates to the corresponding Django template tag defined in
``djust.templatetags.djust_pwa`` by rendering through Django's template engine.
This is necessary because some tags (e.g. ``djust_pwa_head``) are inclusion tags
that render sub-templates.

Usage in templates::

    {% djust_pwa_head name="My App" theme_color="#007bff" %}
    {% djust_pwa_manifest name="My App" theme_color="#007bff" %}
    {% djust_sw_register %}
    {% djust_offline_indicator %}
"""

import logging
from typing import Any, Dict, List

from . import TagHandler, register

logger = logging.getLogger(__name__)


def _build_django_tag(tag_name: str, kwargs: Dict[str, str]) -> str:
    """
    Build a Django template string for a tag with keyword arguments.

    Parameters
    ----------
    tag_name : str
        The Django template tag name (e.g. "djust_pwa_head").
    kwargs : dict
        Keyword arguments to pass to the tag.

    Returns
    -------
    str
        A Django template string ready for rendering.
    """
    parts = ["{%", "load djust_pwa", "%}{%", tag_name]
    for key, value in kwargs.items():
        # Values are already resolved strings
        parts.append('%s="%s"' % (key, value))
    parts.append("%}")
    return " ".join(parts)


def _render_django_tag(tag_name: str, kwargs: Dict[str, str]) -> str:
    """
    Render a Django PWA template tag and return the HTML output.

    Parameters
    ----------
    tag_name : str
        The Django template tag name.
    kwargs : dict
        Keyword arguments to pass to the tag.

    Returns
    -------
    str
        The rendered HTML string.
    """
    from django.template import Template, Context as DjangoContext

    tpl_str = _build_django_tag(tag_name, kwargs)
    try:
        return Template(tpl_str).render(DjangoContext({}))
    except Exception as e:
        logger.error("Error rendering {%% %s %%}: %s", tag_name, e)
        return ""


def _extract_kwargs(
    args: List[str], context: Dict[str, Any], handler: TagHandler
) -> Dict[str, str]:
    """
    Parse tag arguments into a dict of keyword arguments.

    Only named parameters (key=value) are included.

    Parameters
    ----------
    args : list
        Raw arguments from the Rust parser.
    context : dict
        Template context for variable resolution.
    handler : TagHandler
        Handler instance (provides ``_resolve_arg``).

    Returns
    -------
    dict
        Resolved keyword arguments.
    """
    kwargs = {}
    for arg in args:
        resolved = handler._resolve_arg(arg, context)
        if isinstance(resolved, tuple):
            kwargs[resolved[0]] = str(resolved[1])
    return kwargs


@register("djust_pwa_head")
class PwaHeadHandler(TagHandler):
    """
    Handler for ``{% djust_pwa_head name="..." theme_color="..." %}``.

    Renders the ``djust/pwa_head.html`` inclusion tag which outputs manifest link,
    service worker registration, offline styles, and mobile meta tags.
    """

    def render(self, args: List[str], context: Dict[str, Any]) -> str:
        kwargs = _extract_kwargs(args, context, self)
        return _render_django_tag("djust_pwa_head", kwargs)


@register("djust_pwa_manifest")
class PwaManifestHandler(TagHandler):
    """
    Handler for ``{% djust_pwa_manifest name="..." theme_color="..." %}``.

    Renders an inline PWA manifest ``<link>`` tag with data URI and
    a ``<meta name="theme-color">`` tag.
    """

    def render(self, args: List[str], context: Dict[str, Any]) -> str:
        kwargs = _extract_kwargs(args, context, self)
        return _render_django_tag("djust_pwa_manifest", kwargs)


@register("djust_sw_register")
class SwRegisterHandler(TagHandler):
    """
    Handler for ``{% djust_sw_register %}``.

    Renders a ``<script>`` block that registers the service worker.
    """

    def render(self, args: List[str], context: Dict[str, Any]) -> str:
        kwargs = _extract_kwargs(args, context, self)
        return _render_django_tag("djust_sw_register", kwargs)


@register("djust_offline_indicator")
class OfflineIndicatorHandler(TagHandler):
    """
    Handler for ``{% djust_offline_indicator %}``.

    Renders the offline status indicator dot and associated CSS.
    """

    def render(self, args: List[str], context: Dict[str, Any]) -> str:
        kwargs = _extract_kwargs(args, context, self)
        return _render_django_tag("djust_offline_indicator", kwargs)
