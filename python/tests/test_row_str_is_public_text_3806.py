"""A row's ``__str__`` is public text (#3806): the behaviour, pinned as documented.

``djust_exclude_fields`` / ``djust_serializable_fields`` / the floor decide which FIELDS of a row a
template can read (``{{ row.secret }}`` is empty). They do not decide what ``str(row)`` says:
``__str__`` is one of the identity keys (``_IDENTITY_KEYS``) every serialized model carries, so
``{{ row }}`` prints whatever the model's ``__str__`` returns, exactly as Django does. A model whose
``__str__`` builds its text from a field the floor withholds therefore shows that field's value in the
rendered page. The stock ``User.__str__`` is the username, so no stock model leaks; the author of a
model is the only one who can. ``docs/SECURE_DEFAULTS.md`` and ``docs/SECURITY_GUIDELINES.md`` say so
(option (a) of the issue: declared, not changed).

This file runs every path that ships ``str(row)`` through the engines djust renders with, against
Django's engine as the control, and states what does NOT ship it, so the documented claim is the
measured one:

* ships it: a top-level loop over a QuerySet (``{% for r in qs %}{{ r }}``), a single model in the
  context (``{{ row }}``), ``{{ qs.first }}`` / ``{{ qs.last }}``, a row reached through a relation's
  ``.all`` (``{{ shelf.rows.all.first }}``), and a row whose ``__str__`` is read beside a field read;
* does not ship a withheld FIELD on any of those (``{{ row.secret }}`` is empty), so the contrast in
  every test below is real.

Refs #3806, #3803, #3788, #3790, #1646, #1468.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from django.apps import AppConfig
from django.apps.registry import Apps
from django.db import connection, models

from djust.serialization import _IDENTITY_KEYS, DjangoJSONEncoder, model_identity
from python.tests.test_queryset_all_traversal_3777 import (
    _backend_render,
    _django_render,
    _liveview_render,
)

# The models live in their own app registry (see test_queryset_all_floor_and_errors_3777.py for why).
_REGISTRY = Apps(installed_apps=[AppConfig("row_str_3806", sys.modules[__name__])])


class StrShelf(models.Model):
    """A row reached THROUGH a foreign key: ``secret`` is withheld, ``__str__`` prints it."""

    name = models.CharField(max_length=20)
    secret = models.CharField(max_length=20, default="")

    djust_exclude_fields = ("secret",)

    def __str__(self) -> str:
        return f"shelf[{self.name}/{self.secret}]"

    class Meta:
        app_label = "row_str_3806"
        apps = _REGISTRY


class StrDenied(models.Model):
    """``secret`` is withheld by ``djust_exclude_fields``; ``__str__`` prints it anyway."""

    shelf = models.ForeignKey(StrShelf, on_delete=models.CASCADE, related_name="denied")
    name = models.CharField(max_length=20)
    secret = models.CharField(max_length=20)

    djust_exclude_fields = ("secret",)

    def __str__(self) -> str:
        return f"{self.name}[{self.secret}]"

    class Meta:
        app_label = "row_str_3806"
        apps = _REGISTRY


class StrAllowed(models.Model):
    """``note`` is outside the ``djust_serializable_fields`` allowlist; ``__str__`` prints it."""

    shelf = models.ForeignKey(StrShelf, on_delete=models.CASCADE, related_name="allowed")
    name = models.CharField(max_length=20)
    note = models.CharField(max_length=20)

    djust_serializable_fields = ("id", "name")

    def __str__(self) -> str:
        return f"{self.name}[{self.note}]"

    class Meta:
        app_label = "row_str_3806"
        apps = _REGISTRY


class StrPassword(models.Model):
    """``password`` is on the floor; ``__str__`` prints it anyway."""

    name = models.CharField(max_length=20)
    password = models.CharField(max_length=40)

    def __str__(self) -> str:
        return f"{self.name}[{self.password}]"

    class Meta:
        app_label = "row_str_3806"
        apps = _REGISTRY


class StrPublic(models.Model):
    """The author guidance, followed: ``__str__`` is built from public fields only."""

    name = models.CharField(max_length=20)
    secret = models.CharField(max_length=20)

    djust_exclude_fields = ("secret",)

    def __str__(self) -> str:
        return self.name

    class Meta:
        app_label = "row_str_3806"
        apps = _REGISTRY


_TABLES = (StrShelf, StrDenied, StrAllowed, StrPassword, StrPublic)

SECRET = "sec-3806"
NOTE = "note-3806"
PASSWORD = "pw-3806"
SHELF_SECRET = "sec-3806-shelf"


@pytest.fixture
def rows(transactional_db):
    with connection.schema_editor() as editor:
        for model in _TABLES:
            editor.create_model(model)
    shelf = StrShelf.objects.create(name="shelf", secret=SHELF_SECRET)
    StrDenied.objects.create(shelf=shelf, name="alpha", secret=SECRET)
    StrDenied.objects.create(shelf=shelf, name="beta", secret=SECRET + "-b")
    StrAllowed.objects.create(shelf=shelf, name="gamma", note=NOTE)
    StrPassword.objects.create(name="delta", password=PASSWORD)
    StrPublic.objects.create(name="epsilon", secret=SECRET + "-p")
    try:
        yield shelf
    finally:
        with connection.schema_editor() as editor:
            for model in reversed(_TABLES):
                editor.delete_model(model)


RENDERS = [
    pytest.param(_backend_render, id="DjustTemplateBackend"),
    pytest.param(_liveview_render, id="LiveView"),
]


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", html).strip()


# ---------------------------------------------------------------------------
# The paths that ship str(row). Each is (id, template, context factory, what must appear).
# ---------------------------------------------------------------------------
SHIPPING_PATHS = [
    (
        "top-level-loop",
        "{% for r in rows %}{{ r }};{% endfor %}",
        lambda: {"rows": StrDenied.objects.order_by("pk")},
        f"alpha[{SECRET}];beta[{SECRET}-b];",
        SECRET,
    ),
    (
        "single-model",
        "{{ row }}",
        lambda: {"row": StrDenied.objects.order_by("pk").first()},
        f"alpha[{SECRET}]",
        SECRET,
    ),
    (
        "foreign-key-read-whole",
        "{{ row.shelf }}",
        lambda: {"row": StrDenied.objects.order_by("pk").first()},
        f"shelf[shelf/{SHELF_SECRET}]",
        SHELF_SECRET,
    ),
    (
        "relation-all-first",
        "{{ shelf.denied.all.first }}",
        lambda: {"shelf": StrShelf.objects.get()},
        f"alpha[{SECRET}]",
        SECRET,
    ),
    (
        "relation-all-last",
        "{{ shelf.denied.all.last }}",
        lambda: {"shelf": StrShelf.objects.get()},
        f"beta[{SECRET}-b]",
        SECRET,
    ),
    (
        "allowlisted-model-top-level-loop",
        "{% for r in rows %}{{ r }};{% endfor %}",
        lambda: {"rows": StrAllowed.objects.order_by("pk")},
        f"gamma[{NOTE}];",
        NOTE,
    ),
    (
        "floor-field-in-str",
        "{{ row }}",
        lambda: {"row": StrPassword.objects.get()},
        f"delta[{PASSWORD}]",
        PASSWORD,
    ),
]
PATH_IDS = [p[0] for p in SHIPPING_PATHS]

#: A TOP-LEVEL QuerySet read by ``.first`` / ``.last``. The DjustTemplateBackend ships ``str(row)``
#: for it (its raw-context sidecar); a LiveView does not render the row at all (see
#: ``TestALiveViewsTopLevelQuerySetFirst``), so these run on the backend only.
BACKEND_ONLY_PATHS = [
    (
        "queryset-first",
        "{{ rows.first }}",
        lambda: {"rows": StrDenied.objects.order_by("pk")},
        f"alpha[{SECRET}]",
        SECRET,
    ),
    (
        "queryset-last",
        "{{ rows.last }}",
        lambda: {"rows": StrDenied.objects.order_by("pk")},
        f"beta[{SECRET}-b]",
        SECRET,
    ),
    (
        "first-read-whole-and-by-field",
        "{{ rows.first }}|{{ rows.first.name }}",
        lambda: {"rows": StrDenied.objects.order_by("pk")},
        f"alpha[{SECRET}]|alpha",
        SECRET,
    ),
]
BACKEND_IDS = [p[0] for p in BACKEND_ONLY_PATHS]


@pytest.mark.parametrize("render", RENDERS)
@pytest.mark.parametrize("path", SHIPPING_PATHS, ids=PATH_IDS)
class TestStrOfARowShipsAsPublicText:
    def test_the_text_of_str_is_in_the_page(self, rows, render, path):
        _id, template, ctx, expected, _secret = path
        assert _text(render(template, ctx())) == expected

    def test_it_is_what_django_prints_for_the_same_template(self, rows, render, path):
        """Django parity: the page is the same bytes (modulo whitespace) Django's engine renders."""
        _id, template, ctx, _expected, _secret = path
        assert _text(render(template, ctx())) == _text(_django_render(template, ctx()))


