"""
Shared template-tag registry and helpers.

All templatetag sub-modules import ``register``, ``_resolve``, and
``_parse_kv_args`` from here so that every tag registers on the same
``template.Library`` instance.
"""

import re
import uuid  # noqa: F401 — re-exported for sub-modules
from typing import Any, Dict, List, Union
from urllib.parse import urlsplit

from django import template
from django.utils.html import conditional_escape  # noqa: F401
from django.utils.safestring import mark_safe  # noqa: F401

from djust.components.utils import CURRENCY_SYMBOLS  # noqa: F401
from djust.components.utils import interpolate_color  # noqa: F401

register = template.Library()

# Re-exports for sub-modules (see _charts.py, _forms.py, etc.)
__all__ = [
    "register",
    "uuid",
    "conditional_escape",
    "safe_url",
    "mark_safe",
    "CURRENCY_SYMBOLS",
    "interpolate_color",
    "_resolve",
    "_parse_kv_args",
]

# Schemes allowed in an href/action navigation context. Anything else
# (javascript:, vbscript:, data:, …) is replaced with "#".
_SAFE_URL_SCHEMES = frozenset({"http", "https", "mailto", "tel", "ftp", "ftps"})

# Browsers ignore leading/embedded ASCII control chars + whitespace when
# resolving a URL scheme, so an attacker can write "java\tscript:" / "java\nscript:"
# / "java\x00script:" to dodge a naive prefix check. Strip them ALL before the
# scheme probe so the evasions collapse to the canonical form.
_CTRL_WS_RE = re.compile(r"[\x00-\x20]+")


def safe_url(value: Any) -> str:
    """Escape a URL for an HTML attribute AND neutralize dangerous schemes.

    HTML-escaping (``conditional_escape``) prevents attribute breakout but does
    NOT stop a ``javascript:`` URI (which needs no escapable characters) — so a
    built-in component rendering a user-supplied URL into ``href``/``action``
    must validate the scheme too (finding #2, CWE-79). Use this at every
    navigation-context URL sink in the component tags; it is NOT for ``<img src>``
    (where ``javascript:`` doesn't execute and ``data:`` images are legitimate).

    Policy:
      * Relative / anchor / query / scheme-less URLs (``/x``, ``#frag``, ``?q=1``)
        → allowed (HTML-escaped).
      * Absolute URL whose scheme is in :data:`_SAFE_URL_SCHEMES` → allowed.
      * Any other scheme (``javascript:``/``vbscript:``/``data:``/…), including
        control-char/whitespace-obfuscated variants, and any value that fails to
        parse → replaced with ``"#"`` (fail-closed).
    """
    s = str(value).strip()
    if not s:
        return ""
    try:
        scheme = urlsplit(s).scheme.lower()
    except ValueError:
        return "#"
    probe = _CTRL_WS_RE.sub("", s).lower()
    if scheme and scheme not in _SAFE_URL_SCHEMES:
        return "#"
    # Belt-and-suspenders: catch parser-evasion where the obfuscated value has
    # an empty/odd urlsplit scheme but a browser would still see a bad scheme.
    if probe.startswith(("javascript:", "vbscript:", "data:")):
        return "#"
    escaped: str = conditional_escape(s)
    return escaped


def _resolve(value: Any, context: Dict[str, Any]) -> Any:
    """Resolve a template variable or return the literal value."""
    if isinstance(value, template.Variable):
        try:
            return value.resolve(context)
        except template.VariableDoesNotExist:
            return ""
    return value


def _parse_kv_args(bits: List[str], parser: Any) -> Dict[str, Union[str, template.Variable]]:
    """Parse key=value arguments from template tag tokens."""
    kwargs: Dict[str, Union[str, template.Variable]] = {}
    for bit in bits:
        if "=" in bit:
            key, val = bit.split("=", 1)
            # Strip quotes for literal strings
            if (val.startswith('"') and val.endswith('"')) or (
                val.startswith("'") and val.endswith("'")
            ):
                kwargs[key] = val[1:-1]
            else:
                kwargs[key] = template.Variable(val)
        else:
            raise template.TemplateSyntaxError(
                f"Unexpected argument '{bit}'. Use key=value format."
            )
    return kwargs
