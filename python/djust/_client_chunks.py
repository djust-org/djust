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
# Inert markup the client never scans: <template> content is not in the
# document tree, and <noscript> content is text when scripts run.
_INERT_RE = re.compile(
    r"<(template|noscript)(?=[\s/>])[^<>]*>.*?(?:</\1[^<>]*>|\Z)", re.DOTALL | re.IGNORECASE
)


def _attr(*names: str) -> "re.Pattern[str]":
    alt = "|".join(re.escape(n) for n in names)
    return re.compile(r"\s(?:" + alt + r")(?=[\s=/]|$)", re.I)


def _classes(attrs: str) -> FrozenSet[str]:
    m = _CLASS_RE.search(attrs)
    if not m:
        return frozenset()
    return frozenset((m.group(1) or m.group(2) or m.group(3) or "").split())


def _words(words: Iterable[str]) -> str:
    """A regex for any of ``words`` as a whole class token / attribute name."""
    return r"(?<![\w-])(?:" + "|".join(re.escape(w) for w in words) + r")(?![\w-])"


_COMPONENT_CLASSES = frozenset(
    "rich-select combobox dj-theme-toggle popover-wrapper dj-popconfirm-wrapper collapsible "
    "ctx-wrapper code-block-copy dropzone kanban rte multi-select split-pane".split()
)
_KEYBOARD_CLASSES = frozenset("dj-modal dj-accordion dj-dropdown dj-dropdown-menu".split())

_UPLOAD = _attr("dj-upload", "dj-upload-drop")
_VIRTUAL = _attr("dj-virtual")
_CURSOR = re.compile(r"""\s(?i:dj-hook)\s*=\s*["']?CursorOverlay(?=["'\s/]|$)""")
_TUTORIAL = re.compile(r"""\s(?i:id)\s*=\s*["']?dj-tutorial-bubble(?=["'\s/]|$)""")
_FLIP = _attr("dj-flip")
_TRANSITIONS = _attr("dj-transition", "dj-remove", "dj-transition-group")
_VIEWPORT = _attr("dj-viewport-top", "dj-viewport-bottom")
_PREFETCH = _attr("dj-prefetch")
_TRACK_STATIC = _attr("dj-track-static")
_DIALOG = _attr("dj-dialog")

# name -> (the marker as a whole word anywhere in the page, as a regex source,
# test on (lower-cased tag name, attrs)). The regex documents the marker; the
# page pre-check is the literal words of _NEEDLES (#4363), and only the chunks
# it names get the tag scan.
_Matcher = Callable[[str, str], bool]
_MARKERS: Dict[str, Tuple[str, _Matcher]] = {
    "uploads": (
        _words(["dj-upload", "dj-upload-drop"]),
        lambda tag, attrs: bool(_UPLOAD.search(attrs)),
    ),
    "virtual-list": (_words(["dj-virtual"]), lambda tag, attrs: bool(_VIRTUAL.search(attrs))),
    "cursor-overlay": (r"(?-i:CursorOverlay)", lambda tag, attrs: bool(_CURSOR.search(attrs))),
    "tutorial": (_words(["dj-tutorial-bubble"]), lambda tag, attrs: bool(_TUTORIAL.search(attrs))),
    "component-behaviors": (
        _words(sorted(_COMPONENT_CLASSES)),
        lambda tag, attrs: bool(_classes(attrs) & _COMPONENT_CLASSES),
    ),
    "keyboard-nav": (
        r"""role\s*=\s*["']?(?:dialog|tablist)(?![\w-])|""" + _words(sorted(_KEYBOARD_CLASSES)),
        lambda tag, attrs: bool(_ROLE_RE.search(attrs) or _classes(attrs) & _KEYBOARD_CLASSES),
    ),
    "flip": (_words(["dj-flip"]), lambda tag, attrs: bool(_FLIP.search(attrs))),
    "transitions": (
        _words(["dj-transition", "dj-remove", "dj-transition-group"]),
        lambda tag, attrs: bool(_TRANSITIONS.search(attrs)),
    ),
    "infinite-scroll": (
        _words(["dj-viewport-top", "dj-viewport-bottom"]),
        lambda tag, attrs: bool(_VIEWPORT.search(attrs)),
    ),
    "prefetch": (
        _words(["dj-prefetch"]),
        lambda tag, attrs: tag == "a" and bool(_PREFETCH.search(attrs)),
    ),
    "track-static": (
        _words(["dj-track-static"]),
        lambda tag, attrs: bool(_TRACK_STATIC.search(attrs)),
    ),
    "dialog": (
        _words(["dj-dialog"]),
        lambda tag, attrs: tag == "dialog" and bool(_DIALOG.search(attrs)),
    ),
}

# The pre-check (#4363): per chunk, literal words, any of which the page must
# contain (lower-cased) for the chunk to be a candidate. A substring test per
# word replaced one alternation regex over the page, which cost ~0.25 us per
# character (20 ms on an 82 KB page). Each word must stand alone (no ``[\w-]``
# beside it), as in the regex; the pre-check may still let through a page the
# regex would not (``dialog`` outside a ``role=``), and the tag scan below stays
# the final check, so the result is the same.
_NEEDLES: Dict[str, Tuple[str, ...]] = {
    "uploads": ("dj-upload", "dj-upload-drop"),
    "virtual-list": ("dj-virtual",),
    "cursor-overlay": ("cursoroverlay",),
    "tutorial": ("dj-tutorial-bubble",),
    "component-behaviors": tuple(sorted(_COMPONENT_CLASSES)),
    "keyboard-nav": ("dialog", "tablist", *sorted(_KEYBOARD_CLASSES)),
    "flip": ("dj-flip",),
    "transitions": ("dj-transition", "dj-remove", "dj-transition-group"),
    "infinite-scroll": ("dj-viewport-top", "dj-viewport-bottom"),
    "prefetch": ("dj-prefetch",),
    "track-static": ("dj-track-static",),
    "dialog": ("dj-dialog",),
}


def _word_char(ch: str) -> bool:
    return ch.isalnum() or ch in "_-"


def _has_word(text: str, word: str) -> bool:
    """Whether ``word`` occurs in ``text`` as a whole word (no ``[\\w-]`` on either side).

    ``str.find`` per occurrence: the word is rare on most pages, so this is a
    few C-speed scans, not a regex step per character (#4363).
    """
    start = text.find(word)
    end_offset = len(word)
    while start != -1:
        end = start + end_offset
        if (start == 0 or not _word_char(text[start - 1])) and (
            end == len(text) or not _word_char(text[end])
        ):
            return True
        start = text.find(word, start + 1)
    return False


def chunks_in_markup(masked: str) -> FrozenSet[str]:
    """The chunks whose element markers occur in start tags of ``masked``.

    ``masked`` is the page with raw-text regions (scripts, styles, comments,
    title, textarea) masked, as post_processing's ``_mask_document_text``
    returns it, so a marker quoted in a script or a code sample is not a hit;
    ``<template>`` and ``<noscript>`` content is skipped here too. One compiled
    pre-check over the page (each chunk's marker words as whole words of the
    lower-cased page, found with ``str.find``, #4363) picks the candidate chunks; a page that names
    none of the markers costs that and no tag scan.
    """
    lowered = masked.lower()
    candidates = {
        name for name, words in _NEEDLES.items() if any(_has_word(lowered, w) for w in words)
    }
    if not candidates:
        return frozenset()
    markup = _INERT_RE.sub(" ", masked)
    found = set()
    for m in _TAG_RE.finditer(markup):
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