@pytest.mark.parametrize("path", BACKEND_ONLY_PATHS, ids=BACKEND_IDS)
class TestStrOfAQuerySetsFirstAndLastShipsOnTheBackend:
    def test_the_text_of_str_is_in_the_page(self, rows, path):
        _id, template, ctx, expected, _secret = path
        assert _text(_backend_render(template, ctx())) == expected

    def test_it_is_what_django_prints_for_the_same_template(self, rows, path):
        _id, template, ctx, _expected, _secret = path
        assert _text(_backend_render(template, ctx())) == _text(_django_render(template, ctx()))


class TestALiveViewsTopLevelQuerySetFirst:
    """Measured while writing this file, and NOT part of #3806: a LiveView attribute that is a
    QuerySet, read as ``{{ rows.first }}`` / ``{{ rows.last }}``, renders nothing (Django and the
    backend print the row). It withholds more than the documented behaviour, never less, so it is a
    parity gap and not a leak. A strict xfail: when it is fixed this goes red and the documents'
    list of paths that ship ``str(row)`` must gain the LiveView spelling."""

    @pytest.mark.xfail(strict=True, reason="LiveView: top-level QuerySet .first/.last render empty")
    def test_it_prints_the_row_like_django(self, rows):
        out = _liveview_render("{{ rows.first }}", {"rows": StrDenied.objects.order_by("pk")})
        assert _text(out) == f"alpha[{SECRET}]"


