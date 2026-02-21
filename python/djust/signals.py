"""Django signals emitted by the djust framework.

These signals allow external packages (e.g. djust-monitor) to observe
framework events without tight coupling.
"""

from django.dispatch import Signal

# Sent when the VDOM engine falls back to a full HTML update instead of
# sending efficient patches.  Receivers get keyword arguments:
#
#   sender      – the LiveView class (e.g. TaskListView)
#   reason      – str, one of:
#       "first_render"       – no previous VDOM to diff against (mount)
#       "component_event"    – component VDOM separate from parent
#       "embedded_child"     – embedded child view always gets full HTML
#       "patch_compression"  – too many patches, HTML was smaller
#       "no_patches"         – Rust diff returned empty (template structure changed)
#       "no_change"          – diff produced 0 patches (state change outside root)
#   event_name  – str, the event that triggered the render (e.g. "increment")
#   view_name   – str, fully qualified view class name
#   html_size           – int, size of the full HTML in bytes
#   previous_html_size  – int or None, size of the previous render (None on first)
#   patch_count         – int or None, number of patches before compression
#   version             – int, current VDOM version
#
#   Diagnostic fields (present for "no_change" and "no_patches" reasons):
#   context_snapshot        – dict or None, template context keys + values (truncated)
#   html_snippet            – str or None, first 500 chars of rendered HTML
#   previous_html_snippet   – str or None, first 500 chars of previous render
full_html_update = Signal()

liveview_server_error = Signal()
"""
Sent whenever send_error() is called on the WebSocket consumer.

Kwargs sent:
    sender    (type)  — the LiveView class, or None if no view is mounted yet
    error     (str)   — human-readable error message sent to the client
    view_name (str)   — "module.ClassName" of the view, or "" if unknown
    context   (dict)  — extra kwargs passed to send_error() (e.g. validation_details)
"""
