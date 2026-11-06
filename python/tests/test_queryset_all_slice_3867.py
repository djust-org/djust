"""#3867 item 3: a QuerySet-method read under a ``|slice`` of the top-level name is a recorded gap.

``{% for u in users|slice:':2' %}[{{ u.groups.all.count }}:{% for g in u.groups.all %}...`` reads
``u.groups.all`` as rows AND through a method (#3801), so the rows are kept and the method is
answered from the render sidecar, which is keyed by the TOP-LEVEL name: the engine reaches
``users.N.groups.all.count`` for a loop variable bound over ``users`` by the alias ``u == users.<N>``.
A filtered operand registers no alias (``slice`` shifts indices: ``set_loop_mapping`` /
``set_alias`` in ``crates/djust_templates/src/renderer.rs`` refuse an operand with a ``|``, an XSS
boundary), so ``u`` is a row dict of the SERIALIZED ``users`` with no link to the raw row, and the
method reads empty where Django prints ``2``.

Since ADR-027 a bound value normally carries its own handle, which is why a plain object under
``rows|slice`` agrees (#2504, ``test_sidecar_on_all_render_paths_2501.py``). The JIT-serialized rows
are plain dicts and carry none. Closing this means shipping a handle-bearing row for a name that
needs one (a change to what the serializer ships for a QuerySet), not a narrowing in the extractor or
the sidecar, so it is DECLINED here with the measurement below and recorded so the decision is
mechanical:

* the four mixed-read spellings differ from Django on a QuerySet and on a ``list[Model]``, through
  the Django-backend engine and a LiveView (16 cells, each ``xfail(strict=True)``);
* the gap is exactly the mixed read: the SAME slice over a method-only read, over a plain loop, or
  over a row field agrees (the controls below), and so does the mixed read with no slice.

``TestASlicedListOfModelsOnTheBackend`` is a different finding made while measuring this (#3888): a
relation read inside a sliced loop over a ``list[Model]`` is empty on the Django-backend engine with
no kept rows involved.

Engine path: the Django-backend engine and a LiveView; Django's own engine is the oracle, on REAL
``User`` / ``Group`` rows (#1650 / #1638).

Refs #3867, #3863, #3801, #3888, #2504.
"""

# ruff: noqa: F811

from __future__ import annotations

import html as html_lib

import pytest

from python.tests.test_queryset_all_followups_3788 import MAKE_CONTEXT, RENDERS  # noqa: F401
from python.tests.test_queryset_all_residuals_3801 import E, LOOP, MIXED, U
from python.tests.test_queryset_all_traversal_3777 import (  # noqa: F401
    _backend_render,
    _django_render,
    world,
)

SLICE = "users|slice:':2'"

#: Mixed reads (rows and a method on one ``.all``) with the loop over a sliced ``users``.
MIXED_UNDER_SLICE = {
    "count-then-loop": "{% for u in "
    + SLICE
    + " %}[{{ u.groups.all.count }}:"
    + LOOP
    + "]{% endfor %}",
    "loop-then-count": "{% for u in "
    + SLICE
    + " %}["
    + LOOP
    + ":{{ u.groups.all.count }}]{% endfor %}",
    "with-the-slice": "{% with s="
    + SLICE
    + " %}{% for u in s %}[{{ u.groups.all.count }}:"
    + LOOP
    + "]{% endfor %}{% endwith %}",
    "slice-count-beside-an-unsliced-loop": "{% for u in "
    + SLICE
    + " %}[{{ u.groups.all.count }}]{% endfor %}"
    + U
    + "["
    + LOOP
    + "]"
    + E,
}

#: The same slice where nothing is read both ways: these agree, so the gap is the mixed read.
CONTROLS = {
    "row-field": "{% for u in " + SLICE + " %}{{ u.username }},{% endfor %}",
    "method-only": "{% for u in " + SLICE + " %}[{{ u.groups.all.count }}]{% endfor %}",
    "loop-only": "{% for u in " + SLICE + " %}[" + LOOP + "]{% endfor %}",
    "index-then-method": "{{ users.0.groups.all.count }}|{{ users.1.groups.all.count }}",
}


def _got(render, source, shape) -> str:
    return html_lib.unescape(render(source, MAKE_CONTEXT[shape]())).replace("<!--dj-if-->", "")


@pytest.mark.django_db
class TestAMixedReadUnderASliceIsADeclinedGap3867:
    @pytest.mark.xfail(
        strict=True, reason="#3867 item 3: the filtered operand has no sidecar alias; declined"
    )
    @pytest.mark.parametrize("render", RENDERS)
    @pytest.mark.parametrize("shape", list(MAKE_CONTEXT))
    @pytest.mark.parametrize("name", list(MIXED_UNDER_SLICE))
    def test_agrees_with_django(self, world, render, shape, name) -> None:
        source = MIXED_UNDER_SLICE[name]
        assert _got(render, source, shape) == _django_render(source, MAKE_CONTEXT[shape]())

    def test_each_spelling_is_a_real_difference(self, world) -> None:
        """The control for the xfails above: Django prints a count, and the native engine prints
        the rows but not the count (so an xfail is not hiding a template error)."""
        for source in MIXED_UNDER_SLICE.values():
            expected = _django_render(source, MAKE_CONTEXT["QuerySet"]())
            assert any(ch.isdigit() for ch in expected), expected
            assert _got(_backend_render, source, "QuerySet") != expected

    @pytest.mark.parametrize("render", RENDERS)
    @pytest.mark.parametrize("name", list(CONTROLS))
    def test_a_slice_without_a_mixed_read_agrees_on_a_queryset(self, world, render, name) -> None:
        source = CONTROLS[name]
        assert _got(render, source, "QuerySet") == _django_render(
            source, MAKE_CONTEXT["QuerySet"]()
        )

    @pytest.mark.parametrize("render", RENDERS)
    @pytest.mark.parametrize("name", ["count-then-loop", "loop-then-count"])
    def test_the_same_mixed_read_without_the_slice_agrees(self, world, render, name) -> None:
        for shape in MAKE_CONTEXT:
            source = MIXED[name]
            assert _got(render, source, shape) == _django_render(source, MAKE_CONTEXT[shape]())


@pytest.mark.django_db
class TestASlicedListOfModelsOnTheBackend:
    """#3888: no kept rows here; the relation read under the slice is empty on the backend only."""

    @pytest.mark.xfail(
        strict=True, reason="#3888: a relation read in a sliced loop over list[Model], backend"
    )
    @pytest.mark.parametrize("name", ["loop-only", "method-only"])
    def test_the_backend_agrees_with_django(self, world, name) -> None:
        source = CONTROLS[name]
        assert _got(_backend_render, source, "list[Model]") == _django_render(
            source, MAKE_CONTEXT["list[Model]"]()
        )

    @pytest.mark.parametrize("name", ["loop-only", "method-only"])
    def test_it_is_the_backend_only(self, world, name) -> None:
        """The control: the same template over the same list through a LiveView agrees."""
        from python.tests.test_queryset_all_traversal_3777 import _liveview_render

        source = CONTROLS[name]
        assert _got(_liveview_render, source, "list[Model]") == _django_render(
            source, MAKE_CONTEXT["list[Model]"]()
        )
