"""LiveView-backed storybook pages.

The storybook's detail page was a plain Django view. That renders a component's
markup but cannot make it *work*: `dj-click` is a server event, and a plain view
ships no server to reach, so an accordion showed a chevron and did nothing when
clicked. The page that exists to demonstrate djust was demonstrating a dead
component.

The fix is the framework's own DEP-002 mechanism, the same one the component
gallery's `/lv/` pages use. A descriptor component declared as a class attribute
on a `LiveView` gets its event auto-registered
(`djust/components/base.py:686`, `Accordion.Meta.event = "accordion_toggle"`),
and `get_context_data` re-renders on every event. All this module adds is the
join between the two: the descriptor's state is merged into the example's
kwargs, so re-rendering produces the updated markup.

Note what this is NOT. It is not a client-side shim that intercepts `dj-click`
and toggles a class locally. That would have made the component *look* live
while the page demonstrated the opposite of the framework, and it is the
approach this module replaced.
"""

from typing import Any, Dict, Optional

from django.http import Http404

from djust import LiveView
from djust.decorators import event_handler

from djust.components.descriptors import (
    Accordion,
    Collapsible,
    Carousel,
    Dropdown,
    Modal,
    Sheet,
    Tabs,
    Tooltip,
)

#: Component name -> the descriptor instance that gives it live state.
#:
#: Keys are the storybook's component names; values are the descriptors the
#: component gallery uses for the same widgets. A name absent here is a
#: component whose examples are static markup — correct for a card or a badge,
#: and the page documents it the same way.
_INTERACTIVE = {
    "accordion": Accordion,
    "carousel": Carousel,
    "collapsible": Collapsible,
    "dropdown": Dropdown,
    "modal": Modal,
    "sheet": Sheet,
    "tabs": Tabs,
    "tooltip": Tooltip,
}


class StorybookSidebarMixin:
    """The sidebar's state and handlers, shared by every storybook page.

    The sidebar search box and the collapsible category headers were driven by a
    `<script>` in `storybook_base.html` that filtered `.sb-sidebar-link` elements
    by writing `style.display`. Three things were wrong with that on a page whose
    entire purpose is demonstrating djust:

    * the browser owned the state, on a framework that exists to keep it on the
      server;
    * the markup was the data source — a filtered-out link was still there, just
      hidden, so the sidebar and the server disagreed about what existed;
    * it could not survive a re-render, which is why the script also had to
      re-bind itself on every `djust:dom-update`.

    Both are server events now. Filtering in Python and then `{% regroup %}`-ing
    the *filtered* list also retires the empty-category cleanup the script
    hand-rolled — a category with no matches simply is not in the regrouped
    output.
    """

    def _init_sidebar(self, current_component: Optional[str] = None) -> None:
        from .storybook import build_storybook_index_context

        #: Every component, unfiltered — the sidebar's denominator.
        self.all_components = build_storybook_index_context().get("components", [])
        #: What the sidebar actually renders. Kept as real state rather than a
        #: template-side filter so the server and the DOM cannot disagree.
        self.sidebar_components = list(self.all_components)
        self.search_query = ""
        self.collapsed_categories: list = []
        self.current_component = current_component

    @event_handler
    def search(self, value: str = "", **kwargs: Any) -> None:
        """`dj-input` on the sidebar search box."""
        self.search_query = value.strip()
        self._refresh_sidebar()
        self._on_search()

    @event_handler
    def toggle_category(self, value: str = "", **kwargs: Any) -> None:
        """`dj-click` on a sidebar category header."""
        collapsed = list(self.collapsed_categories)
        if value in collapsed:
            collapsed.remove(value)
        else:
            collapsed.append(value)
        self.collapsed_categories = collapsed

    def _refresh_sidebar(self) -> None:
        q = self.search_query.lower()
        if not q:
            self.sidebar_components = list(self.all_components)
            return
        self.sidebar_components = [
            c
            for c in self.all_components
            if q in c["display_name"].lower() or q in c["name"].lower()
        ]

    def _on_search(self) -> None:
        """Hook for subclasses that also filter page content by the query."""


class StorybookAccessMixin:
    """The gallery's own access gate, honoured on every transport.

    The index and category pages used to call `views._check_access()` from a
    plain Django function. That covers only the initial HTTP GET: mounted over a
    WebSocket they would have been open, because `check_view_auth` reads
    `login_required` / `permission_required` / `check_permissions`
    (`djust/auth/core.py:57-69`) and a plain function has none of them. Moving
    the same predicate onto the LiveView closes that without changing *who* can
    see the page — the rule is deliberately identical to `_check_access`.

    Deliberately NOT on `StorybookSidebarMixin`. `StorybookDetailView` has never
    been gated (it sets `login_required = False` and checks nothing), so putting
    this on the shared mixin would silently change who can reach the detail page
    — a separate defect, tracked on its own rather than fixed in passing here.
    """

    def check_permissions(self, request: Any) -> None:
        from django.conf import settings
        from django.core.exceptions import PermissionDenied

        gallery_public = getattr(settings, "DJUST_THEMING_GALLERY_PUBLIC", settings.DEBUG)
        if gallery_public:
            return

        user = getattr(request, "user", None)
        if not (
            user is not None
            and getattr(user, "is_authenticated", False)
            and getattr(user, "is_staff", False)
        ):
            raise PermissionDenied("Gallery is only available in DEBUG mode or for staff users.")


