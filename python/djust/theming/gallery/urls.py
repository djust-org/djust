from django.urls import path

from . import live_views, views

urlpatterns = [
    path("", views.gallery_view, name="gallery"),
    path("editor/", views.editor_view, name="editor"),
    path("editor/export/", views.editor_export_view, name="editor_export"),
    path("diff/", views.diff_view, name="diff"),
    path("storybook/", views.storybook_index_view, name="storybook"),
    path(
        "storybook/category/<str:category>/",
        views.storybook_category_view,
        name="storybook_category",
    ),
    # A LiveView, not `views.storybook_detail_view`. The examples have to
    # actually work when clicked, and `dj-click` is a server event — a plain
    # view has no server for it to reach. See live_views for the mechanism.
    path(
        "storybook/<str:component_name>/",
        live_views.StorybookDetailView.as_view(),
        name="storybook_detail",
    ),
]
