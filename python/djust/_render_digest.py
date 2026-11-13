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


def supplied_digest(request: Any) -> str | None:
    """The digest the client echoed, or ``None`` if absent or not shaped like one."""
    value = request.headers.get(HEADER) if hasattr(request, "headers") else None
    if not isinstance(value, str) or len(value) != _TAG_CHARS:
        return None
    if not _TAG_ALPHABET.issuperset(value):
        return None
    return value


def digests_match(expected: str | None, supplied: str | None) -> bool:
    """Constant-time equality; ``False`` when either side is missing."""
    if not expected or not supplied:
        return False
    return hmac.compare_digest(expected.encode("ascii"), supplied.encode("ascii"))


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