# The contrast: the SAME rows, the same secret, read as a FIELD, are withheld on every engine.
FIELD_READS = [
    ("{{ row.secret }}", lambda: {"row": StrDenied.objects.order_by("pk").first()}, SECRET),
    (
        "{% for r in rows %}{{ r.secret }}{% endfor %}",
        lambda: {"rows": StrDenied.objects.order_by("pk")},
        SECRET,
    ),
    ("{{ rows.first.secret }}", lambda: {"rows": StrDenied.objects.order_by("pk")}, SECRET),
    ("{{ shelf.denied.all.first.secret }}", lambda: {"shelf": StrShelf.objects.get()}, SECRET),
    (
        "{{ row.shelf.secret }}",
        lambda: {"row": StrDenied.objects.order_by("pk").first()},
        SHELF_SECRET,
    ),
    ("{{ row.note }}", lambda: {"row": StrAllowed.objects.get()}, NOTE),
    ("{{ row.password }}", lambda: {"row": StrPassword.objects.get()}, PASSWORD),
]


@pytest.mark.parametrize("render", RENDERS)
@pytest.mark.parametrize("template,ctx,secret", FIELD_READS, ids=[f[0][:40] for f in FIELD_READS])
class TestTheFieldFloorStillHoldsBesideIt:
    def test_the_withheld_field_is_not_in_the_page(self, rows, render, template, ctx, secret):
        assert secret not in render(template, ctx())

    def test_django_prints_it_so_the_contrast_is_real(self, rows, render, template, ctx, secret):
        assert secret in _django_render(template, ctx())


