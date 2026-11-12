"""What a path-JIT row carries, against Django's engine, through every entry (#3911 and its chain).

A QuerySet (and a ``list[Model]`` on a LiveView) reaches the Rust engine as path-JIT rows: each row
holds the fields the template reads, as the extractor (``extract_template_variables``) reports
them. The extractor could not say three things, and each was a wrong render:

* **"this row, whole, beside these fields"** (#3911, #3862 item 4). ``{{ u }}{{ u.pk }}`` gave
  ``users: ['pk']``, so the row was ``{'pk': 1}`` and ``{{ u }}`` printed the dict. A whole read
  is now a mark that settles into the path ``__str__`` when the same row is also read by field;
  the serializers answer it with ``str(row)``, the key the renderer prints a serialized object
  by. A whole read with no field read still leaves the row whole. A ROOT read whole files
  ``__djust_text__`` instead: the JIT makes it ``__str__`` for one model and drops it for a list
  (``{{ rows|pprint }}`` prints the list, not each row).
* **"a field named at render time"** (#3910). ``dictsort:key`` names its field through a context
  variable. The list is now filed with ``*key``; the Python JIT resolves ``key`` from the render's
  context and adds the ONE column it names (floored, plain columns only).
* **"a field read through ``{% regroup %}``'s ``.list``"** (#3920): those reads are moved onto
  the regroup's source.

And two renderer-side rules that the rows exposed:

* ``dictsort:'a.b'`` is a PATH (#3912), resolved one part at a time as Django's
  ``_property_resolver`` resolves it.
* a key naming a method or a related manager is not orderable in Django, which renders an empty
  loop; it no longer sorts by the called value (#3919).

The definition of correct is what Django's engine renders for the same template and context.

Engine path: the Rust extractor (``crates/djust_templates/src/parser.rs``), the Rust and Python
row serializers (``serialize_queryset``, ``optimization/codegen.py``), the Python JIT
(``mixins/jit.py``, ``template/rendering.py``) and the Rust ``dictsort``.

Refs #3911, #3910, #3912, #3918, #3919, #3920, #3862.
"""

# ruff: noqa: F811

from __future__ import annotations

import html as html_lib
import itertools

import pytest
from django.contrib.auth.models import Group, Permission, User
from django.template import Context as DjangoContext
from django.template import Engine

from djust import _rust
from python.tests.test_queryset_all_traversal_3777 import (  # noqa: F401
    _backend_render,
    _django_render,
    _liveview_render,
    _raw_render,
)

# ---------------------------------------------------------------------------------------------
# The world: three users whose order differs from their pk order in EVERY sortable column,
# so a sort that does nothing cannot agree with Django by accident.
# ---------------------------------------------------------------------------------------------
#: (username, email, first_name, is_active, date_joined day, groups) in creation (pk) order.
PEOPLE = [
    ("cara", "b@example.com", "Zed", True, 2, ("staff", "ops")),
    ("abe", "c@example.com", "Amy", False, 3, ("staff",)),
    ("bob", "a@example.com", "Mia", True, 1, ()),
]


@pytest.fixture
def world(db):
    import datetime

    from django.utils import timezone

    groups = {name: Group.objects.create(name=name) for name in ("staff", "ops")}
    for username, email, first, active, day, member_of in PEOPLE:
        user = User.objects.create(
            username=username,
            email=email,
            first_name=first,
            is_active=active,
            date_joined=timezone.make_aware(datetime.datetime(2026, 1, day, 12, 0)),
        )
        user.groups.add(*(groups[name] for name in member_of))


def _pk_order_is_not_any_sort_order() -> None:
    """The fixture's own guard: for every column a test sorts by, the sorted order differs
    from the pk order in both directions."""
    rows = list(User.objects.order_by("pk").values("pk", "username", "email", "first_name"))
    pks = [r["pk"] for r in rows]
    for column in ("username", "email", "first_name"):
        ordered = [r["pk"] for r in sorted(rows, key=lambda r: r[column])]
        assert ordered != pks and ordered != pks[::-1], column


