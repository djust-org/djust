"""#3777 review follow-ups: the floor, custom QuerySets, errors, plain-data rows.

``test_queryset_all_traversal_3777.py`` proves the traversal renders the rows.
The independent review of PR #3785 found what it did not pin:

1. **The serialization floor through ``.all``.** Replacing the row walk with a
   raw ``getattr`` that skips the gate left every one of those 41 tests green.
   ``password`` / ``is_superuser`` / ``is_staff``, a per-model allowlist and a
   per-model denylist must hold on every row reached through ``.all`` -- reverse
   FK, M2M with an explicit through model, a nested chain, the indexed spelling.
2. **A custom QuerySet.** ``SomeQS.as_manager()`` (and a related manager's
   ``all()``, which hands back the model's own queryset class) is a subclass
   whose class NAME is not ``QuerySet``; ``|length`` / ``{% if %}`` / ``|join``
   stayed wrong for it.
3. **A raising ``all()``.** Only the two exceptions the Python codegen twin
   catches (``TypeError``, ``AttributeError``) may be swallowed; a database
   error must reach the caller's ``[JIT ERROR]`` handler instead of rendering a
   user with no groups and no log line.
4. **Plain-data rows** from a model method named ``all``.

Expectations that Django also produces are DERIVED from Django's engine, not
typed in (#2727). The floor cases state theirs, because the floor is exactly
where djust deliberately differs from Django.
"""

from __future__ import annotations

import html as html_lib
import logging
import re
import sys

import pytest
from django.apps import AppConfig
from django.apps.registry import Apps
from django.contrib.auth.models import Group, User
from django.db import OperationalError, connection, models

from djust import _rust
from python.tests.test_queryset_all_traversal_3777 import (
    _backend_render,
    _django_render,
    _liveview_render,
)

# ---------------------------------------------------------------------------
# Models: every shape a floor field can be reached through `.all`
# ---------------------------------------------------------------------------


# The models live in their OWN app registry: a model of an app that is not in
# INSTALLED_APPS is invisible to ``_meta``'s relation tree, so a many-to-many's
# reverse query name (``QSLabel.shelves``) does not resolve and
# ``shelf.labels.all()`` raises ``FieldError`` before the serializer is even in
# question. (``isolate_apps`` needs an importable app package; this module is
# not one, so the registry is built by hand.)
_REGISTRY = Apps(installed_apps=[AppConfig("queryset_all_3777", sys.modules[__name__])])


def _build_models():
    class QSShelf(models.Model):
        name = models.CharField(max_length=20)
        labels = models.ManyToManyField("QSLabel", through="QSTagging", related_name="shelves")

        class Meta:
            app_label = "queryset_all_3777"
            apps = _REGISTRY

    class QSLabel(models.Model):
        name = models.CharField(max_length=20)
        password = models.CharField(max_length=40)

        class Meta:
            app_label = "queryset_all_3777"
            apps = _REGISTRY

    class QSTagging(models.Model):
        shelf = models.ForeignKey(QSShelf, on_delete=models.CASCADE)
        label = models.ForeignKey(QSLabel, on_delete=models.CASCADE)

        class Meta:
            app_label = "queryset_all_3777"
            apps = _REGISTRY

    class VolumeQS(models.QuerySet):
        """A custom QuerySet: its class name is ``VolumeQS``, not ``QuerySet``."""

        def cheap(self):
            return self.all()

    class QSVolume(models.Model):
        shelf = models.ForeignKey(QSShelf, on_delete=models.CASCADE, related_name="volumes")
        title = models.CharField(max_length=20)
        password = models.CharField(max_length=40)
        is_staff = models.BooleanField(default=False)
        secret = models.CharField(max_length=40)
        labels = models.ManyToManyField(QSLabel, related_name="volumes")

        djust_exclude_fields = ("secret",)
        objects = VolumeQS.as_manager()

        def __str__(self) -> str:
            return self.title

        class Meta:
            app_label = "queryset_all_3777"
            apps = _REGISTRY

    class QSLocked(models.Model):
        shelf = models.ForeignKey(QSShelf, on_delete=models.CASCADE, related_name="locked")
        title = models.CharField(max_length=20)
        note = models.CharField(max_length=40)

        djust_serializable_fields = ("id", "title")

        class Meta:
            app_label = "queryset_all_3777"
            apps = _REGISTRY

    return QSShelf, QSLabel, QSTagging, VolumeQS, QSVolume, QSLocked


