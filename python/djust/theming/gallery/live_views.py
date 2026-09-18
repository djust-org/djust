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
from djust.components.base import BoundComponent
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


# ---------------------------------------------------------------------------
# Demo events — the storybook hosting its own previews
# ---------------------------------------------------------------------------
#
# A component renders `dj-click="something"` because a host is expected to
# answer it; that is the contract, and the documentation for each tells a
# developer to write the handler. On a storybook page this view IS the host, so
# every event its own previews emit has to resolve. An unanswered `dj-click` is
# a server error, not a no-op — clicking it produced an error frame and a
# console traceback on a page whose whole job is to look dependable.
#
# Twenty events across seventeen components were unanswered. They are uniform
# enough to drive from one table: each names a state kwarg the component
# already accepts — or a list of them, when one event moves more than one —
# so re-rendering with those kwargs changed is the whole fix. This mirrors
# what DEP-002 already does for the container components, for the ones that
# have no descriptor to declare.


def _text(_current: Any, incoming: Any) -> Any:
    return incoming


def _as_int(_current: Any, incoming: Any) -> Any:
    try:
        return int(incoming)
    except (TypeError, ValueError):
        return None  # leave the kwarg out rather than write a bad one


def _flip(current: Any, _incoming: Any) -> Any:
    return not current


def _step(delta: int):
    """Add `delta` to the current value — carousel slides, date-picker months."""

    def _shift(current: Any, _incoming: Any) -> Any:
        try:
            return int(current) + delta
        except (TypeError, ValueError):
            return None

    return _shift


def _append_row(current: Any, _incoming: Any) -> Any:
    # `rows` is a list of `{"value": ...}` dicts (see `form_array`).
    return list(current or []) + [{"value": ""}]


def _add_tag(current: Any, incoming: Any) -> Any:
    tags = list(current or [])
    if incoming and incoming not in tags:
        tags.append(incoming)
    return tags


def _toggle_member(current: Any, incoming: Any) -> Any:
    """Add/remove `incoming` — `reactions` keeps its picks in a list."""
    active = list(current or [])
    if incoming in active:
        active.remove(incoming)
    else:
        active.append(incoming)
    return active


def _add_card(current: Any, _incoming: Any) -> Any:
    """Append a card to the first kanban column."""
    columns = [dict(c) for c in (current or [])]
    if not columns:
        return columns
    cards = list(columns[0].get("cards") or [])
    cards.append({"id": f"new-{len(cards)}", "title": "New card"})
    columns[0]["cards"] = cards
    return columns


def _toggle_node(current: Any, incoming: Any) -> Any:
    """Flip `expanded` on the tree node whose id is `incoming`."""

    def walk(nodes: Any) -> Any:
        out = []
        for node in nodes or []:
            node = dict(node)
            if node.get("id") == incoming:
                node["expanded"] = not node.get("expanded", False)
            if node.get("children"):
                node["children"] = walk(node["children"])
            out.append(node)
        return out

    return walk(current)


#: event name -> (the example kwarg it sets, how to compute the new value)
_DEMO_EVENTS: Dict[str, Any] = {
    "set_rating": ("value", _text),
    "rate_response": ("value", _text),
    "toggle_select": ("value", _text),
    "date_select": ("selected", _text),
    "set_step": ("active", _as_int),
    "date_prev_month": ("month", _step(-1)),
    "date_next_month": ("month", _step(1)),
    "toggle_expand": ("expanded", _flip),
    "toggle_preview": ("preview", _flip),
    "inline_edit": ("editing", _flip),
    "toggle_split_menu": ("is_open", _flip),
    "toggle_notifications": ("is_open", _flip),
    "toggle_sheet": ("is_open", _flip),
    "close_sheet": ("is_open", lambda _c, _v: False),
    "close_palette": ("is_open", lambda _c, _v: False),
    "accept_cookies": ("accepted", lambda _c, _v: True),
    "dismiss_alert": ("dismissed", lambda _c, _v: True),
    "add_row": ("rows", _append_row),
    # These three carry no state the component can be re-rendered with — the
    # host is expected to act on them itself (send a message, record a review).
    # Answered anyway, so the preview reports no failure; there is simply
    # nothing for the page to change.
    "approve": ("status", lambda _c, _v: "approved"),
    "reject": ("status", lambda _c, _v: "rejected"),
    "send": ("sent", lambda _c, _v: True),
    # Found by re-running the audit after the examples above gained content:
    # giving a component something to show also gives it something to click.
    # `carousel` emits next/prev only once it has slides, `data_table` emits a
    # sort event only once it has columns, and `color_picker` / `combobox` /
    # `tag_input` fall back to their `name` as the event name — so their
    # examples now pass an explicit `event` rather than making the handler
    # depend on what the example happened to be called.
    "dismiss_announcement": ("dismissed", lambda _c, _v: True),
    "carousel_next": ("active", _step(1)),
    "carousel_prev": ("active", _step(-1)),
    "set_color": ("value", _text),
    "set_language": ("value", _text),
    "add_tag": ("tags", _add_tag),
    "on_table_sort": ("sort_by", _text),
    "select_file": ("selected", _text),
    "tree_select": ("selected", _text),
    "tree_expand": ("nodes", _toggle_node),
    "clear_filters": ("active_count", lambda _c, _v: 0),
    "kanban_add_card": ("columns", _add_card),
    "react": ("active", _toggle_member),
    "toggle_sidebar": ("collapsed", _flip),
    "toggle_list": ("expanded", _flip),
    # `switch` is a checkbox whose slider is drawn from `.dj-switch-checked`,
    # a server-rendered class. Until the example named an `action` the input
    # carried no `dj-change`, so the box flipped its own `checked` property and
    # nothing else moved — the switch appeared not to toggle at all.
    "toggle_switch": ("checked", _flip),
    # `loading_overlay` is only visible while `active`, and `model_selector`
    # has no open state of its own to toggle.
    "toggle_loading": ("active", _flip),
    "toggle_model_selector": ("is_open", _flip),
    # Picking an option sets the value and closes the menu, so one event moves
    # two kwargs.
    "select_model": [("value", _text), ("is_open", lambda _c, _v: False)],
    # `segmented_progress` steps are buttons now, carrying their 1-based number;
    # `current` is 1-based too, so the value lands directly.
    "set_segment": ("current", _as_int),
}


