"""A keyed digest of a rendered root, for the page-POST fallback (#4108).

The stateless page-POST builds a fresh view for every event, so it has no
earlier render to diff against. It can still answer with patches: it renders
once before the handler and once after, and sends the difference. That is
correct only if the render before the handler is what the browser shows. The
digest is how the server knows: the page GET and every fallback answer carry
the digest of the render they deliver, the client echoes the latest one with
its next fallback POST, and the server compares it with the digest of its own
pre-handler render. Equal: the answer is patches. Anything else: the whole
page, as before.

What the digest is, and is not:

* A MAC (HMAC-SHA256 under a key derived from ``SECRET_KEY`` for this purpose
  only, truncated to 128 bits) over the canonical form of HTML the client was
  already sent, together with a binding to the request (:func:`digest_binding`).
  Nothing is stored; any process sharing ``SECRET_KEY`` computes the same value.
* Not an input to the answer. Patches are always the difference between the
  server's own two renders. The echoed value only selects between "send the
  difference" and "send the whole page"; a forged, stale or foreign digest
  selects the whole page.
* Not an oracle. A match tells the client that the page it was sent is the
  page the server would send it now, which the whole-page answer says in full.

The digest says the server's pre-handler render is the render it last SENT. It
does not say the browser's DOM still has that render's structure: a script may
have inserted or removed a node, a ``dj-remove`` exit may still be playing.
Patches on this transport are addressed by path, so a shifted DOM would take a
patch on the wrong node. The second half of the token closes that: the
**shape** of a render (:func:`dom_shape`) is a checksum of its element tree
(tags, ``id`` and key attributes, and which children are text or ``dj-if``
markers), computed with the same child-counting rule the client's path walker
uses. The server sends the shape with the digest; the client computes the shape
of its own DOM, sends that with the digest, and applies patches only while its
DOM still has it. The server answers with patches only when the shape the
client reports is the shape of its own pre-handler render.

The canonical form leaves out what differs between two renders of equal state:
the generated ``dj-id`` attributes (a diff render numbers new nodes after the
old ones, a fresh render numbers them in order; patches on this transport are
therefore addressed by path, see :func:`path_addressed`) and the value of
Django's masked CSRF token, which is re-masked on every call.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import string
import zlib
from html import unescape
from typing import Any

from django.utils.crypto import salted_hmac

#: Request header the client echoes the digest in.
HEADER = "X-Djust-Render-Digest"
#: ``<meta name=...>`` the page GET carries it in, outside ``dj-root``.
META_NAME = "djust-render-digest"
#: Field of a fallback answer that names the digest of the render it delivers.
ANSWER_FIELD = "render_digest"

# Purpose-specific key: ``salted_hmac`` derives it from this salt and SECRET_KEY,
# as the project's other keyed values do (``mixins/sticky.py``,
# ``security/service_worker.py``).
_SALT = "djust.http_fallback.render_digest.v1"
_TAG_BYTES = 16
_TAG_CHARS = 22  # urlsafe base64 of 16 bytes, unpadded
_TAG_ALPHABET = frozenset(string.ascii_letters + string.digits + "-_")

_DJ_ID = re.compile(r' dj-id="[0-9A-Za-z]*"')

# Django's masked CSRF token: 32 mask characters, then the secret shifted by them.
_CSRF_CHARS = string.ascii_letters + string.digits
_CSRF_INDEX = {char: index for index, char in enumerate(_CSRF_CHARS)}
_CSRF_SECRET_LENGTH = 32
_CSRF_TOKEN = re.compile(r"(?<![0-9A-Za-z])[0-9A-Za-z]{64}(?![0-9A-Za-z])")
_CSRF_PLACEHOLDER = "csrf-token"

_SHAPE_CHARS = 16  # crc32 and adler32 of the shape text, as hex
_SHAPE_ALPHABET = frozenset("0123456789abcdef")

# One tag or comment of the renderer's serialization. Attribute values are
# quoted, so a ">" inside one does not end the tag. A comment ends at "-->" or
# at "--!>", as it does in the browser's HTML parser (CodeQL alert 3151).
_TOKEN = re.compile(
    r"<!--(?P<comment>.*?)--!?>"
    r"|<(?P<close>/?)(?P<tag>[A-Za-z][^\s/>]*)(?P<attrs>(?:[^>\"']|\"[^\"]*\"|'[^']*')*)>",
    re.DOTALL,
)
# One attribute of a start tag: name, then a double-quoted, single-quoted or
# bare value, or none.
_ATTRS = re.compile(r"""([^\s"'=<>/]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?""")
#: Attributes client code writes on a page that is still the server's render.
#: ``data-open``, ``data-current``, ``data-position`` and ``data-visible`` are
#: written by djust's own client (popovers, the theme toggle, the tutorial
#: bubble), so they stay out although an app may render them too; the
#: http-only-mode guide names them (#4114). Kept equal to ``_SHAPE_SKIPPED`` /
#: ``_SHAPE_SKIPPED_PREFIX`` in ``12-vdom-patch.js`` by
#: ``test_the_client_and_the_server_leave_out_the_same_attributes`` and the
#: shared vector ``every_skipped_attribute``.
_SKIPPED_ATTRS = frozenset(
    "class style dj-id open hidden disabled checked selected tabindex role "
    "data-live-hydrated data-open data-visible data-position data-current "
    "data-theme data-theme-mode".split()
)
_SKIPPED_ATTR_PREFIXES = ("aria-", "data-djust-", "data-dj-", "data-upload-", "data-stream-")
_ASCII_SPACE_RUN = re.compile(r"[ \t\n\r\f]+")
_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
#: Elements whose every text child counts (``isWhitespacePreserving`` in the client).
_KEEPS_WHITESPACE = frozenset(("pre", "code", "textarea", "script", "style"))
#: Elements after whose start tag the HTML parser drops one newline.
_DROPS_FIRST_NEWLINE = frozenset(("pre", "textarea", "listing"))
#: Elements whose content is text up to their own end tag.
_RAW_TEXT = frozenset(("script", "style", "textarea", "title"))

#: Keys of a patch that name a node by its generated id.
_ID_HINTS = ("d", "ref_d", "child_d")


def digest_binding(view: Any, request: Any) -> tuple[str, ...] | None:
    """What a digest is bound to besides the rendered HTML.

    The ONE place the binding is computed. Today: the session, the signed-in
    user as the session records it (id and auth hash, so no query is made), the
    request path and the view class. A digest issued to another session, user
    or page therefore never matches here.

    Saved state is becoming per page load rather than per session and route
    (#4106). When that identifier exists, add it to the tuple returned here and
    nowhere else.

    ``None`` when the request has no session key: nothing ties a digest to this
    caller, so none is issued or accepted.
    """
    session = getattr(request, "session", None)
    session_key = getattr(session, "session_key", None)
    if not session_key or not isinstance(session_key, str):
        return None
    try:
        user_id = str(session.get("_auth_user_id", ""))
        user_hash = str(session.get("_auth_user_hash", ""))
    except Exception:  # noqa: BLE001 - an unreadable session issues no digest
        return None
    view_class = type(view)
    return (
        session_key,
        user_id,
        user_hash,
        str(getattr(request, "path", "")),
        "%s.%s" % (view_class.__module__, view_class.__qualname__),
    )


def _csrf_secret(request: Any) -> str | None:
    """The request's CSRF secret, as ``CsrfViewMiddleware`` holds it."""
    meta = getattr(request, "META", None)
    value = meta.get("CSRF_COOKIE") if isinstance(meta, dict) else None
    if not isinstance(value, str):
        return None
    if len(value) == 2 * _CSRF_SECRET_LENGTH:
        # A cookie written before Django 4.1 holds a masked token.
        return _unmask(value)
    return value if len(value) == _CSRF_SECRET_LENGTH else None


def _unmask(token: str) -> str | None:
    try:
        mask = [_CSRF_INDEX[char] for char in token[:_CSRF_SECRET_LENGTH]]
        cipher = [_CSRF_INDEX[char] for char in token[_CSRF_SECRET_LENGTH:]]
    except KeyError:
        return None
    return "".join(_CSRF_CHARS[c - m] for c, m in zip(cipher, mask))


def canonical_html(html: str, request: Any) -> str:
    """``html`` without the parts that differ between two renders of equal state."""
    html = _DJ_ID.sub("", html)
    secret = _csrf_secret(request)
    if secret is None:
        return html

    def replace(match: re.Match[str]) -> str:
        unmasked = _unmask(match.group(0))
        if unmasked is not None and hmac.compare_digest(unmasked, secret):
            return _CSRF_PLACEHOLDER
        return match.group(0)

    return _CSRF_TOKEN.sub(replace, html)


def render_digest(view: Any, request: Any, html: Any) -> str | None:
    """The digest of the rendered root ``html`` for this request, or ``None``."""
    if not isinstance(html, str):
        return None
    binding = digest_binding(view, request)
    if binding is None:
        return None
    content = hashlib.sha256(canonical_html(html, request).encode("utf-8")).hexdigest()
    message = json.dumps([*binding, content], separators=(",", ":"))
    tag = salted_hmac(_SALT, message, algorithm="sha256").digest()[:_TAG_BYTES]
    return base64.urlsafe_b64encode(tag).decode("ascii").rstrip("=")


def _is_dj_if_comment(text: str) -> bool:
    """``isDjIfComment`` in the client: only these comments count as children."""
    text = text.strip()
    return text in ("dj-if", "/dj-if") or text.startswith(("dj-if ", "dj-if\t"))


def _counts_as_text(text: str, keeps_whitespace: bool) -> bool:
    """``isSignificantChild`` in the client, for a text node."""
    if not text:
        return False
    if keeps_whitespace or text == " ":
        return True
    return bool(text.strip(" \t\n\r\f"))


def _shape_attr_counts(name: str) -> bool:
    """Whether attribute ``name`` is part of the shape (``_shapeAttrCounts`` in the client).

    Left out are the attributes client code writes on a page that is still the
    server's render: presentation and widget state. Everything else an element
    carries is part of what it is.
    """
    return name not in _SKIPPED_ATTRS and not name.startswith(_SKIPPED_ATTR_PREFIXES)


def _shape_text(text: str) -> str:
    """Text as the shape holds it: runs of ASCII whitespace as one space, trimmed."""
    return _ASCII_SPACE_RUN.sub(" ", text).strip(" ")


def _counted_attrs(attrs: str) -> dict[str, str]:
    """The counted attributes of a start tag's attribute text, by lower-case name."""
    pairs: dict[str, str] = {}
    for found in _ATTRS.finditer(attrs):
        name = found.group(1).lower()
        if name in pairs or not _shape_attr_counts(name):
            continue
        value = found.group(2)
        if value is None:
            value = found.group(3)
        if value is None:
            value = found.group(4) or ""
        pairs[name] = unescape(value) if "&" in value else value
    return pairs


def _element_piece(tag: str, pairs: dict[str, str]) -> str:
    """``<tag`` and its counted attributes, sorted by name, each ended by NUL."""
    if pairs.get("name") == "csrfmiddlewaretoken":
        # Django masks the token again on every render.
        pairs = {name: value for name, value in pairs.items() if name != "value"}
    return "<" + tag + "".join(" %s=%s\0" % (name, pairs[name]) for name in sorted(pairs))


#: ``dj-update`` value whose subtree the client owns and the shape does not enter (#4283).
_OPAQUE_UPDATE = "ignore"


def _is_opaque(pairs: dict[str, str]) -> bool:
    """``dj-update="ignore"``: the client owns the element's children (``domShape``)."""
    return pairs.get("dj-update") == _OPAQUE_UPDATE


def _is_client_owned(pairs: dict[str, str]) -> bool:
    """An element whose children the client manages in a way a path cannot follow:
    ``dj-update`` other than ``ignore`` (append, prepend, replace) and any
    ``dj-virtual`` list (#4283). Its render is answered with the whole page."""
    if "dj-update" in pairs and pairs["dj-update"] != _OPAQUE_UPDATE:
        return True
    return any(name == "dj-virtual" or name.startswith("dj-virtual-") for name in pairs)


class RenderShape:
    """What one walk of a rendered root finds (:func:`render_shape`).

    ``shape``: the checksum :func:`dom_shape` returns. ``opaque_paths``: the
    path of every ``dj-update="ignore"`` element, by the path walker's
    child-counting rule (a patch at or under one would address client-owned
    nodes). ``client_owned``: whether the render holds a ``dj-update`` other
    than ``ignore`` or a ``dj-virtual`` list.
    """

    __slots__ = ("shape", "opaque_paths", "client_owned")

    def __init__(self, shape: str, opaque_paths: list[tuple[int, ...]], client_owned: bool):
        self.shape = shape
        self.opaque_paths = opaque_paths
        self.client_owned = client_owned


def dom_shape(html: Any) -> str | None:
    """A checksum of the tree of the rendered root ``html``: what sits where.

    The text hashed is, for each node under the root in document order:

    * an element: ``<tag``, then each counted attribute as `` name=value``
      + NUL, sorted by name (:func:`_shape_attr_counts`), then its children,
      then ``>``. A ``dj-update="ignore"`` element's children are left out: the
      client owns them (flash messages it adds, #4283);
    * a text node that counts as a child: ``"`` + its text with whitespace
      runs collapsed + NUL;
    * a ``dj-if`` boundary comment: ``!``.

    Which nodes count is the rule the client's path walker uses
    (``isSignificantChild``), so two trees with one shape resolve every path
    to corresponding nodes; and because the shape holds each element's
    attributes and text, a tree whose rows were reordered has another shape
    unless the rows are equal in all of that, in which case the order makes no
    difference. ``12-vdom-patch.js`` (``domShape``) computes the same text
    from the live DOM; ``tests/js/fixtures/dom_shape_vectors_4108.json`` holds
    the two together.

    ``None`` for anything this reader cannot follow (unbalanced tags): the
    caller then answers with the whole page.
    """
    found = render_shape(html)
    return None if found is None else found.shape


def render_shape(html: Any) -> RenderShape | None:
    """:func:`dom_shape`'s walk, with the client-owned regions it passes (#4283).

    ``None`` when the shape is ``None``.
    """
    if not isinstance(html, str):
        return None
    out: list[str] = []
    stack: list[str] = []
    #: per open element: the number of significant children seen so far
    counts: list[int] = []
    #: per open element below the root: its path
    paths: list[tuple[int, ...]] = []
    opaque_paths: list[tuple[int, ...]] = []
    client_owned = False
    #: the stack depth of the opaque element being skipped, or 0
    opaque_depth = 0
    position = 0
    after_start_of = ""  # the tag whose start tag was the previous token

    def inside_opaque() -> bool:
        return bool(opaque_depth) and len(stack) >= opaque_depth

    def text_child(text: str, parent: str, references: bool = True) -> None:
        if inside_opaque():
            return
        if references and "&" in text:
            # The client sees the character, not the reference to it.
            text = unescape(text)
        if _counts_as_text(text, parent in _KEEPS_WHITESPACE):
            out.append('"' + _shape_text(text) + "\0")
            counts[-1] += 1

    while True:
        match = _TOKEN.search(html, position)
        if match is None:
            break
        text = html[position : match.start()]
        position = match.end()
        if after_start_of in _DROPS_FIRST_NEWLINE and text.startswith("\n"):
            # The HTML parser drops a newline right after <pre>.
            text = text[1:]
        after_start_of = ""
        if stack:
            text_child(text, stack[-1])
        comment = match.group("comment")
        if comment is not None:
            if stack and not inside_opaque() and _is_dj_if_comment(comment):
                out.append("!")
                counts[-1] += 1
            continue
        tag = match.group("tag").lower()
        if match.group("close"):
            if not stack or stack[-1] != tag:
                return None
            if opaque_depth and len(stack) == opaque_depth:
                opaque_depth = 0
            stack.pop()
            counts.pop()
            if stack:
                paths.pop()
                if not inside_opaque():
                    out.append(">")
            continue
        attrs = match.group("attrs")
        pairs = _counted_attrs(attrs)
        if _is_client_owned(pairs):
            client_owned = True
        if not stack:
            # The root element itself: its own attributes are not part of the
            # shape (the page GET stamps some on it).
            stack.append(tag)
            counts.append(0)
            if attrs.rstrip().endswith("/") or tag in _VOID:
                return None
            if _is_opaque(pairs):
                client_owned = True  # a root the client owns entirely
            continue
        void = attrs.rstrip().endswith("/") or tag in _VOID
        if inside_opaque():
            if not void:
                stack.append(tag)
                counts.append(0)
                paths.append(())
                if tag in _RAW_TEXT:
                    end = re.compile(r"</%s\s*>" % re.escape(tag), re.IGNORECASE).search(
                        html, position
                    )
                    if end is None:
                        return None
                    position = end.end()
                    stack.pop()
                    counts.pop()
                    paths.pop()
            continue
        path = (paths[-1] if paths else ()) + (counts[-1],)
        counts[-1] += 1
        out.append(_element_piece(tag, pairs))
        if void:
            out.append(">")
            continue
        stack.append(tag)
        counts.append(0)
        paths.append(path)
        after_start_of = tag
        if _is_opaque(pairs):
            # Hashed as an element; its children are the client's.
            opaque_paths.append(path)
            opaque_depth = len(stack)
        if tag in _RAW_TEXT:
            end = re.compile(r"</%s\s*>" % re.escape(tag), re.IGNORECASE).search(html, position)
            if end is None:
                return None
            raw = html[position : end.start()]
            if tag in _DROPS_FIRST_NEWLINE and raw.startswith("\n"):
                raw = raw[1:]
            # <script> and <style> hold their text as written; <textarea> and
            # <title> hold character references.
            text_child(raw, tag, references=tag in ("textarea", "title"))
            position = end.end()
            after_start_of = ""
            if opaque_depth == len(stack):
                opaque_depth = 0
            stack.pop()
            counts.pop()
            paths.pop()
            out.append(">")
    if stack:
        return None
    data = "".join(out).encode("utf-8", "surrogatepass")
    shape = "%08x%08x" % (zlib.crc32(data) & 0xFFFFFFFF, zlib.adler32(data) & 0xFFFFFFFF)
    return RenderShape(shape, opaque_paths, client_owned)


def answer_shape(request: Any, html: Any) -> RenderShape | None:
    """:func:`render_shape` of the rendered root ``html`` in its canonical form."""
    return render_shape(canonical_html(html, request)) if isinstance(html, str) else None


_UNSET: Any = object()


def render_token(view: Any, request: Any, html: Any, found: Any = _UNSET) -> str | None:
    """``<digest>.<shape>`` for the rendered root ``html``: what the page GET
    and every fallback answer carry, or ``None`` when either half is missing.
    ``found`` is :func:`answer_shape`'s result, when the caller has it."""
    digest = render_digest(view, request, html)
    if found is _UNSET:
        found = answer_shape(request, html)
    shape = None if found is None else found.shape
    if not digest or not shape:
        return None
    return digest + "." + shape


def supplied_token(request: Any) -> tuple[str, str] | None:
    """``(digest, shape)`` as the client sent them, or ``None``.

    The digest is the one the client was given; the shape is the one it
    computed from its own DOM. ``None`` if the header is absent or either half
    is not shaped like one.
    """
    value = request.headers.get(HEADER) if hasattr(request, "headers") else None
    if not isinstance(value, str) or len(value) != _TAG_CHARS + 1 + _SHAPE_CHARS:
        return None
    digest, separator, shape = value.partition(".")
    if separator != "." or len(digest) != _TAG_CHARS or len(shape) != _SHAPE_CHARS:
        return None
    if not _TAG_ALPHABET.issuperset(digest) or not _SHAPE_ALPHABET.issuperset(shape):
        return None
    return digest, shape


def tokens_match(
    html: Any, request: Any, expected_digest: str | None, supplied: tuple[str, str] | None
) -> bool:
    """Whether the client's ``(digest, shape)`` are those of the render ``html``.

    The digest is compared in constant time. The shape is not a secret: it is
    recomputed here from the server's own render (in its canonical form, as the
    token names it) and compared with what the client reports about its DOM.
    """
    if not expected_digest or supplied is None or not isinstance(html, str):
        return False
    digest, shape = supplied
    if not hmac.compare_digest(expected_digest.encode("ascii"), digest.encode("ascii")):
        return False
    return dom_shape(canonical_html(html, request)) == shape


def path_addressed(patches: list[Any]) -> list[Any]:
    """``patches`` without the generated-id hints, so the client resolves by path.

    The ids in a diff's patches are those of this request's baseline render. The
    browser's DOM may carry ids from an earlier answer (a fresh render numbers
    nodes in order; a diff render keeps old numbers and gives new nodes later
    ones), and the client looks a patch's node up by id before it tries the
    path, so a stale id would select the wrong node. The digest already
    established that the structure is the baseline's, which is what a path
    needs.
    """
    out = []
    for patch in patches:
        if isinstance(patch, dict) and any(key in patch for key in _ID_HINTS):
            patch = {key: value for key, value in patch.items() if key not in _ID_HINTS}
        out.append(patch)
    return out
