"""Server-side stale-asset check for ``dj-track-static`` (#2966).

On a reconnect the client sends the ``[dj-track-static]`` URLs its page loaded
(``track_static`` on the mount frame). A URL is reported stale when it names a
hashed file of the CURRENT ``ManifestStaticFilesStorage`` manifest's asset at
an older hash: the manifest still has the asset under its original name, but
maps it to a different hashed name. That is exactly "a deploy changed this
file", and it needs no page render, no GET and no session write.

Anything this cannot judge is never reported, because a false positive on a
``dj-track-static="reload"`` element reloads the page on every reconnect:

* a storage without a manifest (``StaticFilesStorage``, DEBUG setups);
* a URL outside ``STATIC_URL`` or with no recognisable hash;
* a hashed name whose original is not in the manifest.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, List
from urllib.parse import unquote, urlsplit

logger = logging.getLogger(__name__)

#: Bounds on the client-supplied list (the mount frame is untrusted input).
MAX_TRACKED_URLS = 64
MAX_URL_LENGTH = 2048

# ``HashedFilesMixin.hashed_name``: ``<root>.<hash><ext>`` for ``<root><ext>``.
_HASHED_BASENAME_RE = re.compile(r"^(?P<root>.+)\.(?P<hash>[0-9a-f]{6,64})(?P<ext>\.[^./]*)?$")


def _manifest() -> "dict[str, str] | None":
    from django.contrib.staticfiles.storage import staticfiles_storage

    hashed = getattr(staticfiles_storage, "hashed_files", None)
    if isinstance(hashed, dict) and hashed:
        return hashed
    return None


def _static_name(url: str) -> "str | None":
    """The storage name ``url`` points at under ``STATIC_URL``, or ``None``."""
    from django.conf import settings

    static_url = getattr(settings, "STATIC_URL", None)
    if not static_url:
        return None
    base = urlsplit(static_url)
    target = urlsplit(url)
    if base.netloc and target.netloc != base.netloc:
        return None
    if not base.netloc and target.netloc:
        # The client sends a same-origin URL as its path, so a host here is
        # another origin's asset, not one this storage serves.
        return None
    prefix = base.path if base.path.endswith("/") else base.path + "/"
    if not target.path.startswith(prefix):
        return None
    return unquote(target.path[len(prefix) :])


def stale_static_urls(urls: Any) -> List[str]:
    """The URLs in ``urls`` that name an older hashed build of a current asset."""
    if not isinstance(urls, list) or not urls:
        return []
    manifest = _manifest()
    if manifest is None:
        return []
    current = set(manifest.values())
    stale: List[str] = []
    for url in urls[:MAX_TRACKED_URLS]:
        if not isinstance(url, str) or not url or len(url) > MAX_URL_LENGTH:
            continue
        name = _static_name(url)
        if not name or name in current or name in manifest:
            continue
        directory, basename = os.path.split(name)
        m = _HASHED_BASENAME_RE.match(basename)
        if m is None:
            continue
        original = os.path.join(directory, m.group("root") + (m.group("ext") or ""))
        now = manifest.get(original)
        if not isinstance(now, str):
            continue
        now_match = _HASHED_BASENAME_RE.match(os.path.basename(now))
        # Same hash shape as the current build, different hash: a redeploy.
        if now_match is None or len(now_match.group("hash")) != len(m.group("hash")):
            continue
        if url not in stale:
            stale.append(url)
    return stale
