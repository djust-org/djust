"""
Server-side helper utilities for djust-components.

Convenience functions that LiveView event handlers can import to reduce
boilerplate when working with component template tags.

Usage::

    from djust.components.helpers import push_toast, confirm_action

    class MyView(LiveView):
        @event_handler
        def save(self):
            self.toast = push_toast("Saved!", type="success")

        @event_handler
        def delete(self, item_id):
            state = confirm_action(
                message=f"Delete item {item_id}?",
                risk="high",
                approve_event="confirm_delete",
                reject_event="cancel_delete",
            )
            self.confirm = state
"""

from typing import Any, Dict, Optional


def push_toast(
    message: str,
    *,
    type: str = "info",
    duration: int = 3000,
    dismissible: bool = True,
    dismiss_event: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a dict suitable for passing to the ``{% dj_toast %}`` template tag.

    Returns a plain dict so the LiveView can store it on ``self`` and reference
    each key in the template via ``toast.message``, ``toast.type``, etc.

    Args:
        message: The notification text shown to the user.
        type: One of ``info``, ``success``, ``warning``, ``error``.
        duration: Auto-dismiss time in milliseconds. ``0`` means manual dismiss only.
        dismissible: Whether the toast shows a dismiss button.
        dismiss_event: Optional djust event name fired when the user dismisses.

    Returns:
        A dict with keys ``message``, ``type``, ``duration``, ``dismissible``,
        and ``dismiss_event``.

    Example::

        self.toast = push_toast("Changes saved!", type="success")

        # In template:
        # {% if toast %}
        #     {% dj_toast message=toast.message type=toast.type
        #        duration=toast.duration dismissible=toast.dismissible %}
        # {% endif %}
    """
    if type not in ("info", "success", "warning", "error"):
        raise ValueError(
            f"Invalid toast type {type!r}. Must be one of: info, success, warning, error."
        )
    if duration < 0:
        raise ValueError("duration must be >= 0")

    return {
        "message": message,
        "type": type,
        "duration": duration,
        "dismissible": dismissible,
        "dismiss_event": dismiss_event,
    }


def confirm_action(
    message: str,
    *,
    risk: str = "medium",
    approve_event: str = "confirm",
    reject_event: str = "cancel",
    approve_label: str = "Confirm",
    reject_label: str = "Cancel",
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a dict suitable for driving an ``{% approval_gate %}`` or custom
    confirmation UI.

    Returns a plain dict that can be stored on the LiveView and passed to the
    template context.

    Args:
        message: Explanation shown to the user (e.g. "Delete 47 records?").
        risk: Risk level — ``low``, ``medium``, ``high``, or ``critical``.
        approve_event: djust event name fired when the user approves.
        reject_event: djust event name fired when the user rejects.
        approve_label: Text for the approve button.
        reject_label: Text for the reject button.
        data: Optional dict of extra data to include (e.g. item IDs). The
            LiveView can use this in the approve/reject handlers.

    Returns:
        A dict with keys ``message``, ``risk``, ``approve_event``,
        ``reject_event``, ``approve_label``, ``reject_label``, ``pending``
        (always ``True``), and ``data``.

    Example::

        self.confirm = confirm_action(
            message="Delete this project?",
            risk="high",
            approve_event="confirm_delete",
            reject_event="cancel_delete",
            data={"project_id": project.id},
        )

        # In template:
        # {% if confirm.pending %}
        #     {% approval_gate message=confirm.message risk=confirm.risk
        #        approve_event=confirm.approve_event
        #        reject_event=confirm.reject_event
        #        approve_label=confirm.approve_label
        #        reject_label=confirm.reject_label %}
        # {% endif %}
    """
    valid_risks = ("low", "medium", "high", "critical")
    if risk not in valid_risks:
        raise ValueError(f"Invalid risk level {risk!r}. Must be one of: {', '.join(valid_risks)}.")

    return {
        "message": message,
        "risk": risk,
        "approve_event": approve_event,
        "reject_event": reject_event,
        "approve_label": approve_label,
        "reject_label": reject_label,
        "pending": True,
        "data": data or {},
    }
