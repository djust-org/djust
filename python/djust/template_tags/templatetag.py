"""
Django ``templatetag`` built-in handler for djust's Rust template engine.

Handles ``{% templatetag openblock %}`` and friends so that template syntax
characters can be rendered literally.

See https://docs.djangoproject.com/en/stable/ref/templates/builtins/#templatetag
"""

import logging
from typing import Any, Dict, List

from . import TagHandler, register

logger = logging.getLogger(__name__)

# Maps templatetag keywords to their literal output
_TEMPLATETAG_MAP = {
    "openblock": "{%",
    "closeblock": "%}",
    "openvariable": "{{",
    "closevariable": "}}",
    "openbrace": "{",
    "closebrace": "}",
    "opencomment": "{#",
    "closecomment": "#}",
}


@register("templatetag")
class TemplatetagHandler(TagHandler):
    """
    Handler for ``{% templatetag openblock %}`` etc.

    Renders the literal characters that Django's template syntax would
    otherwise interpret as tag delimiters.

    Supported keywords: openblock, closeblock, openvariable, closevariable,
    openbrace, closebrace, opencomment, closecomment.
    """

    def render(self, args: List[str], context: Dict[str, Any]) -> str:
        if not args:
            logger.warning("{%% templatetag %%} requires a keyword argument")
            return ""

        keyword = args[0].strip("'\"")
        result = _TEMPLATETAG_MAP.get(keyword)

        if result is None:
            logger.warning("{%% templatetag %s %%}: unknown keyword", keyword)
            return ""

        return result