ENTRIES = [
    pytest.param(_backend_render, id="backend"),
    pytest.param(_liveview_render, id="liveview"),
    pytest.param(_raw_render, id="render_template"),
]

SHAPES = ["QuerySet", "list[Model]"]


def _clean(html: str) -> str:
    return html_lib.unescape(html).replace("<!--dj-if-->", "")


def _users(shape: str):
    users = User.objects.all().order_by("pk")
    return users if shape == "QuerySet" else list(users)


def _paths(source: str) -> dict[str, list[str]]:
    return {name: list(paths) for name, paths in _rust.extract_template_variables(source).items()}


def _agrees(render, source: str, context: dict) -> tuple[str, str]:
    return _clean(render(source, dict(context))), _django_render(source, dict(context))


# ---------------------------------------------------------------------------------------------
# The extractor's output
# ---------------------------------------------------------------------------------------------
class TestTheExtractorSaysWhatTheRowNeeds:
    @pytest.mark.parametrize(
        "source,root,paths",
        [
            pytest.param(
                "{% for u in users %}{{ u }}{{ u.pk }}{% endfor %}",
                "users",
                ["__str__", "pk"],
                id="loop-var-whole-and-by-field",
            ),
            pytest.param(
                "{% for u in users %}{{ u|upper }}{{ u.pk }}{% endfor %}",
                "users",
                ["__str__", "pk"],
                id="filtered-whole-read",
            ),
            pytest.param(
                "{{ user }}{{ user.pk }}",
                "user",
                ["__djust_text__", "pk"],
                id="a-root-read-whole-is-the-value-or-the-list",
            ),
            pytest.param(
                "{% with r=rows|first %}{{ r.pk }}{% endwith %}{{ rows|pprint }}",
                "rows",
                ["__djust_text__", "pk"],
                id="a-list-printed-whole-is-not-a-row-printed-whole",
            ),
            pytest.param(
                "{% for u in users %}{{ u }}{% endfor %}", "users", [], id="whole-only-stays-whole"
            ),
            pytest.param(
                "{% if users %}{% for u in users %}{{ u.pk }}{% endfor %}{% endif %}",
                "users",
                ["pk"],
                id="a-condition-is-not-a-whole-read",
            ),
            pytest.param(
                "{{ users|length }}{% for u in users %}{{ u.pk }}{% endfor %}",
                "users",
                ["pk"],
                id="the-list-read-whole-is-not-a-row-read-whole",
            ),
            pytest.param(
                "{% if user %}{{ user.username }}{% endif %}",
                "user",
                ["username"],
                id="a-truth-test-asks-no-text",
            ),
            pytest.param(
                "{% for u in users %}{% with v=u %}{{ v }}{{ v.pk }}{% endwith %}{% endfor %}",
                "users",
                ["__str__", "pk"],
                id="through-a-with-alias",
            ),
            pytest.param(
                "{% for u in users %}{% for g in u.groups.all %}{{ g }}{{ g.name }}"
                "{% endfor %}{% endfor %}",
                "users",
                ["groups.all", "groups.all.__str__", "groups.all.name"],
                id="nested-all-rows",
            ),
            pytest.param(
                "{% for k, v in pairs %}{{ k }}{{ v.pk }}{% endfor %}",
                "pairs",
                ["pk"],
                id="unpacking-names-part-of-an-item",
            ),
            pytest.param(
                "{% for u in users|dictsort:key %}{{ u.pk }}{% endfor %}",
                "users",
                ["*key", "pk"],
                id="variable-sort-key-names-its-variable",
            ),
            pytest.param(
                "{% for u in users|dictsort:key %}{{ u }}{% endfor %}",
                "users",
                ["*key"],
                id="variable-sort-key-over-whole-rows",
            ),
            pytest.param(
                "{% for g in groups %}{% for u in g.user_set.all|dictsort:'username' %}x"
                "{% endfor %}{% endfor %}",
                "groups",
                ["user_set.all", "user_set.all.username"],
                id="nested-list-key-without-a-sibling-read",
            ),
            pytest.param(
                "{% for u in users %}{{ u.pk }}{% endfor %}"
                "{% regroup users by username as g %}{% for grp in g %}"
                "{% for u in grp.list %}{{ u.email }}{% endfor %}{% endfor %}",
                "users",
                ["email", "pk", "username"],
                id="regroup-list-reads-are-source-reads",
            ),
        ],
    )
    def test_paths(self, source, root, paths) -> None:
        assert _paths(source)[root] == paths

    def test_the_whole_row_path_is_only_filed_at_a_root(self) -> None:
        source = (
            "{% for u in users %}{{ u.pk }}{% for g in u.groups.all|dictsort:k %}{{ g.name }}"
            "{% endfor %}{% endfor %}"
        )
        assert "*" not in " ".join(_paths(source)["users"])

    def test_template_paths_keep_the_jit_markers_internal(self) -> None:
        """``template_paths()`` is public (``serialize_for_template``, the unsupplied-name
        diagnostic, ``djust_audit``): a whole read stays an empty list there (#3911/#3910)."""
        from djust.mixins.jit import public_template_paths

        source = (
            "{{ card }}{{ card.title }}{% for u in users|dictsort:key %}{{ u }}{{ u.pk }}"
            "{% endfor %}"
        )
        public = public_template_paths(_rust.extract_template_variables(source))
        assert public["card"] == ["title"]
        assert public["users"] == ["pk"]

    def test_item_key_only_reads(self) -> None:
        source = (
            "{% for u in users|dictsort:'get_full_name' %}{{ u.pk }}{% endfor %}"
            "{% for u in users|dictsort:'username' %}{{ u.username }}{% endfor %}"
        )
        assert dict(_rust.extract_item_key_only_reads(source)) == {"users": ["get_full_name"]}


