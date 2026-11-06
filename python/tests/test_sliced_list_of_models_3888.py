"""#3888: a relation read under a filtered loop operand over a ``list[Model]`` is empty on the backend.

Diagnosed, NOT fixed here: closing it changes what the backend ships for a raw model (or adds an
engine mechanism), which is a design decision recorded in #3888. This file pins the measured
boundary so that decision, and any fix for it, is checked against Django's own engine.

The mechanism, measured symptom-up on REAL ``User`` / ``Group`` rows (Django's engine is the oracle):

* A raw ``Model`` in a list crosses into the engine as the DICT ``normalize_django_value`` makes of
  it (the serialization floor, ``crates/djust_core/src/lib.rs``: "a RAW Django model reaching Value
  conversion"). That dict holds the model's fields and forward-FK objects. It holds no manager
  (``groups``, ``user_permissions``) and no ``*_id`` attname.
* A read of anything the dict lacks is answered from the render sidecar, which is keyed by the
  TOP-LEVEL name: for ``{% for u in users %}`` the engine registers the alias ``u == users.<N>`` and
  resolves ``u.groups.all`` as ``users.N.groups.all`` against the raw list.
* A FILTERED operand registers no alias (``{% for u in users|slice:':2' %}``: ``set_loop_mapping`` /
  ``set_alias`` in ``crates/djust_templates/src/renderer.rs`` refuse an operand with a ``|``, because
  ``slice`` shifts indices and ``dictsort`` reorders; the same alias keys the safe-string marks, an
  XSS boundary), so ``u`` is the dict, the sidecar is unreachable, and the read is empty.
* A QuerySet, and a LiveView ``list[Model]``, agree because the TOP-LEVEL rows are path-JIT
  serialized (``_jit_serialize_queryset`` / ``_jit_serialize_model_list``): the relation data is IN
  the row, so no sidecar is needed. ``DjustTemplate.render`` JITs a ``QuerySet`` and a single
  ``Model`` and leaves a ``list[Model]`` / ``tuple[Model]`` raw.

Measured against a throwaway prototype of the Python option (path-JIT a top-level ``list[Model]``
and merge the rows over the normalized dict, so ``__str__`` and the fields stay): all 80 top-level
cells below turn red (strict xpass) and every control stays green, while the nested cells and the
``dictsort`` cells stay xfailed. That is the evidence for what the option reaches and does not.

So the trigger is not ``slice`` and not the list: it is a filter that yields a NEW list
(``slice`` with any bounds, ``first``, ``dictsort``, a chained ``slice``, ``{% with s=users|slice %}``)
over rows that carry no handle and no relation data. ``reversed`` (a loop keyword the engine maps
itself), ``last`` (the row here has no groups, which Django also prints empty), ``join`` and every
row FIELD agree. The cells below are the whole matrix; the xfails are strict, so a fix turns them red
and that is the prompt to remove the marker.

Engine path: the Django-backend engine; LiveView and the QuerySet are the controls.

Refs #3888, #3867, #3863, #3801, #2504, ADR-027.
"""

# ruff: noqa: F811

from __future__ import annotations

import html as html_lib

import pytest
from django.contrib.auth.models import Permission, User

from python.tests.test_queryset_all_traversal_3777 import (  # noqa: F401
    _backend_render,
    _django_render,
    _liveview_render,
    world,
)

#: A relation read through the loop variable ``u``, each spelling a different consumer of the sidecar.
READS = {
    "loop": "[{% for g in u.groups.all %}{{ g.name }},{% endfor %}]",
    "all-count": "[{{ u.groups.all.count }}]",
    "manager-count": "[{{ u.groups.count }}]",
    "all-first-name": "[{{ u.groups.all.first.name }}]",
    "empty-m2m-count": "[{{ u.user_permissions.count }}]",
}

#: Loop operands built over ``users`` by a filter that yields a new list.
FILTERED = {
    "slice-head": "{% for u in users|slice:':2' %}<R>{% endfor %}",
    "slice-tail": "{% for u in users|slice:'1:' %}<R>{% endfor %}",
    "slice-whole": "{% for u in users|slice:':3' %}<R>{% endfor %}",
    "slice-reversed": "{% for u in users|slice:'::-1' %}<R>{% endfor %}",
    "slice-of-slice": "{% for u in users|slice:':2'|slice:':2' %}<R>{% endfor %}",
    "dictsort": "{% for u in users|dictsort:'username' %}<R>{% endfor %}",
    "with-slice": "{% with s=users|slice:':2' %}{% for u in s %}<R>{% endfor %}{% endwith %}",
    "first": "{% with u=users|first %}<R>{% endwith %}",
}

