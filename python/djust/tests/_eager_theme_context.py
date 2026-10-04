"""``theme_context`` with every lazy chunk rendered at once (#3028).

The processor returns ``LazyThemeHTML`` chunks that render on first read. The
older theming tests patch the tag bodies / ``generate_css_for_state`` around a
``theme_context(...)`` call and assert on what was rendered, so they call this
wrapper to read each chunk inside their patch window, which is what the
processor itself did before #3028. Tests of the lazy contract itself live in
``test_theming_lazy_context_3028.py`` and call the processor directly.
"""

from __future__ import annotations

from typing import Any

from djust.theming.context_processors import theme_context

CHUNK_KEYS = (
    "theme_head",
    "theme_panel",
    "theme_mode_toggle",
    "theme_preset_selector",
    "theme_switcher",
)


def eager_theme_context(request: Any) -> dict:
    ctx = theme_context(request)
    for key in CHUNK_KEYS:
        str(ctx[key])
    return ctx
