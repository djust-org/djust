"""URLs for the component catalogue — the pages a developer browses to choose
and use a component.

Mounted at ``components/`` by :mod:`djust.theming.urls`, so the pages are
``/theme/components/``, ``/theme/components/category/<category>/`` and
``/theme/components/<component_name>/``. They were ``…/gallery/storybook/…``
until the catalogue took the name the docs and the site already use for it;
:mod:`djust.theming.urls` keeps the old paths as permanent redirects.

All three are LiveViews. ``dj-click`` and ``dj-input`` are server events, and a
plain Django view has no server for them to reach — which is why the index and
category pages used to carry a ``<script>`` that re-implemented filtering in
the browser. ``views.components_index_view`` and
``views.components_category_view`` remain for HTTP-only callers but are not
routed here.
"""

from django.urls import path

from . import live_views

urlpatterns = [
    # ``category/`` must stay above ``<str:component_name>/`` or it is
    # swallowed as a component named "category".
    path("", live_views.ComponentsIndexView.as_view(), name="components"),
    path(
        "category/<str:category>/",
        live_views.ComponentsCategoryView.as_view(),
        name="components_category",
    ),
    path(
        "interactive_dropdown_menu/",
        live_views.InteractiveDropdownCatalogueView.as_view(),
        name="components_interactive_dropdown_menu",
    ),
    path(
        "<str:component_name>/",
        live_views.ComponentsDetailView.as_view(),
        name="components_detail",
    ),
]
