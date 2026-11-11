"""#3892: a field named only in a filter ARGUMENT (``dictsort:'username'``) is a read of each row.

A QuerySet (or a ``list[Model]`` on a LiveView) reaches the engine as path-JIT rows: a row holds the
fields the template reads, and the extractor (``extract_template_variables``) is what says which. It
attributed ``u.<field>`` reads in a loop body and nothing else, so::

    {% for u in users|dictsort:'username' %}{{ u.pk }},{% endfor %}

gave ``users: ['pk']``, the rows had no ``username``, the sort found no key and (Django's own
``dictsort`` answers ``''`` for a missing key rather than raising) the loop rendered EMPTY. Reading
``u.username`` anywhere else in the template hid it.

The extractor is the Rust parser's (``crates/djust_templates/src/parser.rs``); the Python JIT, the
LiveView and ``DjustTemplateBackend`` all call it, so one change reaches every entry. Pinned here:

* the extractor's output for every spelling that reads a key off each item (``TestExtraction``);
* WHICH Django built-in filters read a named attribute, derived by probing Django itself
  (``TestWhichFiltersReadAKey``), so the two-name table is a measurement, not a recollection;
* the rendered result against Django's engine, through every entry the rows reach a template by
  (``TestRendersLikeDjango``);
* what must NOT change: a row read whole (``{{ u }}``) stays a whole row (``TestWholeRowsStayWhole``).

Engine path: the Rust parser's extraction, consumed by the Django-backend engine and by a LiveView.

Refs #3892, #3888, #1646.
"""

# ruff: noqa: F811

from __future__ import annotations

import html as html_lib

import pytest
from django.contrib.auth.models import Permission, User
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate
from django.template.defaultfilters import register as _builtin_filters

from djust import _rust
from python.tests.test_queryset_all_traversal_3777 import (  # noqa: F401
    _backend_render,
    _django_render,
    _liveview_render,
    world,
)
from python.tests.test_sliced_list_of_models_3888 import _ctx, _expected, _got

DICTSORT = "{% for u in users|dictsort:'username' %}{{ u.pk }},{% endfor %}"


def _pk_list(reverse: bool = False, suffix: bool = False, group: bool = False) -> str:
    """What Django prints for the three rows, from the pks the database actually gave them.

    The rows are created u0, u1, u2 in that order, which is also their username order. Their pks
    are NOT 1, 2, 3 in a full run (the sequence moves on with every earlier test that creates a
    user), so a literal pk in an expectation is an order dependence, not a claim.
    """
    from django.contrib.auth import get_user_model

    pks = list(get_user_model().objects.order_by("pk").values_list("pk", flat=True))
    names = [f"u{i}" for i in range(len(pks))]
    if reverse:
        pks, names = pks[::-1], names[::-1]
    if group:
        return "".join(f"{n}:{k}," for n, k in zip(names, pks))
    if suffix:
        return "".join(f"{k}{n}," for n, k in zip(names, pks))
    return "".join(f"{k}," for k in pks)


def _paths(source: str) -> dict[str, list[str]]:
    return {name: list(paths) for name, paths in _rust.extract_template_variables(source).items()}


