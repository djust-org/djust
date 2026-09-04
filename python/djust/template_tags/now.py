"""Django's now tag, including named formats, localization, and ``as`` bindings."""

from django.template.defaulttags import now

from . import register
from ._builtin import DjangoBuiltinTagHandler


@register("now")
class NowTagHandler(DjangoBuiltinTagHandler):
    def __init__(self) -> None:
        super().__init__("now", now)
