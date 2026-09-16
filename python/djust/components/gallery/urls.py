"""URL patterns for the component gallery.

This app is OPTIONAL and is not pulled in by ``djust`` itself. Two steps are
required, and omitting either one fails in a way that is hard to read:

1. Add ``"djust.components"`` to ``INSTALLED_APPS``. The gallery's LiveView
   routes render templates under ``djust_components/gallery/``, which live in
   *this* app's ``templates/`` directory — and Django only scans an app's
   templates when the app is installed. Without it every ``/lv/`` route raises
   ``TemplateDoesNotExist: djust_components/gallery/index.html``, which reads
   like a packaging bug rather than a missing ``INSTALLED_APPS`` entry.

2. Include these patterns in your project's ``urls.py``::

       path("components/", include("djust.components.gallery.urls")),

   The module path is ``djust.components.gallery.urls``. Earlier revisions of
   this docstring said ``djust_components.gallery.urls``, which has never
   existed — ``djust_components`` is the *template* namespace, not a Python
   package.

For a one-off look without touching project URLs, ``manage.py
component_gallery`` serves the same gallery standalone on port 8765.
"""

from django.urls import path

from .live_views import (
    GalleryIndexView,
    LayoutGalleryView,
    FormGalleryView,
    DataGalleryView,
    OverlayGalleryView,
    FeedbackGalleryView,
    NavGalleryView,
    IndicatorGalleryView,
    TypographyGalleryView,
    MiscGalleryView,
)
from .views import gallery_category_view, gallery_index_view, gallery_view

# Per-category LiveView routes
_category_views = {
    "layout": LayoutGalleryView,
    "form": FormGalleryView,
    "data": DataGalleryView,
    "overlay": OverlayGalleryView,
    "feedback": FeedbackGalleryView,
    "navigation": NavGalleryView,
    "indicator": IndicatorGalleryView,
    "typography": TypographyGalleryView,
    "misc": MiscGalleryView,
}

urlpatterns = [
    path("", GalleryIndexView.as_view(), name="gallery-index"),
    path("all/", gallery_view, name="gallery-all"),
    path("static-index/", gallery_index_view, name="gallery-static-index"),
    path("<slug:category_slug>/", gallery_category_view, name="gallery-category"),
]

# Add LiveView routes: /lv/layout/, /lv/form/, etc.
for slug, view_class in _category_views.items():
    urlpatterns.append(
        path(f"lv/{slug}/", view_class.as_view(), name=f"gallery-{slug}-lv"),
    )
