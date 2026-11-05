"""The policy for attributes a caller PASSES THROUGH to a tag (#3818).

A *passthrough* writer takes attribute names and values from its caller and prints them into a
tag it does not otherwise control: ``{% live_input ... placeholder=x formaction=u %}``, a Django
widget's ``attrs`` rendered by ``FormMixin.as_live``, ``render_icon(name, aria_label=...)``.
``djust._html.build_tag`` escapes a VALUE (so it cannot leave its quotes) and, since #3774,
checks a NAME (:func:`djust._html.attr_name`). Neither says anything about what the attribute
*does*: ``formaction="javascript:..."`` has no characters to escape, and ``onfocus=...`` is a
legal name. This module is that missing half, in one place, so every passthrough writer judges
the same way instead of each growing its own list.

The policy (:func:`passthrough_attrs`):

* an event-handler attribute (``on*``; the framework spellings ``@click``, ``x-on:click``,
  ``v-on:click``, ``hx-on:click``) is **refused** with ``ValueError``: it is inline script
  (use a ``dj-*`` binding);
* an attribute that holds a URL the browser follows or loads is judged by the shared allowlist
  (:func:`djust.components.utils.url_allowed`). ``href`` / ``src`` / ``action`` / ``formaction``
  with a script-bearing scheme (``javascript:``, ``vbscript:``, ``data:``, ``livescript:``,
  ``mhtml:``; read the way a browser reads it: case, leading space, tabs and newlines inside
  the scheme) is **refused** with ``ValueError``; any other scheme the allowlist rejects renders
  ``#`` (and a warning). The attributes that have never raised (``poster``, ``srcset``, ``ping``,
  ``data``, ``background``, ``xlink:href``, the framework's ``dj-navigate`` / ``dj-patch``
  directives) render ``#`` for a rejected scheme and never raise. A list-valued ``srcset`` /
  ``ping`` is judged entry by entry;
* ``type`` cannot be turned into a control that submits or navigates: ``image`` / ``submit`` /
  ``reset`` / ``button`` are **refused**. Every other ``type`` (``date``, ``color`` ...) keeps
  overriding, as it always did;
* every other attribute passes through unchanged: ``aria-*``, ``data-*``, ``placeholder``,
  ``maxlength``, ``pattern``, ``x-model`` ... The hole was three named classes, not "unknown
  attributes", and an allowlist of attribute names would break every ``x-*`` / ``hx-*`` / custom
  attribute a project already passes.

What the policy deliberately does NOT judge, because it is the page author's own markup and no
browser treats it as a URL or as script by itself: ``style``, an Alpine / Vue / htmx
EXPRESSION attribute (``x-data``, ``x-init``, ``:href``, ``hx-vals``), and any ``data-*`` /
``dj-*`` attribute other than the framework's own URL directives. Passing user data to those is
the template author's decision, exactly as it is for markup they write by hand.

``djust.theming``'s ``_passthrough_attrs`` has the same policy with the same sets and the same
severities, implemented there (its helpers are pinned by the #3749 / #3765 gate-offs, which
mutate that file's text). ``python/tests/test_passthrough_attr_policy_3818.py`` runs the two
against the same payloads and fails if they disagree, and pins each shared set to theming's.

The named ``*_ATTRS`` sets are public so that test, and a reviewer, can compare them.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

from djust._html import attr_name

logger = logging.getLogger(__name__)

#: Attributes whose value is a URL, and which have always RAISED for a script-bearing scheme.
URL_ATTRS_RAISING = frozenset({"href", "src", "action", "formaction"})

#: URL-valued attributes that render ``#`` for a rejected scheme and never raise.
URL_ATTRS_QUIET = frozenset(
    {
        "ping",
        "srcset",
        "poster",
        "data",
        "background",
        "dj-navigate",
        "dj-patch",
        "dj-patch-reload",
        "xlink:href",
    }
)

#: The quiet attributes that only ever LOAD AN IMAGE (an inline ``data:image/...`` is fine).
IMAGE_URL_ATTRS = frozenset({"poster", "srcset"})

#: Schemes a URL attribute has always raised for rather than rendered.
RAISING_SCHEMES = ("javascript", "vbscript", "data", "livescript", "mhtml")

#: ``type`` values that make a control submit or navigate.
REFUSED_TYPES = frozenset({"image", "submit", "reset", "button"})

#: Name prefixes (lower-case) of inline-script event-handler attributes. ``on`` is the HTML
#: family (``onclick``, ``onfocus`` ...: every attribute name that starts with it is an event
#: handler or nothing); the others are the Vue / Alpine / htmx spellings of the same thing.
_EVENT_PREFIXES = ("on", "@", "x-on", "v-on", "hx-on")

_ASCII_WS = re.compile(r"[\t\n\f\r ]+")


def _plain_url(value: Any) -> Any:
    """``value`` as the plain text that is both JUDGED and PRINTED (``None`` stays ``None``).

    A value is converted to text exactly ONCE, here, so the check and the writer see the same
    string (an object whose ``__str__`` answers differently the second time cannot pass the check
    and print something else). A marked-safe URL becomes ordinary text so it is escaped:
    ``mark_safe("&#x6A;avascript:x")`` has no scheme until a browser decodes the reference.
    """
    if value is None or type(value) is str:
        return value
    return str.__str__(str(value))


def _raises_for_scheme(value: Any) -> bool:
    compact = "".join(ch for ch in str(value) if ord(ch) > 32 and ord(ch) != 127).lower()
    return compact.startswith(tuple(f"{scheme}:" for scheme in RAISING_SCHEMES))


def _judge_url(name: str, value: Any) -> Any:
    """``value`` for a URL attribute ``name``: itself, ``"#"``, or ``ValueError``."""
    from djust.components.utils import _srcset_candidates, url_allowed

    if name in URL_ATTRS_RAISING:
        text = _plain_url(value)
        if _raises_for_scheme(text):
            raise ValueError(
                f"{name}= refuses a script-bearing URL (javascript:, vbscript:, data:, "
                "livescript:, mhtml:)"
            )
        if not url_allowed(text):
            logger.warning(
                "passthrough attribute %s= URL with a disallowed scheme rendered as '#'", name
            )
            return "#"
        return text

    image = name in IMAGE_URL_ATTRS
    text = _plain_url(value)
    if name == "srcset":
        parts = []
        rejected = False
        for url, descriptors in _srcset_candidates(str(text)):
            if not url_allowed(url, image=True):
                logger.warning(
                    "passthrough attribute %s= URL with a disallowed scheme rendered as '#'", name
                )
                url = "#"
                rejected = True
            parts.append(" ".join([url, *descriptors]))
        # An all-allowed list is printed as the caller wrote it (byte-identical output).
        return ", ".join(parts) if rejected else text
    if name == "ping":
        kept = []
        rejected = False
        for url in (u for u in _ASCII_WS.split(str(text)) if u):
            if not url_allowed(url, image=image):
                logger.warning(
                    "passthrough attribute %s= URL with a disallowed scheme rendered as '#'", name
                )
                url = "#"
                rejected = True
            kept.append(url)
        return " ".join(kept) if rejected else text
    if not url_allowed(text, image=image):
        logger.warning(
            "passthrough attribute %s= URL with a disallowed scheme rendered as '#'", name
        )
        return "#"
    return text


def passthrough_attrs(attrs: Mapping[str, Any]) -> dict[str, Any]:
    """The attributes of ``attrs`` (HTML names, ``_`` already ``-``) a tag may print.

    Returns a NEW dict with every value that needed judging judged; everything else is the
    caller's own object, unchanged, so ordinary output is byte-identical. ``None`` and ``False``
    values mean "not present" to every writer and are returned as given, unjudged. Raises
    ``ValueError`` (naming the attribute, never its value) for a name that is not a legal HTML
    attribute name (:func:`djust._html.attr_name`), an event-handler attribute, a
    script-bearing URL in ``href`` / ``src`` / ``action`` / ``formaction``, or a ``type`` that
    makes the control submit or navigate. See the module docstring for the full policy.
    """
    out: dict[str, Any] = {}
    for key, value in attrs.items():
        if value is None or value is False:
            out[key] = value
            continue
        name = attr_name(key)
        lowered = name.lower()
        if lowered.startswith(_EVENT_PREFIXES):
            raise ValueError(
                f"{key!r}: inline event-handler attributes are not passed through; "
                "use a dj-* binding (dj_click, dj_change ...)"
            )
        if lowered == "type" and value is not True:
            if str(value).strip().lower() in REFUSED_TYPES:
                raise ValueError(
                    f"{key}= cannot make this control submit or navigate "
                    f"({', '.join(sorted(REFUSED_TYPES))})"
                )
        elif value is not True and (lowered in URL_ATTRS_RAISING or lowered in URL_ATTRS_QUIET):
            value = _judge_url(lowered, value)
        out[key] = value
    return out
