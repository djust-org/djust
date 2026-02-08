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
#   event_name  – str, the event that triggered the render (e.g. "increment")
#   view_name   – str, fully qualified view class name
#   html_size           – int, size of the full HTML in bytes
#   previous_html_size  – int or None, size of the previous render (None on first)
#   patch_count         – int or None, number of patches before compression
#   version             – int, current VDOM version
full_html_update = Signal()