@pytest.mark.parametrize("render", RENDERS)
class TestTheAuthorGuidanceIsEnough:
    """Building ``__str__`` from public fields only is the whole remedy: nothing else ships."""

    def test_a_public_str_ships_the_public_text_and_nothing_withheld(self, rows, render):
        out = render("{% for r in rows %}{{ r }};{% endfor %}", {"rows": StrPublic.objects.all()})
        assert _text(out) == "epsilon;"
        assert SECRET not in out
        assert SECRET not in render(
            "{% for r in rows %}{{ r.secret }}{% endfor %}", {"rows": StrPublic.objects.all()}
        )


class TestTheSerializedRowCarriesStr:
    """The reason: ``__str__`` is an identity key, kept by every producer of a serialized model."""

    def test_it_is_an_identity_key(self):
        assert "__str__" in _IDENTITY_KEYS

    def test_model_identity_has_the_text_even_when_its_fields_are_withheld(self, rows):
        row = StrDenied.objects.order_by("pk").first()
        assert model_identity(row)["__str__"] == f"alpha[{SECRET}]"

    def test_the_full_serializer_ships_str_but_not_the_withheld_field(self, rows):
        row = StrDenied.objects.order_by("pk").first()
        shipped = DjangoJSONEncoder()._serialize_model_safely(row)
        assert shipped["__str__"] == f"alpha[{SECRET}]"
        assert "secret" not in shipped
        assert shipped["name"] == "alpha"

    def test_an_allowlist_does_not_constrain_it(self, rows):
        row = StrAllowed.objects.get()
        shipped = DjangoJSONEncoder()._serialize_model_safely(row)
        assert shipped["__str__"] == f"gamma[{NOTE}]"
        assert "note" not in shipped

    def test_a_floor_field_is_withheld_as_a_field_but_not_from_str(self, rows):
        row = StrPassword.objects.get()
        shipped = DjangoJSONEncoder()._serialize_model_safely(row)
        assert "password" not in shipped
        assert shipped["__str__"] == f"delta[{PASSWORD}]"


class TestTheDocsSayIt:
    """The two documents state the behaviour this file measured (option (a) of #3806): each has
    a section with the anchor below, and the section carries every claim the tests above pin."""

    ROOT = Path(__file__).resolve().parents[2]

    ANCHORS = {
        "docs/SECURE_DEFAULTS.md": "**A row's `__str__` is public text (#3806).**",
        "docs/SECURITY_GUIDELINES.md": "### 8. A Row's `__str__` Is Public Text (#3806)",
    }

    @pytest.mark.parametrize("doc", sorted(ANCHORS))
    def test_the_section_exists_and_names_the_behaviour_and_the_guidance(self, doc):
        text = (self.ROOT / doc).read_text()
        assert text.count(self.ANCHORS[doc]) == 1, doc
        start = text.index(self.ANCHORS[doc])
        section = text[start : start + 3500]
        for claim in (
            "`__str__`",
            "djust_exclude_fields",
            "djust_serializable_fields",
            "public",
            "Django",
            "{{ row }}",
            "{% for r in rows %}{{ r }}",
            "{{ row.shelf }}",
            ".all.first",
            "public fields only",
        ):
            assert claim in section, (doc, claim)

    def test_every_path_the_docs_list_is_a_path_this_file_runs(self):
        """The docs list the paths that ship ``str(row)``; each is a template rendered above."""
        rendered = "\n".join(p[1] for p in SHIPPING_PATHS + BACKEND_ONLY_PATHS)
        for spelling in (
            "{% for r in rows %}{{ r }}",
            "{{ row }}",
            "{{ row.shelf }}",
            ".all.first",
            ".all.last",
            "{{ rows.first }}",
            "{{ rows.last }}",
        ):
            assert spelling in rendered, spelling
