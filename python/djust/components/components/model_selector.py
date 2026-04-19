"""Model Selector component for choosing AI models."""

import html
from typing import Optional

from djust import Component


class ModelSelector(Component):
    """Rich select for AI model selection with metadata display.

    Each option shows model name, description, context window size,
    and pricing tier badge.

    Usage in a LiveView::

        self.model_select = ModelSelector(
            name="model",
            options=[
                {
                    "value": "gpt-4",
                    "label": "GPT-4",
                    "description": "Most capable model",
                    "context_window": "128k",
                    "tier": "premium",
                },
                {
                    "value": "gpt-3.5",
                    "label": "GPT-3.5 Turbo",
                    "description": "Fast and cost-effective",
                    "context_window": "16k",
                    "tier": "standard",
                },
            ],
            value="gpt-4",
            event="select_model",
        )

    In template::

        {{ model_select|safe }}

    CSS Custom Properties::

        --dj-model-sel-bg: dropdown background
        --dj-model-sel-border: border color
        --dj-model-sel-radius: border radius
        --dj-model-sel-hover-bg: option hover background
        --dj-model-sel-active-bg: selected option background

    Args:
        name: Form field name
        options: List of model option dicts (value, label, description,
                 context_window, tier)
        value: Currently selected model value
        event: djust event fired on selection
        placeholder: Placeholder text
        disabled: Whether selector is disabled
        label: Optional label text
        custom_class: Additional CSS classes
    """

    TIER_LABELS = {
        "free": "Free",
        "standard": "Standard",
        "premium": "Premium",
        "enterprise": "Enterprise",
    }

    def __init__(
        self,
        name: str = "model",
        options: Optional[list] = None,
        value: str = "",
        event: str = "select_model",
        placeholder: str = "Select a model...",
        disabled: bool = False,
        label: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            name=name,
            options=options,
            value=value,
            event=event,
            placeholder=placeholder,
            disabled=disabled,
            label=label,
            custom_class=custom_class,
            **kwargs,
        )
        self.name = name
        self.options = options or []
        self.value = str(value) if value else ""
        self.event = event
        self.placeholder = placeholder
        self.disabled = disabled
        self.label = label
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        e_name = html.escape(self.name)
        e_event = html.escape(self.event or self.name)
        e_placeholder = html.escape(self.placeholder)
        disabled_attr = " disabled" if self.disabled else ""
        disabled_cls = " dj-model-sel--disabled" if self.disabled else ""

        cls = f"dj-model-sel{disabled_cls}"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        # Find selected option
        selected_opt = None
        for opt in self.options:
            if isinstance(opt, dict) and str(opt.get("value", "")) == self.value:
                selected_opt = opt
                break

        if selected_opt:
            selected_html = self._option_inner(selected_opt)
        else:
            selected_html = f'<span class="dj-model-sel__placeholder">{e_placeholder}</span>'

        # Build option list
        opt_parts = []
        for opt in self.options:
            if not isinstance(opt, dict):
                continue
            ov = str(opt.get("value", ""))
            active_cls = " dj-model-sel__opt--active" if ov == self.value else ""
            inner = self._option_inner(opt)
            opt_parts.append(
                f'<div class="dj-model-sel__opt{active_cls}" '
                f'data-value="{html.escape(ov)}" '
                f'dj-click="{e_event}" '
                f'role="option" aria-selected="{"true" if ov == self.value else "false"}">'
                f"{inner}</div>"
            )

        label_html = ""
        if self.label:
            label_html = f'<label class="dj-model-sel__label">{html.escape(self.label)}</label>'

        return (
            f'<div class="{cls}">'
            f"{label_html}"
            f'<input type="hidden" name="{e_name}" value="{html.escape(self.value)}">'
            f'<div class="dj-model-sel__trigger" tabindex="0" role="combobox" '
            f'aria-expanded="false" aria-haspopup="listbox"{disabled_attr}>'
            f"{selected_html}"
            f'<span class="dj-model-sel__chevron">&#9662;</span>'
            f"</div>"
            f'<div class="dj-model-sel__dropdown" role="listbox">'
            f"{''.join(opt_parts)}"
            f"</div></div>"
        )

    @staticmethod
    def _option_inner(opt):
        """Render inner HTML for a model option."""
        label = html.escape(str(opt.get("label", "")))
        desc = html.escape(str(opt.get("description", ""))) if opt.get("description") else ""
        ctx_win = (
            html.escape(str(opt.get("context_window", ""))) if opt.get("context_window") else ""
        )
        tier = str(opt.get("tier", "")).lower()
        tier_label = (
            html.escape(ModelSelector.TIER_LABELS.get(tier, tier.capitalize())) if tier else ""
        )

        parts = [f'<span class="dj-model-sel__name">{label}</span>']
        if desc:
            parts.append(f'<span class="dj-model-sel__desc">{desc}</span>')

        meta = []
        if ctx_win:
            meta.append(f'<span class="dj-model-sel__ctx">{ctx_win}</span>')
        if tier_label:
            safe_tier = html.escape(tier)
            meta.append(
                f'<span class="dj-model-sel__tier dj-model-sel__tier--{safe_tier}">'
                f"{tier_label}</span>"
            )
        if meta:
            parts.append(f'<span class="dj-model-sel__meta">{"".join(meta)}</span>')

        return f'<span class="dj-model-sel__info">{"".join(parts)}</span>'
