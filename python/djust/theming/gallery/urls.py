from django.urls import path

from . import views

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
    path("storybook/<str:component_name>/", views.storybook_detail_view, name="storybook_detail"),
]