#: The same reads where the operand has an alias (or the row carries its own data).
UNFILTERED = {
    "plain": "{% for u in users %}<R>{% endfor %}",
    "reversed-keyword": "{% for u in users reversed %}<R>{% endfor %}",
    "with-alias": "{% with s=users %}{% for u in s %}<R>{% endfor %}{% endwith %}",
}


def _source(operand: str, read: str) -> str:
    return operand.replace("<R>", READS[read])


def _ctx(shape: str) -> dict:
    users = User.objects.all().order_by("pk")
    return {
        "users": {
            "list[Model]": list(users),
            "tuple[Model]": tuple(users),
            "QuerySet": users,
        }[shape]
    }


def _got(render, source, shape) -> str:
    return html_lib.unescape(render(source, _ctx(shape))).replace("<!--dj-if-->", "")


def _expected(source, shape) -> str:
    return _django_render(source, _ctx(shape))


def _broken_cells() -> list:
    cells = []
    for shape in ("list[Model]", "tuple[Model]"):
        for op in FILTERED:
            for read in READS:
                cells.append(
                    pytest.param(
                        shape,
                        op,
                        read,
                        marks=pytest.mark.xfail(
                            strict=True,
                            reason="#3888: a raw model row has no handle under a filtered operand",
                        ),
                        id=f"{shape}-{op}-{read}",
                    )
                )
    return cells


@pytest.mark.django_db
class TestTheFilteredOperandOverListOfModels:
    """The recorded gap: every filtered operand x relation read over a raw ``list`` / ``tuple``."""

    @pytest.mark.parametrize("shape,op,read", _broken_cells())
    def test_the_backend_agrees_with_django(self, world, shape, op, read) -> None:
        source = _source(FILTERED[op], read)
        assert _got(_backend_render, source, shape) == _expected(source, shape)

    @pytest.mark.parametrize("op", list(FILTERED))
    @pytest.mark.parametrize("read", list(READS))
    def test_each_cell_expects_a_value(self, world, op, read) -> None:
        """The control for the strict xfails: Django prints a VALUE in every cell, so an xfail cannot
        be hiding a cell where both engines print only brackets (``last`` is excluded for exactly
        that reason: its row is in no group). It does not assert the backend DIFFERS: that is the
        xfail above, and it must be the only thing a fix turns red."""
        source = _source(FILTERED[op], read)
        expected = _expected(source, "list[Model]")
        assert expected.replace("[", "").replace("]", "").replace(",", "") != "", expected


@pytest.mark.django_db
class TestWhatAgreesSoTheBoundaryIsTheFilter:
    @pytest.mark.parametrize("shape", ["list[Model]", "tuple[Model]"])
    @pytest.mark.parametrize("op", list(UNFILTERED))
    @pytest.mark.parametrize("read", list(READS))
    def test_an_operand_with_an_alias_agrees(self, world, shape, op, read) -> None:
        """Same rows, same reads, no filter: the alias reaches the sidecar."""
        source = _source(UNFILTERED[op], read)
        assert _got(_backend_render, source, shape) == _expected(source, shape)

    @pytest.mark.parametrize("op", [op for op in FILTERED if op != "dictsort"])
    @pytest.mark.parametrize("read", list(READS))
    def test_a_queryset_agrees_because_its_rows_carry_the_data(self, world, op, read) -> None:
        """``dictsort`` is excluded: it reads a field named in its ARGUMENT, which the JIT rows do not
        carry (a different invariant, #3892; pinned in ``TestDictsortOverJitRows``)."""
        source = _source(FILTERED[op], read)
        assert _got(_backend_render, source, "QuerySet") == _expected(source, "QuerySet")

    @pytest.mark.parametrize("op", ["slice-head", "slice-reversed", "with-slice", "first"])
    @pytest.mark.parametrize("read", list(READS))
    def test_a_liveview_over_the_same_list_agrees(self, world, op, read) -> None:
        source = _source(FILTERED[op], read)
        assert _got(_liveview_render, source, "list[Model]") == _expected(source, "list[Model]")

    @pytest.mark.parametrize("op", list(FILTERED))
    def test_a_row_field_agrees_under_every_filter(self, world, op) -> None:
        """The dict DOES hold the fields: only what it lacks needs the sidecar."""
        source = FILTERED[op].replace("<R>", "[{{ u.username }}|{{ u.pk }}|{{ u }}]")
        assert _got(_backend_render, source, "list[Model]") == _expected(source, "list[Model]")

    def test_a_forward_foreign_key_object_agrees_under_a_slice(self, world) -> None:
        """A forward FK is in the dict (the serializer follows it), so it needs no sidecar."""
        source = "{% for p in perms|slice:':2' %}[{{ p.content_type.app_label }}]{% endfor %}"
        perms = list(Permission.objects.order_by("pk")[:3])
        got = html_lib.unescape(_backend_render(source, {"perms": perms}))
        assert got == _django_render(source, {"perms": perms})
        assert got == "[admin][admin]"

    def test_rows_that_carry_the_relation_agree_under_a_slice(self, world) -> None:
        """The mechanism in one cell: the SAME sliced loop over plain dict rows whose data is in the
        row prints the groups. What a raw model row lacks is the data (or a handle), not the filter."""
        rows = [
            {"groups": {"all": [{"name": "staff"}, {"name": "ops"}]}},
            {"groups": {"all": [{"name": "staff"}]}},
            {"groups": {"all": []}},
        ]
        source = "{% for u in users|slice:':2' %}" + READS["loop"] + "{% endfor %}"
        assert _backend_render(source, {"users": rows}) == "[staff,ops,][staff,]"


