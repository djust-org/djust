"""Django URL reversal through the shared compile/render bridge.

Keep operands as template expressions until Django's URLNode resolves them.
This preserves types, prevents context values from being resolved twice, and
uses the current request namespace and autoescape scope.
"""

from django.template.defaulttags import url

from . import register
from ._builtin import DjangoBuiltinTagHandler


@register("url")
class UrlTagHandler(DjangoBuiltinTagHandler):
    # Django's node binds only its `as <name>` target (#4124).
    BINDS_ONLY_AS_VAR = True
    # Django's URLNode reads its operands and `context.request` (for the
    # current application namespace), nothing else of the context (#4315).
    # `reverse()` also reads thread-locals that are not context keys: the
    # active language (`get_language()` under `i18n_patterns`), the URLconf
    # (`get_urlconf()`, set from `request.urlconf`) and the script prefix
    # (`get_script_prefix()`). They are taken as fixed for a connection, so a
    # language switch without a reload keeps the URLs already rendered.
    # `request` is never in a turn's changed keys (it rides the render
    # sidecar, not the state), so ALSO_READS is belt-and-braces: it keeps the
    # declaration true should a view ever put `request` in state.
    READS_ONLY_ARGS = True
    ALSO_READS = frozenset({"request"})

    # The native argument channel preserves the assignment-name marker; all
    # other positions are raw source tokens under the built-in bridge policy.
    ACCEPTS_AS_VAR = True

    def __init__(self) -> None:
        super().__init__("url", url)
