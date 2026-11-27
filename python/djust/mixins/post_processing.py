"""
PostProcessingMixin - Debug info and client script injection for LiveView.
"""

import json
import logging
import re
from typing import Any, Dict, List, cast

# The client chunks and the injected bundle's URLs live in _client_chunks
# (#4007, #4320).
from .._client_chunks import (
    chunks_for_first_render,
    client_bundle_urls,
    preload_links,
)

logger = logging.getLogger(__name__)

# #2987: find the document's real <head>/<body> boundaries. Anything tag-shaped
# inside a raw-text region is text, not markup, so the lookups run on a masked
# copy: ``_mask_raw_text`` (script/style bodies and comments, #2663) plus the
# RCDATA elements title/textarea. The masks are length-preserving, so positions
# found in the masked copy index the original string. An unterminated region
# runs to the end of the document, as it does in the HTML tokenizer.
_RCDATA_RE = re.compile(
    r"<(title|textarea)(?=[\s/>])[^<>]*>.*?(?:</\1[^<>]*>|\Z)",
    re.DOTALL | re.IGNORECASE,
)


# A tag name ends at whitespace, "/" or ">": ``<body-shell>`` is not ``<body>``.
# End tags may carry junk before ">" (``</head foo>``), as browsers accept.
# ``[^<>]*`` (not ``[^>]*``) keeps a tag from spanning into the next one, and
# keeps the scan linear when many tags have no ">" (PR #3017 review).
_HEAD_CLOSE_RE = re.compile(r"</head(?=[\s/>])[^<>]*>", re.IGNORECASE)
_BODY_OPEN_RE = re.compile(r"<body(?=[\s/>])", re.IGNORECASE)
_BODY_CLOSE_TAG_RE = re.compile(r"</body(?=[\s/>])[^<>]*>", re.IGNORECASE)
_HTML_CLOSE_TAG_RE = re.compile(r"</html(?=[\s/>])[^<>]*>", re.IGNORECASE)


def _mask_document_text(html: str) -> str:
    from .template import _mask_raw_text

    masked = _mask_raw_text(html)
    return _RCDATA_RE.sub(lambda m: "\x00" * len(m.group(0)), masked)


def _find_head_close(masked: str) -> int:
    """Index of the document's real closing ``</head>`` tag, or ``-1`` (#2987).

    ``masked`` is the page after :func:`_mask_document_text`. A plain
    ``str.replace("</head>", ...)`` hit the first (or every) ``</head>``
    *string*, including one inside an inline script or a comment. This returns
    the first real ``</head>``, provided it comes before ``<body``.
    """
    head_close = _HEAD_CLOSE_RE.search(masked)
    if head_close is None:
        return -1
    body_open = _BODY_OPEN_RE.search(masked, 0, head_close.start())
    return -1 if body_open else head_close.start()


def _find_body_close(masked: str) -> int:
    """Index of the document's last real closing ``</body>`` tag, or ``-1``."""
    found = -1
    for match in _BODY_CLOSE_TAG_RE.finditer(masked):
        found = match.start()
    return found


def _find_html_close(masked: str) -> int:
    """Index of the document's last real closing ``</html>`` tag, or ``-1``
    (#3018: the handler-metadata fallback when a page has no ``</body>``)."""
    found = -1
    for match in _HTML_CLOSE_TAG_RE.finditer(masked):
        found = match.start()
    return found


def bug_capture_scrub() -> List[str]:
    """``LIVEVIEW_CONFIG['bug_capture_default_scrub']`` as a clean list of names.

    The debug panel's State tab removes these top-level keys from both states
    before it encodes a bug capture (#4184). Anything that is not a list or
    tuple of strings gives an empty list, with a warning: a bare string
    ``"password"`` would otherwise be split into characters.
    """
    from ..config import config

    names = config.get("bug_capture_default_scrub", []) or []
    if not isinstance(names, (list, tuple)) or not all(isinstance(n, str) for n in names):
        logger.warning(
            "LIVEVIEW_CONFIG['bug_capture_default_scrub'] must be a list of field names; "
            "ignoring it"
        )
        return []
    return list(names)


