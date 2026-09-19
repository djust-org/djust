from django.urls import path

from . import views

urlpatterns = [
    # A plain Django view, deliberately. The gallery renders all 25
    # `{% theme_* %}` tags, and those are registered with Django's template
    # engine only — nothing registers them with djust's Rust engine, so
    # rendering this page as a LiveView raises "Invalid block tag: 'theme_button'"
    # on the first one. Its interactivity therefore comes from `components.js`,
    # which stands down on any page that has a djust mount root (see the guard
    # in that file), so LiveView pages are server-driven by default and only
    # this kind of plain page falls back to the client.
    #
    # `xframe_options_sameorigin` because the diff page embeds this one in a
    # same-origin iframe.
    path("", views.gallery_view, name="gallery"),
    path("editor/", views.editor_view, name="editor"),
    path("editor/export/", views.editor_export_view, name="editor_export"),
    path("diff/", views.diff_view, name="diff"),
]
