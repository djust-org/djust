"""Page-shell fingerprint: when live navigation must become a full page load (#3036).

``dj-navigate`` / ``live_redirect`` swaps only the ``dj-root``. The document
around it (the ``<head>`` stylesheets and scripts, and any script placed
outside ``dj-root``) stays the PREVIOUS page's. A destination whose page shell
differs from the one the browser holds would render without its stylesheet or
its scripts, and nothing errors.

The server therefore sends a short fingerprint of the destination's page shell
on the ``live_redirect_mount`` reply, and renders the current page's
fingerprint into its ``<head>`` as ``<meta name="djust-page-shell">``. The
client compares them and, when they differ, does a normal page load instead of
the in-place swap. Pages with the same shell keep the fast path.

The fingerprint is a hash of what the page shell asks the browser to load,
taken from the page TEMPLATE (inheritance flattened), not from a rendered
response. That keeps it cheap, identical on the HTTP load and the redirect, and
independent of per-request values. It is a hash, so it carries no template or
view content to the client. A value interpolated into an asset URL
(``<script src="{{ cdn }}/x.js">``) is part of the template text, not of its
value, so two pages that differ only in that value look alike.

What counts:

* ``<link rel="stylesheet">`` and ``<link rel="modulepreload">``
* ``<script>`` (``src``, ``type``, and inline text) outside ``dj-root``
* ``<style>`` in ``<head>``
* ``{% include %}`` tags in ``<head>`` (the partial's contents are not read)

``<script>`` blocks that are data, not code (``application/ld+json``,
``application/json``, ``text/template``, ...), ``<meta>``, ``<title>`` and
everything inside ``dj-root`` do not count; they have their own paths
(``page_title``, the root swap) or never load anything.

Limits: the hash is of the template TEXT, so ``{{ block.super }}`` in an
overriding block is not expanded (the Rust inheritance flattening drops the
parent's content there), and a parent template that differs per tenant while
the child's file does not is not told apart.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Elements that never have children or an end tag.
_VOID = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

#: ``<link rel>`` values that load something the page needs.
_ASSET_RELS = frozenset({"stylesheet", "modulepreload"})

# Template syntax, not markup. Bounded, single-line (Django tags cannot span
# lines), so a stray ``{%`` cannot make the scan quadratic.
_TEMPLATE_COMMENT_RE = re.compile(r"\{#[^\n]{0,1000}?#\}")
_TEMPLATE_TAG_RE = re.compile(r"\{%[^\n]{0,1000}?%\}|\{\{[^\n]{0,1000}?\}\}")
_PLACEHOLDER_RE = re.compile(r"djtpl_([0-9a-f]+)_")
_INCLUDE_TAG_RE = re.compile(r"\{%-?\s*include\s")

#: ``<script type>`` values that run code or change how it loads. Anything
#: else (``application/ld+json``, ``application/json``, ``text/template``,
#: ``speculationrules``, ...) is data and never loads anything.
_EXECUTABLE_SCRIPT_TYPES = frozenset({"", "module", "importmap"})

#: Fingerprint length in hex digits (64 bits). A collision only means a
#: missed fallback, the behaviour before this existed.
_DIGEST_LEN = 16

#: ``(template_name, wrapper_template, origins) -> fingerprint``; see
#: :func:`page_shell`. ``_CONTENT_CACHE`` memoises :func:`shell_fingerprint` by
#: the content of its sources, which is what makes inline ``template`` views
#: cheap: their text is the key, so it can never be stale.
_CACHE: Dict[Any, Optional[str]] = {}
_CONTENT_CACHE: Dict[str, Optional[str]] = {}
_CACHE_MAX = 256


def _mask_template_syntax(source: str) -> str:
    """Make template syntax safe to hand to an HTML parser, keeping its text.

    ``href="{% static "a.css" %}"`` is valid Django, but an HTML parser ends the
    attribute at the inner quote and would see two different stylesheets as the
    same truncated value. Each ``{% ... %}`` / ``{{ ... }}`` becomes a
    quote-free, space-free token carrying the hex of its text, so the text still
    reaches the hash. ``{# ... #}`` comments render nothing and are dropped.
    """
    source = _TEMPLATE_COMMENT_RE.sub("", source)
    return _TEMPLATE_TAG_RE.sub(
        lambda m: "djtpl_" + m.group(0).encode("utf-8", "replace").hex() + "_", source
    )


def _unmask(text: str) -> str:
    return _PLACEHOLDER_RE.sub(lambda m: bytes.fromhex(m.group(1)).decode("utf-8", "replace"), text)


class _ShellCollector(HTMLParser):
    """Collect the page-shell entries of one template source."""

    def __init__(self, root_attr: str) -> None:
        super().__init__(convert_charrefs=True)
        self.root_attr = root_attr
        self.entries: List[List[str]] = []
        self.root_state = 0  # 0 not seen, 1 inside, 2 closed
        self._stack: List[str] = []
        self._root_depth = 0
        self._head_open = False
        self._capture: Optional[Tuple[str, Dict[str, str]]] = None
        self._text: List[str] = []

    # -- helpers -------------------------------------------------------

    @staticmethod
    def _attrs(raw: List[Tuple[str, Optional[str]]]) -> Dict[str, str]:
        return {name.lower(): (value or "") for name, value in raw}

    @staticmethod
    def _runs_code(script_type: str) -> bool:
        """Whether a ``<script type>`` loads or runs code (see the set above).

        A type that is itself template syntax is unknown, so it counts."""
        return (
            script_type in _EXECUTABLE_SCRIPT_TYPES
            or "javascript" in script_type
            or "ecmascript" in script_type
            or "djtpl_" in script_type
        )

    @property
    def _outside_root(self) -> bool:
        return self.root_state != 1

    # -- HTMLParser ----------------------------------------------------

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        # HTML has no self-closing syntax for non-void elements: ``<div/>`` opens.
        self.handle_starttag(tag, attrs)

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        attr = self._attrs(attrs)
        if tag in _VOID:
            if tag == "link" and self._outside_root:
                rels = set(attr.get("rel", "").lower().split())
                if rels & _ASSET_RELS:
                    self.entries.append(["link", " ".join(sorted(rels)), attr.get("href", "")])
            return
        if self.root_state == 0 and self.root_attr in attr:
            self.root_state = 1
            self._root_depth = len(self._stack)
        self._stack.append(tag)
        if tag == "head":
            self._head_open = True
        elif tag == "script" and self._outside_root:
            self._capture = ("script", attr)
            self._text = []
        elif tag == "style" and self._outside_root and self._head_open:
            self._capture = ("style", attr)
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._text.append(data)
        elif self._head_open and self._outside_root:
            for token in _PLACEHOLDER_RE.finditer(data):
                tag = _unmask(token.group(0))
                if _INCLUDE_TAG_RE.match(tag):
                    self.entries.append(["include", " ".join(tag.split())])

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag not in self._stack:
            return
        capture = self._capture
        if capture is not None and capture[0] == tag:
            text = " ".join("".join(self._text).split())
            attr = capture[1]
            script_type = attr.get("type", "").split(";")[0].strip().lower()
            if tag == "script" and not self._runs_code(script_type):
                pass
            elif tag == "script" and attr.get("src"):
                self.entries.append(["script", attr["src"], attr.get("type", "")])
            else:
                digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:_DIGEST_LEN]
                self.entries.append([f"{tag}-inline", attr.get("type", ""), digest])
            self._capture = None
            self._text = []
        # An end tag closes everything the template left open inside it.
        while self._stack:
            if self._stack.pop() == tag:
                break
        if tag == "head":
            self._head_open = False
        if self.root_state == 1 and len(self._stack) <= self._root_depth:
            self.root_state = 2


def _entries(source: str) -> Optional[List[List[str]]]:
    """The page-shell entries of ``source``, or None when it has no root."""
    source = _mask_template_syntax(source)
    for root_attr in ("dj-root", "dj-view"):
        collector = _ShellCollector(root_attr)
        try:
            collector.feed(source)
            collector.close()
        except (AssertionError, ValueError):
            # A partial shell must not be treated as a complete fingerprint.
            return None
        if collector.root_state:
            return collector.entries
    return None


def shell_fingerprint(*sources: Optional[str]) -> Optional[str]:
    """Fingerprint the page shell of template ``sources`` (view, then wrapper).

    None when no source has a ``dj-root`` / ``dj-view`` element to define
    "outside the root"; the client then keeps the fast path, as before.
    Memoised by the content of ``sources``.
    """
    texts = [source for source in sources if source]
    if not texts:
        return None
    key = hashlib.sha256("\0".join(texts).encode("utf-8", "replace")).hexdigest()
    if key in _CONTENT_CACHE:
        return _CONTENT_CACHE[key]
    parts: List[List[List[str]]] = []
    for source in texts:
        found = _entries(source)
        if found is not None:
            parts.append(found)
    fingerprint: Optional[str] = None
    if parts:
        blob = json.dumps(parts, separators=(",", ":"), sort_keys=True)
        fingerprint = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:_DIGEST_LEN]
    if len(_CONTENT_CACHE) >= _CACHE_MAX:
        _CONTENT_CACHE.clear()
    _CONTENT_CACHE[key] = fingerprint
    return fingerprint


def _origin(template_name: str) -> str:
    """The file ``template_name`` currently resolves to."""
    from django.template import loader

    template = loader.get_template(template_name)
    origin = getattr(getattr(template, "template", template), "origin", None)
    return str(getattr(origin, "name", "") or "")


def page_shell(view: Any) -> Optional[str]:
    """The page-shell fingerprint of ``view``'s page, or None. Never raises.

    The HTTP load and a ``live_redirect`` mount both call this with the view
    instance, so the two sides are computed identically.

    A view that names its template (``template_name``) is cached outside
    ``DEBUG``, where templates do not change under a running process;
    resolving the inheritance chain is the expensive part. The key is the name
    plus the file each name resolves to, so a project whose loader serves a
    different file per tenant gets one entry per file. An inline ``template``
    is memoised by its content in :func:`shell_fingerprint`.
    """
    try:
        from django.conf import settings

        inline = getattr(view, "template", None)
        name = None if inline else getattr(view, "template_name", None)
        wrapper = getattr(view, "wrapper_template", None) or None
        cacheable = bool(name) and not settings.DEBUG
        key = None
        if cacheable:
            key = (name, wrapper, _origin(name), _origin(wrapper) if wrapper else None)
            if key in _CACHE:
                return _CACHE[key]

        from .runtime import _view_document_source, template_source

        sources = [_view_document_source(view)]
        if wrapper:
            sources.append(template_source(wrapper))
        fingerprint = shell_fingerprint(*sources)
        if cacheable:
            if len(_CACHE) >= _CACHE_MAX:
                _CACHE.clear()
            _CACHE[key] = fingerprint
        return fingerprint
    except Exception as exc:  # noqa: BLE001 — a fingerprint must never break a page or a mount
        from ._exposure_diagnostics import log_failure_for

        # Template loading can raise errors that carry view values (ADR-038).
        log_failure_for(
            logger,
            (view,),
            exc,
            "page shell fingerprint unavailable for %s",
            type(view).__name__,
            level="debug",
            traceback=True,
        )
        return None