# -- a container: the same gap one level down, and not only for a list ----------------------------
NESTED = "{% for u in page.users|slice:':2' %}" + READS["loop"] + "{% endfor %}"


def _nested(shape: str) -> dict:
    return {"page": {"users": _ctx(shape)["users"]}}


@pytest.mark.django_db
class TestANestedNameIsTheSameGap:
    """The top-level JIT does not reach ``page.users``, so a QuerySet there has no relation data in
    its rows either: the gap is "a row with no handle and no data under a filtered operand", not
    "a list"."""

    @pytest.mark.parametrize("shape", ["list[Model]", "QuerySet"])
    @pytest.mark.xfail(
        strict=True, reason="#3888: a row under a filtered operand has no handle or relation data"
    )
    def test_the_backend_agrees_with_django(self, world, shape) -> None:
        got = html_lib.unescape(_backend_render(NESTED, _nested(shape)))
        assert got == _django_render(NESTED, _nested(shape))

    @pytest.mark.parametrize("shape", ["list[Model]", "QuerySet"])
    def test_the_unfiltered_nested_loop_agrees(self, world, shape) -> None:
        source = "{% for u in page.users %}" + READS["loop"] + "{% endfor %}"
        got = html_lib.unescape(_backend_render(source, _nested(shape)))
        assert got == _django_render(source, _nested(shape)) == "[staff,ops,][staff,][]"

    def test_a_liveview_agrees_on_the_nested_list(self, world) -> None:
        got = html_lib.unescape(_liveview_render(NESTED, _nested("list[Model]")))
        assert got == _django_render(NESTED, _nested("list[Model]")) == "[staff,ops,][staff,]"


# -- a different invariant found while pinning the above (#3892) -----------------------------------
DICTSORT = "{% for u in users|dictsort:'username' %}{{ u.pk }},{% endfor %}"


@pytest.mark.django_db
class TestDictsortOverJitRows:
    """#3892: the sort key is a field named only in the filter ARGUMENT, so a path-JIT row (which holds
    only the fields the template reads) has none, and the loop renders EMPTY instead of sorted."""

    @pytest.mark.parametrize(
        "render,shape",
        [
            pytest.param(_backend_render, "QuerySet", id="backend-QuerySet"),
            pytest.param(_liveview_render, "QuerySet", id="liveview-QuerySet"),
            pytest.param(_liveview_render, "list[Model]", id="liveview-list"),
        ],
    )
    @pytest.mark.xfail(strict=True, reason="#3892: dictsort's key field is not in the JIT row")
    def test_agrees_with_django(self, world, render, shape) -> None:
        assert _got(render, DICTSORT, shape) == _expected(DICTSORT, shape)

    def test_reading_the_key_field_elsewhere_makes_it_agree(self, world) -> None:
        """The control, and why it is easy to miss: any other read of ``u.username`` fixes it."""
        source = "{% for u in users|dictsort:'username' %}{{ u.pk }}{{ u.username }},{% endfor %}"
        assert _got(_backend_render, source, "QuerySet") == _expected(source, "QuerySet")

    def test_the_raw_list_on_the_backend_agrees(self, world) -> None:
        assert _got(_backend_render, DICTSORT, "list[Model]") == _expected(DICTSORT, "list[Model]")
