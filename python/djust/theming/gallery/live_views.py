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

import functools
import json
from typing import Any, Dict, Optional

from django.http import Http404

from djust import LiveView
from djust.components.descriptors.base import LiveComponent, TypedState
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
        values = dict(self.state.values)
        for key, transform in pairs:
            if key not in values:
                values[key] = _example_value(self.state.examples, key)
            values[key] = transform(values[key], value)
        # Reassigned, not mutated in place: the State's dirty flag and the
        # change-detection snapshot both see the new dict.
        self.state.values = values

    handler.__name__ = event
    handler.__qualname__ = event
    return event_handler(handler)


def _example_value(examples: Any, key: str) -> Any:
    """The starting value for a state kwarg, read from the first example.

    The examples already carry a sensible base for every kwarg a demo handler
    drives, so the event table does not have to restate them. Module-level on
    purpose: the handlers run with ``self`` bound to the preview's
    ``BoundComponent``, which refuses ``_``-prefixed attribute lookups, so a
    private method on the component class is unreachable from them (#2921
    review 🔴1).
    """
    return examples[0].get(key) if examples else None


def _make_descriptor_handler(descriptor_cls: Any):
    """The preview's handler for a descriptor's event (`accordion_toggle`, …).

    The descriptor's own `_handle_event` decides what the click means — an
    accordion toggles, tabs select, a modal flips — against a State rebuilt
    from the preview's values, so the storybook does not restate any of it.
    """
    state_cls = descriptor_cls.State
    fields = [n for n in state_cls.__annotations__ if not n.startswith("_")]

    def handler(self: Any, value: Any = "", **kwargs: Any) -> None:
        current = self.state.values
        state = state_cls(**{k: current[k] for k in fields if k in current})
        descriptor_cls()._handle_event(state, value=value, **kwargs)
        self.state.values = {**current, **{k: state[k] for k in fields}}

    handler.__name__ = descriptor_cls.Meta.event
    handler.__qualname__ = descriptor_cls.Meta.event
    return event_handler(handler)


def render_preview_examples(
    component_name: str, component_type: str, examples: list, values: Dict[str, Any]
) -> list[Dict[str, Any]]:
    """Render a component's examples against the CURRENT preview values.

    Memoised on the arguments' JSON (a small LRU): a GET renders the examples
    once for the page's `styles` and again through `{{ preview }}`; a click
    renders them once for the new values.

    This is the whole trick. The examples are kwarg dicts, so merging the
    values into them and re-rendering is what turns a click into updated
    markup: `accordion_toggle` sets `active`, `active` lands in the kwargs,
    and the re-render carries the open item. Returns `[{"html", "kwargs",
    "kwargs_display"}]` for both component kinds, so the page has one preview
    mechanism rather than two.

    Template components go through their **tag**, not their template — those
    are different programs (`storybook._render_template_examples`).
    """
    key = (
        component_name,
        component_type,
        json.dumps(examples, sort_keys=True, default=str),
        json.dumps(values, sort_keys=True, default=str),
    )
    cached = _PREVIEW_RENDER_CACHE.get(key)
    if cached is not None:
        return cached
    rendered = _render_preview_examples(component_name, component_type, examples, values)
    if len(_PREVIEW_RENDER_CACHE) >= 64:
        _PREVIEW_RENDER_CACHE.clear()
    _PREVIEW_RENDER_CACHE[key] = rendered
    return rendered


_PREVIEW_RENDER_CACHE: Dict[Any, list] = {}


def _render_preview_examples(
    component_name: str, component_type: str, examples: list, values: Dict[str, Any]
) -> list[Dict[str, Any]]:
    if component_type == "python":
        from .component_registry import render_python_component_example

        rendered = []
        for kwargs in examples:
            live_kwargs = {**kwargs, **values}
            rendered.append(
                {
                    "html": render_python_component_example(component_name, live_kwargs),
                    "kwargs": live_kwargs,
                    "kwargs_display": ", ".join(f"{k}={v!r}" for k, v in live_kwargs.items()),
                }
            )
        return rendered

    from .storybook import _render_template_examples

    if not examples:
        return []
    return _render_template_examples(
        component_name, [{**example, **values} for example in examples]
    )


