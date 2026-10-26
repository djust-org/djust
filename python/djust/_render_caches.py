"""Per-template render caches, and when they may be used.

Three costs repeated on every render of an unchanged template (reported
downstream by Point Guard, N-37 and N-39):

- the JIT's extraction source (``mixins/jit.py`` ``_jit_template_content``):
  the ``{% extends %}`` chain resolved in Rust and every ``{% include %}`` read
  from disk, then hashed;
- the dj-model allowlist (``RustLiveView.dj_model_fields(cache=True)``): a walk
  of the template and its includes, with the ``{% load %}`` hook for each.

Each is a function of the template's text and the template directories, so it
is kept per template while templates are cached: with ``DEBUG`` off, when
Django's cached loader also serves a template unchanged until restart. With
``DEBUG`` on, hot reload edits templates, so every render builds them again,
as before. A change of ``DEBUG``, ``TEMPLATES`` or ``INSTALLED_APPS``
(``override_settings``) and a hot-reload template refresh clear them.
"""

from __future__ import annotations

import logging
from typing import Any

from django.test.signals import setting_changed

logger = logging.getLogger(__name__)

_INVALIDATING_SETTINGS = ("DEBUG", "TEMPLATES", "INSTALLED_APPS")


def render_caches_enabled() -> bool:
    """Whether per-template render caches may be used: ``DEBUG`` is off."""
    from django.conf import settings

    return not settings.DEBUG


def clear_render_caches() -> None:
    """Forget every per-template render cache (JIT source, dj-model allowlist)."""
    from .mixins.jit import clear_template_source_caches

    clear_template_source_caches()
    try:
        from ._rust import clear_dj_model_fields_cache
    except ImportError:  # the extension is optional at import time
        return
    clear_dj_model_fields_cache()


def _on_setting_changed(**kwargs: Any) -> None:
    if kwargs.get("setting") in (*_INVALIDATING_SETTINGS, None):
        clear_render_caches()


setting_changed.connect(_on_setting_changed)
