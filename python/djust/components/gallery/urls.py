"""URL patterns for the component gallery.

Include in your project's urls.py::

    path("gallery/", include("djust_components.gallery.urls")),
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