# ---------------------------------------------------------------------------------------------
# The extractor
# ---------------------------------------------------------------------------------------------
class TestExtraction:
    """What the extractor says a template reads, for each spelling of "a field of each item"."""

    @pytest.mark.parametrize(
        "source,users",
        [
            pytest.param(
                "{% for u in users|dictsort:'username' %}{{ u.pk }}{% endfor %}",
                ["pk", "username"],
                id="dictsort-single-quote",
            ),
            pytest.param(
                '{% for u in users|dictsort:"username" %}{{ u.pk }}{% endfor %}',
                ["pk", "username"],
                id="dictsort-double-quote",
            ),
            pytest.param(
                "{% for u in users|dictsortreversed:'username' %}{{ u.pk }}{% endfor %}",
                ["pk", "username"],
                id="dictsortreversed",
            ),
            pytest.param(
                "{% for u in users|dictsort:'profile.name' %}{{ u.pk }}{% endfor %}",
                ["pk", "profile.name"],
                id="a-dotted-key-is-a-dotted-path",
            ),
            pytest.param(
                "{% for u in users|slice:':2'|dictsort:'username' %}{{ u.pk }}{% endfor %}",
                ["pk", "username"],
                id="after-a-slice",
            ),
            pytest.param(
                "{% for u in users|dictsort:'username'|slice:':2' %}{{ u.pk }}{% endfor %}",
                ["pk", "username"],
                id="before-a-slice",
            ),
            pytest.param(
                "{% for u in users|dictsort:'username'|dictsort:'email' %}{{ u.pk }}{% endfor %}",
                ["email", "pk", "username"],
                id="two-sorts-read-two-keys",
            ),
            pytest.param(
                "{% for u in users|dictsort:'username' reversed %}{{ u.pk }}{% endfor %}",
                ["pk", "username"],
                id="for-reversed",
            ),
            pytest.param(
                "{% with s=users|dictsort:'username' %}{% for u in s %}{{ u.pk }}{% endfor %}"
                "{% endwith %}",
                ["pk", "username"],
                id="through-a-with-alias",
            ),
            pytest.param(
                "{{ users|dictsort:'username'|length }}{% for u in users %}{{ u.pk }}{% endfor %}",
                ["pk", "username"],
                id="in-a-variable-node",
            ),
            pytest.param(
                "{% if users|dictsort:'username' %}x{% endif %}"
                "{% for u in users %}{{ u.pk }}{% endfor %}",
                ["pk", "username"],
                id="in-an-if-operand",
            ),
        ],
    )
    def test_the_key_is_a_field_of_every_item(self, source, users) -> None:
        assert _paths(source)["users"] == users

    def test_a_nested_iterable_files_the_key_under_its_own_path(self) -> None:
        source = (
            "{% for p in page.users|dictsort:'username' %}{{ p.pk }}{% endfor %}{{ page.title }}"
        )
        # ``users`` alone is the iterable read itself; the two fields are what each item reads.
        assert _paths(source)["page"] == ["title", "users", "users.pk", "users.username"]

    def test_an_inner_iterable_of_a_loop_variable_carries_the_key_up_to_the_list(self) -> None:
        source = (
            "{% for u in users %}{{ u.pk }}"
            "{% for g in u.groups.all|dictsort:'name' %}{{ g.pk }}{% endfor %}{% endfor %}"
        )
        assert _paths(source)["users"] == ["groups.all", "groups.all.name", "groups.all.pk", "pk"]

    def test_an_index_segment_in_the_iterable_path_is_not_part_of_it(self) -> None:
        """``rows.0.items`` and ``rows.items`` are the same list once the index is dropped, so the
        key is matched to the field reads beside it after that normalization."""
        source = "{% for r in rows.0.items|dictsort:'k' %}{{ r.pk }}{% endfor %}"
        assert _paths(source)["rows"] == ["items", "items.k", "items.pk"]

    def test_the_iterable_and_the_field_reads_may_spell_the_index_differently(self) -> None:
        """The key sits under ``rows.0.items``, the field read under ``rows.1.items``: after the
        indexes are dropped they are one list, and only then is the key matched to its sibling."""
        source = (
            "{% for r in rows.0.items|dictsort:'k' %}{% endfor %}"
            "{% for r in rows.1.items %}{{ r.pk }}{% endfor %}"
        )
        assert _paths(source)["rows"] == ["items", "items.k", "items.pk"]

    @pytest.mark.parametrize(
        "head",
        ['"abc"', "'abc'", "5"],
        ids=["double-quoted", "single-quoted", "number"],
    )
    def test_a_literal_head_names_no_list(self, head) -> None:
        """``{{ 5|dictsort:'k' }}`` has no list to put the key on, and must not mint a root for
        the literal (``5``, ``"abc"``)."""
        assert _paths("{{ " + head + "|dictsort:'k' }}") == {}

    def test_regroup_without_by_is_refused_before_extraction(self) -> None:
        """Why ``record_regroup_item_key`` does not re-check the keyword: the tag never gets this far."""
        with pytest.raises((RuntimeError, ValueError)):
            _rust.extract_template_variables("{% regroup users x username as g %}")

    # -- the key is only filed when the rows are read by field ------------------------------
    @pytest.mark.parametrize(
        "source",
        [
            "{% for u in users|dictsort:'username' %}{{ u }}{% endfor %}",
            "{{ users|dictsort:'username'|length }}",
            "{% for u in users|dictsort:'username' %}{% endfor %}",
        ],
    )
    def test_a_row_read_whole_files_no_key(self, source) -> None:
        """An empty path list means "serialize the row whole", which already carries the key and
        the ``__str__`` a bare ``{{ u }}`` prints. A one-field list would replace that."""
        assert _paths(source)["users"] == []

    def test_an_inner_list_read_whole_files_no_key(self) -> None:
        source = (
            "{% for u in users %}{{ u.pk }}"
            "{% for g in u.groups.all|dictsort:'name' %}{{ g }}{% endfor %}{% endfor %}"
        )
        assert _paths(source)["users"] == ["groups.all", "pk"]

    # -- a key that is not a literal field name ---------------------------------------------
    def test_a_variable_key_is_a_variable_read_and_names_no_field(self) -> None:
        """``dictsort:key`` takes its field from the context at render time. A static pass cannot
        know it: the variable is filed, no field is invented (#3910)."""
        paths = _paths("{% for u in users|dictsort:key %}{{ u.pk }}{% endfor %}")
        assert paths["users"] == ["pk"]
        assert paths["key"] == []

    @pytest.mark.parametrize(
        "key",
        ["_secret", "profile._secret", "", "a b", "a|b", "0", "1.x", ".x", "x.", "a..b"],
    )
    def test_a_key_that_is_not_a_plain_path_files_nothing(self, key) -> None:
        """Django refuses an underscore-led segment, and the key is a STRING, so the parser never
        checked it as a variable: without this the serializer would be asked for ``_secret``."""
        source = "{% for u in users|dictsort:'" + key + "' %}{{ u.pk }}{% endfor %}"
        assert _paths(source)["users"] == ["pk"]

    def test_a_numeric_literal_key_files_nothing(self) -> None:
        paths = _paths("{% for u in users|dictsort:0 %}{{ u.pk }}{% endfor %}")
        assert paths["users"] == ["pk"]

    # -- regroup names an attribute of each item too ----------------------------------------
    def test_regroup_by_is_a_field_of_every_item(self) -> None:
        source = (
            "{% regroup users by username as g %}{% for grp in g %}{{ grp.grouper }}{% endfor %}"
            "{% for u in users %}{{ u.pk }}{% endfor %}"
        )
        assert _paths(source)["users"] == ["pk", "username"]

    def test_regroup_alone_leaves_the_rows_whole(self) -> None:
        """The groups hold the rows; the source has no field singled out, so it stays whole."""
        source = (
            "{% regroup users by username as g %}"
            "{% for grp in g %}{% for u in grp.list %}{{ u.pk }}{% endfor %}{% endfor %}"
        )
        assert _paths(source)["users"] == []

    def test_regroup_reads_through_a_filtered_source_and_attribute(self) -> None:
        source = (
            "{% regroup users|dictsort:'email' by username|lower as g %}{{ g|length }}"
            "{% for u in users %}{{ u.pk }}{% endfor %}"
        )
        assert _paths(source)["users"] == ["email", "pk", "username"]

    # -- what is not a read of an item's field ------------------------------------------------
    @pytest.mark.parametrize(
        "operand",
        [
            "users|slice:':2'",
            "users|first",
            "users|join:', '",
            "users|unordered_list",
            "users|default:'x'",
        ],
    )
    def test_other_operands_file_no_key(self, operand) -> None:
        source = "{% for u in " + operand + " %}{{ u.pk }}{% endfor %}"
        assert _paths(source)["users"] == ["pk"]


