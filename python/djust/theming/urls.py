from django.apps import apps
from django.urls import include, path

from . import views

app_name = "djust_theming"

urlpatterns = [
    path("theme.css", views.theme_css_view, name="theme_css"),
    path("deferred.css", views.deferred_theme_css_view, name="deferred_theme_css"),
    path("gallery/", include("djust.theming.gallery.urls")),
]

# The component gallery lives in the optional `djust.components` app. Expose it
# under this namespace when that app is installed, so the theming gallery's
# topbar can link to it through `reverse()` instead of hardcoding a path the
# host project may never have routed — which is what made that link 404.
#
# Guarded rather than imported unconditionally: `djust/theming/urls.py` is
# imported at URLconf load, and a project that has not installed
# `djust.components` must not fail to boot because this app wanted to offer a
# link to it.
if apps.is_installed("djust.components"):
    urlpatterns.append(
        path("components/", include("djust.components.gallery.urls")),
    )
