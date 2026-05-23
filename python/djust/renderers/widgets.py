"""Native widget vocabulary for LVN-II (ADR-019).

This module is the frozen tag vocabulary the ``NativeRenderer`` will
emit and the native clients (``djust-native-ios``, ``djust-native-android``)
will translate to platform widgets.

The vocabulary is the SwiftUI ∩ Jetpack Compose intersection — 12
widgets covering ~80% of typical app screens. Adding new widgets is a
versioned change: bumping the vocabulary requires a minor version bump
in djust core AND coordinated releases of both native client libraries.

See ``docs/adr/019-liveview-native.md`` §"Three layers" §3 for the
SwiftUI / Compose mapping table and the rationale for freezing at this
set in v1.

This module ships in LVN-II PR-1 (spec only). LVN-II PR-2 will use these
constants in ``NativeRenderer`` to validate emitted VNode tag names.
"""

from __future__ import annotations

from typing import FrozenSet

__all__ = [
    "WIDGET_TAGS",
    "EVENT_ATTRS",
    "STYLE_ATTRS",
    "is_widget_tag",
]


# ------------------------------------------------------------------ #
# Frozen widget vocabulary (v1)
# ------------------------------------------------------------------ #

#: The 12 widget tag names a native template may emit. Mirrored
#: 1:1 in djust-native-ios (SwiftUI) and djust-native-android (Compose).
#: ``frozenset`` is intentional — additions require an ADR.
WIDGET_TAGS: FrozenSet[str] = frozenset(
    {
        # Layout containers
        "Stack",  # SwiftUI VStack            / Compose Column
        "HStack",  # SwiftUI HStack            / Compose Row
        "ZStack",  # SwiftUI ZStack            / Compose Box
        # Leaf widgets
        "Text",  # SwiftUI Text              / Compose Text
        "Button",  # SwiftUI Button            / Compose Button
        "TextField",  # SwiftUI TextField         / Compose TextField
        "Toggle",  # SwiftUI Toggle            / Compose Switch
        "List",  # SwiftUI List              / Compose LazyColumn
        "Image",  # SwiftUI Image             / Compose Image
        # Layout helpers
        "ScrollView",  # SwiftUI ScrollView        / Compose Modifier.verticalScroll
        "Spacer",  # SwiftUI Spacer            / Compose Spacer
        "NavigationView",  # SwiftUI NavigationView    / Compose NavHost
    }
)

#: Event-handler attribute names a native template uses. Mirrors the
#: browser's ``dj-click`` family. ``dj-tap`` reads more natively on
#: mobile; both clients (iOS + Android) accept it for primary tap.
EVENT_ATTRS: FrozenSet[str] = frozenset(
    {
        "dj-tap",
        "dj-change",
        "dj-input",
    }
)

#: Style attribute names the native clients honor in v1. Intentionally
#: small — a constrained vocabulary avoids the "CSS-in-native" sprawl
#: that doomed earlier attempts. Anything outside this set is silently
#: ignored by the native client (no error — graceful degradation).
STYLE_ATTRS: FrozenSet[str] = frozenset(
    {
        "padding",
        "spacing",
        "alignment",
        "foregroundColor",
        "font",
    }
)


def is_widget_tag(tag: str) -> bool:
    """True iff ``tag`` is in the frozen v1 widget vocabulary.

    Used by ``NativeRenderer`` (LVN-II PR-2) to validate emitted VNode
    tags. A native template that uses an unknown tag fails loudly at
    render time so the author catches typos early.
    """
    return tag in WIDGET_TAGS