class StorybookDetailView(StorybookSidebarMixin, LiveView):
    """One component's storybook page, with its examples actually working."""

    template_name = "djust_theming/gallery/storybook_detail.html"
    login_required = False

    # Declared as class attributes so `__set_name__` registers them in
    # `_component_descriptors` and the framework wires each one's event.
    # A component with no descriptor here simply has no live state.
    accordion = Accordion()
    carousel = Carousel()
    collapsible = Collapsible()
    dropdown = Dropdown()
    modal = Modal()
    sheet = Sheet()
    tabs = Tabs()
    tooltip = Tooltip()

    def mount(self, request: Any, component_name: Optional[str] = None, **kwargs: Any) -> None:
        from .component_registry import _COMPONENT_TO_CATEGORY
        from .storybook import build_storybook_detail_context
        from djust.theming.contracts import COMPONENT_CONTRACTS

        if not component_name or (
            component_name not in COMPONENT_CONTRACTS
            and component_name not in _COMPONENT_TO_CATEGORY
        ):
            raise Http404(f"Unknown component: {component_name}")

        try:
            ctx = build_storybook_detail_context(component_name)
        except KeyError as exc:
            raise Http404(f"Unknown component: {component_name}") from exc

        self.component_name = component_name
        self._base_ctx = ctx
        #: The rating page's value. `None` until a star is clicked, so the
        #: example's own `value=` stands until then (see `_rating_state`).
        self._rating_value: Optional[int] = None
        self._init_sidebar(component_name)

    def _descriptor_state(self) -> Dict[str, Any]:
        """The live state of this component's descriptor, if it has one."""
        name = self.component_name
        if name == "rating":
            return self._rating_state()
        if name not in _INTERACTIVE:
            return {}
        state = getattr(self, name, None)
        # The descriptor resolves to its state dict once the framework has
        # bound it; before that (or for a non-interactive name) there is none.
        return dict(state) if isinstance(state, dict) else {}

    def _rating_state(self) -> Dict[str, Any]:
        """Rating is the one interactive component with no descriptor.

        `djust.components.descriptors` has no `Rating` — it covers the
        containers (tabs, modal, accordion), not value inputs — so there is
        nothing for `_INTERACTIVE` to point at and the component's
        `dj-click="set_rating"` reached no handler. The click produced a server
        error instead of a rating.

        Holding the number here rather than adding a descriptor is deliberate:
        a descriptor is public framework API, and inventing one to make a demo
        page work is the wrong order. If rating deserves one, it should be
        designed against the other value-input components, not here.
        """
        if self._rating_value is None:
            return {}
        return {"value": self._rating_value}

    @event_handler
    def set_rating(self, value: str = "", **kwargs: Any) -> None:
        """`dj-click` on a star. `data-value` is the star's 1-based position."""
        try:
            self._rating_value = int(value)
        except (TypeError, ValueError):
            # A malformed value leaves the current rating alone rather than
            # clearing it — the click was meaningless, not a request for zero.
            return

    def _render_examples(self) -> list[Dict[str, Any]]:
        """Render the component's examples against the CURRENT descriptor state.

        This is the whole trick. The examples are kwarg dicts, so merging the
        descriptor's state into them and re-rendering is what turns a click into
        updated markup: `accordion_toggle` sets `active`, `active` lands in the
        kwargs, and the re-render carries the open item.
        """
        from .component_registry import PYTHON_COMPONENT_EXAMPLES, render_python_component_example

        raw_examples = PYTHON_COMPONENT_EXAMPLES.get(self.component_name, [])
        state = self._descriptor_state()

        rendered = []
        for kwargs in raw_examples:
            live_kwargs = {**kwargs, **state}
            rendered.append(
                {
                    "html": render_python_component_example(self.component_name, live_kwargs),
                    "kwargs": live_kwargs,
                    "kwargs_display": ", ".join(f"{k}={v!r}" for k, v in live_kwargs.items()),
                }
            )
        return rendered

    def _render_live_template_examples(self) -> list:
        """Template-component examples re-rendered against descriptor state.

        The python branch has always done this (`_render_examples`), which is why
        the accordion works there. Template components did not, so `tabs`,
        `dropdown` and `modal` — all three of which have a descriptor in
        `_INTERACTIVE` — rendered from their example kwargs alone and never moved
        when clicked. Their templates now carry `dj-click`, and without this join
        the event reaches the server, the state changes, the page re-renders and
        the markup comes back identical: a control that is wired to nothing.
        """
        from .storybook import _render_template_examples

        fallback = self._base_ctx.get("template_examples_html") or []
        state = self._descriptor_state()
        if not state:
            return fallback

        examples = self._base_ctx.get("examples") or []
        if not examples:
            return fallback

        return _render_template_examples(
            self.component_name, [{**example, **state} for example in examples]
        )

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        ctx.update(self._base_ctx)
        ctx["all_components"] = self.all_components
        ctx["current_component"] = self.current_component
        if self._base_ctx.get("component_type") == "python":
            ctx["python_examples_html"] = self._render_examples()
        else:
            ctx["template_examples_html"] = self._render_live_template_examples()
        return ctx


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------


