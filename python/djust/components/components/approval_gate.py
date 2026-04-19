"""Approval Gate component for AI agent action confirmations."""

import html

from djust import Component


class ApprovalGate(Component):
    """Inline confirmation card for AI agent actions with risk levels.

    Displays a message with approve/reject buttons, styled by risk level.

    Usage in a LiveView::

        self.gate = ApprovalGate(
            message="Delete 47 records?",
            risk="high",
            approve_event="confirm",
            reject_event="cancel",
        )

        # Critical action
        self.deploy = ApprovalGate(
            message="Deploy to production?",
            risk="critical",
            approve_event="deploy_confirm",
            reject_event="deploy_cancel",
            approve_label="Deploy Now",
        )

    In template::

        {{ gate|safe }}

    CSS Custom Properties::

        --dj-approval-bg: card background
        --dj-approval-border: card border color
        --dj-approval-radius: border radius (default: 0.5rem)
        --dj-approval-approve-bg: approve button background
        --dj-approval-reject-bg: reject button background

        # Risk-level accent colors
        --dj-approval-low-color
        --dj-approval-medium-color
        --dj-approval-high-color
        --dj-approval-critical-color

    Args:
        message: Description of the action requiring approval
        risk: Risk level (low, medium, high, critical)
        approve_event: djust event fired on approval
        reject_event: djust event fired on rejection
        approve_label: Label for approve button (default: "Approve")
        reject_label: Label for reject button (default: "Reject")
        custom_class: Additional CSS classes
    """

    VALID_RISKS = {"low", "medium", "high", "critical"}

    def __init__(
        self,
        message: str = "",
        risk: str = "medium",
        approve_event: str = "approve",
        reject_event: str = "reject",
        approve_label: str = "Approve",
        reject_label: str = "Reject",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            message=message,
            risk=risk,
            approve_event=approve_event,
            reject_event=reject_event,
            approve_label=approve_label,
            reject_label=reject_label,
            custom_class=custom_class,
            **kwargs,
        )
        self.message = message
        self.risk = risk if risk in self.VALID_RISKS else "medium"
        self.approve_event = approve_event
        self.reject_event = reject_event
        self.approve_label = approve_label
        self.reject_label = reject_label
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        cls = f"dj-approval dj-approval--{self.risk}"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        e_msg = html.escape(self.message)
        e_approve_evt = html.escape(self.approve_event)
        e_reject_evt = html.escape(self.reject_event)
        e_approve_lbl = html.escape(self.approve_label)
        e_reject_lbl = html.escape(self.reject_label)

        risk_icon = {
            "low": (
                '<svg class="dj-approval__icon" viewBox="0 0 24 24" fill="none" '
                'stroke="currentColor" stroke-width="2" width="20" height="20">'
                '<circle cx="12" cy="12" r="10"/>'
                '<path d="M12 16v-4M12 8h.01"/></svg>'
            ),
            "medium": (
                '<svg class="dj-approval__icon" viewBox="0 0 24 24" fill="none" '
                'stroke="currentColor" stroke-width="2" width="20" height="20">'
                '<circle cx="12" cy="12" r="10"/>'
                '<path d="M12 16v-4M12 8h.01"/></svg>'
            ),
            "high": (
                '<svg class="dj-approval__icon" viewBox="0 0 24 24" fill="none" '
                'stroke="currentColor" stroke-width="2" width="20" height="20">'
                '<path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>'
                '<path d="M12 9v4M12 17h.01"/></svg>'
            ),
            "critical": (
                '<svg class="dj-approval__icon" viewBox="0 0 24 24" fill="none" '
                'stroke="currentColor" stroke-width="2" width="20" height="20">'
                '<path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>'
                '<path d="M12 9v4M12 17h.01"/></svg>'
            ),
        }

        icon = risk_icon.get(self.risk, risk_icon["medium"])
        risk_label = html.escape(self.risk.capitalize())

        return (
            f'<div class="{cls}" role="alert">'
            f'<div class="dj-approval__header">'
            f"{icon}"
            f'<span class="dj-approval__risk">{risk_label} Risk</span>'
            f"</div>"
            f'<div class="dj-approval__message">{e_msg}</div>'
            f'<div class="dj-approval__actions">'
            f'<button class="dj-approval__btn dj-approval__btn--reject" '
            f'dj-click="{e_reject_evt}">{e_reject_lbl}</button>'
            f'<button class="dj-approval__btn dj-approval__btn--approve" '
            f'dj-click="{e_approve_evt}">{e_approve_lbl}</button>'
            f"</div>"
            f"</div>"
        )