class Preview(LiveComponent):
    """The storybook page's live preview: one bound component owning the
    example state, rendered by `{% storybook_preview %}`.

    Why a component and not view attributes (ADR-032): an event that changes
    only this component's State is answered by re-rendering the preview and
    patching its subtree — the page, with its sidebar of `{% url %}` rows, is
    not rendered at all. That needs the state the previews depend on to live
    in ONE slot, so the descriptor state (accordion, tabs, modal, …) and the
    demo values the storybook hosts by hand are both `values` here, merged
    into every example the way `_render_examples` always did.

    The page template reads it as the bare `{{ preview }}` and nothing else,
    which is what keeps the template component-opaque for it.
    """

    class State(TypedState):
        component_name: str = ""
        component_type: str = ""
        #: The example kwarg dicts (`PYTHON_COMPONENT_EXAMPLES[name]` for a
        #: python component, the contract's examples for a template one).
        #: NOTE: these class-level `[]` / `{}` defaults are shared between
        #: `State()` instances; `mount` replaces both with fresh containers
        #: and the handlers reassign `values` rather than mutating it.
        examples: list = []
        #: Descriptor state + demo values, merged into every example.
        values: dict = {}
        #: The playground's overrides (`variant`, `size`, …) over the first
        #: example — the reader's choices, separate from `values` so the
        #: examples stay what they document.
        playground: dict = {}

    template = (
        "{% load theme_tags %}"
        "{% storybook_preview component_name component_type examples values playground %}"
    )

    @event_handler()
    def set_option(self, value: Any = "", **kwargs: Any) -> None:
        """A playground chip: ``dj-value="variant:ghost"`` / ``"disabled:true"``.

        ``:`` rather than ``=``: the client reads ``a=b`` in a ``dj-value`` as
        named params, so the handler would see an empty ``value``.
        """
        key, sep, raw = str(value).partition(":")
        if not sep or not key:
            return
        parsed: Any = raw
        if raw in ("true", "false"):
            parsed = raw == "true"
        self.state.playground = {**self.state.playground, key: parsed}


for _descriptor_cls in _INTERACTIVE.values():
    setattr(Preview, _descriptor_cls.Meta.event, _make_descriptor_handler(_descriptor_cls))
for _event, _effects in _DEMO_EVENTS.items():
    if not hasattr(Preview, _event):
        setattr(Preview, _event, _make_demo_handler(_event, _effects))
del _descriptor_cls, _event, _effects


def _make_forwarder(event: str):
    """A view-level `@event_handler` that forwards to the preview component.

    A click inside the rendered preview carries `component_id="preview"` and is
    routed to the component directly; an event sent without it — the shape the
    storybook tests use, and what the descriptors' `Meta.event` alias used to
    answer on the view — reaches this forwarder instead. Either way the only
    state that changes is the preview's, so both routes patch the preview alone.
    """

    def handler(self: Any, value: Any = "", **kwargs: Any) -> None:
        getattr(self.preview, event)(value=value, **kwargs)

    handler.__name__ = event
    handler.__qualname__ = event
    return event_handler(handler)