def _make_demo_handler(event: str, effects: Any):
    """One `@event_handler` per event, named so dispatch finds it.

    `effects` is one `(key, transform)` pair, or a list of them when a single
    event moves more than one kwarg — `model_selector`'s `select_model` sets the
    chosen value *and* closes the menu, and a table that could only name one
    key would have to leave the menu hanging open.
    """
    pairs = effects if isinstance(effects, list) else [effects]

    # `value` is annotated `Any` rather than `str`: the framework validates a
    # handler's parameters against its annotations, and a checkbox's `dj-change`
    # sends a bool. Naming it `str` rejected the event before it ran —
    # "expected str, got bool (True)" — which left `switch` unable to toggle
    # even once its input carried `dj-change`.
    def handler(self: Any, value: Any = "", **kwargs: Any) -> None:
        for key, transform in pairs:
            if key not in self._demo_values:
                self._demo_values[key] = self._example_value(key)
            self._demo_values[key] = transform(self._demo_values[key], value)

    handler.__name__ = event
    handler.__qualname__ = event
    return event_handler(handler)


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
        #: What the demo handlers have set, keyed by example kwarg. Empty at
        #: mount, because `_render_examples` merges this dict into *every*
        #: example: seeding it from `examples[0]` made every preview on the page
        #: render the first example's kwargs. A two-state switch showed two
        #: switches both on, and five progress bars all read 25%. Keys land here
        #: only once the reader has moved them; `_make_demo_handler` reads the
        #: starting value from the example on first use, so a transform that
        #: steps (`carousel`'s `active`, `date_picker`'s `month`) still has a
        #: base to step from rather than computing `None + 1`.
        self._demo_values: Dict[str, Any] = {}
        self._init_sidebar(component_name)

    def _example_value(self, key: str) -> Any:
        """The starting value for a state kwarg, read from the first example.

        The examples already carry a sensible base for every kwarg a demo
        handler drives, so the event table does not have to restate them.
        """
        examples = self._base_ctx.get("examples")
        if not examples:
            from .component_registry import PYTHON_COMPONENT_EXAMPLES

            examples = PYTHON_COMPONENT_EXAMPLES.get(self.component_name) or []
        return examples[0].get(key) if examples else None

    def _descriptor_state(self) -> Dict[str, Any]:
        """Everything the preview should be re-rendered against.

        Two sources, merged: a DEP-002 descriptor's state when the component has
        one (accordion, tabs, modal, …), and `_demo_values` for the components
        the storybook hosts by hand. A component can need both — `sheet` has a
        `Sheet` descriptor whose event is `toggle_sheet`, while its own markup
        dispatches `close_sheet`, so the close button needs the second source
        even though the component is in `_INTERACTIVE`.
        """
        state: Dict[str, Any] = {}
        descriptor = getattr(self, self.component_name, None)
        # ADR-031: a class-level descriptor resolves to a `BoundComponent`
        # whose per-view state is `.state`. It is not a dict, so reading the
        # attribute straight into the merge — which is what this did before —
        # silently contributed nothing: `tabs`, `dropdown`, `modal` and the
        # other five interactive previews lost their state and stopped
        # responding. Same unwrap the components gallery applies
        # (`components/gallery/live_views.py:158`).
        if isinstance(descriptor, BoundComponent):
            descriptor = descriptor.state
        # ...or a plain dict, once the framework has bound it; before that
        # (or for a non-interactive name) there is none.
        if isinstance(descriptor, dict):
            state.update(descriptor)
        state.update(self._demo_state())
        return state

    def _demo_state(self) -> Dict[str, Any]:
        """State for the components whose interaction the storybook hosts.

        A component renders `dj-click="X"` because a host is expected to answer
        it — that is the contract. On a storybook page this view *is* the host,
        so every event its own previews emit has to resolve; an unanswered
        `dj-click` is a server error, not a no-op. Twenty of them were
        unanswered, which is why `rating` errored on click and so would have the
        other nineteen.

        Kept as one dict rather than a descriptor per component because most of
        these have a single state parameter (`value`, `active`, `expanded`,
        `is_open`) and the mapping is mechanical — see `_DEMO_EVENTS`.
        """
        return dict(self._demo_values)

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


# Install the demo handlers onto the detail view. `setattr` rather than twenty
# written-out methods: the framework wires its own descriptor events the same
# way (`components/base.py:__set_name__`), and a table keeps the event names,
# their state kwargs, and their transforms readable as one thing — which is what
# makes it obvious when a component gains an event and this table does not.
for _event, _effects in _DEMO_EVENTS.items():
    if not hasattr(StorybookDetailView, _event):
        setattr(StorybookDetailView, _event, _make_demo_handler(_event, _effects))
del _event, _effects


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
