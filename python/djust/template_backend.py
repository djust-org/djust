"""Deprecated alias of :mod:`djust.template`, removed in djust 2.0.

Import from ``djust.template`` instead, and name
``djust.template.backend.DjustTemplateBackend`` in ``TEMPLATES``.
"""

from ._deprecation import is_announced, warn_deprecated

# Import-time warning. stacklevel=3 blames the importing statement: level 1 is
# warn_deprecated, level 2 is this module's body, and level 3 is the first frame
# past the import system (``warnings`` skips importlib's own frames). stacklevel=2
# would blame this file. Skipped when ``warn_deprecated_settings`` already warned
# about the same module through ``TEMPLATES`` (one warning per cause, not two).
if not is_announced("djust.template_backend"):
    warn_deprecated(
        "djust.template_backend",
        since="1.4",
        removed_in="2.0.0",
        instead="djust.template (in TEMPLATES: 'djust.template.backend.DjustTemplateBackend')",
        stacklevel=3,
    )

from .template import *  # noqa: F401,F403,E402

# The names this shim exports are exactly its target's (not ``warn_deprecated``).
from .template import __all__  # noqa: F401,E402