# ---------------------------------------------------------------------------------------------
# The filter table is measured, not recalled
# ---------------------------------------------------------------------------------------------
class _Tripwire:
    """An item that records every name read off it, by attribute or by key."""

    def __init__(self, log: set) -> None:
        self._log = log

    def __getattr__(self, name):
        self.__dict__["_log"].add(name)
        raise AttributeError(name)

    def __getitem__(self, key):
        self._log.add(key)
        raise KeyError(key)

    def __str__(self) -> str:
        return "item"

    def __len__(self) -> int:
        return 1


def _filters_that_read_x() -> set[str]:
    """Every Django built-in filter that reads ``x`` off the items of an iterable it is given."""
    found = set()
    for name in sorted(_builtin_filters.filters):
        for form in ("{{ items|%s:'x' }}", "{{ items|%s }}"):
            log: set = set()
            items = [_Tripwire(log), _Tripwire(log)]
            try:
                template = DjangoTemplate("{% load %}" + form % name)
            except Exception:  # noqa: BLE001 — wrong arity for this form; try the other
                continue
            try:
                template.render(DjangoContext({"items": items}))
            except Exception:  # noqa: BLE001 — a filter may refuse the tripwires; the read is what counts
                pass
            if "x" in log:
                found.add(name)
            break
    return found


