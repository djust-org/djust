"""#3888: a relation read under a filtered loop operand over a ``list[Model]`` agrees with Django.

Was: empty on the Django-backend engine. A raw ``Model`` crossed the engine boundary as the dict
``normalize_django_value`` makes of it (fields and forward-FK objects; no manager, no ``*_id``
attname), and whatever the dict lacked was read from the render sidecar through the loop alias
``u == users.<N>``. A FILTERED operand registers no alias (``set_loop_mapping`` / ``set_alias`` in
``crates/djust_templates/src/renderer.rs`` refuse an operand with a ``|``: ``slice`` shifts indices,
``dictsort`` reorders, and the same alias keys the safe-string marks, an XSS boundary), so the read
was empty.

Now: the model crosses as a floored live handle (``floored_model_handle`` in
``crates/djust_core/src/lib.rs``): the row carries its own handle through any filter (ADR-027), and the
handle is the floor proxy, so the floor is enforced per attribute read. This file is the matrix that
was pinned as strict xfails by the diagnosis PR; every marker the change fixes is gone. The full
differential (container x operand x read, four models) and the security matrix are in
``python/tests/test_model_live_handle_3888.py``.

What is still red, and why it is not this issue: a QuerySet or a LiveView row with ``dictsort`` over a
field the template reads nowhere else (#3892): the row is path-JIT serialized and has no such field.

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


def _cells() -> list:
    return [
        pytest.param(shape, op, read, id=f"{shape}-{op}-{read}")
        for shape in ("list[Model]", "tuple[Model]")
        for op in FILTERED
        for read in READS
    ]


@pytest.mark.django_db
class TestTheFilteredOperandOverListOfModels:
    """Every filtered operand x relation read over a raw ``list`` / ``tuple``."""

    @pytest.mark.parametrize("shape,op,read", _cells())
    def test_the_backend_agrees_with_django(self, world, shape, op, read) -> None:
        source = _source(FILTERED[op], read)
        assert _got(_backend_render, source, shape) == _expected(source, shape)

    @pytest.mark.parametrize("op", list(FILTERED))
    @pytest.mark.parametrize("read", list(READS))
    def test_each_cell_expects_a_value(self, world, op, read) -> None:
        """The control for the cells above: Django prints a VALUE in every cell, so an agreement
        cannot be two engines printing only brackets (``last`` is excluded for exactly that reason:
        its row is in no group)."""
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
    """The top-level JIT does not reach ``page.users``, so a QuerySet there had no relation data in
    its rows either: the gap was "a row with no handle and no data under a filtered operand", not
    "a list". Its rows are raw models, so they are handles now."""

    @pytest.mark.parametrize("shape", ["list[Model]", "QuerySet"])
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
