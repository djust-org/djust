"""The injected client bundle, its chunk URLs and the chunks a page uses.

Since #4007 the LiveView injects the CORE client bundle
(``djust/client-core[.min].js``), which loads the optional feature modules
(``djust/chunks/<name>[.min].js``) only on pages that use them. This module is
the server's half of that:

* :data:`CLIENT_CHUNKS` names the chunks ``scripts/build-client.sh`` builds
  (``CHUNK_SPECS``) and ``static/djust/src/13c-chunk-loader.js`` loads.
* :func:`client_bundle_urls` is the URL of the bundle the LiveView injects and
  of every chunk, exactly as the page requests them, for the client tag and
  for ``generate_sw``'s precache list.
* :func:`chunks_for_first_render` is the chunks the first render needs, so the
  page can preload them (#4320) and the core does not wait one round trip per
  chunk before it binds.

The markers here mirror ``_DJUST_CHUNK_SELECTORS`` / ``_DJUST_CHUNK_PAGE_TRIGGERS``
in the loader. A miss costs only the round trip the preload would have saved
(the client still finds and loads the chunk); a false hit costs one unused
download.
"""

from __future__ import annotations

import re
from typing import Callable, Dict, FrozenSet, Iterable, List, Tuple

# Kept in step with CHUNK_SPECS and the loader by
# tests/unit/test_client_chunks_4007.py.
CLIENT_CHUNKS: Tuple[str, ...] = (
    "uploads",
    "virtual-list",
    "cursor-overlay",
    "tutorial",
    "service-worker",
    # #4320
    "component-behaviors",
    "keyboard-nav",
    "error-overlay",
    "dj-style",
    "flip",
    "transitions",
    "infinite-scroll",
    "prefetch",
    "track-static",
    "dialog",
)


def static_url(path: str) -> str:
    """``static(path)``, or ``/static/<path>`` when staticfiles cannot resolve it."""
    from django.templatetags.static import static

    try:
        return str(static(path))
    except (ValueError, AttributeError):
        return f"/static/{path}"


def versioned_url(url: str) -> str:
    """``url`` with ``v=<djust version>`` in its query (#3658).

    A protocol reload fetches the bundle again; an unhashed static URL could be
    served from the browser cache and loop the reload. A hashed Manifest URL
    tolerates the query.
    """
    from urllib.parse import quote

    from . import __version__

    return f"{url}{'&' if '?' in url else '?'}v={quote(str(__version__), safe='')}"


def client_bundle_urls() -> Tuple[str, Dict[str, str]]:
    """The injected bundle's URL and ``{chunk name: URL}`` (empty for the full bundle).

    Minified unless ``DEBUG`` (``DJUST_CLIENT_JS_MINIFIED`` overrides);
    ``LIVEVIEW_CONFIG['client_bundle'] = 'full'`` selects the single bundle,
    which loads no chunks.
    """
    from django.conf import settings

    from .config import config

    use_min = getattr(settings, "DJUST_CLIENT_JS_MINIFIED", not settings.DEBUG)
    suffix = ".min.js" if use_min else ".js"
    full_bundle = config.get("client_bundle", "core") == "full"
    bundle = versioned_url(
        static_url(("djust/client" if full_bundle else "djust/client-core") + suffix)
    )
    if full_bundle:
        return bundle, {}
    return bundle, {
        name: versioned_url(static_url(f"djust/chunks/{name}{suffix}")) for name in CLIENT_CHUNKS
    }


# ---------------------------------------------------------------------------
# First-render detection (#4320)
# ---------------------------------------------------------------------------

# A start tag. ``[^<>]*`` keeps a tag from spanning into the next one and the
# scan linear (as post_processing's boundary regexes do).
_TAG_RE = re.compile(r"<([a-zA-Z][\w-]*)([^<>]*)>")
_CLASS_RE = re.compile(r"""\sclass\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+))""", re.I)
_ROLE_RE = re.compile(r"""\srole\s*=\s*["']?(dialog|tablist)(?=["'\s/]|$)""", re.I)


def _attr(*names: str) -> "re.Pattern[str]":
    alt = "|".join(re.escape(n) for n in names)
    return re.compile(r"\s(?:" + alt + r")(?=[\s=/]|$)", re.I)


def _classes(attrs: str) -> FrozenSet[str]:
    m = _CLASS_RE.search(attrs)
    if not m:
        return frozenset()
    return frozenset((m.group(1) or m.group(2) or m.group(3) or "").split())


_COMPONENT_CLASSES = frozenset(
    "rich-select combobox dj-theme-toggle popover-wrapper dj-popconfirm-wrapper collapsible "
    "ctx-wrapper code-block-copy dropzone kanban rte multi-select split-pane".split()
)
_KEYBOARD_CLASSES = frozenset("dj-modal dj-accordion dj-dropdown dj-dropdown-menu".split())

