"""Django's static tag, including storage resolution and ``as`` bindings."""

from django.templatetags.static import do_static

from . import register
from ._builtin import DjangoBuiltinTagHandler


@register("static")
class StaticTagHandler(DjangoBuiltinTagHandler):
    # Django's node binds only its `as <name>` target (#4124).
    BINDS_ONLY_AS_VAR = True
    # Its output is its path operand through the storage backend; it reads no
    # other context name (#4315).
    READS_ONLY_ARGS = True

    def __init__(self) -> None:
        super().__init__("static", do_static)