# ---------------------------------------------------------------------------------------------
# Rendered against Django
# ---------------------------------------------------------------------------------------------
ROW_CASES = [
    ("whole-and-by-field", "{% for u in users %}{{ u }}{{ u.pk }},{% endfor %}"),
    ("filtered-whole", "{% for u in users %}{{ u|upper }}{{ u.pk }},{% endfor %}"),
    (
        "with-alias",
        "{% for u in users %}{% with v=u %}{{ v }}:{{ v.username }},{% endwith %}{% endfor %}",
    ),
    (
        "nested-all-rows",
        "{% for u in users %}{% for g in u.groups.all %}{{ g }}-{{ g.name }},{% endfor %};"
        "{% endfor %}",
    ),
    (
        "nested-all-sorted-whole",
        "{% for u in users %}{{ u.pk }}[{% for g in u.groups.all|dictsort:'name' %}{{ g }},"
        "{% endfor %}]{% endfor %}",
    ),
    ("variable-key", "{% for u in users|dictsort:key %}{{ u.pk }},{% endfor %}"),
    (
        "variable-key-with-a-relation",
        "{% for u in users|dictsort:key %}{{ u.pk }}[{% for g in u.groups.all %}{{ g.name }}"
        "{% endfor %}]{% endfor %}",
    ),
    ("variable-key-reversed", "{% for u in users|dictsortreversed:key %}{{ u.pk }},{% endfor %}"),
    ("method-key", "{% for u in users|dictsort:'get_full_name' %}{{ u.pk }},{% endfor %}"),
    ("manager-key", "{% for u in users|dictsort:'groups' %}{{ u.pk }},{% endfor %}"),
    (
        "regroup-list-beside-a-field-read",
        "{% for u in users %}{{ u.pk }},{% endfor %}|{% regroup users by username as g %}"
        "{% for grp in g %}{% for u in grp.list %}{{ u.username }}{{ u.is_active }}"
        "{% endfor %};{% endfor %}",
    ),
]


