"""
Django template Loader subclasses that normalize comment handling
between djust's Rust template engine and Django's classical engine.

## The problem (#1551)

djust ships two template renderers:

1. The Rust engine (``djust._rust.render_template_with_dirs``) — used for
   LiveView WebSocket responses and production page renders. Its lexer
   at ``crates/djust_templates/src/lexer.rs:289-305`` treats ``{# ... #}``
   as opaque even across newlines.

2. Django's classical engine — used by ``client.get()`` in pytest, by
   Django's debug error-page renderer, and by any view that uses
   ``render()`` directly. Its tokenizer (``django.template.base.Lexer``)
   uses a non-DOTALL regex; multi-line ``{# ... #}`` is NOT recognized
   as a single comment. A ``{% if %}`` inside the comment body gets
   parsed as a real tag → ``TemplateSyntaxError``.

The mismatch is silent: a template renders fine on the dev server
(Rust path) and crashes in CI or on error paths (classical path).

## The fix

These Loader subclasses preprocess ``{# ... #}`` blocks out of the
template source BEFORE Django classical's tokenizer sees them.
Single-line comments are also stripped — Django strips them anyway,
so doing it ourselves is uniform and safe.

## Usage

Replace the default Django loaders in your ``TEMPLATES`` setting::

    TEMPLATES = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [...],
            "APP_DIRS": False,  # explicit loaders shape required
            "OPTIONS": {
                "loaders": [
                    "djust.template.loaders.FilesystemLoader",
                    "djust.template.loaders.AppDirectoriesLoader",
                ],
                ...,
            },
        },
    ]

Or, equivalent and slightly more flexible, with the cached wrapper::

    "loaders": [
        ("django.template.loaders.cached.Loader", [
            "djust.template.loaders.FilesystemLoader",
            "djust.template.loaders.AppDirectoriesLoader",
        ]),
    ],

The preprocessing is a single regex pass and adds < 1 ms overhead per
template load. Loaded templates are cached by Django, so this runs once
per template, not per render.
"""

from __future__ import annotations

import re

from django.template.base import Origin
from django.template.loaders.app_directories import (
    Loader as DjangoAppDirectoriesLoader,
)
from django.template.loaders.filesystem import Loader as DjangoFilesystemLoader

__all__ = [
    "AppDirectoriesLoader",
    "FilesystemLoader",
    "strip_multiline_comments",
]


# Match `{# ... #}` non-greedily, across newlines (re.DOTALL).
#
# Non-greedy is load-bearing: `{# A #} text {# B #}` must produce ` text `,
# not `` (greedy would consume across both comments).
_MULTILINE_COMMENT_RE = re.compile(r"\{#.*?#\}", re.DOTALL)


def strip_multiline_comments(source: str) -> str:
    """Strip all ``{# ... #}`` blocks from a template source string.

    Both single-line and multi-line comments are removed; Django classical
    strips single-line comments anyway, so doing it ourselves is uniform
    and safe.

    Args:
        source: Template source code.

    Returns:
        Source with all ``{# ... #}`` blocks removed.

    Example:
        >>> strip_multiline_comments("{# hi #}<p>x</p>")
        '<p>x</p>'
        >>> strip_multiline_comments("{# A #} text {# B #}")
        ' text '
    """
    return _MULTILINE_COMMENT_RE.sub("", source)


class FilesystemLoader(DjangoFilesystemLoader):
    """Drop-in replacement for ``django.template.loaders.filesystem.Loader``
    that preprocesses multi-line ``{# ... #}`` comments out of the source
    before Django's tokenizer sees them.

    See module docstring for the asymmetry this fixes (#1551).
    """

    def get_contents(self, origin: Origin) -> str:
        source = super().get_contents(origin)
        return strip_multiline_comments(source)


class AppDirectoriesLoader(DjangoAppDirectoriesLoader):
    """Drop-in replacement for
    ``django.template.loaders.app_directories.Loader`` with the same
    ``{# ... #}`` preprocessing as :class:`FilesystemLoader`.
    """

    def get_contents(self, origin: Origin) -> str:
        source = super().get_contents(origin)
        return strip_multiline_comments(source)
