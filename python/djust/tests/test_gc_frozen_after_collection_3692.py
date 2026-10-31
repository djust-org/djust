"""#3692: what test collection built is out of every later garbage collection.

The root ``conftest.py`` calls ``gc.freeze()`` when collection finishes, because
a full collection of a worker holding every imported test module paused it for
up to 3.4 s, longer than the waits the WebSocket tests make. See its
``pytest_collection_finish`` for the measurement.

``gc.get_objects()`` leaves out the permanent generation, which is what makes
this checkable: an object created while this module was imported is absent from
it, one created now is present. The second assertion is the instrument check:
without it, "absent" would also be what a ``get_objects`` that returns nothing
reports.
"""

import gc

#: A GC-tracked container created at import, i.e. DURING collection.
_BUILT_WHILE_COLLECTING = []


def _tracked(obj) -> bool:
    return any(o is obj for o in gc.get_objects())


def test_the_heap_collection_built_is_in_the_permanent_generation():
    built_now = []
    assert _tracked(built_now), "gc.get_objects() must list objects created after the freeze"
    assert not _tracked(_BUILT_WHILE_COLLECTING), (
        "an object built during collection is still visited by every collection: "
        "the root conftest's pytest_collection_finish froze nothing"
    )
    assert gc.get_freeze_count() > 0
