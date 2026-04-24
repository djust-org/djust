"""
Rust-engine handler for ``{% djust_client_config %}``.

The Django-side counterpart in :mod:`djust.templatetags.live_tags` handles
pure-Django templates (rendered via Django's template engine). This handler
registers the same tag with the Rust template engine so that LiveView
templates — which are parsed and rendered by
:mod:`djust._rust` — emit the same ``<meta name="djust-api-prefix">``
bootstrap tag.

Both paths invoke :func:`djust.templatetags.live_tags._resolve_api_prefix`
to guarantee byte-identical output across engines. The shared helper uses
Django's ``reverse()``, so ``FORCE_SCRIPT_NAME`` and custom
``api_patterns(prefix=...)`` mounts are honored uniformly regardless of
which engine rendered the template.

See ``docs/website/guides/server-functions.md`` (Sub-path deploys) and
``docs/website/guides/http-api.md`` for the developer-facing docs.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from . import TagHandler, register

logger = logging.getLogger(__name__)


@register("djust_client_config")
class ClientConfigTagHandler(TagHandler):
    """Handler for ``{% djust_client_config %}`` (Rust template engine).

    Returns a ``<meta>`` tag with the djust API prefix resolved via
    Django's ``reverse()`` (honors ``FORCE_SCRIPT_NAME`` and custom
    ``api_patterns(prefix=...)`` mounts). Mirrors the Django-side
    ``@register.simple_tag`` in ``live_tags.py`` — both invoke the shared
    ``_resolve_api_prefix()`` helper to guarantee identical output across
    engines.

    Security: the resolved prefix is HTML-escaped via
    :func:`django.utils.html.format_html` so a mis-configured
    ``FORCE_SCRIPT_NAME`` value cannot break out of the ``content="..."``
    attribute.
    """

    def render(self, args: List[str], context: Dict[str, Any]) -> str:  # noqa: ARG002
        # ``args`` and ``context`` are unused by this tag (it takes no
        # arguments and its output depends only on Django settings +
        # URLconf state), but they are kept in the signature to match
        # the ``TagHandler.render()`` interface contract — see
        # ``template_tags/__init__.py``. The noqa silences the
        # unused-argument lint for the same reason.
        #
        # Import here to avoid a circular import with live_tags at module
        # load time. live_tags imports from djust.config which pulls in
        # Django settings — safe to defer to render time.
        from django.utils.html import format_html

        from djust.templatetags.live_tags import _resolve_api_prefix

        prefix = _resolve_api_prefix()
        # format_html escapes the interpolated value and returns a
        # SafeString. The Rust CustomTag output path does NOT re-escape the
        # returned string (matches the djust_markdown pattern), so using
        # format_html here produces safe, non-double-escaped HTML.
        return format_html(
            '<meta name="djust-api-prefix" content="{}">',
            prefix,
        )
