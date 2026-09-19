"""`hasattr(djust.components, x)` must answer, not recurse.

`djust.components` resolves the ~150 component classes lazily through a
module-level ``__getattr__``. Written as ``from . import components``, that
asks the import machinery for the attribute ``components`` on this module —
and until the submodule is bound, the lookup comes straight back into
``__getattr__``, which asks again. Any probe for a name that does not exist
recursed until the interpreter gave up.

It shipped in 1.2.0rc9 and broke consumers that probe for optional names:
djust-docs' symbol gate walks the public API with ``hasattr`` and died with
``RecursionError``. 1.2.0rc6 through rc8 are unaffected.
"""

import importlib
import sys

import pytest


@pytest.fixture
def components_module():
    """A freshly imported `djust.components`.

    The bug only bites while the submodule is unbound, so a module left
    imported by an earlier test would hide it.
    """
    for name in [n for n in sys.modules if n.startswith("djust.components")]:
        del sys.modules[name]
    return importlib.import_module("djust.components")


def test_a_missing_name_answers_false_instead_of_recursing(components_module):
    assert hasattr(components_module, "NoSuchComponentAnywhere") is False


def test_a_missing_name_raises_attribute_error(components_module):
    with pytest.raises(AttributeError, match="has no attribute"):
        components_module.NoSuchComponentAnywhere


def test_a_dunder_probe_does_not_recurse(components_module):
    # The import machinery probes dunders on the parent package; each miss
    # re-entered the hook.
    assert hasattr(components_module, "__wrapped__") is False


def test_the_lazy_classes_still_resolve(components_module):
    assert components_module.Accordion.__name__ == "Accordion"
    assert components_module.Badge.__name__ == "Badge"


def test_the_submodule_is_still_reachable_as_an_attribute(components_module):
    assert components_module.components.__name__ == "djust.components.components"


def test_a_name_defined_here_still_wins(components_module):
    # `__getattr__` is only consulted for a missing attribute, so the names
    # this package defines itself must not be shadowed by the subpackage.
    assert components_module.Component.__module__.startswith("djust.components")
