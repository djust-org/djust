"""Django's built-in ``{% csp_nonce_attr %}`` through the tag bridge.

Delegating to Django preserves nonce escaping, form media rendering and the
``as`` assignment form. This handler is registered only when the installed
Django provides the built-in tag.
"""

from django.template.defaulttags import register as _defaulttags

from . import register
from ._builtin import DjangoBuiltinTagHandler


@register("csp_nonce_attr")
class CspNonceAttrTagHandler(DjangoBuiltinTagHandler):
    """Bridge the installed Django's CSP nonce tag compiler and node."""

    def __init__(self) -> None:
        super().__init__("csp_nonce_attr", _defaulttags.tags["csp_nonce_attr"])