QSShelf, QSLabel, QSTagging, VolumeQS, QSVolume, QSLocked = _build_models()

_TABLES = (QSShelf, QSLabel, QSTagging, QSVolume, QSLocked)


@pytest.fixture
def shelves(transactional_db):
    with connection.schema_editor() as editor:
        for model in _TABLES:
            editor.create_model(model)
    full = QSShelf.objects.create(name="full")
    QSShelf.objects.create(name="empty")
    red = QSLabel.objects.create(name="red", password="pw-red")
    QSTagging.objects.create(shelf=full, label=red)
    for title in ("alpha", "beta"):
        volume = QSVolume.objects.create(
            shelf=full,
            title=title,
            password=f"pw-{title}",
            is_staff=True,
            secret=f"sec-{title}",
        )
        volume.labels.add(red)
    QSLocked.objects.create(shelf=full, title="lock", note="hidden-note")
    try:
        yield
    finally:
        with connection.schema_editor() as editor:
            for model in reversed(_TABLES):
                editor.delete_model(model)


SECRETS = ("pw-", "sec-", "hidden-note")


def _serialize(paths: list[str]):
    return _rust.serialize_queryset(list(QSShelf.objects.order_by("pk")), paths)


# ---------------------------------------------------------------------------
# 1. The floor holds on every row reached through `.all`
# ---------------------------------------------------------------------------
FLOOR_SHAPES = [
    pytest.param(
        ["volumes.all.title", "volumes.all.password", "volumes.all.is_staff", "volumes.all.secret"],
        id="reverse-fk+denylist",
    ),
    pytest.param(["volumes.all.0.title", "volumes.all.0.password"], id="indexed"),
    pytest.param(["labels.all.name", "labels.all.password"], id="m2m-through"),
    pytest.param(
        [
            "volumes.all.title",
            "volumes.all.labels.all.name",
            "volumes.all.labels.all.password",
            "volumes.all.password",
        ],
        id="nested-chain",
    ),
    pytest.param(["locked.all.title", "locked.all.note"], id="allowlist"),
    pytest.param(["volumes.all"], id="leaf"),
]


class TestTheFloorHoldsThroughAll3777:
    @pytest.mark.parametrize("paths", FLOOR_SHAPES)
    def test_no_floor_denylist_or_allowlist_value_ships(self, shelves, paths) -> None:
        shipped = repr(_serialize(paths))
        for secret in SECRETS:
            assert secret not in shipped, f"{paths}: {secret!r} reached the wire: {shipped}"

    @pytest.mark.parametrize("paths", FLOOR_SHAPES)
    def test_the_rows_themselves_did_ship(self, shelves, paths) -> None:
        """Non-vacuity: an empty traversal also ships no secret."""
        shipped = repr(_serialize(paths))
        assert re.search(r"alpha|red|lock", shipped), shipped

    def test_a_denied_name_is_omitted_from_each_row_not_blanked(self, shelves) -> None:
        full = _serialize(
            [
                "volumes.all.title",
                "volumes.all.password",
                "volumes.all.is_staff",
                "volumes.all.secret",
            ]
        )[0]
        assert [set(row) for row in full["volumes"]["all"]] == [{"title"}, {"title"}]

    def test_an_allowlist_narrows_the_rows(self, shelves) -> None:
        full = _serialize(["locked.all.title", "locked.all.note"])[0]
        assert [set(row) for row in full["locked"]["all"]] == [{"title"}]

    def test_a_nested_chain_is_gated_at_every_level(self, shelves) -> None:
        full = _serialize(["volumes.all.labels.all.name", "volumes.all.labels.all.password"])[0]
        labels = [row["labels"]["all"] for row in full["volumes"]["all"]]
        assert labels == [[{"name": "red"}], [{"name": "red"}]]

    def test_the_auth_user_floor_through_a_reverse_m2m(self, db) -> None:
        group = Group.objects.create(name="staff")
        user = User.objects.create_user(username="floor-user", password="hunter2-not-in-wire")
        user.is_staff = user.is_superuser = True
        user.save()
        user.groups.add(group)
        out = _rust.serialize_queryset(
            [group],
            [
                "user_set.all.username",
                "user_set.all.password",
                "user_set.all.is_superuser",
                "user_set.all.is_staff",
            ],
        )
        assert out[0]["user_set"]["all"] == [{"username": "floor-user"}]
        assert "hunter2" not in repr(out) and "pbkdf2" not in repr(out)

    @pytest.mark.parametrize("render", [_backend_render, _liveview_render])
    def test_a_template_naming_a_floor_field_renders_it_empty(self, shelves, render) -> None:
        source = (
            "{% for s in shelves %}{% for v in s.volumes.all %}"
            "{{ v.title }}[{{ v.password }}][{{ v.is_staff }}][{{ v.secret }}];"
            "{% endfor %}{% endfor %}"
        )
        got = render(source, {"shelves": QSShelf.objects.order_by("pk")})
        assert got == "alpha[][][];beta[][][];"


