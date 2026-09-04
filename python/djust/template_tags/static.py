"""Django's static tag, including storage resolution and ``as`` bindings."""

from django.templatetags.static import do_static

from . import register
from ._builtin import DjangoBuiltinTagHandler


@register("static")
class StaticTagHandler(DjangoBuiltinTagHandler):
    def __init__(self) -> None:
        super().__init__("static", do_static)