@pytest.mark.django_db
class TestRowsRenderLikeDjango:
    @pytest.mark.parametrize("shape", SHAPES)
    @pytest.mark.parametrize("render", ENTRIES)
    @pytest.mark.parametrize("name,source", ROW_CASES, ids=[c[0] for c in ROW_CASES])
    def test_rows(self, world, render, shape, name, source) -> None:
        _pk_order_is_not_any_sort_order()
        context = {"users": _users(shape), "key": "username"}
        got, want = _agrees(render, source, context)
        assert got == want, (name, shape)

    @pytest.mark.parametrize("render", ENTRIES)
    def test_a_single_model_read_whole_and_by_field(self, world, render) -> None:
        got, want = _agrees(render, "{{ user }}{{ user.pk }}", {"user": User.objects.first()})
        assert got == want
        assert want.startswith("cara")

    @pytest.mark.parametrize("render", ENTRIES)
    def test_the_method_key_is_empty_as_in_django(self, world, render) -> None:
        """The control for ``method-key``: Django really does render nothing (#3919)."""
        source = "{% for u in users|dictsort:'get_full_name' %}{{ u.pk }},{% endfor %}"
        assert _django_render(source, {"users": _users("QuerySet")}) == ""

    @pytest.mark.parametrize("render", ENTRIES)
    def test_a_method_the_template_also_prints_still_prints(self, world, render) -> None:
        """Only a key NOTHING else reads is left out of the row; a printed method is kept."""
        source = "{% for u in users %}[{{ u.get_username }}]{% endfor %}"
        got, want = _agrees(render, source, {"users": _users("QuerySet")})
        assert got == want == "[cara][abe][bob]"

    @pytest.mark.parametrize("render", ENTRIES)
    def test_a_dotted_key_over_dict_rows(self, render) -> None:
        rows = [
            {"pk": 1, "ct": {"m": "b"}},
            {"pk": 2, "ct": {"m": "a"}},
            {"pk": 3, "ct": {"m": "c"}},
        ]
        source = "{% for p in rows|dictsort:'ct.m' %}{{ p.pk }},{% endfor %}"
        got, want = _agrees(render, source, {"rows": rows})
        assert got == want == "2,1,3,"

    @pytest.mark.parametrize("render", ENTRIES)
    def test_a_dotted_key_that_misses_on_one_row_is_empty(self, render) -> None:
        rows = [{"pk": 1, "ct": {"m": "b"}}, {"pk": 2, "ct": {}}]
        source = "{% for p in rows|dictsort:'ct.m' %}{{ p.pk }},{% endfor %}"
        got, want = _agrees(render, source, {"rows": rows})
        assert got == want == ""

    @pytest.mark.parametrize("render", ENTRIES)
    @pytest.mark.parametrize(
        "keys,want",
        [
            pytest.param([None, None], "", id="none-beside-none"),
            pytest.param([1, "a"], "", id="number-beside-text"),
            pytest.param([{"a": 1}, {"a": 2}], "", id="dict-beside-dict"),
            pytest.param([None], "1,", id="one-row-is-never-compared"),
            pytest.param([2, 1.5, True], "3,2,1,", id="numbers-of-three-types"),
        ],
    )
    def test_keys_python_cannot_order_render_an_empty_loop(self, render, keys, want) -> None:
        """Django's ``dictsort`` catches the ``TypeError`` ``sorted()`` raises (#3919's rule,
        and the nullable column the key-shape sweep found)."""
        rows = [{"pk": i + 1, "k": k} for i, k in enumerate(keys)]
        source = "{% for r in rows|dictsort:'k' %}{{ r.pk }},{% endfor %}"
        got, django = _agrees(render, source, {"rows": rows})
        assert got == django == want

    @pytest.mark.parametrize("render", ENTRIES)
    def test_a_dotted_key_over_a_relation(self, db, render) -> None:
        source = "{% for p in perms|dictsort:'content_type.model' %}{{ p.pk }},{% endfor %}"
        perms = Permission.objects.filter(content_type__app_label="auth").order_by("pk")
        got, want = _agrees(render, source, {"perms": perms})
        assert got == want
        assert want.count(",") >= 3, want

    @pytest.mark.parametrize("render", ENTRIES)
    @pytest.mark.parametrize(
        "body",
        ["x", "{{ u }},", "{{ u.pk }},"],
        ids=["no-read", "whole-read", "field-read"],
    )
    def test_a_nested_list_sorted_by_a_key_nothing_else_reads(self, world, render, body) -> None:
        """#3918: the inner rows of ``g.user_set.all`` cross as their text when no field is read,
        so the key is filed for a nested list even without a sibling field read."""
        source = (
            "{% for g in groups %}{% for u in g.user_set.all|dictsort:'username' %}"
            + body
            + "{% endfor %};{% endfor %}"
        )
        got, want = _agrees(render, source, {"groups": Group.objects.order_by("pk")})
        assert got == want
        assert want.strip(";")


