"""Deprecated alias of :mod:`djust.template`, removed in djust 2.0.

Import from ``djust.template`` instead, and name
``djust.template.backend.DjustTemplateBackend`` in ``TEMPLATES``.
"""

from ._deprecation import warn_deprecated

# Import-time warning: the frames above this module are the import system's,
# so stacklevel=2 points at the importing statement where Python can tell.
warn_deprecated(
    "djust.template_backend",
    since="1.4",
    removed_in="2.0.0",
    instead="djust.template (in TEMPLATES: 'djust.template.backend.DjustTemplateBackend')",
    stacklevel=2,
)

from .template import *  # noqa: F401,F403,E402
