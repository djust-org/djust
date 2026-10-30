"""``djust.decorators.__all__`` lists every decorator the top level re-exports.

``djust`` exported ``action`` / ``is_action`` from ``djust.decorators`` while
``djust.decorators.__all__`` omitted them, so ``from djust.decorators import *``
silently missed a documented decorator. Derived from the lazy table, so a new
re-export cannot drift the same way.
"""

import djust
import djust.decorators as decorators

#: In ``djust.decorators.__all__`` but deliberately not at the top level.
DECORATORS_ONLY = {"StateProperty", "alters_data"}


def _top_level_from_decorators():
    return {name for name, (module, _attr) in djust._LAZY.items() if module == "djust.decorators"}


def test_every_top_level_decorator_is_in_decorators_all():
    missing = _top_level_from_decorators() - set(decorators.__all__)
    assert not missing, sorted(missing)


def test_the_only_decorators_extras_are_the_documented_ones():
    extra = set(decorators.__all__) - _top_level_from_decorators()
    assert extra == DECORATORS_ONLY, sorted(extra)


def test_every_decorators_all_name_exists():
    for name in decorators.__all__:
        assert hasattr(decorators, name), name
