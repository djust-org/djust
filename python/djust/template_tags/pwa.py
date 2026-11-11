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
from typing import Any, Dict, FrozenSet, List, Tuple

from django.utils.html import escape

from .._html import safe_html
from . import TagHandler, register

logger = logging.getLogger(__name__)


#: The keyword arguments each PWA tag accepts, by tag name.
#:
#: This table is what lets :func:`_tag_source_and_context` build Django source
#: out of CONSTANTS only (#3824). A tag name or a keyword that is not in it never
#: reaches the source: it raises, the ``except`` arm of
#: :func:`_render_django_tag` logs it and returns the marked diagnostic. It
#: mirrors the signatures of the Django tags in ``djust.templatetags.djust_pwa``
#: (``icons`` is left out: it takes a list of dicts, which the ``str`` kwargs
#: this bridge builds can never be); ``test_pwa_tag_template_injection_3824``
#: pins the two against each other.
_TAG_PARAMS: Dict[str, FrozenSet[str]] = {
    "djust_pwa_head": frozenset({"name", "theme_color"}),
    "djust_pwa_manifest": frozenset(
        {
            "name",
            "short_name",
            "description",
            "theme_color",
            "background_color",
            "display",
            "start_url",
        }
    ),
    "djust_sw_register": frozenset({"sw_url", "scope"}),
    "djust_offline_indicator": frozenset(
        {"online_text", "offline_text", "online_class", "offline_class", "show_when"}
    ),
}


def _tag_source_and_context(tag_name: str, kwargs: Dict[str, str]) -> Tuple[str, Dict[str, str]]:
    """
    Build the Django template source for a PWA tag, and the context it renders in.

    The SOURCE is made of constants only: the library load, a tag name and
    keyword names that were checked against :data:`_TAG_PARAMS`, and one
    context-variable reference per keyword (``name=kw_name``). Every VALUE
    travels in the returned context and is resolved by Django as data, so no
    value, however it is spelled, can close a quote, a ``%}`` or a ``{{`` and
    become template code (#3824). The previous form wrote each resolved value
    into the source as ``key="value"``, which let a context value carrying a
    quote and ``%}`` end the tag early and write its own markup after it.

    Parameters
    ----------
    tag_name : str
        The Django template tag name (e.g. "djust_pwa_head").
    kwargs : dict
        Keyword arguments to pass to the tag, already resolved to strings.

    Returns
    -------
    tuple
        ``(source, context)`` for ``Template(source).render(Context(context))``.

    Raises
    ------
    ValueError
        If ``tag_name`` is not a PWA tag, or a keyword is not one the tag takes.
    """
    allowed = _TAG_PARAMS.get(tag_name)
    if allowed is None:
        raise ValueError("not a PWA tag")
    values: Dict[str, str] = {}
    parts = ["{% load djust_pwa %}{%", tag_name]
    for key, value in kwargs.items():
        if key not in allowed:
            raise ValueError("unsupported argument %r for a PWA tag" % key)
        parts.append("%s=kw_%s" % (key, key))
        values["kw_" + key] = value
    parts.append("%}")
    return " ".join(parts), values


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

    try:
        tpl_str, values = _tag_source_and_context(tag_name, kwargs)
        # Django is untyped under the lenient global config; Template.render
        # returns ``Any`` to mypy but a real ``SafeString`` (str) at runtime.
        return str(Template(tpl_str).render(DjangoContext(values)))
    except Exception:
        logger.exception("Error rendering {%% %s %%}", tag_name)
        # MARKED, so the diagnostic stays an invisible HTML comment (#2434).
        #
        # Since #2379 the bridge escapes a handler's plain-`str` return, and
        # this arm is a `str` carrying markup — so the comment reached the page
        # as the visible text `&lt;!-- djust: … --&gt;`, shouting a server-side
        # failure at the END USER on the one path whose whole job is to be
        # readable in view-source and invisible on the page.
        #
        # Marked rather than emptied, and rather than left to the log alone,
        # for two reasons. The failure is an ABSENCE — no manifest link, no
        # service-worker registration — and an absence is otherwise
        # unattributable from the browser; the comment is what tells a
        # front-end developer which tag went missing and where to look. And
        # `{% call %}`'s sibling diagnostic
        # (`components/function_component.py`) is already marked, by #2379's
        # own single-exit `safe_html`; emptying this one would make two
        # diagnostics of the same kind disagree.
        #
        # `escape` on the interpolated value, per CLAUDE.md's `mark_safe`
        # rule: the marker then covers a CONSTANT shape plus a value that
        # cannot carry `-->` (escape rewrites `>`), so the argument holds
        # without depending on every caller passing a literal. Today all four
        # do.
        return safe_html("<!-- djust: %s render failed (check server logs) -->" % escape(tag_name))


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
