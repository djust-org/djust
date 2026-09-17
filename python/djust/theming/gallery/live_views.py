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


class StorybookDetailView(LiveView):
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
        from .storybook import build_storybook_detail_context, build_storybook_index_context
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
        # The template's `dj-view` names this class — the same contract the
        # component gallery's `_base.html` uses to mount its LiveViews.
        self.view_class_name = type(self).__name__
        self._base_ctx = ctx
        self.all_components = build_storybook_index_context().get("components", [])
        self.current_component = component_name

    def _descriptor_state(self) -> Dict[str, Any]:
        """The live state of this component's descriptor, if it has one."""
        name = self.component_name
        if name not in _INTERACTIVE:
            return {}
        state = getattr(self, name, None)
        # The descriptor resolves to its state dict once the framework has
        # bound it; before that (or for a non-interactive name) there is none.
        return dict(state) if isinstance(state, dict) else {}

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

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        ctx.update(self._base_ctx)
        ctx["all_components"] = self.all_components
        ctx["current_component"] = self.current_component
        if self._base_ctx.get("component_type") == "python":
            ctx["python_examples_html"] = self._render_examples()
        return ctx
