from django.urls import path

from . import live_views, views

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
    # All three storybook pages are LiveViews. `dj-click` and `dj-input` are
    # server events, and a plain Django view has no server for them to reach —
    # which is why the index and category pages used to carry a `<script>` that
    # re-implemented filtering in the browser. `views.storybook_index_view` and
    # `views.storybook_category_view` are left in place but unrouted; the
    # `category/` route must stay above `<str:component_name>/` or it would be
    # swallowed as a component named "category".
    path("storybook/", live_views.StorybookIndexView.as_view(), name="storybook"),
    path(
        "storybook/category/<str:category>/",
        live_views.StorybookCategoryView.as_view(),
        name="storybook_category",
    ),
    path(
        "storybook/<str:component_name>/",
        live_views.StorybookDetailView.as_view(),
        name="storybook_detail",
    ),
]
