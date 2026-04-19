"""Comparison Table component — SaaS pricing/feature comparison."""

import html
from typing import Optional

from djust import Component


class ComparisonTable(Component):
    """Feature comparison table for SaaS plans / product tiers.

    Usage in a LiveView::

        self.compare = ComparisonTable(
            plans=[
                {"name": "Free", "price": "$0/mo"},
                {"name": "Pro", "price": "$19/mo", "highlighted": True},
                {"name": "Enterprise", "price": "Contact us"},
            ],
            features=[
                {"name": "Users", "values": ["1", "10", "Unlimited"]},
                {"name": "Storage", "values": ["1 GB", "50 GB", "500 GB"]},
                {"name": "API Access", "values": [False, True, True]},
                {"name": "SSO", "values": [False, False, True]},
            ],
        )

    In template::

        {{ compare|safe }}

    Args:
        plans: List of dicts with ``name``, optional ``price``, ``highlighted``
        features: List of dicts with ``name`` and ``values`` (list matching plans order).
                  Boolean values render as check/cross marks.
        event: djust click event for plan selection
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        plans: Optional[list] = None,
        features: Optional[list] = None,
        event: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            plans=plans,
            features=features,
            event=event,
            custom_class=custom_class,
            **kwargs,
        )
        self.plans = plans or []
        self.features = features or []
        self.event = event
        self.custom_class = custom_class

    @staticmethod
    def _render_value(val):
        """Render a cell value — booleans as check/cross, strings as text."""
        if val is True:
            return '<span class="dj-compare__check" aria-label="Yes">&#10003;</span>'
        if val is False:
            return '<span class="dj-compare__cross" aria-label="No">&#10007;</span>'
        return html.escape(str(val))

    def _render_custom(self) -> str:
        classes = ["dj-compare"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        if not self.plans:
            return f'<div class="{class_str}"><table class="dj-compare__table"></table></div>'

        e_event = html.escape(self.event) if self.event else ""
        num_plans = len(self.plans)

        # Header row
        header_cells = ['<th class="dj-compare__corner"></th>']
        for i, plan in enumerate(self.plans):
            if not isinstance(plan, dict):
                continue
            name = html.escape(str(plan.get("name", "")))
            price = html.escape(str(plan.get("price", "")))
            highlighted = plan.get("highlighted", False)
            hl_class = " dj-compare__plan--highlighted" if highlighted else ""

            click_attr = ""
            if e_event:
                click_attr = f' dj-click="{e_event}" data-value="{name}"'

            price_html = f'<div class="dj-compare__price">{price}</div>' if price else ""
            header_cells.append(
                f'<th class="dj-compare__plan{hl_class}"{click_attr}>'
                f'<div class="dj-compare__plan-name">{name}</div>'
                f"{price_html}</th>"
            )

        header = f"<thead><tr>{''.join(header_cells)}</tr></thead>"

        # Feature rows
        body_rows = []
        for feat in self.features:
            if not isinstance(feat, dict):
                continue
            fname = html.escape(str(feat.get("name", "")))
            values = feat.get("values", [])
            if not isinstance(values, list):
                values = []

            cells = [f'<th class="dj-compare__feature">{fname}</th>']
            for i in range(num_plans):
                val = values[i] if i < len(values) else ""
                highlighted = False
                if i < len(self.plans) and isinstance(self.plans[i], dict):
                    highlighted = self.plans[i].get("highlighted", False)
                hl_class = " dj-compare__cell--highlighted" if highlighted else ""
                cells.append(
                    f'<td class="dj-compare__cell{hl_class}">{self._render_value(val)}</td>'
                )
            body_rows.append(f"<tr>{''.join(cells)}</tr>")

        body = f"<tbody>{''.join(body_rows)}</tbody>"

        return (
            f'<div class="{class_str}">'
            f'<table class="dj-compare__table" role="grid">{header}{body}</table></div>'
        )
