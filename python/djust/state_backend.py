"""Deprecated alias of :mod:`djust.state_backends`, removed in djust 2.0."""

from ._deprecation import warn_deprecated

# Import-time warning: the frames above this module are the import system's,
# so stacklevel=2 points at the importing statement where Python can tell.
warn_deprecated(
    "djust.state_backend",
    since="1.4",
    removed_in="2.0.0",
    instead="djust.state_backends",
    stacklevel=2,
)

from .state_backends import *  # noqa: F401,F403,E402
