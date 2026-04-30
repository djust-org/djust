"""Rich Select component for programmatic use in LiveViews."""

import html
import re
from typing import Dict, List, Optional

from djust import Component


# Built-in variants for which djust ships CSS out of the box. Matches the
# Badge / Button / Tag / Alert signal set, plus the theme-standard
# `primary` / `secondary` for projects with more than 5 categories.
#
# This set is the canonical source of truth across the Component API
# (``rich_select.py``) and the templatetag (``djust_components.py``) —
# the templatetag imports from here rather than duplicating.
_BUILTIN_VARIANTS = frozenset(
    {
        "default",
        "info",
        "success",
        "warning",
        "danger",
        "muted",
        "primary",
        "secondary",
    }
)

# Any variant name consumers pass through must match this pattern so it can
# be safely interpolated into a CSS class attribute. Downstream projects can
# define their own variants (e.g. `indigo`, `accent-2`) by shipping a
# matching `.rich-select-option--variant-<name>` rule in their own CSS.
_VARIANT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def is_builtin_variant(name: str) -> bool:
    """Return ``True`` if ``name`` is one of the variants djust ships CSS for.

    Public helper for downstream consumers (Components, template tags,
    docs generators) that want to distinguish "djust ships this CSS"
    from "user shipped their own CSS for this name." Both are valid —
    the variant-name regex (``_VARIANT_NAME_RE``) is the gatekeeper —
    but the distinction matters for tooling that wants to warn on
    typos vs. honor genuinely-custom variants.
    """
    return name in _BUILTIN_VARIANTS


