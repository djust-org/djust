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

    Returns the API/SSE prefix ``<meta>`` tags (resolved via Django's
    ``reverse()`` — honors ``FORCE_SCRIPT_NAME`` and custom
    ``api_patterns(prefix=...)`` mounts) plus the auto-derived route-map
    ``<script>`` (#1733). Mirrors the Django-side
    ``@register.simple_tag(takes_context=True)`` in ``live_tags.py`` — both
    invoke the shared ``_client_config_html()`` helper to guarantee
    byte-identical output across engines.

    Security: the resolved prefixes are HTML-escaped via
    :func:`django.utils.html.escape` and the route map is ``json.dumps`` /
    ``format_html``-escaped, so a mis-configured ``FORCE_SCRIPT_NAME`` value
    cannot break out of the ``content="..."`` attribute and the route data
    cannot break out of the ``<script>``.
    """

    def render(self, args: List[str], context: Dict[str, Any]) -> str:  # noqa: ARG002
        # ``args`` is unused (this tag takes no positional arguments). The
        # ``context`` dict is read for ``request`` so the auto-emitted
        # route-map <script> (#1733) can pick up ``request.csp_nonce`` —
        # the same nonce the Django-engine tag uses. Both engines delegate
        # to the shared ``_client_config_html`` helper to guarantee
        # byte-identical output (the dual-registration invariant, PR #993).
        #
        # Import here to avoid a circular import with live_tags at module
        # load time. live_tags imports from djust.config which pulls in
        # Django settings — safe to defer to render time.
        from djust.templatetags.live_tags import _client_config_html

        request = context.get("request") if context else None
        # _client_config_html returns a SafeString (mark_safe) — declared
        # ``-> Any`` on the live_tags side, so coerce to ``str`` at this
        # boundary. The Rust CustomTag output path does NOT re-escape the
        # returned string (matches the djust_markdown pattern), so the
        # individually-escaped meta + route-map markup is emitted safely and
        # not double-escaped.
        return str(_client_config_html(request))