class StorybookIndexView(StorybookAccessMixin, StorybookSidebarMixin, LiveView):
    """The storybook landing page: every component, filterable.

    Was a plain Django function view whose entire filtering behaviour was a
    `<script>` toggling `style.display` on the cards. The chips and the search
    box are server events now, so the grid and the server cannot disagree about
    what is being shown.
    """

    template_name = "djust_theming/gallery/storybook_index.html"
    login_required = False

    def mount(self, request: Any, **kwargs: Any) -> None:
        from .storybook import build_storybook_index_context

        ctx = build_storybook_index_context()
        self._init_sidebar()
        self.total_count = ctx["total_count"]
        self.components_by_category = ctx["components_by_category"]
        self.active_category = "all"
        self.visible_components = list(self.all_components)

    @event_handler
    def set_category(self, value: str = "", **kwargs: Any) -> None:
        """`dj-click` on a category chip. `"all"` clears the filter."""
        self.active_category = value or "all"
        self._filter_components()

    def _on_search(self) -> None:
        # The grid honours the sidebar's query too. That was a second,
        # separately-written `style.display` loop in storybook_index.html
        # duplicating the sidebar's own — one query, two code paths.
        self._filter_components()

    def _filter_components(self) -> None:
        q = self.search_query.lower()
        category = self.active_category
        self.visible_components = [
            c
            for c in self.all_components
            if (category == "all" or c["category"] == category)
            and (not q or q in c["display_name"].lower() or q in c["name"].lower())
        ]


# ---------------------------------------------------------------------------
# Category
# ---------------------------------------------------------------------------


class StorybookCategoryView(StorybookAccessMixin, StorybookSidebarMixin, LiveView):
    """One category's components. Same sidebar, so the same handlers."""

    template_name = "djust_theming/gallery/storybook_category.html"
    login_required = False

    def mount(self, request: Any, category: Optional[str] = None, **kwargs: Any) -> None:
        from .component_registry import COMPONENT_CATEGORIES, get_all_components_with_metadata

        if category not in COMPONENT_CATEGORIES:
            raise Http404(f"Unknown category: {category}")

        self.category = category
        self._init_sidebar()

        # Template components carry contract counts; python components have no
        # contract, which is why the enrichment is conditional.
        from djust.theming.contracts import COMPONENT_CONTRACTS

        enriched = []
        for comp in get_all_components_with_metadata():
            if comp["category"] != category:
                continue
            if comp["name"] in COMPONENT_CONTRACTS:
                contract = COMPONENT_CONTRACTS[comp["name"]]
                comp = dict(comp)
                comp["required_count"] = len(contract.required_context)
                comp["optional_count"] = len(contract.optional_context)
                comp["slot_count"] = len(contract.available_slots)
                comp["a11y_count"] = len(contract.accessibility)
            enriched.append(comp)

        self.category_components = enriched


# There is deliberately no `ThemeGalleryView` here.
#
# The theme gallery looks like a candidate for the same treatment as the
# storybook — it renders the same `theme_tabs` / `theme_modal` /
# `theme_dropdown` components, and they are descriptor-friendly. It cannot be
# one: `gallery.html` uses all 25 `{% theme_* %}` tags, and those are registered
# with **Django's** template engine only. Nothing registers them with djust's
# Rust engine, so as a LiveView the page raises on the first tag it meets:
#
#     RuntimeError: Template error: Invalid block tag on line 1:
#     'theme_button'. Did you forget to register or load this tag?
#
# Turning this into a LiveView means registering the theming library with the
# Rust engine first. Until that exists, the gallery stays a plain Django view
# and `components.js` drives its components — with a guard that makes it stand
# down on any page carrying a djust mount root, so LiveView pages never get
# both paths at once.