class TestWhichFiltersReadAKey:
    def test_exactly_the_two_sorts_read_a_named_attribute_of_each_item(self) -> None:
        """Django is the oracle. If this fails, a Django release added a filter that reads a field
        of each item: add it to ``ITEM_KEY_FILTERS`` in ``crates/djust_templates/src/parser.rs``."""
        assert _filters_that_read_x() == {"dictsort", "dictsortreversed"}

    @pytest.mark.parametrize("name", sorted(_builtin_filters.filters))
    def test_the_extractor_files_a_key_for_exactly_those_filters(self, name) -> None:
        reads = name in {"dictsort", "dictsortreversed"}
        for form in ("|%s:'x'", "|%s"):
            source = "{% for u in users" + form % name + " %}{{ u.pk }}{% endfor %}"
            try:
                paths = _paths(source)
            except (RuntimeError, ValueError):
                continue  # this filter takes the other number of arguments
            assert paths["users"] == (["pk", "x"] if reads else ["pk"]), (name, source)
            return
        pytest.fail(f"neither spelling of {name} parses")


# ---------------------------------------------------------------------------------------------
# Rendered, against Django, through every entry
# ---------------------------------------------------------------------------------------------
RENDERS = [
    pytest.param(_backend_render, "QuerySet", id="backend-QuerySet"),
    pytest.param(_backend_render, "list[Model]", id="backend-list"),
    pytest.param(_liveview_render, "QuerySet", id="liveview-QuerySet"),
    pytest.param(_liveview_render, "list[Model]", id="liveview-list"),
]


@pytest.mark.django_db
class TestRendersLikeDjango:
    @pytest.mark.parametrize("render,shape", RENDERS)
    def test_dictsort_over_a_key_nothing_else_reads(self, world, render, shape) -> None:
        assert _got(render, DICTSORT, shape) == _expected(DICTSORT, shape) == _pk_list()

    @pytest.mark.parametrize("render,shape", RENDERS)
    def test_dictsortreversed(self, world, render, shape) -> None:
        source = "{% for u in users|dictsortreversed:'username' %}{{ u.pk }},{% endfor %}"
        assert _got(render, source, shape) == _expected(source, shape) == _pk_list(reverse=True)

    @pytest.mark.parametrize("render,shape", RENDERS)
    def test_through_a_with_alias(self, world, render, shape) -> None:
        source = (
            "{% with s=users|dictsort:'username' %}"
            "{% for u in s %}{{ u.pk }},{% endfor %}{% endwith %}"
        )
        assert _got(render, source, shape) == _expected(source, shape) == _pk_list()

    @pytest.mark.parametrize("render,shape", RENDERS)
    def test_after_a_slice(self, world, render, shape) -> None:
        source = "{% for u in users|slice:'::-1'|dictsort:'username' %}{{ u.pk }},{% endfor %}"
        assert _got(render, source, shape) == _expected(source, shape) == _pk_list()

    @pytest.mark.parametrize("render,shape", RENDERS)
    def test_with_the_key_read_elsewhere_too(self, world, render, shape) -> None:
        """The control, and why it was easy to miss: any other read of the key made it agree."""
        source = "{% for u in users|dictsort:'username' %}{{ u.pk }}{{ u.username }},{% endfor %}"
        assert _got(render, source, shape) == _expected(source, shape) == _pk_list(suffix=True)

    @pytest.mark.xfail(
        strict=True,
        reason="#3912: the Rust dictsort looks 'content_type.model' up as ONE key; the path is "
        "attributed (TestExtraction), the sort cannot use it",
    )
    @pytest.mark.parametrize("render", [_backend_render, _liveview_render])
    def test_a_dotted_key_over_a_relation(self, world, render) -> None:
        """``content_type.model`` is two hops from a ``Permission`` row; the second hop is only
        in the filter argument. Empty here with the key read elsewhere too: the filter, not rows."""
        source = "{% for p in perms|dictsort:'content_type.model' %}{{ p.pk }},{% endfor %}"
        perms = Permission.objects.filter(content_type__app_label="auth").order_by("pk")
        got = html_lib.unescape(render(source, {"perms": perms})).replace("<!--dj-if-->", "")
        want = _django_render(source, {"perms": perms})
        assert got == want
        assert want.count(",") >= 3, want  # sorted by a key the rows differ in, not an empty tie

    @pytest.mark.parametrize("render", [_backend_render, _liveview_render])
    def test_regroup_by_a_field_the_rest_of_the_template_reads_by_field(
        self, world, render
    ) -> None:
        """Before: the LiveView grouped by ``None`` (the rows had no ``username``)."""
        source = (
            "{% regroup users by username as g %}"
            "{% for grp in g %}{{ grp.grouper }},{% endfor %}|"
            "{% for u in users %}{{ u.pk }},{% endfor %}"
        )
        assert _got(render, source, "QuerySet") == _expected(source, "QuerySet")
        assert _expected(source, "QuerySet") == "u0,u1,u2,|" + _pk_list()

    @pytest.mark.parametrize("render", [_backend_render, _liveview_render])
    def test_regroup_on_a_sorted_source(self, world, render) -> None:
        source = (
            "{% regroup users|dictsort:'username' by username as g %}"
            "{% for grp in g %}{{ grp.grouper }}:{% for u in grp.list %}{{ u.pk }}{% endfor %},"
            "{% endfor %}"
        )
        assert _got(render, source, "QuerySet") == _expected(source, "QuerySet")

    @pytest.mark.parametrize("render,shape", RENDERS)
    def test_a_key_that_is_no_field_renders_what_django_renders(self, world, render, shape) -> None:
        """The key is now asked of the serializer, so a name that is not a field must degrade to
        Django's empty loop and not raise."""
        source = "{% for u in users|dictsort:'no_such_field' %}{{ u.pk }},{% endfor %}"
        assert _got(render, source, shape) == _expected(source, shape) == ""

    def test_django_itself_renders_a_missing_key_as_an_empty_loop(self, world) -> None:
        """Why the loop was EMPTY and not an error: ``dictsort`` swallows the missing key. So
        "make the filter raise like Django" (an option in the issue) would not agree with Django."""
        source = "{% for u in users|dictsort:'no_such_field' %}{{ u.pk }}{% endfor %}"
        assert _django_render(source, _ctx("QuerySet")) == ""

    @pytest.mark.xfail(
        strict=True,
        reason="#3910: the field is a context VARIABLE; no static pass can attribute it",
    )
    # backend-list is left out: a raw list is not JIT-serialized there, so it already agrees.
    @pytest.mark.parametrize("render,shape", RENDERS[:1] + RENDERS[2:])
    def test_a_variable_key_is_not_attributed(self, world, render, shape) -> None:
        source = "{% for u in users|dictsort:key %}{{ u.pk }},{% endfor %}"
        ctx = {**_ctx(shape), "key": "username"}
        got = html_lib.unescape(render(source, ctx)).replace("<!--dj-if-->", "")
        assert got == _django_render(source, ctx)


