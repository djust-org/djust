"""Django regroup compilation with typed source rows and named tuple results."""

from django.template.defaulttags import regroup

from . import register
from ._builtin import DjangoBuiltinTagHandler


@register("regroup")
class RegroupTagHandler(DjangoBuiltinTagHandler):
    ACCEPTS_AS_VAR = True

    def __init__(self) -> None:
        super().__init__("regroup", regroup)
