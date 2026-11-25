"""Django's static tag, including storage resolution and ``as`` bindings."""

from django.templatetags.static import do_static

from . import register
from ._builtin import DjangoBuiltinTagHandler


@register("static")
class StaticTagHandler(DjangoBuiltinTagHandler):
    # Django's node binds only its `as <name>` target (#4124).
    BINDS_ONLY_AS_VAR = True

    def __init__(self) -> None:
        super().__init__("static", do_static)