@pytest.mark.django_db
class TestWholeRowsStayWhole:
    """The reason a key is filed only beside field reads: a row read whole is a whole row."""

    @pytest.mark.parametrize("render,shape", RENDERS)
    def test_a_sorted_loop_that_prints_the_row_still_prints_it(self, world, render, shape) -> None:
        source = "{% for u in users|dictsort:'username' %}{{ u }},{% endfor %}"
        assert _got(render, source, shape) == _expected(source, shape) == "u0,u1,u2,"

    @pytest.mark.parametrize("render,shape", RENDERS)
    def test_a_sorted_count_does_not_restrict_the_rows(self, world, render, shape) -> None:
        source = "{{ users|dictsort:'username'|length }}{% for u in users %}{{ u }}{% endfor %}"
        assert _got(render, source, shape) == _expected(source, shape) == "3u0u1u2"

    @pytest.mark.parametrize("render,shape", RENDERS)
    def test_regroup_alone_still_groups(self, world, render, shape) -> None:
        source = (
            "{% regroup users by username as g %}{% for grp in g %}{{ grp.grouper }}:"
            "{% for u in grp.list %}{{ u.pk }}{% endfor %},{% endfor %}"
        )
        assert _got(render, source, shape) == _expected(source, shape) == _pk_list(group=True)


@pytest.mark.django_db
class TestTheServerFloorStillApplies:
    """The key reaches the serializer as the path ``{{ u.<key> }}`` would, so the same floor
    (``_ALWAYS_EXCLUDED_FIELDS``) answers it: sorting by a floored field leaks nothing."""

    @pytest.mark.parametrize("render", [_backend_render, _liveview_render])
    def test_a_sort_key_cannot_read_a_floored_field(self, db, render) -> None:
        User.objects.create_user("zed", password="s3cret-pass-xyz")
        User.objects.create_user("amy", password="another-pass-123")
        source = "{% for u in users|dictsort:'password' %}{{ u.pk }}[{{ u.password }}],{% endfor %}"
        users = User.objects.all().order_by("pk")
        got = html_lib.unescape(render(source, {"users": users}))
        assert "pbkdf2" not in got and "s3cret" not in got and "another-pass" not in got
        direct = html_lib.unescape(
            render("{% for u in users %}[{{ u.password }}]{% endfor %}", {"users": users})
        )
        assert direct == "[][]", "the control: a direct read of the floored field is empty"
