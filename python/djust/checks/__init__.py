"""
Django system checks for the djust framework.

Registers checks with Django's check framework that also run via
``python manage.py check``. Categories:

- Configuration (C0xx) -- settings validation
- LiveView (V0xx) -- LiveView subclass validation
- Security (S0xx) -- AST-based security checks
- Templates (T0xx) -- template file scanning
- Code Quality (Q0xx) -- AST-based quality checks
- Accessibility (Y0xx) -- template ARIA/WCAG scanning
"""

# This package was split from a single checks.py module (#1822).
# Importing it fires every @register('djust') decorator (Django check
# discovery via AppConfig.checks) and re-exports every public + private
# symbol the test suite imports or monkeypatches by path.

import sys as _sys

from . import (  # noqa: F401  (imported for @register side effects + re-export)
    utils,
    configuration,
    integrations,
    components,
    security,
    templates,
    accessibility,
    quality,
)

_pkg = _sys.modules[__name__]
for _mod in (
    utils,
    configuration,
    integrations,
    components,
    security,
    templates,
    accessibility,
    quality,
):
    for _name in dir(_mod):
        # Skip dunders and the per-submodule ``import djust.checks as
        # _root`` alias (an implementation detail, not public surface).
        if _name.startswith("__") or _name == "_root":
            continue
        setattr(_pkg, _name, getattr(_mod, _name))
del _pkg, _mod, _name, _sys