_UPLOAD = _attr("dj-upload", "dj-upload-drop")
_VIRTUAL = _attr("dj-virtual")
_CURSOR = re.compile(r"""\sdj-hook\s*=\s*["']?CursorOverlay(?=["'\s/]|$)""")
_TUTORIAL = re.compile(r"""\sid\s*=\s*["']?dj-tutorial-bubble(?=["'\s/]|$)""")
_FLIP = _attr("dj-flip")
_TRANSITIONS = _attr("dj-transition", "dj-remove", "dj-transition-group")
_VIEWPORT = _attr("dj-viewport-top", "dj-viewport-bottom")
_PREFETCH = _attr("dj-prefetch")
_TRACK_STATIC = _attr("dj-track-static")
_DIALOG = _attr("dj-dialog")

# name -> (substrings that must occur in the page, test on (tag name, attrs)).
_Matcher = Callable[[str, str], bool]
_MARKERS: Dict[str, Tuple[Tuple[str, ...], _Matcher]] = {
    "uploads": (("dj-upload",), lambda tag, attrs: bool(_UPLOAD.search(attrs))),
    "virtual-list": (("dj-virtual",), lambda tag, attrs: bool(_VIRTUAL.search(attrs))),
    "cursor-overlay": (("CursorOverlay",), lambda tag, attrs: bool(_CURSOR.search(attrs))),
    "tutorial": (("dj-tutorial-bubble",), lambda tag, attrs: bool(_TUTORIAL.search(attrs))),
    "component-behaviors": (
        tuple(sorted(_COMPONENT_CLASSES)),
        lambda tag, attrs: bool(_classes(attrs) & _COMPONENT_CLASSES),
    ),
    "keyboard-nav": (
        ("role=", "dj-modal", "dj-accordion", "dj-dropdown"),
        lambda tag, attrs: bool(_ROLE_RE.search(attrs) or _classes(attrs) & _KEYBOARD_CLASSES),
    ),
    "flip": (("dj-flip",), lambda tag, attrs: bool(_FLIP.search(attrs))),
    "transitions": (
        ("dj-transition", "dj-remove"),
        lambda tag, attrs: bool(_TRANSITIONS.search(attrs)),
    ),
    "infinite-scroll": (("dj-viewport-",), lambda tag, attrs: bool(_VIEWPORT.search(attrs))),
    "prefetch": (
        ("dj-prefetch",),
        lambda tag, attrs: tag == "a" and bool(_PREFETCH.search(attrs)),
    ),
    "track-static": (("dj-track-static",), lambda tag, attrs: bool(_TRACK_STATIC.search(attrs))),
    "dialog": (("dj-dialog",), lambda tag, attrs: tag == "dialog" and bool(_DIALOG.search(attrs))),
}


def chunks_in_markup(masked: str) -> FrozenSet[str]:
    """The chunks whose element markers occur in start tags of ``masked``.

    ``masked`` is the page with raw-text regions (scripts, styles, comments,
    title, textarea) masked, as post_processing's ``_mask_document_text``
    returns it, so a marker quoted in a script or a code sample is not a hit.
    A page that mentions none of the markers costs one substring test each.
    """
    candidates = [
        name for name, (needles, _) in _MARKERS.items() if any(n in masked for n in needles)
    ]
    if not candidates:
        return frozenset()
    found = set()
    for m in _TAG_RE.finditer(masked):
        tag = m.group(1).lower()
        attrs = m.group(2)
        for name in candidates:
            if name not in found and _MARKERS[name][1](tag, attrs):
                found.add(name)
        if len(found) == len(candidates):
            break
    return frozenset(found)


def chunks_for_first_render(
    masked: str, *, declares_uploads: bool, debug: bool, strict_styles: bool
) -> List[str]:
    """The chunks the first render of a page uses, in :data:`CLIENT_CHUNKS` order.

    Element markers come from the HTML; the page-level triggers from the view
    and settings: a view that declares uploads, ``DEBUG`` (the error overlay)
    and strict CSP styles (``dj-style``). A service worker's control of the
    page is a browser-side fact, so ``service-worker`` and the worker-driven
    part of ``prefetch`` are never preloaded.
    """
    wanted = set(chunks_in_markup(masked))
    if declares_uploads:
        wanted.add("uploads")
    if debug:
        wanted.add("error-overlay")
    if strict_styles:
        wanted.add("dj-style")
    return [name for name in CLIENT_CHUNKS if name in wanted]


def preload_links(urls: Iterable[str], nonce_attr: str) -> str:
    """``<link rel="preload" as="script">`` for each URL, carrying the nonce attribute.

    The chunks are classic scripts the loader inserts without ``crossorigin``,
    so the hint is ``preload``/``as="script"`` without one (``modulepreload``
    is for module scripts); the request mode then matches and the loader's
    ``<script>`` reuses the preloaded response.
    """
    from django.utils.html import escape

    return "".join(
        f'<link rel="preload" as="script" href="{escape(url)}"{nonce_attr}>' for url in urls
    )
