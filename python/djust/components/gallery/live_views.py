"""LiveView-based gallery views for interactive component browsing.

Uses descriptor-based components (DEP-002) for interactive state management.
Theme is handled at the site level via the gallery_theme context processor —
views don't need to know about theming.
"""

from django.http import Http404

from djust import LiveView
from djust.decorators import event_handler

from djust.components.descriptors import (
    Accordion,
    Collapsible,
    Modal,
    Sheet,
    Tabs,
)

from .views import _render_component_cards


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class GalleryCategoryMixin:
    """Base for per-category gallery views.

    Handles category validation, sidebar data, navigation, and component
    rendering.  Theme is NOT this mixin's concern — it comes from the
    ``gallery_theme`` context processor.

    Subclasses declare their category and interactive components::

        class LayoutGalleryView(GalleryCategoryMixin, LiveView):
            category_slug = "layout"
            accordion = Accordion()
            tabs = Tabs()
    """

    template_name = "djust_components/gallery/category.html"
    login_required = False
    category_slug = ""

    def mount(self, request, **kwargs):
        from .examples import CATEGORIES, CATEGORY_ORDER
        from .registry import get_gallery_data

        slug = self.category_slug
        if slug not in CATEGORIES:
            raise Http404(f"Unknown category: {slug}")

        self.category_label = CATEGORIES[slug]
        self.view_class_name = type(self).__name__

        # Sidebar
        data = get_gallery_data()
        categories = data["categories"]
        self.category_cards = [
            {
                "slug": s,
                "label": CATEGORIES.get(s, s.title()),
                "count": len(categories.get(CATEGORIES.get(s, s.title()), [])),
            }
            for s in CATEGORY_ORDER
        ]

        # Components for this category
        self.raw_components = categories.get(self.category_label, [])
        self.active_category = slug

        # Prev/next navigation
        idx = CATEGORY_ORDER.index(slug)
        self.prev_category = (
            {"slug": CATEGORY_ORDER[idx - 1], "label": CATEGORIES.get(CATEGORY_ORDER[idx - 1], "")}
            if idx > 0
            else None
        )
        self.next_category = (
            {"slug": CATEGORY_ORDER[idx + 1], "label": CATEGORIES.get(CATEGORY_ORDER[idx + 1], "")}
            if idx < len(CATEGORY_ORDER) - 1
            else None
        )

    def get_context_data(self, **kwargs):
        """Re-render component examples with current descriptor state."""
        self.rendered_components = []
        for comp in self.raw_components:
            ctx = self._build_extra_context(comp["name"])
            rendered_html = _render_component_cards([comp], extra_context=ctx)
            self.rendered_components.append(
                {
                    "name": comp["name"],
                    "label": comp["label"],
                    "type": comp["type"],
                    "rendered_html": rendered_html,
                }
            )
        return super().get_context_data(**kwargs)

    def _build_extra_context(self, comp_name):
        """Pass descriptor state into template rendering automatically."""
        descriptors = getattr(type(self), "_component_descriptors", {})
        if comp_name not in descriptors:
            return {}
        state = getattr(self, comp_name, None)
        if state is None or not isinstance(state, dict):
            return {}
        return dict(state)


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------


class GalleryIndexView(LiveView):
    """Landing page showing category cards with search."""

    template_name = "djust_components/gallery/index.html"
    login_required = False

    def mount(self, request, **kwargs):
        from .examples import CATEGORIES, CATEGORY_ORDER
        from .registry import get_gallery_data

        data = get_gallery_data()
        categories = data["categories"]
        self._category_cards = [
            {
                "slug": s,
                "label": CATEGORIES.get(s, s.title()),
                "count": len(categories.get(CATEGORIES.get(s, s.title()), [])),
            }
            for s in CATEGORY_ORDER
        ]
        self.category_cards = list(self._category_cards)
        self.total_count = sum(c["count"] for c in self._category_cards)
        self.search_query = ""
        self.filtered_cards = []
        self.filtered_count = 0

        # Flat list of all components for search
        self._all_components = []
        for s in CATEGORY_ORDER:
            label = CATEGORIES.get(s, s.title())
            for comp in categories.get(label, []):
                self._all_components.append(
                    {
                        "name": comp["name"],
                        "label": comp["label"],
                        "category_slug": s,
                        "category_label": label,
                    }
                )

    @event_handler
    def search(self, value="", **kwargs):
        self.search_query = value.strip()
        if not self.search_query:
            self.filtered_cards = []
            self.filtered_count = 0
        else:
            q = self.search_query.lower()
            results = [
                c
                for c in self._all_components
                if q in c["label"].lower()
                or q in c["name"].lower()
                or q in c["category_label"].lower()
            ]
            # Deduplicate by (category_slug, label) — show each unique label once
            seen = set()
            deduped = []
            for c in results:
                key = (c["category_slug"], c["label"])
                if key not in seen:
                    seen.add(key)
                    deduped.append(c)
            self.filtered_cards = deduped
            self.filtered_count = len(deduped)


# ---------------------------------------------------------------------------
# Per-category views
# ---------------------------------------------------------------------------


class LayoutGalleryView(GalleryCategoryMixin, LiveView):
    category_slug = "layout"
    accordion = Accordion()
    tabs = Tabs()
    collapsible = Collapsible()
    modal = Modal()
    sheet = Sheet()


class FormGalleryView(GalleryCategoryMixin, LiveView):
    category_slug = "form"


class DataGalleryView(GalleryCategoryMixin, LiveView):
    category_slug = "data"
    tabs = Tabs()


class OverlayGalleryView(GalleryCategoryMixin, LiveView):
    category_slug = "overlay"
    modal = Modal()
    sheet = Sheet()


class FeedbackGalleryView(GalleryCategoryMixin, LiveView):
    category_slug = "feedback"


class NavGalleryView(GalleryCategoryMixin, LiveView):
    category_slug = "navigation"
    accordion = Accordion()
    tabs = Tabs()


class IndicatorGalleryView(GalleryCategoryMixin, LiveView):
    category_slug = "indicator"


class TypographyGalleryView(GalleryCategoryMixin, LiveView):
    category_slug = "typography"


class MiscGalleryView(GalleryCategoryMixin, LiveView):
    category_slug = "misc"
    accordion = Accordion()
    tabs = Tabs()
    modal = Modal()
