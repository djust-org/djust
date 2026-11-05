"""The policy for attributes a caller PASSES THROUGH to a tag (#3818).

A *passthrough* writer takes attribute names and values from its caller and prints them into a
tag it does not otherwise control: ``{% live_input ... placeholder=x formaction=u %}``, a Django
widget's ``attrs`` rendered by ``FormMixin.as_live``, ``render_icon(name, aria_label=...)``.
``djust._html.build_tag`` escapes a VALUE (so it cannot leave its quotes) and, since #3774,
checks a NAME (:func:`djust._html.attr_name`). Neither says anything about what the attribute
*does*: ``formaction="javascript:..."`` has no characters to escape, and ``onfocus=...`` is a
legal name. This module is that missing half, in one place, so every passthrough writer judges
the same way instead of each growing its own list.

The policy (:func:`passthrough_attrs`) separates what the DEVELOPER spells from what DATA can
carry. A developer-spelled problem (an attribute NAME) is a bug at development time and raises
``ValueError``; a hostile VALUE, which a template can take from data (``formaction=profile.website``),
never raises, because an error page that an attacker's stored value triggers for every viewer is an
availability hole of its own. Django's way is to neutralise hostile data, and so is this:

* an event-handler attribute NAME (``on*``; the framework spellings ``@click``, ``x-on:click``,
  ``v-on:click``, ``hx-on:click``) raises ``ValueError``: it is inline script whatever its value
  (use a ``dj-*`` binding);
* a name that is not a legal HTML attribute name (:func:`djust._html.attr_name`) raises;
* an attribute that holds a URL the browser follows or loads is judged by the shared allowlist
  (:func:`djust.components.utils.url_allowed`). A value whose scheme the allowlist rejects
  (``javascript:``, ``vbscript:``, ``data:``, ``blob:``, ``file:``, custom schemes; read the way a
  browser reads it: case, leading space, tabs and newlines inside the scheme) renders ``#`` and is
  logged (the attribute name only, never the value). That covers ``href`` / ``src`` / ``action`` /
  ``formaction``, ``poster``, ``srcset``, ``ping``, ``data``, ``background``, ``xlink:href`` and the
  framework's ``dj-navigate`` / ``dj-patch`` directives. A list-valued ``srcset`` / ``ping`` is
  judged entry by entry;
* ``type`` cannot be turned into a control that submits or navigates: a VALUE of ``image`` /
  ``submit`` / ``reset`` / ``button`` (it can come from data, ``type=field.kind``) is DROPPED, so
  the tag keeps the type it fixed itself, and logged. Every other ``type`` (``date``, ``color`` ...)
  keeps overriding, as it always did. The judged text is what is returned for a non-``str`` value,
  so an object whose ``__str__`` changes between calls cannot pass the check and print something
  else;
* every other attribute passes through unchanged: ``aria-*``, ``data-*``, ``placeholder``,
  ``maxlength``, ``pattern``, ``x-model`` ... The hole was three named classes, not "unknown
  attributes", and an allowlist of attribute names would break every ``x-*`` / ``hx-*`` / custom
  attribute a project already passes.

What the policy deliberately does NOT judge, because it is the page author's own markup and no
browser treats it as a URL or as script by itself: ``style``, an Alpine / Vue / htmx
EXPRESSION attribute (``x-data``, ``x-init``, ``:href``, ``hx-vals``), and any ``data-*`` /
``dj-*`` attribute other than the framework's own URL directives. Passing user data to those is
the template author's decision, exactly as it is for markup they write by hand.

``djust.theming``'s ``_passthrough_attrs`` implements the same idea for its component attributes
(its helpers are pinned by the #3749 / #3765 gate-offs, which mutate that file's text, so it does
not import this module). It is NOT identical, and the differences are deliberate:

* theming RAISES ``ValueError`` for a script-bearing ``href`` / ``src`` / ``action`` /
  ``formaction`` (it always has); the shared policy renders ``#``;
* the shared policy also refuses the ``@`` / ``x-on`` / ``v-on`` / ``hx-on`` event spellings (theming's
  name rule rejects ``@`` and ``:`` outright, but accepts ``hx-on-click``) and drops a submitting
  ``type`` (theming does not judge ``type``);
* ``xlink:href`` is a URL attribute only here (theming's name rule rejects ``:``);
* an ``srcset`` / ``ping`` list that is entirely allowed is printed as written here, and
  re-joined with single spaces (so trailing or doubled whitespace is normalised) there.

``python/tests/test_passthrough_attr_policy_3818.py`` runs the two against the same payloads,
asserts exactly these differences and nothing else, and pins the URL attribute sets equal.

The named ``*_ATTRS`` sets are public so that test, and a reviewer, can compare them.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

from djust._html import attr_name

logger = logging.getLogger(__name__)

#: Navigation attributes: a URL the browser follows. (Theming RAISES for a script scheme here; the
#: shared policy renders ``#``, see the module docstring.)
URL_ATTRS_NAVIGATION = frozenset({"href", "src", "action", "formaction"})

#: The other URL-valued attributes.
URL_ATTRS_OTHER = frozenset(
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


def _judge_url(name: str, value: Any) -> Any:
    """``value`` for a URL attribute ``name``: itself, or ``"#"``. Never raises (a value can be data)."""
    from djust.components.utils import _srcset_candidates, url_allowed

    if name in URL_ATTRS_NAVIGATION:
        text = _plain_url(value)
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
    ``ValueError`` (naming the attribute, never its value) only for a NAME: one that is not a
    legal HTML attribute name (:func:`djust._html.attr_name`) or an event-handler attribute. A
    VALUE never raises: a URL the allowlist rejects becomes ``"#"`` and a ``type`` that makes the
    control submit or navigate is left out of the result (both logged by attribute name). See the
    module docstring for the full policy.
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
            # Converted to text ONCE: the text that is judged is the text that is returned.
            text = value if type(value) is str else str.__str__(str(value))
            if text.strip().lower() in REFUSED_TYPES:
                logger.warning(
                    "passthrough attribute %s= value would make the control submit or navigate; "
                    "left out",
                    name,
                )
                continue
            value = text
        elif value is not True and (lowered in URL_ATTRS_NAVIGATION or lowered in URL_ATTRS_OTHER):
            value = _judge_url(lowered, value)
        out[key] = value
    return out
