"""URL shape checks that read a URL the way a browser does.

The rule: a URL is normalised the way a browser reads it (WHATWG "basic URL parser"), then
tested. A value that is protocol-relative after normalisation is not a path on this site.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["url_as_parsed", "url_is_protocol_relative"]

_URL_EDGE_JUNK_RE = re.compile(r"^[\x00-\x20]+|[\x00-\x20]+$")
_URL_TAB_NEWLINE_RE = re.compile(r"[\t\n\r]")


def url_as_parsed(value: Any) -> str:
    """``value`` as a browser reads it before it parses it (WHATWG "basic URL parser"): leading
    and trailing C0 controls and spaces are dropped and every TAB, LF and CR is removed.
    """
    return _URL_TAB_NEWLINE_RE.sub("", _URL_EDGE_JUNK_RE.sub("", str(value)))


def url_is_protocol_relative(value: Any) -> bool:
    """Whether ``value``, once normalised (:func:`url_as_parsed`), is protocol-relative: it
    starts with two characters that are each a slash or a backslash. A percent escape is not
    decoded, and a single leading slash is a path on this origin.
    """
    text = url_as_parsed(value)
    return len(text) >= 2 and text[0] in "/\\" and text[1] in "/\\"