class PostProcessingMixin:
    """Post-processing: get_debug_info, _inject_client_script."""

    def get_debug_info(self) -> Dict[str, Any]:
        """
        Get debug information about this LiveView instance.

        Returns:
            Dict with debug information
        """
        from .._exposure import explicit_debug_projection
        from ..config import config

        # Do not inspect arbitrary descriptors or handler default values.
        # Typed public handler metadata will be supplied by ADR-036/037.
        return {
            **self._explicit_debug_update(explicit_debug_projection(self)),
            "handlers": {},
            "template": None,
            "config": {
                "maxHistory": config.get("debug_panel_max_history", 50),
                "bugCaptureScrub": bug_capture_scrub(),
            },
        }

    def _debug_state_sizes(self) -> Dict[str, Dict[str, Any]]:
        """Return size breakdown of public state variables for debug toolbar."""
        from .._exposure import explicit_debug_projection

        return cast(
            Dict[str, Dict[str, Any]],
            self._explicit_debug_update(explicit_debug_projection(self))["state_sizes"],
        )

    def get_debug_update(self) -> Dict[str, Any]:
        """
        Get a slim debug payload for event responses (skip static handler metadata).

        Unlike get_debug_info(), which also carries the config and the (empty)
        handler map, this returns only the parts that change per event:
        variables and view class.
        """
        from .._exposure import explicit_debug_projection

        return self._explicit_debug_update(explicit_debug_projection(self))

    def _explicit_debug_update(self, projection: Dict[str, Any]) -> Dict[str, Any]:
        """Format already-bounded debug primitives, never inspect the view again."""
        variables = {}
        sizes = {}
        for name, value in projection.items():
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            size = len(encoded.encode("utf-8"))
            redacted = value == "[redacted]"
            variables[name] = {
                "name": name,
                "type": "redacted" if redacted else type(value).__name__,
                "value": encoded[:100] + ("..." if len(encoded) > 100 else ""),
                "size_bytes": None if redacted else size,
            }
            # Sizes describe the exported value only; hidden values are not read.
            sizes[name] = {"memory": None, "serialized": None if redacted else size}
        return {"view_class": type(self).__name__, "variables": variables, "state_sizes": sizes}

    def _inject_client_script(self, html: str) -> str:
        """Inject the LiveView client JavaScript into the HTML"""
        from ..config import config
        from django.conf import settings

        use_websocket = config.get("use_websocket", True)

        # The two inline scripts below carry the request's CSP nonce, as the
        # handler-metadata script does (mixins/template.py): under a policy
        # without 'unsafe-inline' they were blocked on every page, so the
        # client fell back to its defaults and the DEBUG panel had no info.
        from ..utils import get_csp_nonce

        from django.utils.html import escape

        nonce = get_csp_nonce(getattr(self, "request", None))
        nonce_attr = f' nonce="{escape(nonce)}"' if nonce else ""

        from ..config import debug_panel_enabled

        panel_enabled = debug_panel_enabled()
        debug_info_script = ""
        debug_css_link = ""
        if panel_enabled:
            from ..security.json_script import escape_json_for_script  # the defining module (#3820)

            debug_info = self.get_debug_info()
            # get_debug_info() includes repr()s of user-controlled public view
            # attributes; json.dumps does NOT escape </script>, so a value
            # containing "</script><script>…" would break out of this inline
            # block (finding #8, CWE-79). escape_json_for_script() neutralizes
            # <, >, & (and U+2028/2029).
            debug_info_json = escape_json_for_script(json.dumps(debug_info))
            debug_info_script = f"""
            <script data-turbo-track="reload"{nonce_attr}>
                window.DJUST_DEBUG_INFO = {debug_info_json};
            </script>
            """
            debug_css_link = '<link rel="stylesheet" href="/static/djust/debug-panel.css" data-turbo-track="reload">'

        config_script = f"""
        <script data-turbo-track="reload"{nonce_attr}>
            // djust configuration
            window.DJUST_USE_WEBSOCKET = {str(use_websocket).lower()};
            // Enable debug logging for client-dev.js (development only)
            window.djustDebug = {str(settings.DEBUG).lower()};
            window.DEBUG_MODE = {str(settings.DEBUG).lower()};
        </script>
        {debug_info_script}
        """

        from django.templatetags.static import static

        # v0.6.0 P1: serve the pre-minified client.min.js in production.
        # DEBUG=True continues to serve the readable client.js so stack
        # traces point at meaningful line numbers and contributors can
        # poke at source directly. An explicit override
        # ``DJUST_CLIENT_JS_MINIFIED`` in settings (bool) takes precedence
        # over the DEBUG heuristic if an operator wants to test the
        # minified file locally.
        #
        # #4007: the default is the CORE bundle, which loads the optional
        # chunks (CLIENT_CHUNKS) only when the page uses them (#4320: and the
        # head preloads the ones this first render uses, below). Their static()
        # URLs ride on the script tag as data-djust-chunks, so a hashed
        # ManifestStaticFilesStorage name is used; the loaded <script> takes
        # this tag's nonce. ``client_bundle: "full"`` injects every module.
        client_js_url, chunk_urls = client_bundle_urls()
        chunks_attr = ""
        if chunk_urls:
            chunks_attr = f' data-djust-chunks="{escape(json.dumps(chunk_urls, sort_keys=True))}"'

        script = (
            f'<script src="{escape(client_js_url)}" defer data-turbo-track="reload"'
            f"{chunks_attr}{nonce_attr}></script>"
        )

        # Official adapters (ADR-025 milestone C). Opt-in via
        # DJUST_CONFIG['extensions']; nothing is emitted when unused.
        # These MUST come after client.js: they register into
        # window.djust.hooks / window.djust.commands, which client.js
        # creates. `defer` scripts execute in document order, so appending
        # here is what guarantees the ordering.
        from ..extensions import get_extension_static_paths

        for ext_path in get_extension_static_paths():
            try:
                ext_url = static(ext_path)
            except (ValueError, AttributeError):
                ext_url = f"/static/{ext_path}"
            script += f'\n        <script src="{ext_url}" defer data-turbo-track="reload"{nonce_attr}></script>'

        # #4146: under a strict CSP NavBar's menu toggle is a marker that
        # navbar-toggle.js handles. The navbar renders its own <script> for it,
        # but a <script> a later patch inserts never runs, so a strict page loads
        # the listener up front, with the client; it registers once per page.
        from ..components.utils import csp_strict as _csp_strict

        if _csp_strict():
            from django.apps import apps as _apps

            if _apps.is_installed("djust.components"):
                try:
                    toggle_url = static("djust_components/navbar-toggle.js")
                except (ValueError, AttributeError):
                    toggle_url = "/static/djust_components/navbar-toggle.js"
                script += f'\n        <script src="{toggle_url}" defer data-turbo-track="reload"{nonce_attr}></script>'

        if settings.DEBUG:
            # debug-panel.js MUST load before client-dev.js so that
            # DjustDebugPanel is defined when client-dev.js calls
            # initDebugPanel() (fixes #193 and #196). With the panel off
            # (``debug_panel: False``) client-dev.js still loads for hot
            # reload; its initDebugPanel() skips when DjustDebugPanel is absent.
            if panel_enabled:
                try:
                    debug_panel_js_url = static("djust/debug-panel.js")
                except (ValueError, AttributeError):
                    debug_panel_js_url = "/static/djust/debug-panel.js"
                script += f'\n        <script src="{debug_panel_js_url}" defer data-turbo-track="reload"{nonce_attr}></script>'

            try:
                client_dev_js_url = static("djust/client-dev.js")
            except (ValueError, AttributeError):
                client_dev_js_url = "/static/djust/client-dev.js"
            script += f'\n        <script src="{client_dev_js_url}" defer data-turbo-track="reload"{nonce_attr}></script>'

        full_script = config_script + script
        # ADR-036: public owner contracts of the initial page (set by get();
        # escaped JSON, outside dj-root). A data block, not executable script.
        initial_contracts = self.__dict__.pop("_initial_parameter_contracts", None)
        if initial_contracts:
            full_script = (
                '<script type="application/json" data-djust-parameter-contracts>'
                f"{initial_contracts}</script>" + full_script
            )

        # The HTTP event fallback and djust.call need the CSRF token even when
        # the project renames the cookie (CSRF_COOKIE_NAME) or keeps it out of
        # JavaScript's reach (CSRF_COOKIE_HTTPONLY, CSRF_USE_SESSIONS). The
        # client reads these via window.djust.csrfToken() (00-namespace.js).
        csrf_meta = ""
        request = getattr(self, "request", None)
        if request is not None:
            from django.middleware.csrf import get_token
            from django.utils.html import escape

            csrf_meta = (
                f'<meta name="djust-csrf-cookie" content="{escape(settings.CSRF_COOKIE_NAME)}">'
                f'<meta name="djust-csrf-token" content="{escape(get_token(request))}">'
            )
        # Both go into the document's real <head>, once (#2987).
        masked = _mask_document_text(html)
        head_close = _find_head_close(masked)
        head_inject = ""
        if csrf_meta:
            if head_close >= 0:
                head_inject += csrf_meta
            else:
                full_script = csrf_meta + full_script
        # #4108: the digest of the render this page shows, for the page-POST
        # fallback (set by get(); see ``_render_digest``). Outside dj-root, so
        # the VDOM baseline is unaffected.
        page_digest = self.__dict__.pop("_page_render_digest", None)
        if page_digest:
            from .._render_digest import META_NAME

            digest_meta = f'<meta name="{META_NAME}" content="{escape(page_digest)}">'
            if head_close >= 0:
                head_inject += digest_meta
            else:
                full_script = digest_meta + full_script
        # #3979: free-form style values are data-dj-style-* attributes under a strict CSP;
        # the client applies them only on a page that says so.
        from ..components.utils import STYLES_META, csp_strict

        styles_meta_first = False
        if csp_strict():
            if head_close >= 0:
                head_inject += STYLES_META
            else:
                # The client reads it from the document head only: written before any
                # other content (after a doctype), the parser puts it in the implied head.
                styles_meta_first = True
        # #4320: preload the chunks this first render uses, so the core finds
        # them in flight (or done) instead of fetching them one round trip
        # after it has run. Same URLs and nonce as the loader's <script>s.
        if chunk_urls and head_close >= 0:
            from ..components.utils import csp_strict as _strict_styles

            upload_manager = getattr(self, "_upload_manager", None)
            preload = chunks_for_first_render(
                masked,
                declares_uploads=bool(upload_manager and upload_manager.get_upload_state()),
                debug=bool(settings.DEBUG),
                strict_styles=_strict_styles(),
            )
            head_inject += preload_links((chunk_urls[name] for name in preload), nonce_attr)
        if debug_css_link and head_close >= 0:
            head_inject += debug_css_link
        # #3036: what this page's shell loads, so a live navigation to a page
        # with a different shell becomes a full load. Needs a real <head> to
        # live in; without one the client keeps the in-place swap.
        if head_close >= 0:
            from .._page_shell import page_shell

            shell = page_shell(self)
            if shell:
                head_inject += f'<meta name="djust-page-shell" content="{shell}">'
        if head_inject:
            html = html[:head_close] + head_inject + html[head_close:]

        # Found before the head insertion, so shift it past the inserted text.
        body_close = _find_body_close(masked)
        if body_close >= 0 and head_inject and body_close >= head_close:
            body_close += len(head_inject)
        if body_close >= 0:
            html = html[:body_close] + full_script + html[body_close:]
        else:
            html += full_script
        if styles_meta_first:
            doctype = re.match(r"\s*<!doctype[^>]*>", html, re.IGNORECASE)
            at = doctype.end() if doctype else 0
            html = html[:at] + STYLES_META + html[at:]

        return html