# ---------------------------------------------------------------------------
# 2. A custom QuerySet subclass is a QuerySet
# ---------------------------------------------------------------------------
CUSTOM_QS_SOURCES = {
    "if": "{% for s in shelves %}[{% if s.volumes.all %}Y{% else %}N{% endif %}]{% endfor %}",
    "length": "{% for s in shelves %}[{{ s.volumes.all|length }}]{% endfor %}",
    "join": "{% for s in shelves %}[{{ s.volumes.all|join:'/' }}]{% endfor %}",
    "first": "{% for s in shelves %}[{{ s.volumes.all|first }}]{% endfor %}",
    "loop": "{% for s in shelves %}[{% for v in s.volumes.all %}{{ v.title }},{% endfor %}]{% endfor %}",
}


class TestACustomQuerySetIsIterated3777:
    def test_the_related_manager_really_hands_back_the_custom_class(self, shelves) -> None:
        rows = QSShelf.objects.get(name="full").volumes.all()
        assert type(rows).__name__ == "VolumeQS" and type(rows).__name__ != "QuerySet"

    @pytest.mark.parametrize("render", [_backend_render, _liveview_render])
    @pytest.mark.parametrize("name", list(CUSTOM_QS_SOURCES))
    def test_every_spelling_agrees_with_django(self, shelves, render, name) -> None:
        source = CUSTOM_QS_SOURCES[name]
        ctx = lambda: {"shelves": QSShelf.objects.order_by("pk")}  # noqa: E731
        expected = _django_render(source, ctx())
        assert re.search(r"Y|N|\d|alpha", expected), expected
        assert html_lib.unescape(render(source, ctx())) == expected


# ---------------------------------------------------------------------------
# 3. A raising `all()`
# ---------------------------------------------------------------------------
class _Rows:
    def __init__(self, error: BaseException | None = None, marker: str | None = None) -> None:
        def all(self=None):  # noqa: A001 -- the name is the point
            if error is not None:
                raise error
            return []

        if marker:
            setattr(all, marker, True)
        self.all = all


class _Holder:
    def __init__(self, **kwargs) -> None:
        self.groups = _Rows(**kwargs)


ALL_SHAPES = [
    pytest.param(["groups.all.name"], id="object"),
    pytest.param(["groups.all.0.name"], id="indexed"),
    pytest.param(["groups.all"], id="leaf"),
]