# ---------------------------------------------------------------------------------------------
# A parent template's reads, under DjustTemplateBackend (#3920)
# ---------------------------------------------------------------------------------------------
@pytest.mark.django_db
class TestAParentTemplatesReadsAreReads:
    def test_a_sort_in_the_parent_and_a_field_read_in_the_child(self, world, tmp_path) -> None:
        from djust.template import DjustTemplateBackend

        (tmp_path / "base_3920.html").write_text(
            "{% for u in users|dictsort:'username' %}{% block row %}{% endblock %}{% endfor %}|"
            "{% block tail %}{% endblock %}"
        )
        # The child's own loop reads ``pk`` by field, so the child alone asks for ``pk`` rows;
        # the parent's sort key exists only in the parent.
        child = (
            '{% extends "base_3920.html" %}{% block row %}{{ u.pk }},{% endblock %}'
            "{% block tail %}{% for x in users %}{{ x.pk }}.{% endfor %}{% endblock %}"
        )
        users = User.objects.all().order_by("-pk")
        backend = DjustTemplateBackend(
            params={"NAME": "djust", "DIRS": [str(tmp_path)], "APP_DIRS": False, "OPTIONS": {}}
        )
        got = _clean(str(backend.from_string(child).render(context={"users": users})))
        want = (
            Engine(dirs=[str(tmp_path)]).from_string(child).render(DjangoContext({"users": users}))
        )
        assert got == want
        assert want.count(",") == 3 and want.count(".") == 3


# ---------------------------------------------------------------------------------------------
# Keys the engine has no comparison of its own for: Python's ordering decides (#3919 review)
# ---------------------------------------------------------------------------------------------
def _key_kinds() -> list:
    import datetime
    import uuid
    from decimal import Decimal

    from django.utils.translation import gettext_lazy

    u = [uuid.UUID(int=n) for n in (7, 3, 9)]
    day = datetime.date(2026, 1, 2)
    moment = datetime.datetime(2026, 1, 1, 12, 0)
    return [
        pytest.param([u[0], u[1], u[2]], id="uuid"),
        pytest.param([gettext_lazy("pear"), gettext_lazy("apple"), gettext_lazy("fig")], id="lazy"),
        pytest.param([Decimal("2.5"), 1.25, Decimal("3")], id="decimal-beside-float"),
        pytest.param([day, moment, day], id="date-beside-datetime"),
        pytest.param(
            [datetime.date(2026, 3, 1), datetime.date(2026, 1, 1), datetime.date(2026, 2, 1)],
            id="dates",
        ),
        pytest.param([(2, "b"), (1, "z"), (2, "a")], id="tuples"),
        pytest.param([[2, 1], [1], [2]], id="lists"),
        pytest.param([(1, "a"), (1, 2)], id="tuples-python-cannot-order"),
    ]


class TestKeysTheEngineHasNoComparisonFor:
    """A sort over values the engine has no comparison of its own for asks Python's, as
    ``sorted()`` does: shown and sorted where Django sorts, empty only where Python raises."""

    @pytest.mark.parametrize("render", ENTRIES)
    @pytest.mark.parametrize("keys", _key_kinds())
    def test_the_sort_agrees_with_django(self, render, keys) -> None:
        rows = [{"pk": i + 1, "k": k} for i, k in enumerate(keys)]
        for name in ("dictsort", "dictsortreversed"):
            source = "{% for r in rows|" + name + ":'k' %}{{ r.pk }},{% endfor %}"
            got = _outcome(lambda: _clean(render(source, {"rows": rows})))
            want = _outcome(lambda: _django_render(source, {"rows": rows}))
            assert got == want, (name, keys)