class RichSelect(Component):
    """Select dropdown where each option can include icons, images, descriptions,
    badges, or variant coloring alongside the label.

    Usage in a LiveView::

        self.assignee = RichSelect(
            name="assignee",
            options=[
                {"value": "alice", "label": "Alice", "icon": "A", "variant": "success"},
                {"value": "bob", "label": "Bob", "badge": "Admin", "variant": "info"},
            ],
            value="alice",
            event="select_assignee",
        )

    Or colour a picker from a value → variant map (convenience, mirrors
    ``Badge.status()``)::

        self.status_picker = RichSelect(
            name="status",
            options=[
                {"value": "NEW", "label": "New"},
                {"value": "SETTLED", "label": "Settled"},
                {"value": "DENIED", "label": "Denied"},
            ],
            value="NEW",
            event="set_status",
            variant_map={"NEW": "info", "SETTLED": "success", "DENIED": "danger"},
        )

    When an option has a variant, its row is tinted in the dropdown and —
    if it is the currently selected option — the trigger itself is tinted
    to match. Variants available: ``info``, ``success``, ``warning``,
    ``danger``, ``muted`` (plus the implicit ``default`` = no tint).

    In template::

        {{ assignee|safe }}

    Args:
        name: form field name
        options: list of dicts with keys: value, label, and optional icon, image,
                 description, badge, variant
        value: currently selected value
        event: dj-click event name for selection
        placeholder: text shown when nothing is selected
        disabled: disables the control; suppresses trigger variant tint
        searchable: adds search input to filter options
        label: optional label text
        variant_map: optional dict mapping option value → variant name;
                     applied to any option that doesn't already declare
                     its own ``variant`` key
    """

    def __init__(
        self,
        name: str = "",
        options: Optional[List[Dict]] = None,
        value: str = "",
        event: str = "",
        placeholder: str = "Select...",
        disabled: bool = False,
        searchable: bool = False,
        label: str = "",
        variant_map: Optional[Dict[str, str]] = None,
        **kwargs,
    ):
        super().__init__(
            name=name,
            options=options,
            value=value,
            event=event,
            placeholder=placeholder,
            disabled=disabled,
            searchable=searchable,
            label=label,
            variant_map=variant_map,
            **kwargs,
        )
        self.name = name
        self.options = options or []
        self.value = str(value) if value else ""
        self.event = event
        self.placeholder = placeholder
        self.disabled = disabled
        self.searchable = searchable
        self.label = label
        self.variant_map = variant_map or {}

    def _resolve_variant(self, opt: Dict) -> str:
        """Return the variant to render for a given option.

        Per-option ``variant`` key wins; otherwise the constructor's
        ``variant_map`` is consulted. The name is validated against
        ``_VARIANT_NAME_RE`` so it can be safely interpolated into a
        CSS class attribute — malformed or attacker-controlled names
        fall back to ``"default"``.

        Names outside the built-in set (``info``, ``success``, etc.) are
        accepted verbatim, which lets downstream projects add their own
        variants by shipping a matching
        ``.rich-select-option--variant-<name>`` CSS rule.
        """
        explicit = opt.get("variant", "")
        if explicit:
            return explicit if _VARIANT_NAME_RE.match(str(explicit)) else "default"
        mapped = self.variant_map.get(str(opt.get("value", "")), "")
        if mapped:
            return mapped if _VARIANT_NAME_RE.match(str(mapped)) else "default"
        return "default"

    def _render_custom(self) -> str:
        """Render the rich select HTML."""
        e_name = html.escape(self.name)
        e_placeholder = html.escape(self.placeholder)
        dj_event = html.escape(self.event or self.name)
        disabled_attr = " disabled" if self.disabled else ""
        disabled_cls = " rich-select--disabled" if self.disabled else ""

        # Find selected option
        selected_opt = None
        for opt in self.options:
            if isinstance(opt, dict) and str(opt.get("value", "")) == self.value:
                selected_opt = opt
                break

        # Trigger inherits the selected option's variant (when enabled).
        # Disabled pickers keep a neutral trigger so the "greyed out" cue
        # isn't competing with a bright colour signal.
        trigger_variant_cls = ""
        if selected_opt and not self.disabled:
            variant = self._resolve_variant(selected_opt)
            if variant != "default":
                trigger_variant_cls = f" rich-select-trigger--variant-{variant}"

        if selected_opt:
            selected_html = self._option_html(selected_opt)
        else:
            selected_html = f'<span class="rich-select-placeholder">{e_placeholder}</span>'

        # Build option list. Each option carries an onclick that closes the
        # dropdown — the subsequent dj-click round-trip re-renders with the
        # new value so the trigger updates its variant tint automatically.
        opt_parts = []
        for opt in self.options:
            if not isinstance(opt, dict):
                continue
            ov = str(opt.get("value", ""))
            active_cls = " rich-select-option--active" if ov == self.value else ""
            variant = self._resolve_variant(opt)
            variant_cls = f" rich-select-option--variant-{variant}" if variant != "default" else ""
            opt_parts.append(
                f'<div class="rich-select-option{active_cls}{variant_cls}" '
                f'data-value="{html.escape(ov)}" '
                f'dj-click="{dj_event}" '
                f'role="option" aria-selected="{"true" if ov == self.value else "false"}" '
                f"onclick=\"this.closest('.rich-select').classList.remove('rich-select--open')\">"
                f"{self._option_html(opt)}"
                f"</div>"
            )

        label_html = (
            f'<label class="form-label">{html.escape(self.label)}</label>' if self.label else ""
        )

        # Trigger carries both the open/close toggle (onclick + keyboard) and
        # its variant tint class. Keyboard handlers match the template-tag
        # variant so parity is maintained between both entry points. Disabled
        # pickers omit the handlers entirely.
        trigger_behaviour = (
            ""
            if self.disabled
            else " onclick=\"this.parentElement.classList.toggle('rich-select--open')\""
            " onkeydown=\"if(event.key==='Enter'||event.key===' '){event.preventDefault();"
            "this.parentElement.classList.toggle('rich-select--open');}\""
        )

        return (
            f'<div class="rich-select{disabled_cls}">'
            f"{label_html}"
            f'<input type="hidden" name="{e_name}" value="{html.escape(self.value)}">'
            f'<div class="rich-select-trigger{trigger_variant_cls}" '
            f'tabindex="0" role="combobox" '
            f'aria-expanded="false" aria-haspopup="listbox"{disabled_attr}'
            f"{trigger_behaviour}>"
            f"{selected_html}"
            f'<span class="rich-select-chevron">&#9662;</span>'
            f"</div>"
            f'<div class="rich-select-dropdown" role="listbox">'
            f"{''.join(opt_parts)}"
            f"</div>"
            f"</div>"
        )

    @staticmethod
    def _option_html(opt):
        """Render inner HTML for an option."""
        parts = []
        icon = opt.get("icon", "")
        image = opt.get("image", "")
        label = html.escape(str(opt.get("label", "")))
        description = opt.get("description", "")
        badge_text = opt.get("badge", "")

        if image:
            parts.append(
                f'<img class="rich-select-option-image" src="{html.escape(str(image))}" alt="">'
            )
        elif icon:
            parts.append(f'<span class="rich-select-option-icon">{html.escape(str(icon))}</span>')

        text_parts = [f'<span class="rich-select-option-label">{label}</span>']
        if description:
            text_parts.append(
                f'<span class="rich-select-option-desc">{html.escape(str(description))}</span>'
            )

        parts.append(f'<span class="rich-select-option-text">{"".join(text_parts)}</span>')

        if badge_text:
            parts.append(
                f'<span class="rich-select-option-badge">{html.escape(str(badge_text))}</span>'
            )

        return "".join(parts)
