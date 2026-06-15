"""Synthetic LiveView fixtures for the ``djust.V012`` sticky-child own-dj-view check.

V012 (#1803) flags a sticky-child LiveView (``sticky = True``) whose own
template root carries a ``dj-view`` attribute. The framework's
``{% live_render ... sticky=True %}`` wrapper already emits
``<div dj-view dj-sticky-view="<id>" ...>``; a child that also declares its
own ``dj-view`` produces a nested/duplicate ``dj-view`` inside the wrapper,
which breaks the child's client-side mount (events silently don't bind).

These classes use inline ``template`` strings (not ``template_name`` files)
so the V012 scanner can read the root markup directly from the class without
touching the filesystem. They are NOT real views — they exist purely so the
V012 walk over ``LiveView`` subclasses has resolvable sticky-child classes
covering every combination (sticky / not, dj-view on root / not).
"""

from __future__ import annotations

from djust import LiveView


class StickyChildWithOwnDjView(LiveView):
    """Sticky child whose OWN template root carries ``dj-view`` — the footgun.

    This is the V012 trigger: the ``{% live_render ... sticky=True %}`` wrapper
    already provides the ``dj-view`` binding, so this nested ``dj-view`` is
    wrong and the child's events silently won't bind in the browser.
    """

    sticky = True
    sticky_id = "v012_footgun"
    template = (
        '<div dj-root dj-view="djust.tests.checkviews_v012.StickyChildWithOwnDjView">'
        '<button dj-click="dismiss">x</button></div>'
    )


class StickyChildNoDjView(LiveView):
    """Correctly-written sticky child: no own ``dj-view`` on its root.

    The wrapper provides the binding. V012 must stay silent for this class.
    """

    sticky = True
    sticky_id = "v012_correct"
    template = '<div class="widget"><button dj-click="dismiss">x</button></div>'


class StickyChildDjViewInComment(LiveView):
    """Sticky child that documents the wrapper inside a ``{% comment %}`` block.

    This mirrors the demo's ``audio_player.html`` shape: the comment literally
    shows the wrapper tag ``<div dj-view dj-sticky-view=... >`` as an example,
    but the REAL root (``<div class="widget">``) has no ``dj-view``. V012 must
    strip comments before scanning so the example tag does NOT false-positive.
    """

    sticky = True
    sticky_id = "v012_comment"
    template = (
        "{% comment %}\n"
        "Rendered inside a sticky wrapper "
        '(``<div dj-view dj-sticky-view="v012_comment" dj-sticky-root ...>``) '
        "by the {% live_render %} tag — do not add another dj-view here.\n"
        "{% endcomment %}\n"
        '<div class="widget"><button dj-click="dismiss">x</button></div>'
    )


class NonStickyViewWithDjView(LiveView):
    """A normal page view (NOT sticky) whose root legitimately carries
    ``dj-view``. Page views REQUIRE ``dj-view`` to be browser-mountable, so
    V012 must NOT flag this — the whole point of the check is to distinguish
    sticky children (must not declare dj-view) from page views (must)."""

    template = (
        '<div dj-root dj-view="djust.tests.checkviews_v012.NonStickyViewWithDjView">'
        "<button dj-click='go'>x</button></div>"
    )
