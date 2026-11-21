"""Support for the marked-safe page-template sweep (#4157, ``marked_safe_sweep_4009``).

* ``register``: a stand-in for django-allauth's ``allauth`` template library. djust's
  ``allauth/elements/*`` templates ``{% load allauth %}`` for ``{% slot %}``; the sweep renders
  them without allauth installed, so a slot renders its default body (what allauth does when the
  caller fills nothing).
* ``urlpatterns``: the URLconf the sweep renders under, so the pages' ``{% url %}`` tags resolve
  (a djust admin site named :data:`ADMIN_SITE_NAME` with ``Group`` registered, and the theming
  gallery).
"""

from django import template
from django.urls import include, path

register = template.Library()

ADMIN_SITE_NAME = "page_sweep_4157_admin"


class _SlotNode(template.Node):
    def __init__(self, nodelist):
        self.nodelist = nodelist

    def render(self, context):
        return self.nodelist.render(context)


@register.tag
def slot(parser, token):
    nodelist = parser.parse(("endslot",))
    parser.delete_first_token()
    return _SlotNode(nodelist)


def _urlpatterns():
    from django.contrib.auth.models import Group

    from djust.admin_ext import DjustAdminSite, DjustModelAdmin

    site = DjustAdminSite(name=ADMIN_SITE_NAME)
    site.register(Group, DjustModelAdmin)
    return [
        path("sweep-admin/", site.urls),
        path("sweep-theming/", include("djust.theming.urls")),
    ]


urlpatterns = _urlpatterns()
