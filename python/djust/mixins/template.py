"""
TemplateMixin - Template loading, rendering, and HTML extraction for LiveView.
"""

import asyncio
import functools
import json
import logging
import os
import re
from html import escape as _html_escape
from typing import Any, Dict, Optional, Tuple, TYPE_CHECKING

from .._child_rendering import reconcile_child_render
from ..template_libraries import library_render_scope
from ..utils import get_template_dirs
from .context import _drop_request_scoped_values

if TYPE_CHECKING:  # pragma: no cover — imported only for type hints
    from django.http import HttpRequest

    from ..http_streaming import ChunkEmitter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level regexes for streaming render split (hoisted for perf — see
# PR review: avoid re.compile() on every request).
# ---------------------------------------------------------------------------

# Match a real root OPEN tag carrying ``dj-root`` as an ATTRIBUTE NAME, on ANY
# element (#2892 — ``<main>``/``<section>``/``<article>`` are the semantically
# right choice for a page's content region, and the div-only pattern silently
# skipped the initial-GET normalisation for them).
#
# * Group 1 is the element name; ``_find_closing_tag_pos`` balances that name.
# * ``html`` / ``head`` / ``body`` are excluded: the Rust VDOM's ``find_root``
#   (``crates/djust_vdom/src/parser.rs``) searches INSIDE ``<body>``, so a root
#   on those elements can never agree with the WS frame. The render path warns
#   about such a root instead (``_warn_unmatched_root``).
# * QUOTE-AWARE: the tag body is consumed as whole quoted strings or single
#   unquoted characters (``_TAG_BODY_UNIT``), so ``dj-root`` INSIDE an
#   attribute value can never match. A user-supplied
#   ``value="x dj-root onfocus=…"`` is text, not a root — matching it would
#   let the dj-view stamp land inside the value and break out of it (XSS,
#   found in review of #2981).
# * The name must be preceded by whitespace and followed by whitespace, ``=``,
#   ``>`` or ``/`` — so ``dj-root-other``, ``dj-rooted``, ``data-dj-root`` etc.
#   do NOT match.
# * Unquoted units exclude ``<``, so a scan that started at a stray ``<`` stops
#   at the next one instead of running to the next ``>`` (linear, not
#   quadratic, on tag soup with no ``>``).
#
# The Rust twin that must agree on what a root is:
# ``crates/djust_live/src/lib.rs::find_dj_root_content_range`` (#1646).
_QUOTED = r""""[^"]*"|'[^']*'"""
_TAG_BODY_UNIT = r"""(?:%s|[^'"<>])""" % _QUOTED
_ROOT_TAG_NAME = r"<(?!(?:html|head|body)(?=[\s/>]))([A-Za-z][A-Za-z0-9-]*)(?=[\s/>])"


def _root_open_re(tag_name: str, attr: str) -> "re.Pattern[str]":
    # The leading lookahead asserts the tag CLOSES before any attribute is
    # tried. Without it, an unclosed tag carrying many ``dj-root`` names
    # (``<a dj-root dj-root …`` with no ``>``) retried the tail scan once per
    # name — quadratic (17 s at 80 KB in review of #3023). With it, an unclosed
    # tag fails in one linear pass, and in a closed tag the first matching
    # name always succeeds.
    closes = r"(?=" + _TAG_BODY_UNIT + r"*>)"
    body = _TAG_BODY_UNIT + r"*?(?<=\s)" + attr + r"(?=[\s=>/])" + _TAG_BODY_UNIT + r"*>"
    return re.compile(tag_name + closes + body, re.IGNORECASE)


_DJ_ROOT_RE = _root_open_re(_ROOT_TAG_NAME, "dj-root")

# Same as ``_DJ_ROOT_RE`` for ``dj-view``. Used as a FALLBACK to
# ``_DJ_ROOT_RE``: when a template declares only ``dj-view`` (the
# auto-inferred-dj-root case, see PR #297) and no literal ``dj-root``
# attribute, the dj-root replacement step must still find the root element in
# the page shell — otherwise it falls through to returning the un-normalized
# ``_full_template`` render, leaving HTML comments and as-authored whitespace
# in the initial-GET dj-root that the WS (``render_with_diff``) frame has
# already stripped. That structural mismatch is what triggers the
# first-hydration ``morphChildren`` re-render / flash (#1737), and — when the
# VDOM itself was built from the wrong subtree — the per-event patch failures
# of #2892. The attribute-name boundary also keeps ``<body
# dj-view-transitions>`` and ``dj-viewport-*`` from matching.
_DJ_VIEW_RE = _root_open_re(_ROOT_TAG_NAME, "dj-view")

# Any tag carrying a dj-root / dj-view attribute, INCLUDING the elements the
# two patterns above exclude. Only used to decide whether a page that yielded
# no usable root was trying to declare one (``_warn_unmatched_root``).
_ANY_ROOT_ATTR_RE = _root_open_re(r"<[A-Za-z][A-Za-z0-9-]*(?=[\s/>])", "dj-(?:root|view)")

# Within an open tag already matched above: quoted strings (skipped) or a
# ``dj-root`` / ``dj-view`` attribute name with its value, if any. Tokenising
# this way means a ``dj-root``/``dj-view`` inside an attribute value is never
# mistaken for the attribute (#2981 stamp placement).
_ROOT_ATTR_TOKEN_RE = re.compile(
    _QUOTED + r"""|(?<=\s)dj-(root|view)(?=[\s=>/])(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'>]+))?""",
    re.IGNORECASE,
)

# View classes already warned about an unusable root (one warning per class
# per process — the render path runs on every GET).
_UNMATCHED_ROOT_WARNED: "set[str]" = set()

# Match ``</body>`` tolerating trailing whitespace inside the tag (``</body >``).
_BODY_CLOSE_RE = re.compile(r"</body\s*>", re.IGNORECASE)

# #2663: regions the HTML tokenizer treats as RAW TEXT — ``<script>`` and
# ``<style>`` bodies and HTML comments. Anything tag-shaped inside them is
# text, not markup: a JavaScript comment reading ``<div dj-root>`` must not
# be found as the root element, nor move the div depth counter. Every
# dj-root locating sink in this module searches a MASKED copy (see
# ``_mask_raw_text``) so a phantom tag in a script can never select the
# wrong element and turn the whole document into the liveview template.
#
# #3019: the open and close tags use ``[^<>]*`` (not ``[^>]*``), so a tag
# cannot span into the next one. With ``[^>]*`` every bare ``<script`` (or
# ``</script``) with no ``>`` after it scanned to the end of the document,
# which made the mask quadratic on such input (3 s at 32 000 of them) — the
# same shape #3017 fixed for its ``</head>`` / ``</body>`` / RCDATA patterns.
_RAW_TEXT_RE = re.compile(
    r"<!--.*?(?:-->|\Z)"
    r"|<script\b[^<>]*>.*?(?:</script[^<>]*>|\Z)"
    r"|<style\b[^<>]*>.*?(?:</style[^<>]*>|\Z)",
    re.DOTALL | re.IGNORECASE,
)


def _mask_raw_text(html: str) -> str:
    """Return ``html`` with every raw-text region (script/style body, HTML
    comment) replaced by NULs of the SAME length, so any match position found
    in the masked string indexes the original string unchanged (#2663)."""
    return _RAW_TEXT_RE.sub(lambda m: "\x00" * len(m.group(0)), html)


#: #3030: after a ``<``, the rest of a tag up to the ``>`` that ends it, a
#: ``<`` that aborts it, or an unterminated quote — quoted values are skipped
#: whole, so a ``>`` or ``<`` inside one belongs to the value. Linear: every
#: unit consumes input and no unit can start another's text.
_TAG_REST_RE = re.compile(r"""[^<>"']*(?:(?:"[^"]*"|'[^']*')[^<>"']*)*""")
#: The raw-text openers the Rust walker (``skip_raw_text_region``) knows.
_RAW_OPEN_RE = re.compile(r"(script|style)[ \t\n\r/>]", re.IGNORECASE)
_RAW_CLOSE_RES = {name: re.compile(r"</" + name, re.IGNORECASE) for name in ("script", "style")}


def _mask_for_root_search(html: str) -> str:
    """``html`` masked for the dj-root / dj-view search, walking it tag by tag
    exactly as the Rust twin does (``crates/djust_live/src/lib.rs::
    find_dj_root_content_range``, #1646).

    * A comment, ``<script>`` or ``<style>`` region is masked whole
      (``skip_raw_text_region``: an unterminated one runs to the end).
    * In every other tag, each ``<`` inside a quoted attribute value is masked
      (#3030). ``re.search`` can start at any ``<``; without this, the
      ``<section dj-root>`` inside ``<div data-h="<section dj-root>">`` was
      picked as the root, the dj-view stamp's ``"`` closed ``data-h`` early,
      and the Rust side (which skips quoted values) picked the next real tag.
    * A tag starts at ``<`` followed by a letter, ``/`` or ``!``, as in the
      HTML tokenizer; any other ``<`` is text. The Rust walker applies the
      same rule.

    Length-preserving, so positions index the original string. When a tag
    never ends (an unterminated quote, or EOF), the Rust walker stops looking;
    here the rest of the string keeps the plain #2663 raw-text mask, as it
    had before #3030.
    """
    out: "list[str]" = []
    last = 0
    i = html.find("<")
    n = len(html)
    while 0 <= i < n - 1:
        nxt = html[i + 1]
        if html.startswith("<!--", i):
            end = html.find("-->", i + 4)
            end = n if end < 0 else end + 3
            out.append(html[last:i])
            out.append("\x00" * (end - i))
            last = i = end
            i = html.find("<", i)
            continue
        raw = _RAW_OPEN_RE.match(html, i + 1)
        if raw:
            gt = html.find(">", i)
            end = n
            if gt >= 0:
                close = _RAW_CLOSE_RES[raw.group(1).lower()].search(html, gt + 1)
                if close:
                    cgt = html.find(">", close.start())
                    end = n if cgt < 0 else cgt + 1
            out.append(html[last:i])
            out.append("\x00" * (end - i))
            last = i = end
            i = html.find("<", i)
            continue
        if not (nxt.isascii() and (nxt.isalpha() or nxt in "/!")):
            i = html.find("<", i + 1)
            continue
        rest = _TAG_REST_RE.match(html, i + 1)
        j = rest.end() if rest else i + 1
        if j >= n or html[j] in "\"'":
            # Never ends: the Rust walker gives up here. Keep the #2663
            # raw-text mask over the rest, as before #3030.
            out.append(html[last:i])
            out.append(_mask_raw_text(html[i:]))
            return "".join(out)
        if html[j] == "<":
            i = j  # not a tag; resume at the `<` that aborted it
            continue
        body = html[i:j]
        if "<" in body[1:]:
            out.append(html[last:i])
            out.append(body[0] + body[1:].replace("<", "\x00"))
            last = j
        i = html.find("<", j + 1)
    out.append(html[last:])
    return "".join(out)


def _search_dj_root_open(html: str, *patterns: "re.Pattern[str]") -> "Optional[re.Match[str]]":
    """Find the FIRST real dj-root/dj-view opening tag in ``html``.

    Tries ``patterns`` in order against a masked copy (see
    :func:`_mask_for_root_search`), so a tag-like string inside
    ``<script>``/``<style>``/``<!-- -->`` or inside a quoted attribute value
    is never selected. The returned match's ``start()``/``end()`` index the
    ORIGINAL string (the mask is length-preserving); do not read ``group()``
    from it.
    """
    masked = _mask_for_root_search(html)
    embedded = _embedded_child_spans(html, masked)
    for pattern in patterns:
        if not embedded:
            m = pattern.search(masked)
            if m:
                return m
            continue
        pos = 0
        while True:
            m = pattern.search(masked, pos)
            if m is None:
                break
            span_end = _span_containing(embedded, m.start())
            if span_end is None:
                return m
            pos = span_end
    return None


# #3155: an embedded ``{% live_render %}`` child's wrapper
# (``<div dj-view data-djust-embedded="…">``, sticky or not) carries
# ``dj-view``, and the child's own template may carry ``dj-root``. Neither is
# the PAGE's root: on a page with no ``dj-root`` of its own, taking the child's
# wrapper spliced the whole page into the child's slot (two documents, the
# generic form of #3142).
_EMBEDDED_ATTR_TOKEN_RE = re.compile(
    _QUOTED + r"""|(?<=\s)(data-djust-embedded)(?=[\s=>/])""", re.IGNORECASE
)


def _embedded_child_spans(html: str, masked: str) -> "list[tuple[int, int]]":
    """``(start, end)`` of every embedded-child wrapper element in ``html``,
    outermost only, in document order. A wrapper whose close tag is missing
    runs to the end of the document (refuse to guess inside it)."""
    if "data-djust-embedded" not in masked:
        return []
    # ``masked`` is the caller's ``_mask_for_root_search`` copy, so this walk
    # sees exactly the tags the root search does (#2663).
    spans: "list[tuple[int, int]]" = []
    covered = 0
    for m in _DJ_VIEW_RE.finditer(masked):
        if m.start() < covered:
            continue  # inside an embedded child already recorded
        tag = html[m.start() : m.end()]
        if not any(tok.group(1) for tok in _EMBEDDED_ATTR_TOKEN_RE.finditer(tag)):
            continue
        _close_start, close_end = _find_root_close(html, m)
        covered = len(html) if close_end is None else close_end
        spans.append((m.start(), covered))
    return spans


def _span_containing(spans: "list[tuple[int, int]]", offset: int) -> "Optional[int]":
    """End of the span in ``spans`` containing ``offset``, or ``None``."""
    for start, end in spans:
        if start <= offset < end:
            return end
        if start > offset:
            return None
    return None


@functools.lru_cache(maxsize=64)
def _open_close_res(tag: str) -> "tuple[re.Pattern[str], re.Pattern[str]]":
    """Compiled open/close patterns for ``_find_closing_tag_pos`` (cached:
    the scanner runs on every GET and almost always for the same few tags)."""
    name = re.escape(tag)
    return (
        re.compile(r"<%s(?=[\s/>{])" % name, re.IGNORECASE),
        re.compile(r"</%s\s*>" % name, re.IGNORECASE),
    )


def _root_tag_name(html: str, match: "re.Match[str]") -> str:
    """The element name of a root open tag found by ``_search_dj_root_open``.

    Read from the ORIGINAL string by position (the match ran over a
    length-preserving masked copy, whose group text must not be used)."""
    return html[match.start(1) : match.end(1)].lower()


def _find_root_close(html: str, match: "re.Match[str]") -> "tuple[int, int] | tuple[None, None]":
    """``(close_start, close_end)`` of the element whose open tag is ``match``."""
    return TemplateMixin._find_closing_tag_pos(html, match.end(), _root_tag_name(html, match))


# ---------------------------------------------------------------------------
# #2999: whitespace between inline-level siblings
# ---------------------------------------------------------------------------

# HTML whitespace (space, tab, LF, FF, CR). NOT ``\s``: Python's ``\s`` also
# matches NBSP and the other Unicode spaces, which the Rust parser (and the
# browser) treat as content.
_HTML_WS_RUN_RE = re.compile(r"[ \t\n\r\f]+")
_BLOCK_TAG_RE = re.compile(r"<([A-Za-z][^\s/>]*)")


class TemplateMixin:
    """Template-related methods: get_template, render, render_full_template, render_with_diff,
    and various HTML extraction/stripping helpers."""

    if TYPE_CHECKING:
        # Cooperating attributes/methods supplied by the host class (LiveView)
        # and sibling mixins. Declared type-only so the strict-island mypy run
        # resolves them on this mixin without a runtime change — the real
        # definitions live on LiveView / the other mixins (this mixin is never
        # instantiated standalone). See streaming.py for the same pattern.
        template: Optional[str]
        template_name: Optional[str]
        _full_template: Optional[str]
        _rust_view: Any
        _current_html_size: Optional[int]
        _previous_html_size: Optional[int]
        _rust_render_timing: Any
        _sync_done_this_cycle: bool

        def get_context_data(self, **kwargs: Any) -> Dict[str, Any]: ...

        def _apply_context_processors(
            self, context: Dict[str, Any], request: Any
        ) -> Dict[str, Any]: ...

        def _initialize_rust_view(self, request: Any = None) -> None: ...

        def _sync_state_to_rust(
            self, preloaded_context: Optional[Dict[str, Any]] = None
        ) -> None: ...

        def _record_dj_model_fields_from_rust(self, rust_view: Any) -> None: ...

        def _hydrate_react_components(self, html: str) -> str: ...

        def _reset_temporary_assigns(self) -> None: ...

        def _extract_handler_metadata(self) -> Dict[str, Dict[str, Any]]: ...

    def get_template(self) -> str:
        """
        Get the Rust template source for this view.

        Supports template inheritance via {% extends %} and {% block %} tags.
        Templates are resolved using Rust template inheritance for performance.

        For templates with inheritance, extracts only [dj-root] content
        for VDOM tracking to avoid tracking the entire document.
        """
        if self.template:
            return self.template
        elif self.template_name:
            # Load the raw template source
            from django.template import loader

            template = loader.get_template(self.template_name)
            template_source = template.template.source

            # Check if template uses {% extends %} - if so, resolve inheritance in Rust
            if "{% extends" in template_source or "{%extends" in template_source:
                # Template directories in the EXACT same order Django searches.
                # Shared with ``render_full_template`` step 2 via the single
                # ``get_template_dirs()`` helper so the two paths can never drift
                # apart on which backends honor APP_DIRS (#1646 parallel-path
                # cure). Re-implementing this inline here ONLY recognized the
                # stock Django backend, so under djust's own backend (the
                # ``djust new`` scaffold) the app-template dirs were dropped and
                # ``resolve_template_inheritance`` raised "Template not found"
                # → swallowed → fragment-only on the initial GET (#1801).
                template_dirs_str = get_template_dirs()

                # Get the actual path Django resolved for verification
                django_resolved_path = (
                    template.origin.name
                    if hasattr(template, "origin") and template.origin
                    else None
                )

                # --- Resolve template inheritance in Rust ---
                # This is the ONLY step that legitimately needs the raw-template
                # fallback (a template that genuinely can't be resolved as a
                # standalone inheritance document). It is scoped to a tight
                # try/except that LOGS at WARNING so a resolution error can never
                # again silently degrade an ``{% extends %}`` page to
                # fragment-only without a trace (#1801). Post-resolution work
                # (VDOM extraction / whitespace strip) is OUTSIDE this try — if
                # that raises it is an unexpected framework bug that must surface,
                # not be swallowed.
                try:
                    from djust._rust import resolve_template_inheritance

                    resolved = resolve_template_inheritance(self.template_name, template_dirs_str)
                except Exception as e:
                    logger.warning(
                        "[LiveView] Template inheritance resolution failed for %s "
                        "(searched %d dirs); falling back to raw template source — "
                        "the initial HTTP GET will render the dj-root fragment "
                        "WITHOUT the base template <head> (#1801). Error: %s",
                        self.template_name,
                        len(template_dirs_str),
                        e,
                    )
                    # Set to None so render_full_template won't try to render a
                    # template that contains {% extends %} tags as a standalone
                    # document.
                    self._full_template = None
                    extracted = self._extract_liveview_root_with_wrapper(template_source)
                    extracted = self._strip_comments_and_whitespace(extracted)

                    logger.debug(
                        "[LiveView] Extracted and stripped liveview-root: %d chars (from %d chars)",
                        len(extracted),
                        len(template_source),
                    )
                    return extracted

                # Verify Rust found the same template as Django
                if django_resolved_path:
                    rust_would_find = None
                    for template_dir in template_dirs_str:
                        candidate = os.path.join(template_dir, self.template_name)
                        if os.path.exists(candidate):
                            rust_would_find = os.path.abspath(candidate)
                            break

                    if rust_would_find and os.path.abspath(django_resolved_path) != rust_would_find:
                        logger.warning(
                            "Template resolution mismatch! Django found: %s, "
                            "Rust found: %s, Template dirs order: %s...",
                            django_resolved_path,
                            rust_would_find,
                            template_dirs_str[:3],
                        )

                # Keep inheritance intact for the initial GET: source flattening
                # loses runtime block.super scopes and their parent bodies.
                self._full_template = "{% extends " + json.dumps(self.template_name) + " %}"

                # For VDOM tracking, prefer the child template source — it contains
                # the dj-root block directly without base template surrounding HTML,
                # making extraction simpler and immune to Issue #365 miscount.
                # Fall back to resolved if dj-root is only in the base template.
                #
                # Use the anchored-attribute regexes (NOT a naive substring): a
                # naive ``"dj-root" in template_source`` matches the token ANYWHERE
                # — including documentation/example code that merely *displays*
                # ``dj-root``/``dj-view`` as text, or another word containing it as
                # a substring (``adj-view``). When the real ``<div dj-root>`` lives
                # in the BASE template and the child only mentions the tokens in
                # text, the substring check wrongly picks the child as the VDOM
                # source → extraction finds no real dj-root → render_full_template
                # nests the whole page (two <!DOCTYPE>/two <footer>). The regexes
                # require a REAL ``<div ... dj-root/dj-view ...>`` tag (#1746).
                vdom_source = (
                    template_source
                    if _search_dj_root_open(template_source, _DJ_ROOT_RE, _DJ_VIEW_RE)
                    else resolved
                )
                vdom_template = self._extract_liveview_root_with_wrapper(vdom_source)

                # CRITICAL: Strip comments and whitespace from template BEFORE Rust VDOM sees it
                vdom_template = self._strip_comments_and_whitespace(vdom_template)

                logger.debug(
                    "[LiveView] Template inheritance resolved (%d chars), "
                    "extracted liveview-root for VDOM (%d chars)",
                    len(resolved),
                    len(vdom_template),
                )
                return vdom_template

            # No template inheritance - store full template and extract liveview-root for VDOM
            self._full_template = template_source
            extracted = self._extract_liveview_root_with_wrapper(template_source)
            extracted = self._strip_comments_and_whitespace(extracted)

            logger.debug(
                "[LiveView] No inheritance - extracted and stripped liveview-root: "
                "%d chars (from %d chars)",
                len(extracted),
                len(template_source),
            )
            return extracted
        else:
            raise ValueError("Either template_name or template must be set")

    def render(self, request: Optional["HttpRequest"] = None) -> str:
        """
        Render the view to HTML.

        Returns the rendered HTML from the template. For WebSocket updates,
        caller should use _extract_liveview_content() to get innerHTML only.

        After rendering, temporary_assigns and streams are reset to free memory.

        Args:
            request: The request object

        Returns:
            Rendered HTML with embedded handler metadata
        """
        self._initialize_rust_view(request)
        self._sync_state_to_rust()
        with library_render_scope():
            html = self._rust_view.render()

        # Record dj-model auto-allowlist from the TEMPLATE SOURCE (CWE-915
        # mass-assignment guard). Derived from the Rust template engine's parsed
        # AST (Text-node literals only) — immune to rendered-output poisoning;
        # see ModelBindingMixin._record_dj_model_fields_from_rust.
        self._record_dj_model_fields_from_rust(self._rust_view)

        # Post-process to hydrate React components
        html = self._hydrate_react_components(html)

        # Inject handler metadata for client-side decorators
        html = self._inject_handler_metadata(html, request=request)

        # Reset temporary assigns and streams to free memory after rendering
        self._reset_temporary_assigns()

        return html

    def _inject_handler_metadata(self, html: str, request: Optional["HttpRequest"] = None) -> str:
        """
        Inject handler metadata script into HTML.

        Adds a <script> tag that sets window.handlerMetadata with
        decorator metadata for all handlers. When ``request.csp_nonce`` is
        set (django-csp with ``CSP_INCLUDE_NONCE_IN``), the emitted script
        carries the nonce so apps can drop ``'unsafe-inline'`` from
        ``CSP_SCRIPT_SRC`` (see #655).
        """
        # Extract metadata
        metadata = self._extract_handler_metadata()

        # Skip injection if no metadata
        if not metadata:
            logger.debug("[LiveView] No handler metadata to inject, skipping script injection")
            return html

        logger.debug("[LiveView] Injecting handler metadata script for %s handlers", len(metadata))

        # CSP nonce support (#655): if a nonce is available on the request,
        # emit a nonce attribute so apps can drop 'unsafe-inline' from their
        # CSP script-src directive. Fall through to no-nonce output when
        # django-csp is not installed or the request doesn't carry one —
        # backward compatible with apps still using 'unsafe-inline'.
        req = request if request is not None else getattr(self, "request", None)
        from ..utils import get_csp_nonce

        nonce = get_csp_nonce(req)
        nonce_attr = f' nonce="{nonce}"' if nonce else ""

        # Build script tag
        script = f"""
<script{nonce_attr}>
// Handler metadata for client-side decorators
window.handlerMetadata = window.handlerMetadata || {{}};
Object.assign(window.handlerMetadata, {json.dumps(metadata)});
</script>"""

        # Inject once, before the document's real </body> (else its real
        # </html>, else at the end). #3018: this was
        # ``html.replace("</body>", …)``, which put the script before EVERY
        # ``</body>`` string — including one inside an inline script, a
        # comment or a <textarea>. The lookups run on the length-preserving
        # masked copy #3017 introduced for the CSRF meta and client scripts.
        from .post_processing import _find_body_close, _find_html_close, _mask_document_text

        masked = _mask_document_text(html)
        close = _find_body_close(masked)
        if close < 0:
            close = _find_html_close(masked)
        if close >= 0:
            html = f"{html[:close]}{script}\n{html[close:]}"
            logger.debug("[LiveView] Injected metadata script before the closing tag")
        else:
            html = html + script
            logger.debug("[LiveView] Appended metadata script to end of HTML")

        return html

    def _strip_comments_and_whitespace(self, html: str) -> str:
        """
        Strip HTML comments and normalize whitespace to match Rust VDOM parser behavior.

        IMPORTANT: Preserve whitespace inside <pre>, <code>, <textarea>, <script>,
        and <style> tags.

        IMPORTANT (#1927): ``<script>`` and ``<style>`` are whitespace-preserving
        in the Rust VDOM parser (``crates/djust_vdom/src/parser.rs:475`` —
        ``matches!(tag_ref, "pre" | "code" | "textarea" | "script" | "style")``),
        and this normalizer exists explicitly to "match Rust VDOM parser
        behavior" — yet it previously collapsed whitespace ONLY around
        ``<pre>``/``<code>``/``<textarea>``. The ``re.sub(r"\\s+", " ", html)``
        pass below turned every newline inside an inline ``<script>`` into a
        single space, collapsing the whole body onto ONE line. A ``//`` line
        comment then swallows the rest of the script, so an inline
        ``<script>`` inside the dj-root silently never executed — its
        ``addEventListener`` was never called, with no console error (the live
        symptom #1927/#1848's ``_runInsertedScripts`` fix could not cure
        because the script was already neutered before any morph re-execution).
        This is the #1646 parallel-path-drift twin of the Rust parser's
        preserve set: keep the two in lock-step. Collapsing whitespace inside a
        ``<style>`` block can likewise corrupt CSS, so both are preserved
        verbatim.

        IMPORTANT (#1678): Preserve ``<!--dj-if …-->`` / ``<!--/dj-if-->``
        boundary markers. These are load-bearing VDOM structure — the Rust
        parser counts them as significant children and the client differ
        resolves patch paths against them — NOT cosmetic comments. The Rust
        VDOM parser keeps them, so stripping them here desynced the hydrated
        mount HTML (and SSE / recovery HTML) from the server's ``last_vdom``:
        the client DOM lost every dj-if marker while the server vdom kept
        them, so on a multi-``{% if %}`` container (e.g. a tabbed dashboard
        with one ``{% if active_tab == X %}`` block per tab) the server's
        positional patch paths over-counted the client's children (index N vs
        a marker-less DOM) and every subsequent event fell back to
        ``html_recovery``.
        """
        preserved_blocks: list[str] = []

        def preserve_block(match: "re.Match[str]") -> str:
            preserved_blocks.append(match.group(0))
            return f"__PRESERVED_BLOCK_{len(preserved_blocks) - 1}__"

        # #1927: <script> and <style> are RAW-TEXT, whitespace-preserving
        # elements in the Rust VDOM parser (crates/djust_vdom/src/parser.rs:475),
        # and this normalizer exists to match Rust parser behavior. Their bodies
        # (JS / CSS) carry significant newlines — collapsing them breaks `//`
        # line comments in scripts (the whole single-lined body gets commented
        # out) and can corrupt CSS — so preserve them verbatim, exactly as the
        # Rust parser does. They are raw-text per the HTML spec (`<` inside is
        # literal, no nesting), so the non-greedy `.*?</tag>` match to the first
        # close tag is correct.
        #
        # CRITICAL ORDERING: extract <script>/<style> BEFORE the HTML-comment
        # strip below. A token that LOOKS like an HTML comment inside a raw-text
        # body (e.g. `var s = '<!-- x -->'` in JS, or `/* <!-- */` in CSS) is
        # literal text, NOT a comment — stripping it would corrupt the script.
        # Hiding raw-text blocks behind placeholders first makes the
        # comment-strip pass see only real markup comments. (pre/code/textarea
        # are NOT raw-text — a real `<!-- -->` inside them is a genuine comment
        # and is stripped as before, so they stay extracted AFTER the strip.)
        # End-tag patterns use ``</tag[^>]*>`` (not ``</tag>``): per the HTML5
        # tokenizer an end tag closes on ``</tag`` followed by whitespace, ``/``,
        # bogus attributes, or ``>`` — so ``</script >``, ``</script\n>`` and even
        # ``</script bar>`` all close a <script> in a browser. A bare ``</script>``
        # (or ``</script\s*>``) misses those forms, so the block isn't preserved
        # and the comment-strip below corrupts the JS/CSS body — CodeQL flags it
        # as ``py/bad-tag-filter`` (#2482). ``[^>]*`` matches every closing form.
        html = re.sub(
            r"<script[^>]*>.*?</script[^>]*>", preserve_block, html, flags=re.DOTALL | re.IGNORECASE
        )
        html = re.sub(
            r"<style[^>]*>.*?</style[^>]*>", preserve_block, html, flags=re.DOTALL | re.IGNORECASE
        )

        # Remove HTML comments — but NOT dj-if boundary markers (#1678). The
        # negative lookahead skips comments whose body is ``dj-if …`` or
        # ``/dj-if`` so they survive; all other comments are stripped.
        html = re.sub(r"<!--(?!\s*/?dj-if\b).*?-->", "", html, flags=re.DOTALL)

        # Preserve whitespace inside <pre>, <code>, and <textarea> tags
        html = re.sub(
            r"<pre[^>]*>.*?</pre[^>]*>", preserve_block, html, flags=re.DOTALL | re.IGNORECASE
        )
        html = re.sub(
            r"<code[^>]*>.*?</code[^>]*>", preserve_block, html, flags=re.DOTALL | re.IGNORECASE
        )
        html = re.sub(
            r"<textarea[^>]*>.*?</textarea[^>]*>",
            preserve_block,
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Normalize whitespace: collapse every run of HTML whitespace to one
        # space (#2999: HTML whitespace only — NBSP and other Unicode spaces
        # are content to the Rust parser and the browser, so ``\s`` was wrong).
        html = _HTML_WS_RUN_RE.sub(" ", html)

        # Then drop the space between two tags — and around/between preserved
        # blocks (#1737), whose placeholders hide their ``<`` — unless it sits
        # between two inline-level siblings, where it is the space between two
        # words (#2999: ``<b>A</b> <i>B</i>`` must not read "AB"). This is the
        # Rust parser's rule (``build_children`` in
        # ``crates/djust_vdom/src/parser.rs``): whitespace-only text is dropped
        # unless its nearest neighbour on each side is text or an inline-level
        # element, in which case it is kept as one ``" "`` node. The
        # placeholder cases matter for ``</strong> <code>`` — the ``<code>``
        # block is a placeholder here. Whitespace INSIDE a preserved block is
        # untouched (it's hidden behind the placeholder and restored verbatim
        # below), and whitespace adjacent to actual TEXT (e.g.
        # ``before <pre>``) is part of that text node and is left alone.
        # The decision lives in Rust, next to the parser's own rule
        # (djust_core::html_whitespace), so the two can't drift.
        from djust._rust import collapse_inter_tag_whitespace

        block_tags = []
        for block in preserved_blocks:
            m = _BLOCK_TAG_RE.match(block)
            block_tags.append(m.group(1).lower() if m else "")
        html = collapse_inter_tag_whitespace(html, block_tags)

        # Restore preserved blocks
        for i, block in enumerate(preserved_blocks):
            html = html.replace(f"__PRESERVED_BLOCK_{i}__", block)

        return html

    async def arender_chunks(
        self,
        full_html: str,
        emitter: "ChunkEmitter",
    ) -> None:
        """Async-generator producer that pushes shell-then-body chunks.

        PR-A foundation for v0.9.0 streaming (ADR-015). Replaces the
        synchronous regex-after-render path used by Phase 1
        (:meth:`_split_for_streaming` + :meth:`RequestMixin._make_streaming_response`)
        with a real async iterator. ``await asyncio.sleep(0)`` is used
        between chunks to yield control to the ASGI event loop so the
        shell can flush to the wire before the body chunks arrive at the
        consumer.

        Chunk schedule (4 yields when the page has a full document
        wrapper, fewer for fragment templates):

        1. ``shell_open`` — everything before ``<div dj-root>``
           (``<!DOCTYPE>``, ``<head>``, ``<body>`` open, top chrome).
        2. ``body_open`` — the ``<div dj-root>`` opening tag itself.
        3. ``body_content`` — the children of ``<div dj-root>``.
        4. ``body_close`` — ``</div>`` + ``</body></html>`` + trailing.

        The 4-chunk shape is invariant when ``<div dj-root>`` is present
        (covered by ``test_minimum_chunk_count_with_dj_root``). Templates
        without a ``<div dj-root>`` (raw fragments) yield a single chunk
        equivalent to the non-streaming path.

        Each yield routes through :meth:`ChunkEmitter.emit` so backpressure
        applies. When the emitter is cancelled
        (:meth:`ChunkEmitter.cancel`), :class:`~djust.http_streaming.ChunkEmitterCancelled`
        propagates out and the generator returns cleanly.

        :param full_html: Fully-rendered HTML string from
            :meth:`render_full_template` (post-injection of client script,
            handler metadata, ``dj-view`` attribute).
        :param emitter: Per-request :class:`ChunkEmitter` that this
            coroutine pushes chunks through. The chunks then flow out
            via ``emitter.__aiter__`` to the consumer (typically the
            ``StreamingHttpResponse`` async iterator wired in
            :meth:`RequestMixin.aget`).
        :returns: ``None``. This is a coroutine, not an async generator —
            chunks are delivered exclusively via ``emitter.emit()``.
        """
        from ..http_streaming import ChunkEmitterCancelled

        # Find <div dj-root> opening tag start.
        dj_root_match = _search_dj_root_open(full_html, _DJ_ROOT_RE)
        if not dj_root_match:
            # No dj-root wrapper: fragment template. Single-chunk fallback.
            try:
                await emitter.emit(full_html.encode("utf-8"))
            except ChunkEmitterCancelled:
                logger.debug("arender_chunks: cancelled during fragment emit")
            return

        dj_root_open_start = dj_root_match.start()
        dj_root_open_end = dj_root_match.end()

        # Find the matching close tag for the dj-root element using shared
        # logic. _find_closing_tag_pos balances nesting of the root's own
        # element name (#2892) AND (since #2663) masks script/style/comment
        # raw text, so the script-mask + </body> search the Phase-1 splitter
        # does is redundant here — the chunk boundary is the root's close
        # tag, not the </body>.
        result = _find_root_close(full_html, dj_root_match)
        if result[1] is None:
            # Malformed HTML (no closing </div> for dj-root). Fall back to
            # a single chunk so we never produce broken output.
            try:
                await emitter.emit(full_html.encode("utf-8"))
            except ChunkEmitterCancelled:
                logger.debug("arender_chunks: cancelled during fallback emit")
            return

        # result is (close_start, close_end); close_end is the index just
        # AFTER </div>. Slice the four pieces.
        shell_open = full_html[:dj_root_open_start]
        body_open = full_html[dj_root_open_start:dj_root_open_end]
        body_content = full_html[dj_root_open_end : result[0]]
        # body_close: from the closing </div> through end-of-document.
        body_close_chunk = full_html[result[0] :]

        try:
            # 1. Shell: <!DOCTYPE>, <head>, <body>, top chrome.
            if shell_open:
                await emitter.emit(shell_open.encode("utf-8"))
            # Yield to the loop so ASGI can flush the shell over the wire
            # before we proceed. PR-B uses this same await as the boundary
            # where lazy thunks become eligible to start rendering.
            await asyncio.sleep(0)

            # 2. Body open: <div dj-root ...> opening tag.
            if body_open:
                await emitter.emit(body_open.encode("utf-8"))
            await asyncio.sleep(0)

            # 3. Body content: dj-root children.
            if body_content:
                await emitter.emit(body_content.encode("utf-8"))
            await asyncio.sleep(0)

            # 4. Body close: </div></body></html> + trailing.
            if body_close_chunk:
                await emitter.emit(body_close_chunk.encode("utf-8"))
        except ChunkEmitterCancelled:
            logger.debug("arender_chunks: cancelled mid-stream")
            return

        # 5. Lazy thunks (PR-B + PR-C, ADR-015). The body of the page
        # has already flushed; lazy ``<template id="djl-fill-X">``
        # chunks are appended after </html>. Browsers tolerate
        # post-</html> content per the HTML5 parser tree-construction
        # spec — the template element + its inline <script> activator
        # move into the implicit body and execute in order.
        #
        # PR-C: parallel render via ``asyncio.as_completed``. All
        # thunks start concurrently; chunks emerge in completion order
        # rather than registration order. Total wall-clock time =
        # max(thunk_durations) instead of sum(thunk_durations). Client-
        # side reconciliation is keyed by slot id (``data-target``) so
        # out-of-order arrival is correct by construction.
        if not emitter.thunks:
            return

        # Per-thunk wrapper returns ``(view_id, result, exc)`` so the
        # surfacing task is unambiguously identified at completion
        # time. A naive ``next(t for t in task_to_id if t.done() and
        # t.exception() ...)`` recovery returns the FIRST done-with-
        # exception task — on multi-failure that attributes the wrong
        # view_id. Wrapping packages the identity at thunk-start time.
        async def _wrap(view_id: str, thunk_fn: Any) -> Tuple[str, Any, Optional[Exception]]:
            try:
                result = await thunk_fn()
            except asyncio.CancelledError:
                # Re-raise so as_completed sees the cancellation
                # propagate; otherwise the wrapped task swallows the
                # cancel signal and the caller can't tell.
                raise
            except ChunkEmitterCancelled:
                raise
            except Exception as exc:  # noqa: BLE001 — captured for logging
                return (view_id, None, exc)
            return (view_id, result, None)

        thunk_tasks = [
            asyncio.ensure_future(_wrap(view_id, thunk_fn)) for view_id, thunk_fn in emitter.thunks
        ]

        def _cancel_pending() -> None:
            for task in thunk_tasks:
                if not task.done():
                    task.cancel()

        async def _drain_iterator(it: Any) -> None:
            """Drain a partially-consumed ``asyncio.as_completed``
            iterator so its internally-queued ``_wait_for_one``
            coroutines are awaited and don't trigger
            ``RuntimeWarning: coroutine '_wait_for_one' was never
            awaited`` (#1153).

            CPython's ``asyncio.as_completed`` is a generator that
            yields one ``_wait_for_one()`` coroutine per pending
            task. When we ``return`` mid-loop after an
            ``emitter.cancelled`` check, Python's for-protocol has
            ALREADY pulled the next coroutine via ``next()`` into
            ``completed`` — and any further pending coroutines the
            generator would have produced get discarded along with
            the generator itself.

            ``task.cancel()`` only schedules cancellation; it doesn't
            unblock ``_wait_for_one``'s ``done.get()`` from the
            ``as_completed`` queue. We need to keep iterating + await
            each remaining coroutine so the queue drains. Cancelled
            tasks raise ``CancelledError`` from ``f.result()`` inside
            ``_wait_for_one``; we swallow those, since the cancellation
            was self-inflicted via ``_cancel_pending``.
            """
            for remaining in it:
                try:
                    await remaining
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    # Swallow — we cancelled these tasks ourselves.
                    pass

        as_completed_iter = asyncio.as_completed(thunk_tasks)
        try:
            for completed in as_completed_iter:
                if emitter.cancelled:
                    _cancel_pending()
                    # Drain ``completed`` (already pulled by for-protocol)
                    # plus any further coroutines the iterator would
                    # produce, so no ``_wait_for_one`` is GC'd unawaited.
                    try:
                        await completed
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
                    await _drain_iterator(as_completed_iter)
                    return
                try:
                    view_id, chunk_bytes, exc = await completed
                except ChunkEmitterCancelled:
                    raise
                except asyncio.CancelledError:
                    # Suppressing CancelledError is safe HERE because
                    # this is an INNER thunk task being cancelled by
                    # ``_cancel_pending`` (in response to the outer
                    # emitter cancel). The outer ``arender_chunks``
                    # coroutine's own cancellation propagates via the
                    # next iteration's ``emitter.cancelled`` check and
                    # then the outer ``except ChunkEmitterCancelled``
                    # branch. Re-raising here would short-circuit that.
                    continue
                if exc is not None:
                    from .._exposure_diagnostics import log_failure_for

                    log_failure_for(
                        logger,
                        (self,),
                        exc,
                        "arender_chunks: lazy thunk raised for view_id=%s; "
                        "thunks should catch + emit error envelope themselves",
                        view_id,
                        traceback=True,
                    )
                    continue
                if chunk_bytes is None:
                    continue
                if not isinstance(chunk_bytes, (bytes, bytearray)):
                    chunk_bytes = chunk_bytes.encode("utf-8")
                await emitter.emit(chunk_bytes)
                # Yield between fills so each chunk has a chance to
                # leave the wire before the next completed task is
                # picked up.
                await asyncio.sleep(0)
        except ChunkEmitterCancelled:
            _cancel_pending()
            await _drain_iterator(as_completed_iter)
            logger.debug("arender_chunks: cancelled during lazy phase")
            return
        finally:
            # Defensive — cancel + drain any remaining pending tasks so
            # we don't leak ``coroutine '_wait_for_one' was never
            # awaited`` warnings on paths that bypass the explicit
            # cancellation branches above (e.g. the emit call raising
            # something we don't catch). ``_cancel_pending`` is
            # idempotent; ``_drain_iterator`` is a no-op once the
            # generator is exhausted.
            _cancel_pending()
            try:
                await _drain_iterator(as_completed_iter)
            except Exception:  # noqa: BLE001
                # Best-effort drain — never raise from finally.
                pass

    def _split_for_streaming(self, full_html: str) -> Tuple[str, str, str]:
        """Split rendered HTML into ``(shell_open, main_content, shell_close)``.

        .. deprecated:: 0.9.0
            This synchronous splitter is the Phase 1 (v0.6.1) regex-split-
            after-render path. PR-A introduces :meth:`arender_chunks`, an
            async generator that yields the same chunks with
            ``await asyncio.sleep(0)`` boundaries between them so the
            shell flushes over the ASGI socket before the body bytes are
            queued. This sync helper is retained for the WSGI fallback in
            :meth:`RequestMixin._make_streaming_response`.

        Used by :meth:`_make_streaming_response` to flush the page shell to
        the browser before the main LiveView body is written. The browser
        begins parsing ``<head>`` and loading CSS/JS as soon as the first
        chunk arrives, competitive with Next.js ``renderToPipeableStream``.

        Split boundaries:

        - ``shell_open`` — everything before the outermost ``<div dj-root>``.
        - ``main_content`` — the ``<div dj-root>...</div>`` block plus any
          markup between that closing div and the closing ``</body>``.
        - ``shell_close`` — ``</body></html>`` + any trailing markup.

        Edge cases:

        - HTML without a ``<div dj-root>`` (e.g. a minimal template without
          a document wrapper) returns ``(full_html, "", "")`` so streaming
          falls back to a single-chunk response equivalent to the
          non-streaming path.
        - HTML with a ``<div dj-root>`` but no ``</body>`` returns
          ``(shell_open, main_content, "")`` — the main chunk runs to the
          end of the document.

        The ``dj-root`` match is case-insensitive, precise (hyphenated
        suffixes like ``dj-root-other`` do NOT match), and the
        ``</body>`` match tolerates trailing whitespace (``</body >``).
        ``</body>`` tokens appearing as literal string content inside a
        ``<script>...</script>`` block are skipped so they don't create
        a false split boundary.

        :param full_html: Fully-rendered HTML as returned by
            :meth:`render` / the GET handler.
        :returns: Three-tuple of string chunks that, when concatenated,
            equal ``full_html``.
        """
        m = _search_dj_root_open(full_html, _DJ_ROOT_RE)
        if not m:
            return full_html, "", ""

        dj_root_start = m.start()

        # Mask the raw-text regions (script/style bodies, comments), preserving
        # string length via NUL fill, so a literal "</body>" inside a JS string
        # doesn't get picked up as the real body close. Search the masked
        # tail, then translate the hit position back into the original string.
        # #3019: this used its own ``<script\b[^>]*>.*?</script[^>]*>`` mask,
        # which took 22 s on 32 000 unclosed ``<script>`` tags; the shared
        # masker is linear (an unterminated region runs to the end, as in the
        # HTML tokenizer).
        tail = full_html[dj_root_start:]
        masked_tail = _mask_raw_text(tail)
        body_close = _BODY_CLOSE_RE.search(masked_tail)
        if not body_close:
            return full_html[:dj_root_start], full_html[dj_root_start:], ""

        abs_close = dj_root_start + body_close.start()
        shell_open = full_html[:dj_root_start]
        main_content = full_html[dj_root_start:abs_close]
        shell_close = full_html[abs_close:]
        return shell_open, main_content, shell_close

    def _extract_liveview_content(self, html: str) -> str:
        """
        Extract the inner content of [dj-root] from full HTML.

        This ensures the HTML sent over WebSocket matches what the client expects:
        just the content to insert into the existing [dj-root] container.

        Falls back to [dj-view] if [dj-root] is not present, since dj-root
        is auto-inferred from dj-view (see PR #297).
        """
        # Find the opening tag for [dj-root], falling back to [dj-view]
        opening_match = _search_dj_root_open(html, _DJ_ROOT_RE, _DJ_VIEW_RE)

        if not opening_match:
            return html

        start_pos = opening_match.end()

        result = _find_root_close(html, opening_match)
        if result[0] is not None:
            return html[start_pos : result[0]]
        return html

    def _extract_liveview_root_with_wrapper(self, template: str) -> str:
        """
        Extract the <div dj-root>...</div> section from a template (WITH the wrapper div).

        Falls back to [dj-view] if [dj-root] is not present, since dj-root
        is auto-inferred from dj-view (see PR #297).
        """
        opening_match = _search_dj_root_open(template, _DJ_ROOT_RE, _DJ_VIEW_RE)

        if not opening_match:
            return template

        start_pos = opening_match.start()

        result = _find_root_close(template, opening_match)
        if result[1] is not None:
            return template[start_pos : result[1]]
        return template

    def _extract_liveview_template_content(self, template: str) -> str:
        """
        Extract the innerHTML of [dj-root] from a TEMPLATE (not rendered HTML).

        Falls back to [dj-view] if [dj-root] is not present.
        """
        opening_match = _search_dj_root_open(template, _DJ_ROOT_RE, _DJ_VIEW_RE)

        if not opening_match:
            return template

        start_pos = opening_match.end()

        result = _find_root_close(template, opening_match)
        if result[0] is not None:
            return template[start_pos : result[0]]
        return template

    @staticmethod
    def _find_closing_div_pos(
        template: str, inner_start: int
    ) -> "tuple[int, int] | tuple[None, None]":
        """Find the ``</div>`` that closes the div opened just before
        ``inner_start``. The ``div`` case of :meth:`_find_closing_tag_pos`."""
        return TemplateMixin._find_closing_tag_pos(template, inner_start, "div")

    @staticmethod
    def _find_closing_tag_pos(
        template: str, inner_start: int, tag: str
    ) -> "tuple[int, int] | tuple[None, None]":
        """
        Find the ``</tag>`` that closes the ``<tag>`` opened just before
        inner_start, balancing nested elements of the SAME name (#2892 — the
        root may be any element, not only a ``<div>``).

        Returns (close_start, close_end) or (None, None) if not found.

        Handles Django {% if/else/elif/endif %} branching correctly: when
        {% else %} or {% elif %} is encountered, depth is restored to what
        it was at the matching {% if %}, so mutually-exclusive branches that
        each open a <div> are counted only once.
        """
        # #2663: scan a raw-text-masked copy. ``<script>``/``<style>`` bodies
        # and HTML comments are text, so a ``<div`` or ``</div>`` inside them
        # must not move the depth counter. The mask is length-preserving, so
        # every offset computed below indexes the caller's ``template``.
        template = _mask_raw_text(template)

        # Pre-scan for if/elif/else/endif tags in the region being searched.
        flow_tags = [
            (inner_start + m.start(), inner_start + m.end(), m.group(1))
            for m in re.finditer(
                r"\{%-?\s*(if|elif|else|endif)\b.*?-?%\}",
                template[inner_start:],
                re.DOTALL,
            )
        ]

        # The name must end at a tag boundary: ``<section-x>`` (a custom
        # element) is not a ``<section>``. ``<div\b`` alone matched
        # ``<div-foo>``. ``{`` is a boundary too, so ``<div{{ attrs }}>`` /
        # ``<div{% if x %} ...>`` in template SOURCE still count as opens.
        open_re, close_re = _open_close_res(tag)

        branch_stack: list[int] = []
        depth = 1
        pos = inner_start

        while depth > 0 and pos < len(template):
            open_match = open_re.search(template, pos)
            # Tolerate whitespace before '>' (``</div >`` / ``</div\n>``). A
            # plain ``</div>`` missed those, over-counting depth so the close
            # was never found — the close-side twin of the #1749 open-side
            # under-count. ``close_match.end()`` consumes the full tag incl.
            # trailing whitespace, so splice points stay correct. (#1751)
            close_match = close_re.search(template, pos)

            if close_match is None:
                break

            close_pos = close_match.start()
            open_pos = open_match.start() if open_match else float("inf")
            next_pos = min(open_pos, close_pos)  # type: ignore[type-var]

            # Process any flow-control tags that fall before the next div tag.
            pending = [(ts, te, tt) for ts, te, tt in flow_tags if pos <= ts < next_pos]
            if pending:
                ts, te, tag_type = pending[0]
                if tag_type == "if":
                    branch_stack.append(depth)
                elif tag_type in ("else", "elif"):
                    if branch_stack:
                        depth = branch_stack[-1]  # undo this branch's depth changes
                elif tag_type == "endif":
                    if branch_stack:
                        branch_stack.pop()
                pos = te
                continue

            if open_pos < close_pos:
                depth += 1
                # open_pos < close_pos (an int) implies open_pos is the real
                # int match position, never the float("inf") sentinel.
                pos = int(open_pos) + 1 + len(tag)
            else:
                depth -= 1
                if depth == 0:
                    return close_pos, close_match.end()
                pos = close_match.end()

        return None, None

    def _strip_liveview_root_in_html(self, html: str) -> str:
        """
        Strip comments and whitespace from [dj-root] div in full HTML page.

        Falls back to [dj-view] if [dj-root] is not present.
        """
        opening_match = _search_dj_root_open(html, _DJ_ROOT_RE, _DJ_VIEW_RE)

        if not opening_match:
            return html

        start_pos = opening_match.start()

        result = _find_root_close(html, opening_match)
        if result[1] is not None:
            liveview_div = html[start_pos : result[1]]
            stripped_div = self._strip_comments_and_whitespace(liveview_div)
            return html[:start_pos] + stripped_div + html[result[1] :]
        return html

    @reconcile_child_render(whole_page=True)
    def render_full_template(
        self,
        request: Optional["HttpRequest"] = None,
        serialized_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Render the full template including base template inheritance.
        Used for initial GET requests when using template inheritance.

        Architecture (post-#1370 fix): the page shell (DOCTYPE, head, nav,
        footer — everything from ``{% extends %}``) is rendered by a temporary
        Rust renderer from ``self._full_template``. The ``dj-root`` portion
        is then REPLACED with the output of ``self._rust_view.render()`` —
        the SAME instance the WS path uses. This guarantees marker IDs match
        between the initial HTTP-rendered DOM and subsequent WS diffs.

        Args:
            request: HTTP request object
            serialized_context: Optional pre-serialized context dict

        Returns the complete HTML document (DOCTYPE, html, head, body, etc.)
        """
        # #1784: register self as the active parent view for the duration of
        # this render so an embedded ``{% live_render ... %}`` tag can find its
        # parent during the Rust shell/dj-root render. The Rust engine renders
        # from a JSON-serialized context that structurally cannot carry the
        # live ``LiveView`` object, so without this thread-local the tag raised
        # ``TemplateSyntaxError`` ("no parent view in the current render
        # context") and the initial HTTP GET 500'd. The context manager
        # save/restores so it never leaks across requests/threads, even on
        # error.
        from ..templatetags.live_tags import active_parent_view

        with active_parent_view(self):
            return self._render_full_template_inner(request, serialized_context)

    def _render_full_template_inner(
        self,
        request: Optional["HttpRequest"] = None,
        serialized_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Body of :meth:`render_full_template`. Split out so the
        active-parent-view thread-local (#1784) wraps the entire render in a
        ``with`` block without indenting the whole method."""
        if hasattr(self, "_full_template") and self._full_template:
            # --- Step 1: Render the dj-root content via self._rust_view ---
            # This is the SAME instance the WS path will use for diffing,
            # so marker IDs (if-<8hex>-N) are guaranteed to match.
            # IMPORTANT: do NOT inject handler metadata here — it appends a
            # <script> element that the VDOM doesn't know about, which shifts
            # child indices and breaks path-based patch resolution (#1370).
            # Handler metadata is injected into the page shell (step 3) AFTER
            # the dj-root replacement, so it lives OUTSIDE the diffed subtree.
            self._initialize_rust_view(request)
            self._sync_state_to_rust()
            with library_render_scope():
                liveview_html = self._rust_view.render()
            # Record dj-model auto-allowlist from the TEMPLATE SOURCE (CWE-915
            # mass-assignment guard). Derived from the Rust template AST
            # (Text-node literals) — reflects exactly the developer-exposed
            # static bindings; immune to rendered-output poisoning.
            self._record_dj_model_fields_from_rust(self._rust_view)
            liveview_html = self._hydrate_react_components(liveview_html)

            # #1737: normalize the rendered dj-root so the initial-GET output
            # is structurally identical to the first WS frame. The WS path
            # (``render_with_diff`` → Rust ``render_with_diff()``) applies an
            # additional whitespace pass that plain Rust ``render()`` does NOT
            # — e.g. the single spaces a normalized template keeps around
            # ``{% if %}`` tags survive ``render()`` as ``> <!--dj-if--> <``
            # but are collapsed to ``><!--dj-if-->`` by ``render_with_diff()``.
            # Applying the SAME ``_strip_comments_and_whitespace()`` the
            # template path uses (get_template():154/172/184) converges the
            # two: ``dj-if`` boundary markers are preserved (negative
            # lookahead in the normalizer), ``<pre>``/``<code>``/``<textarea>``
            # whitespace is preserved, and the only residual difference is the
            # dj-id attrs the client stamps onto the prerender DOM (#1610).
            liveview_html = self._strip_comments_and_whitespace(liveview_html)

            # --- Step 2: Render the page shell from _full_template ---
            from djust._rust import RustLiveView

            template_dirs = get_template_dirs()
            temp_rust = RustLiveView(self._full_template, template_dirs)

            safe_keys = []
            context_for_sidecar: Optional[Dict[str, Any]] = None
            if serialized_context is not None:
                from ..serialization import normalize_django_value
                from ..mixins.rust_bridge import _collect_safe_keys

                # #3061: the HTTP GET passes the context WITH processor output
                # applied. Keep the non-serializable request-scoped values
                # out of the normalizer, as ``_sync_state_to_rust`` does for
                # the dj-root (#1786); they reach the shell via the sidecar.
                json_compatible_context = normalize_django_value(
                    _drop_request_scoped_values(self, serialized_context)
                )
                for key, value in json_compatible_context.items():
                    safe_keys.extend(_collect_safe_keys(value, key))
            else:
                context = self.get_context_data()
                context = self._apply_context_processors(context, request)

                from django.http import HttpRequest

                # A `Component`/`LiveComponent`-specific branch used to render
                # eagerly and replace the value with `{"render": html}` here —
                # the SAME wrapper-dict shape #2503 removed from
                # `rust_bridge.py::_sync_state_to_rust`, and the identical bug:
                # `{{ c }}` on THIS page-shell path rendered the dict's Python
                # repr, not the component, because there were no remaining
                # path segments for `Context::get` to resolve past the dict it
                # hit. Caught by Stage 11 review of #2512 as a parallel-path
                # twin (#1646) — this branch was never touched by that PR.
                #
                # Removed, matching the sibling `if serialized_context is not
                # None:` branch above, which never special-cased Component at
                # all: pass the raw context straight to
                # `normalize_django_value`, whose Component-aware arm calls
                # `str(value)` (the SafeString `__str__` → `render()` chain)
                # directly. `str(value.render())` here explicitly stripped
                # the SafeString marker `render()` returns — a second,
                # independent bug this removal also closes, since
                # `normalize_django_value` preserves it.
                #
                # `.render` (the dotted spelling) resolves on this path since
                # #2589 wired the raw-Python sidecar onto `temp_rust` (below,
                # `_set_shell_sidecar`) on BOTH branches — a miss on the
                # eager dict falls through to the #2501 attribute walk, as it
                # does on the dj-root / WS render.
                rendered_context = {
                    key: value
                    for key, value in _drop_request_scoped_values(self, context).items()
                    if not isinstance(value, HttpRequest)
                }
                context_for_sidecar = context

                from ..serialization import normalize_django_value
                from ..mixins.rust_bridge import _collect_safe_keys

                json_compatible_context = normalize_django_value(rendered_context)
                for key, value in json_compatible_context.items():
                    safe_keys.extend(_collect_safe_keys(value, key))

            temp_rust.update_state(json_compatible_context)
            if safe_keys:
                temp_rust.mark_safe_keys(safe_keys)
            # #2589: the page shell gets the SAME raw-Python sidecar the
            # dj-root / WS render gets (``_sync_state_to_rust``), so
            # ``{% querystring %}`` sees ``request`` and ``{{ obj.prop }}``
            # resolves through the #2501 attribute walk here too (#1646).
            # ``build_render_sidecar`` is the shared builder the three
            # non-LiveView entries use; it protects models behind the
            # serialization floor. ``request`` is request-scoped: it rides
            # the sidecar only and never enters ``update_state``.
            self._set_shell_sidecar(temp_rust, request, serialized_context, context_for_sidecar)
            with library_render_scope():
                shell_html = temp_rust.render()

            # --- Step 3: Replace the ENTIRE dj-root div in the shell ---
            # liveview_html already includes its own <div dj-root>...</div>
            # wrapper (since get_template() returns the dj-root template).
            # Replace the shell's <div dj-root>...</div> ENTIRELY (opening
            # tag through closing tag) with liveview_html.
            #
            # #1737: fall back to ``dj-view`` when no literal ``dj-root``
            # attribute is present (the auto-inferred-dj-root case). Without
            # this fallback the replacement misses the root entirely and we
            # return the un-normalized ``_full_template`` shell — leaving
            # comment nodes + as-authored whitespace in the initial-GET
            # dj-root that the WS frame (``render_with_diff`` →
            # ``_strip_comments_and_whitespace`` at get_template()) has
            # already stripped. The structural mismatch is what makes the
            # client's first-hydration ``morphChildren`` rebuild the subtree
            # (visible flash). ``liveview_html`` is rendered from the SAME
            # normalized ``self._rust_view`` the WS path uses, so the
            # replaced dj-root is structurally identical to the first WS
            # frame (modulo the dj-id attrs the client stamps on, per #1610).
            dj_root_match = _search_dj_root_open(shell_html, _DJ_ROOT_RE, _DJ_VIEW_RE)
            if dj_root_match:
                # Start of the root element's opening tag (any element, #2892)
                tag_start = dj_root_match.start()
                # Find the matching close tag via the shared scanner instead of
                # a duplicate hand-rolled depth loop. _find_closing_tag_pos is
                # multi-line-safe on the open side (#1750) and
                # whitespace-tolerant on the close side (#1751), and balances
                # the root's own element name (#2892). The rendered shell
                # carries no ``{% %}`` tags, so the helper's if/else branch
                # handling is inert here; this is purely the balanced scan.
                # Removing the second scanner closes the parallel-path-drift
                # gap (#1646) that let the open-side bug exist in one copy and
                # not the other.
                _close_start, close_end = _find_root_close(shell_html, dj_root_match)
                if close_end is not None:
                    result = shell_html[:tag_start] + liveview_html + shell_html[close_end:]
                    result = self._inject_handler_metadata(result, request=request)
                    return result

            # Fallback: no usable root in the shell. Harmless for a page with
            # no root at all; for a page that DECLARES one it means the
            # normalisation was skipped and every patch will miss (#2892) —
            # say so instead of degrading silently.
            self._warn_unmatched_root(shell_html, found=dj_root_match is not None)
            shell_html = self._inject_handler_metadata(shell_html, request=request)
            return shell_html
        else:
            return self.render(request)

    @staticmethod
    def _stamp_dj_view(html: str, view_path: str) -> str:
        """Add ``dj-view="<view_path>"`` to every real ``dj-root`` open tag that
        has no ``dj-view`` of its own, so the client knows what to mount (#2981).

        The attribute goes right after ``dj-root`` (and its value, if any), so
        the one spelling the old literal replace handled — ``<div dj-root>`` —
        still renders byte-identically as ``<div dj-root dj-view="...">``.
        Tags inside ``<script>``/``<style>`` bodies and HTML comments are text
        and are left alone (#2663).
        """
        attr = ' dj-view="%s"' % _html_escape(view_path, quote=True)
        masked = _mask_raw_text(html)
        # #3155: a dj-root inside an embedded {% live_render %} child is the
        # child's, not this view's — stamping this view's path there would
        # tell the client to mount the page a second time inside the child.
        embedded = _embedded_child_spans(html, _mask_for_root_search(html))
        parts: list[str] = []
        last = 0
        for m in _DJ_ROOT_RE.finditer(masked):
            if embedded and _span_containing(embedded, m.start()) is not None:
                continue
            tag = html[m.start() : m.end()]
            root_attr_end: Optional[int] = None
            has_view = False
            for tok in _ROOT_ATTR_TOKEN_RE.finditer(tag):
                kind = tok.group(1)
                if kind is None:
                    continue  # a quoted attribute value — text, skip it
                if kind.lower() == "view":
                    has_view = True
                elif root_attr_end is None:
                    root_attr_end = tok.end()
            if has_view or root_attr_end is None:
                continue
            insert_at = m.start() + root_attr_end
            parts.append(html[last:insert_at])
            parts.append(attr)
            last = insert_at
        if not parts:
            return html
        parts.append(html[last:])
        return "".join(parts)

    def _warn_unmatched_root(self, shell_html: str, found: bool) -> None:
        """Log once per view class when the page declares a dj-root/dj-view
        root that the initial-GET normalisation could not use (#2892)."""
        if not found and not _ANY_ROOT_ATTR_RE.search(_mask_raw_text(shell_html)):
            return  # No root declared: a plain fragment page, nothing to say.
        label = "%s.%s" % (
            getattr(type(self), "__module__", "?"),
            getattr(type(self), "__qualname__", "?"),
        )
        if label in _UNMATCHED_ROOT_WARNED:
            return
        _UNMATCHED_ROOT_WARNED.add(label)
        reason = (
            "its closing tag was not found"
            if found
            else "it is on <html>, <head> or <body>, which is not supported"
        )
        logger.warning(
            "[LiveView] %s: the page declares a dj-root/dj-view root but %s. "
            "The initial render was NOT normalised to match the WebSocket frame, "
            "so patches will fail and fall back to full re-renders. Put dj-root "
            "on an element inside <body> with a matching close tag (#2892).",
            label,
            reason,
        )

    def _set_shell_sidecar(
        self,
        temp_rust: Any,
        request: Optional["HttpRequest"],
        serialized_context: Optional[Dict[str, Any]],
        raw_context: Optional[Dict[str, Any]],
    ) -> None:
        """Wire the raw-Python sidecar onto the page-shell renderer (#2589).

        Source of raw objects, in precedence order: the view's pre-processor
        context (``_cached_context`` — the HTTP-GET entry normalizes models to
        dicts BEFORE passing ``serialized_context``, so the raw models live
        only there), then whatever the caller passed, then ``request``.
        """
        if not hasattr(temp_rust, "set_raw_py_values"):
            return
        source: Dict[str, Any] = {}
        if serialized_context is not None:
            source.update(serialized_context)
        if raw_context is not None:
            source.update(raw_context)
        cached = getattr(self, "_cached_context", None)
        if cached:
            source.update(cached)
        if request is not None:
            source["request"] = request
        try:
            from ..serialization import build_render_sidecar

            temp_rust.set_raw_py_values(build_render_sidecar(source))
        except Exception as exc:
            from .._exposure_diagnostics import log_failure_for

            # The sidecar is built from template-context values; a serialization
            # error can carry them into the log (ADR-038).
            log_failure_for(
                logger,
                (self,),
                exc,
                "page-shell sidecar unavailable; object attribute lookups on the shell "
                "will resolve as empty",
                level="warning",
                traceback=True,
            )

    @reconcile_child_render()
    def render_with_diff(
        self,
        request: Optional["HttpRequest"] = None,
        extract_liveview_root: bool = False,
        preloaded_context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, Optional[str], int]:
        """
        Render the view and compute diff from last render.

        Args:
            extract_liveview_root: If True, extract innerHTML of [dj-root]
            preloaded_context: If provided, pass to _sync_state_to_rust
                instead of calling get_context_data() again. Used by the
                async websocket path where context was already awaited.

        Returns:
            Tuple of (html, patches_json, version)
        """
        logger.debug(
            "[LiveView] render_with_diff() called (extract_liveview_root=%s)",
            extract_liveview_root,
        )
        logger.debug("[LiveView] _rust_view before init: %s", self._rust_view)

        self._initialize_rust_view(request)

        # If template is a property (dynamic), update the template
        if hasattr(self.__class__, "template") and isinstance(
            getattr(self.__class__, "template"), property
        ):
            logger.debug("[LiveView] template is a property - updating template")
            new_template = self.get_template()
            self._rust_view.update_template(new_template)

        logger.debug("[LiveView] _rust_view after init: %s", self._rust_view)

        # Skip sync if already done this cycle (avoids double-sync which
        # causes false-positive id() changes and defeats the text fast path).
        if not getattr(self, "_sync_done_this_cycle", False):
            self._sync_state_to_rust(preloaded_context=preloaded_context)
        else:
            logger.debug(
                "[LiveView] _sync_done_this_cycle=True — SKIPPING sync (force_full=%s)",
                getattr(self, "_force_full_html", False),
            )
        self._sync_done_this_cycle = False  # Reset for next cycle

        # ADR-019: dispatch through the renderer abstraction. ViewRuntime
        # binds ``_djust_renderer`` to the view after mount when the
        # handshake selected a non-HTML renderer (LVN-I PR-3 / runtime.py).
        # Default fallback is a fresh HtmlRenderer per render — byte-
        # identical to the pre-LVN behavior.
        from ..renderers import HtmlRenderer

        # #1784: register self as the active parent view for the duration of
        # the diff render. ``RequestMixin.get`` calls this AFTER
        # ``render_full_template`` to establish the VDOM baseline, and the Rust
        # ``render_with_diff()`` re-runs any embedded ``{% live_render %}`` tag
        # against a JSON-serialized context that cannot carry the live parent
        # view — same gap ``render_full_template`` guards (parallel-path-drift,
        # per CLAUDE.md). The context manager save/restores so the WS path
        # (which already carries a real ``view``) is unaffected and the
        # thread-local never leaks. The guard wraps the renderer-abstraction
        # call (ADR-019) so it applies to HtmlRenderer and any future renderer.
        from ..templatetags.live_tags import active_parent_view

        renderer = getattr(self, "_djust_renderer", None) or HtmlRenderer(self)
        with active_parent_view(self), library_render_scope():
            result = renderer.render_with_diff(
                request=None,
                extract_liveview_root=False,
                preloaded_context=None,
            )
        html, patches_json, version = result

        # Record dj-model auto-allowlist from the TEMPLATE SOURCE (CWE-915
        # mass-assignment guard). This is the dominant render path — HTTP-GET
        # baseline, every WS mount, and every WS event re-render all funnel
        # through here — so the allowlist tracks the currently-exposed static
        # bindings on every render. Derived from the Rust template AST
        # (Text-node literals only via ``dj_model_fields()``), which reflects any
        # ``update_template`` done above for dynamic templates; immune to
        # rendered-output poisoning (text nodes, interpolated attrs, ``|safe``).
        self._record_dj_model_fields_from_rust(self._rust_view)

        # Capture per-phase Rust timing (render, parse, diff, serialize)
        self._rust_render_timing = self._rust_view.get_render_timing()

        logger.debug(
            "[LiveView] Rendered HTML length: %d chars, starts with: %s...",
            len(html),
            html[:100],
        )

        if extract_liveview_root:
            html = self._extract_liveview_content(html)
            logger.debug("[LiveView] Extracted [dj-root] content (%d chars)", len(html))

        logger.debug(
            "[LiveView] Rust returned: version=%d, patches=%s",
            version,
            "YES" if patches_json else "NO",
        )
        if not patches_json:
            logger.debug("[LiveView] NO PATCHES GENERATED!")
        else:
            from djust.config import config

            if config.get("debug_vdom", False):
                import json as json_module

                patches_list = json_module.loads(patches_json) if patches_json else []
                logger.debug("[LiveView] Generated %d patches:", len(patches_list))
                for i, patch in enumerate(patches_list[:5]):
                    patch_type = patch.get("type", "Unknown")
                    path = patch.get("path", [])

                    if patch_type == "SetAttr":
                        logger.debug(
                            "[LiveView]   Patch %d: %s '%s' = '%s' at path %s",
                            i,
                            patch_type,
                            patch.get("key"),
                            patch.get("value"),
                            path,
                        )
                    elif patch_type == "RemoveAttr":
                        logger.debug(
                            "[LiveView]   Patch %d: %s '%s' at path %s",
                            i,
                            patch_type,
                            patch.get("key"),
                            path,
                        )
                    elif patch_type == "SetText":
                        text_preview = patch.get("text", "")[:50]
                        logger.debug(
                            "[LiveView]   Patch %d: %s to '%s' at path %s",
                            i,
                            patch_type,
                            text_preview,
                            path,
                        )
                    else:
                        logger.debug("[LiveView]   Patch %d: %s", i, patch)

        # Track HTML sizes for diagnostics (used by full_html_update signal)
        self._previous_html_size = getattr(self, "_current_html_size", None)
        self._current_html_size = len(html)

        # Reset temporary assigns and streams to free memory after rendering
        self._reset_temporary_assigns()

        # ADR-036 R1: recovery targets come from what the server rendered.
        from ..validation import note_rendered_recovery_targets

        note_rendered_recovery_targets(self, html)

        return (html, patches_json, version)
