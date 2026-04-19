"""
CSS generation cache utilities.

All CSS generation convenience functions use ``functools.lru_cache`` so that
identical (theme_name, color_preset) / pack_name / design-system combinations
return the cached string on subsequent calls instead of rebuilding CSS from
scratch every time.

Call ``clear_css_cache()`` during development (or from a management command)
to force regeneration after modifying theme definitions.
"""


def clear_css_cache():
    """Clear all CSS generation caches.

    Calls ``cache_clear()`` on every cached convenience function:

    * ``css_generator.generate_theme_css``
    * ``theme_css_generator.generate_theme_css``
    * ``pack_css_generator.generate_pack_css``
    * ``design_system_css.generate_design_system_css``

    Safe to call at any time; subsequent CSS generation calls will simply
    repopulate the cache on demand.
    """
    from .css_generator import generate_theme_css as _color_css
    from .theme_css_generator import generate_theme_css as _theme_css
    from .pack_css_generator import generate_pack_css as _pack_css
    from .design_system_css import generate_design_system_css as _ds_css

    _color_css.cache_clear()
    _theme_css.cache_clear()
    _pack_css.cache_clear()
    _ds_css.cache_clear()