@functools.lru_cache(maxsize=1)
def _all_storybook_components() -> list:
    """The storybook index's component list, built once per process.

    The registry is static, so under `runserver` autoreload the list (and the
    sidebar count) refresh with the process, not with a template edit.
    """
    from .storybook import build_storybook_index_context

    return list(build_storybook_index_context().get("components", []))


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

    @property
    def _all_components(self) -> list:
        """Every component, unfiltered — the sidebar's denominator, and the
        source `_refresh_sidebar` filters from.

        A property over a process-wide cache rather than an instance
        attribute: no template reads it, and an assign is not free — it
        enters the render context and the LiveView state on every render (at
        175 components ~36 KB serialized per event, alongside
        `sidebar_components`, the list the template actually reads), and the
        change-detection snapshot walks every assign before and after each
        event (ADR-032 M4), so a 175-dict list that never changes was being
        fingerprinted twice per click.
        """
        return _all_storybook_components()

    #: Session key for the sidebar's "Recently viewed" group.
    _RECENT_KEY = "djust_storybook_recent"

    def _remember_visit(self, request: Any, component_name: str) -> None:
        """Push this component onto the visitor's recent list.

        Written on the HTTP GET only — that is the request whose session the
        middleware saves; the WebSocket mount's request is synthetic.
        """
        session = getattr(request, "session", None)
        if session is None or getattr(request, "method", "") != "GET":
            return
        try:
            recent = [n for n in list(session.get(self._RECENT_KEY, [])) if n != component_name]
            recent.insert(0, component_name)
            session[self._RECENT_KEY] = recent[:5]
        except Exception:  # noqa: BLE001 — a recents list is never worth a 500
            return
        self.recent_components = self._recent_entries(recent[:5])

    def _recent_entries(self, names: list) -> list:
        by_name = {c["name"]: c for c in self._all_components}
        return [by_name[n] for n in names if n in by_name and n != self.current_component]

    def _chrome_context(self) -> Dict[str, Any]:
        """What the page chrome renders — built for the theme components.

        The sidebar is `theme_nav_group`s (one per category, the expanded
        state on the server, items with `active`), the recents are one more
        group, the topbar is `theme_nav` items. Derived per render from the
        assigns, never stored: a rendered list of dicts is not state.
        """
        from django.urls import reverse

        current = getattr(self, "current_component", None)
        collapsed = set(getattr(self, "collapsed_categories", []) or [])

        def nav_item(comp: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "label": comp["display_name"],
                "url": reverse("djust_theming:storybook_detail", args=[comp["name"]]),
                "active": comp["name"] == current,
            }

        groups: list = []
        for comp in getattr(self, "sidebar_components", []) or []:
            if not groups or groups[-1]["category"] != comp["category"]:
                groups.append(
                    {
                        "category": comp["category"],
                        "expanded": comp["category"] not in collapsed,
                        "items": [],
                    }
                )
            groups[-1]["items"].append(nav_item(comp))
        for group in groups:
            group["count"] = str(len(group["items"]))

        section_items = [
            {"label": "Gallery", "url": reverse("djust_theming:gallery")},
            {"label": "Storybook", "url": reverse("djust_theming:storybook"), "active": True},
            {"label": "Editor", "url": reverse("djust_theming:editor")},
            {"label": "Diff", "url": reverse("djust_theming:diff")},
        ]
        live_url = getattr(self, "_components_gallery_url", None)
        if live_url:
            section_items.append({"label": "Live components", "url": live_url})
        return {
            "sidebar_groups": groups,
            "recent_nav_items": [nav_item(c) for c in getattr(self, "recent_components", [])],
            "section_items": section_items,
        }

    def _init_sidebar(self, current_component: Optional[str] = None) -> None:
        #: What the sidebar actually renders. Kept as real state rather than a
        #: template-side filter so the server and the DOM cannot disagree.
        self.sidebar_components = list(self._all_components)
        self.search_query = ""
        self.collapsed_categories: list = []
        self.current_component = current_component
        request = getattr(self, "request", None)
        session = getattr(request, "session", None)
        try:
            names = list(session.get(self._RECENT_KEY, [])) if session is not None else []
        except Exception:  # noqa: BLE001
            names = []
        self.recent_components = self._recent_entries(names)

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
            self.sidebar_components = list(self._all_components)
            return
        self.sidebar_components = [
            c
            for c in self._all_components
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

    #: The live preview (ADR-032): one bound component owning every example's
    #: state, so its events patch the preview alone. The descriptors in
    #: `_INTERACTIVE` supply the semantics of each event through its handlers.
    preview = Preview()

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
            # The examples are the preview's; the static context must not
            # render them a second time (#2921 review 🟡3).
            ctx = build_storybook_detail_context(component_name, render_examples=False)
        except KeyError as exc:
            raise Http404(f"Unknown component: {component_name}") from exc

        from .storybook import component_description

        ctx["description"] = component_description(component_name)
        self.component_name = component_name
        self._base_ctx = ctx
        self._init_sidebar(component_name)

        # The preview's state, per view (fresh containers — a TypedState's
        # class-level `[]` / `{}` defaults are shared objects).
        component_type = ctx.get("component_type") or "python"
        if component_type == "python":
            from .component_registry import PYTHON_COMPONENT_EXAMPLES

            examples = list(PYTHON_COMPONENT_EXAMPLES.get(component_name) or [])
        else:
            examples = list(ctx.get("examples") or [])
        # A DEP-002 descriptor's defaults seed the values, as the descriptor
        # slot's State did when it lived on the view. Demo values start empty:
        # `render_preview_examples` merges them into *every* example, so seeding
        # them from `examples[0]` would make the whole page show it repeated.
        descriptor_cls = _INTERACTIVE.get(component_name)
        values = dict(descriptor_cls.State()) if descriptor_cls is not None else {}
        preview = self.preview
        preview.state.component_name = component_name
        preview.state.component_type = component_type
        preview.state.examples = examples
        preview.state.values = values
        preview.state.playground = {}
        self._remember_visit(request, component_name)
        # `styles` ("what do I override?") is derived ONCE, at mount, from the
        # examples as the preview first renders them — the memo makes this and
        # the page's `{{ preview }}` one render. Not per event: an open item
        # would change the table and, with it, force a page render for what
        # is otherwise a preview-only click (ADR-032 D1).
        from .storybook import component_events, split_usage, styles_for, usage_with_events

        ctx.pop("styles", None)
        rendered = self._render_examples()
        self.styles = styles_for("".join(e["html"] for e in rendered))

        # Usage with its events: what the first example's markup emits, the
        # descriptor's class-level form when there is one, and for the
        # hand-hosted demo events the kwarg each one drives.
        events = component_events("".join(e["html"] for e in rendered[:1]))
        demo_keys = {
            ev: (effects[0][0] if isinstance(effects, list) else effects[0])
            for ev, effects in _DEMO_EVENTS.items()
        }
        ctx["usage_snippet"] = usage_with_events(
            ctx.get("usage_snippet", ""),
            events,
            descriptor_class=descriptor_cls.__name__ if descriptor_cls is not None else "",
            descriptor_event=descriptor_cls.Meta.event if descriptor_cls is not None else "",
            demo_keys=demo_keys,
            class_name=ctx.get("class_name") or "",
            example=examples[0] if examples else None,
        )
        ctx["usage_parts"] = split_usage(ctx["usage_snippet"])
        ctx["events"] = events
        # Rendered here through the Python component, not the `{% code_snippet %}`
        # tag: the Rust engine renders that tag natively and its output is not
        # highlighted (a gap noted for the docs pass). The component's own
        # `render()` is what the reader gets — highlighted, with `dj-copy`.
        from djust.components.components.code_snippet import CodeSnippet

        if ctx.get("template_source"):
            ctx["template_source_html"] = CodeSnippet(
                code=ctx["template_source"], language="django"
            ).render()
        ctx["usage_html"] = {
            "view": CodeSnippet(code=ctx["usage_parts"]["view"], language="python").render(),
            "template": CodeSnippet(
                code=ctx["usage_parts"]["template"], language="django"
            ).render(),
        }

    def _render_examples(self) -> list[Dict[str, Any]]:
        """The rendered examples, as the preview renders them now."""
        state = self.preview.state
        return render_preview_examples(
            state.component_name, state.component_type, state.examples, state.values
        )

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        ctx.update(self._base_ctx)
        ctx["current_component"] = self.current_component
        self._components_gallery_url = ctx.get("components_gallery_url")
        ctx["prev_component"], ctx["next_component"] = self._neighbours()
        ctx["props_count"] = len(ctx.get("required_context") or []) + len(
            ctx.get("optional_context") or []
        )
        ctx.update(self._chrome_context())
        ctx.update(self._doc_context(ctx))
        return ctx

    def _doc_context(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """The page's documentation blocks, shaped for the theme components:
        breadcrumb items, table rows, table-of-contents entries, the
        previous/next links."""
        from django.urls import reverse

        crumbs = [{"label": "Storybook", "url": reverse("djust_theming:storybook")}]
        if ctx.get("category"):
            crumbs.append(
                {
                    "label": ctx["category"],
                    "url": reverse("djust_theming:storybook_category", args=[ctx["category"]]),
                }
            )
        crumbs.append({"label": ctx.get("display_name", self.component_name), "url": ""})

        def default_of(p: Any) -> str:
            value = p.get("default")
            return "—" if value in (None, "") else str(value)

        props_rows = [
            [p["name"], p["type"], "required", default_of(p)]
            for p in ctx.get("required_context") or []
        ] + [[p["name"], p["type"], "", default_of(p)] for p in ctx.get("optional_context") or []]

        def type_name(annotation: Any) -> str:
            # `<class 'float'>` is the repr of a type, not a type name.
            text = str(annotation or "—")
            return text[8:-2] if text.startswith("<class '") and text.endswith("'>") else text

        params_rows = [
            [p["name"], type_name(p.get("annotation")), str(p.get("default", ""))]
            for p in ctx.get("python_params") or []
        ]
        a11y_rows = [
            [a["description"], a["selector_hint"], a["attr"], a.get("value") or "(present)"]
            for a in ctx.get("accessibility") or []
        ]
        styles_rows = []
        if ctx.get("template_path"):
            styles_rows.append(
                [
                    "template",
                    ctx["template_path"],
                    "Copy it to the same path in your project, or per theme under djust_theming/themes/<theme>/components/.",
                ]
            )
        if ctx.get("module_path"):
            styles_rows.append(
                [
                    "module",
                    ctx["module_path"],
                    "Subclass it, or pass custom_class, to change how it renders.",
                ]
            )
        for sheet in ctx.get("styles") or []:
            lines = ", ".join(str(n) for n in sheet.get("lines", []))
            styles_rows.append(
                [
                    "css",
                    sheet["path"],
                    f"Defines {len(sheet.get('classes', []))} of this component's classes at line {lines}. Override those rules, or the custom properties they read, in a stylesheet loaded after it.",
                ]
            )

        toc = [{"id": "sb-preview", "label": "Preview"}, {"id": "sb-usage", "label": "Usage"}]
        if props_rows:
            toc.append({"id": "sb-props", "label": "Props"})
        elif params_rows:
            toc.append({"id": "sb-props", "label": "Parameters"})
        if a11y_rows:
            toc.append({"id": "sb-a11y", "label": "Accessibility"})
        if ctx.get("available_slots"):
            toc.append({"id": "sb-slots", "label": "Slots"})
        toc.append({"id": "sb-source", "label": "Source & styles"})

        prev_c, next_c = ctx.get("prev_component"), ctx.get("next_component")
        pager = []
        if prev_c:
            pager.append(
                {
                    "label": "← " + prev_c["display_name"],
                    "url": reverse("djust_theming:storybook_detail", args=[prev_c["name"]]),
                }
            )
        if next_c:
            pager.append(
                {
                    "label": next_c["display_name"] + " →",
                    "url": reverse("djust_theming:storybook_detail", args=[next_c["name"]]),
                }
            )
        return {
            "crumbs": crumbs,
            "props_headers": ["Name", "Type", "Required", "Default"],
            "params_headers": ["Name", "Type", "Default"],
            "a11y_headers": ["Requirement", "Element", "Attribute", "Value"],
            "styles_headers": ["File", "Path", "Notes"],
            "props_rows": props_rows,
            "params_rows": params_rows,
            "a11y_rows": a11y_rows,
            "styles_rows": styles_rows,
            "toc_items": toc,
            "pager_items": pager,
        }

    def _neighbours(self) -> tuple:
        """The components before and after this one, in sidebar order."""
        names = self._all_components
        for i, comp in enumerate(names):
            if comp["name"] == self.component_name:
                prev_c = names[i - 1] if i > 0 else None
                next_c = names[i + 1] if i + 1 < len(names) else None
                return prev_c, next_c
        return None, None


for _event in (
    list(_DEMO_EVENTS) + [cls.Meta.event for cls in _INTERACTIVE.values()] + ["set_option"]
):
    if not hasattr(StorybookDetailView, _event):
        setattr(StorybookDetailView, _event, _make_forwarder(_event))
del _event


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
        self.visible_components = list(self._all_components)

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        self._components_gallery_url = ctx.get("components_gallery_url")
        ctx.update(self._chrome_context())
        ctx["category_options"] = [{"value": "all", "label": f"All ({self.total_count})"}] + [
            {"value": g["category"], "label": f"{g['category']} ({g['count']})"}
            for g in self.components_by_category
        ]
        return ctx

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
            for c in self._all_components
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

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        from django.urls import reverse

        ctx = super().get_context_data(**kwargs)
        self._components_gallery_url = ctx.get("components_gallery_url")
        ctx.update(self._chrome_context())
        ctx["crumbs"] = [
            {"label": "Storybook", "url": reverse("djust_theming:storybook")},
            {"label": self.category, "url": ""},
        ]
        return ctx


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
