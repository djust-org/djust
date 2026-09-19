from django.apps import apps
from django.urls import include, path
from django.views.generic import RedirectView

from . import views

app_name = "djust_theming"

urlpatterns = [
    path("theme.css", views.theme_css_view, name="theme_css"),
    path("deferred.css", views.deferred_theme_css_view, name="deferred_theme_css"),
    # The two browsable surfaces, named for what they hold. ``themes/`` was
    # ``gallery/`` and ``components/`` was ``gallery/storybook/``; both old
    # prefixes redirect below, permanently, for one release.
    path("themes/", include("djust.theming.gallery.urls")),
    path("components/", include("djust.theming.gallery.catalogue_urls")),
]

#: Old path -> the route that serves it now. ``RedirectView`` with
#: ``pattern_name`` carries captured kwargs, so a bookmarked component page
#: lands on the same component rather than on the index.
_MOVED = [
    ("gallery/", "djust_theming:gallery"),
    ("gallery/editor/", "djust_theming:editor"),
    ("gallery/editor/export/", "djust_theming:editor_export"),
    ("gallery/diff/", "djust_theming:diff"),
    ("gallery/storybook/", "djust_theming:components"),
    ("gallery/storybook/category/<str:category>/", "djust_theming:components_category"),
    ("gallery/storybook/<str:component_name>/", "djust_theming:components_detail"),
]
urlpatterns += [
    path(old, RedirectView.as_view(pattern_name=target, permanent=True)) for old, target in _MOVED
]

# The older component gallery lives in the optional ``djust.components`` app.
# It used to hold ``components/``; the catalogue has that path now, and the
# gallery keeps its own pages under ``components-legacy/`` rather than being
# removed in the same change that renamed the catalogue.
#
# Guarded rather than imported unconditionally: this module is imported at
# URLconf load, and a project that has not installed ``djust.components`` must
# not fail to boot because this app wanted to offer a link to it.
if apps.is_installed("djust.components"):
    urlpatterns.append(
        path("components-legacy/", include("djust.components.gallery.urls")),
    )