class TestARaisingAllIsNotSilent3777:
    @pytest.mark.parametrize("paths", ALL_SHAPES)
    def test_a_database_error_propagates(self, paths) -> None:
        with pytest.raises(OperationalError, match="db is down"):
            _rust.serialize_queryset([_Holder(error=OperationalError("db is down"))], paths)

    @pytest.mark.parametrize("error", [TypeError("needs args"), AttributeError("no such")])
    @pytest.mark.parametrize("paths", ALL_SHAPES)
    def test_what_the_codegen_swallows_is_still_swallowed(self, paths, error) -> None:
        out = _rust.serialize_queryset([_Holder(error=error)], paths)
        groups = out[0]["groups"]
        if paths == ["groups.all"]:
            assert "all" not in groups  # a callable that cannot be called is omitted
        else:
            assert groups["all"] == []

    def test_the_jit_handler_logs_it_and_falls_back(self, world_users, monkeypatch, caplog) -> None:
        """Through the real LiveView render: the failure reaches the existing
        ``[JIT ERROR]`` handler (which logs through the exposure diagnostics),
        and the page is NOT silently rendered as 'this user has no groups' by
        the traversal."""

        def boom(self):
            raise OperationalError("statement timeout")

        monkeypatch.setattr(User.groups.related_manager_cls, "get_queryset", boom)
        source = "{% for u in users %}{{ u.username }}[{% for g in u.groups.all %}{{ g.name }},{% endfor %}]{% endfor %}"
        with caplog.at_level(logging.DEBUG):
            _liveview_render(source, {"users": User.objects.order_by("pk")})
        logged = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("[JIT ERROR]" in r.getMessage() for r in logged), [
            (r.name, r.levelname, r.getMessage()) for r in caplog.records
        ]


@pytest.fixture
def world_users(db):
    staff = Group.objects.create(name="staff")
    User.objects.create(username="solo").groups.add(staff)


class _BadQuerySet(models.QuerySet):
    def __iter__(self):
        raise TypeError("cannot iterate")


class TestAFailedConversionKeepsTheValue3777:
    def test_a_queryset_that_will_not_iterate_is_not_turned_into_an_empty_list(self, db) -> None:
        """The leaf conversion tolerates the codegen's exceptions by leaving the
        value as it is -- NOT by claiming the relation is empty."""

        class Rows:
            def all(self):
                return _BadQuerySet(model=User)

        class Holder:
            groups = Rows()

        out = _rust.serialize_queryset([Holder()], ["groups.all"])
        assert out[0]["groups"]["all"] != []


# ---------------------------------------------------------------------------
# A refused `all` is OMITTED (the sidecar answers), never an empty list
# ---------------------------------------------------------------------------
class TestARefusedAllIsOmitted3777:
    @pytest.mark.parametrize("marker", ["alters_data", "do_not_call_in_templates"])
    @pytest.mark.parametrize("paths", ALL_SHAPES)
    def test_the_name_is_absent_from_the_output(self, paths, marker) -> None:
        out = _rust.serialize_queryset([_Holder(marker=marker)], paths)
        assert "all" not in out[0]["groups"], out


# ---------------------------------------------------------------------------
# 4. Plain-data rows from a model method named `all`
# ---------------------------------------------------------------------------
class _MethodAll:
    def __init__(self, rows) -> None:
        self._rows = rows

    def all(self):
        return self._rows


class _HolderOf:
    def __init__(self, rows) -> None:
        self.m = _MethodAll(rows)


class TestPlainDataRowsAreKept3777:
    @pytest.mark.parametrize("paths", [["m.all.x"], ["m.all.0.x"]], ids=["object", "indexed"])
    def test_dict_rows_keep_their_data(self, paths) -> None:
        out = _rust.serialize_queryset([_HolderOf([{"x": 1}, {"x": 2}])], paths)
        assert out[0]["m"]["all"] == [{"x": 1}, {"x": 2}]

    def test_list_and_tuple_rows_keep_their_data(self) -> None:
        out = _rust.serialize_queryset([_HolderOf([[1, 2], (3, 4)])], ["m.all.x"])
        assert out[0]["m"]["all"] == [[1, 2], [3, 4]]

    def test_the_template_reads_them(self) -> None:
        """What a LiveView renders for the same rows: Django's own answer."""
        source = "{% for r in m.all %}{{ r.x }},{% endfor %}"
        ctx = {"m": _MethodAll([{"x": 1}, {"x": 2}])}
        assert _django_render(source, ctx) == "1,2,"