# ---------------------------------------------------------------------------------------------
# A variable sort key carries ONE column (#3910 review)
# ---------------------------------------------------------------------------------------------
@pytest.mark.django_db
class TestAVariableKeyCarriesOneColumn:
    SOURCE = "{% for u in users|dictsort:key %}{{ u.pk }},{% endfor %}"

    def _backend_rows(self, key: str) -> list:
        from djust.template import DjustTemplateBackend

        backend = DjustTemplateBackend(
            params={"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
        )
        template = backend.from_string(self.SOURCE)
        return template._jit_serialize_queryset(User.objects.order_by("pk"), "users", {"key": key})

    def test_the_row_holds_the_key_column_and_what_the_template_reads(self, world) -> None:
        rows = self._backend_rows("email")
        assert {frozenset(row) for row in rows} == {frozenset({"pk", "email"})}

    @pytest.mark.parametrize(
        "key",
        ["get_full_name", "groups", "password", "_state", "username.nope", "3", ""],
    )
    def test_a_key_that_is_not_a_plain_column_adds_nothing(self, world, key) -> None:
        """A method, a manager, a floored column, a private or dotted or numeric name: no
        column is added, and the sort renders the empty loop (the fail-closed side)."""
        rows = self._backend_rows(key)
        assert {frozenset(row) for row in rows} == {frozenset({"pk"})}

    @pytest.mark.parametrize("render", ENTRIES)
    @pytest.mark.parametrize("shape", SHAPES)
    def test_a_key_that_changes_between_renders_re_serializes(self, world, render, shape) -> None:
        for key in ("username", "email", "first_name", "username"):
            context = {"users": _users(shape), "key": key}
            got, want = _agrees(render, self.SOURCE, context)
            assert got == want, key

    @pytest.mark.parametrize("render", [_backend_render, _liveview_render])
    def test_a_floored_column_renders_empty_where_django_sorts(self, world, render) -> None:
        """The documented difference: the serialization floor keeps ``password`` off the
        row, so the sort finds no key; Django sorts by the hash."""
        context = {"users": _users("QuerySet"), "key": "password"}
        got, want = _agrees(render, self.SOURCE, context)
        assert got == "" and want.count(",") == 3


# ---------------------------------------------------------------------------------------------
# A sweep over key shapes x row reads, every entry, every shape
# ---------------------------------------------------------------------------------------------
KEYS = [
    "'username'",
    "'email'",
    "'first_name'",
    "'date_joined'",
    "'pk'",
    "'is_active'",
    "'last_login'",
    "'get_username'",
    "'groups'",
    "'nope'",
    "'username.nope'",
    "key",
    "key2",
    "method_key",
    "missing_var",
]
BODIES = [
    "{{ u.pk }},",
    "{{ u }},",
    "{{ u }}{{ u.pk }},",
    "x",
    "{{ u.pk }}[{% for g in u.groups.all %}{{ g.name }}{% endfor %}],",
]


@pytest.mark.django_db
class TestKeyShapeSweep:
    @pytest.mark.parametrize("render", ENTRIES)
    def test_every_key_shape_and_row_read(self, world, render) -> None:
        mismatches = []
        for shape, key, body in itertools.product(SHAPES, KEYS, BODIES):
            source = "{% for u in users|dictsort:" + key + " %}" + body + "{% endfor %}"
            context = {
                "users": _users(shape),
                "key": "username",
                "key2": "email",
                "method_key": "get_full_name",
            }
            got = _outcome(lambda: _clean(render(source, dict(context))))
            want = _outcome(lambda: _django_render(source, dict(context)))
            if got != want:
                mismatches.append((shape, source, got, want))
        assert mismatches == []


def _outcome(call) -> tuple[str, str]:
    """``("ok", text)``, or ``("raises", <exception class>)``: an unresolvable filter ARGUMENT
    (``dictsort:missing_var``) raises ``VariableDoesNotExist`` on Django, and must on djust."""
    try:
        return ("ok", call())
    except Exception as error:  # noqa: BLE001 — the class is the observation
        return ("raises", type(error).__name__)
