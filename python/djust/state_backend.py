"""Deprecated alias of :mod:`djust.state_backends`, removed in djust 2.0."""

from ._deprecation import warn_deprecated

# Import-time warning. stacklevel=3 blames the importing statement: level 1 is
# warn_deprecated, level 2 is this module's body, and level 3 is the first frame
# past the import system (``warnings`` skips importlib's own frames). stacklevel=2
# would blame this file.
warn_deprecated(
    "djust.state_backend",
    since="1.4",
    removed_in="2.0.0",
    instead="djust.state_backends",
    stacklevel=3,
)

from .state_backends import *  # noqa: F401,F403,E402

# The names this shim exports are exactly its target's (not ``warn_deprecated``).
from .state_backends import __all__  # noqa: F401,E402
