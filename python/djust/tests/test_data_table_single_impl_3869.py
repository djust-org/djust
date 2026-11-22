"""``{% data_table %}`` is one implementation on both engines (#3869).

The Rust engine draws the table with the native ``DataTableHandler``. The Django engine's
``data_table`` used to be an inclusion tag over ``djust_components/table.html``, a second
implementation that drew none of the native-only features (``expandable``, ``editable_columns``,
``exportable`` ...). It is now a simple tag that hands the handler the same operands
(``render_data_table``), so both engines draw the same bytes, and ``table.html`` is gone.

* ``TestBothEnginesDrawTheSameBytes``: three row shapes x three flag sets, byte for byte.
* ``test_expandable_is_wired_under_the_django_engine``: the feature the issue named.
* ``test_table_html_is_gone``: no template file, so no override of it can take effect.
* ``TestTheExposureFloor``: a model row under the Django engine never prints a floored field.
  Two independent pins: ``_python_operand`` hands the handler floored dicts (the operand test),
  and ``table_row`` floors a model itself (the print tests).
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from pathlib import Path

import pytest
from django.template import Engine, TemplateDoesNotExist

import djust.components
from djust.tests.test_component_dj_attr_parity_3669 import _django, _rust
from djust.tests.test_data_table_residuals_3895 import TableAccount3895, _account

COLUMNS = [
    {"key": "name", "label": "Name"},
    {"key": "age", "label": "Age", "sortable": False},
    {"key": "u", "label": "Link", "link": "u"},
]
ROWS = [
    {"id": 1, "name": "Ann <b>", "age": 31, "u": "/a"},
    {"id": 2, "name": "Bo & Co", "age": 40, "u": "/b"},
]

SHAPES = {
    "list-of-dicts": lambda: list(ROWS),
    "tuple-of-dicts": lambda: tuple(ROWS),
    # a value the Rust engine reads after its JSON text: a Decimal, a date, a UUID, a nested dict
    "typed-cells": lambda: [
        {
            "id": 1,
            "name": datetime.datetime(2026, 1, 2, 3, 4, 5),
            "age": decimal.Decimal("31.50"),
            "u": uuid.UUID(int=7),
            "extra": {"k": (1, 2)},
            "t": (3, 4),
        }
    ],
    "model-instances": lambda: [
        TableAccount3895(id=1, username="ann", password="pbkdf2$secret"),
        TableAccount3895(id=2, username="bo", password="pbkdf2$other"),
    ],
}
MODEL_COLUMNS = [
    {"key": "username", "label": "User"},
    {"key": "password", "label": "Password"},
    {"key": "id", "label": "Id"},
]

FLAGS = {
    "plain": "",
    "features": "expandable=True editable_columns=sel exportable=True reorderable=True "
    "column_visibility=True density_toggle=True copyable=True",
    "paged": "selectable=True search=True paginate=True total_pages=3 page=2 striped=True "
    'sort_by="name" row_click_event="open"',
}


TYPED_COLUMNS = [*COLUMNS, {"key": "extra", "label": "Extra"}, {"key": "t", "label": "T"}]
COLUMNS_OF = {"model-instances": MODEL_COLUMNS, "typed-cells": TYPED_COLUMNS}


def _context(shape: str) -> dict:
    return {
        "rows": SHAPES[shape](),
        "cols": COLUMNS_OF.get(shape, COLUMNS),
        "sel": ["name"],
    }


class TestBothEnginesDrawTheSameBytes:
    @pytest.mark.parametrize("flags", sorted(FLAGS))
    @pytest.mark.parametrize("shape", sorted(SHAPES))
    def test_the_django_engine_draws_what_the_rust_engine_draws(
        self, shape: str, flags: str
    ) -> None:
        src = "{% data_table rows=rows columns=cols " + FLAGS[flags] + " %}"
        context = _context(shape)
        assert _django(src, context) == _rust(src, context)


def test_expandable_is_wired_under_the_django_engine() -> None:
    """The feature the issue named: ``table.html`` never drew it."""
    src = "{% data_table rows=rows columns=cols expandable=True %}"
    out = _django(src, {"rows": list(ROWS), "cols": COLUMNS})
    assert out.count('dj-click="on_table_expand"') == len(ROWS), out


def test_the_other_native_only_features_are_wired_under_the_django_engine() -> None:
    for flag, needle in (
        ("exportable=True", "on_table_export"),
        ("reorderable=True", "on_table_reorder"),
        ("copyable=True", "on_table_copy"),
        ("importable=True", "on_table_import"),
        ("row_drag=True", "on_table_row_drag"),
    ):
        out = _django(
            "{% data_table rows=rows columns=cols " + flag + " %}",
            {"rows": list(ROWS), "cols": COLUMNS},
        )
        assert needle in out, (flag, out)


def test_the_handler_is_handed_exactly_the_tags_parameters(monkeypatch) -> None:
    """Review N3: the operands are the signature's parameters. A local added to the tag function
    (``locals()`` once built the dict) must not become an operand. Gate-off: build the dict from
    ``locals()`` again and add a local above it, and this fails."""
    import inspect

    from djust.components import rust_handlers
    from djust.components.templatetags import djust_components as tags

    seen: dict = {}
    monkeypatch.setattr(rust_handlers, "render_data_table", lambda **params: seen.update(params))
    tags.data_table(rows=[], columns=[])
    assert list(seen) == list(inspect.signature(tags.data_table).parameters)


def test_table_html_is_gone() -> None:
    """No template file, so an override of it (``djust_components/table.html``) has no effect."""
    templates = Path(djust.components.__file__).parent / "templates"
    assert not (templates / "djust_components" / "table.html").exists()
    with pytest.raises(TemplateDoesNotExist):
        Engine(dirs=[str(templates)]).get_template("djust_components/table.html")


class TestTheExposureFloor:
    """A floored field (``password``, ``djust_exclude_fields``) never reaches the page. There are
    TWO independent protections, each with its own pin (review S4):

    * ``_python_operand`` (``render_data_table``) hands the handler the serialised dict of a model
      row, so the exposure floor already applied: ``test_the_handler_is_handed_floored_dicts``.
      Gate-off: let ``_python_operand`` return the raw value, and that test fails (the print tests
      still pass, because the second protection holds).
    * ``table_row`` floors a model itself (``normalize_django_value``) when the handler is handed
      a raw one: ``test_table_row_floors_a_model_itself``. Gate-off: have ``table_row`` read a
      model's fields, and that test fails (the operand tests still pass). The print tests pass
      with either one removed: the Django engine's operands are floored before the handler
      reads them, so they are end-to-end checks of the pair, not a pin of either.
    """

    def test_the_handler_is_handed_floored_dicts(self, monkeypatch) -> None:
        from djust.components import rust_handlers

        seen: list[dict] = []

        def spy(self, kw, context):  # noqa: ANN001
            seen.append(kw)
            return ""

        monkeypatch.setattr(rust_handlers.DataTableHandler, "render_kwargs", spy)
        for rows in ([_account()], (m for m in [_account()])):
            seen.clear()
            rust_handlers.render_data_table(rows=rows, columns=MODEL_COLUMNS)
            (kw,) = seen
            (row,) = kw["rows"]
            assert isinstance(row, dict), kw["rows"]
            assert row["username"] == "ann", row
            assert not {"password", "email"} & set(row), row

    def test_python_operand_is_the_floored_dict_of_a_model(self) -> None:
        from djust.components.rust_handlers import _python_operand

        (row,) = _python_operand([_account()])  # type: ignore[misc]
        assert isinstance(row, dict) and "password" not in row and "email" not in row, row

    def test_table_row_floors_a_model_itself(self) -> None:
        from djust.components.utils import table_row

        row = table_row(_account(), ["username", "password", "email"])
        assert row["username"] == "ann", row
        assert "password" not in row and "email" not in row, row

    def test_a_model_row_is_its_floored_dict_under_the_django_engine(self) -> None:
        src = "{% data_table rows=rows columns=cols %}"
        out = _django(src, {"rows": [_account()], "cols": MODEL_COLUMNS})
        assert "ann" in out
        assert "pbkdf2$secret" not in out and "secret" not in out, out

    def test_a_generator_of_models_is_floored_too(self) -> None:
        src = "{% data_table rows=rows columns=cols %}"
        out = _django(src, {"rows": (m for m in [_account()]), "cols": MODEL_COLUMNS})
        assert "ann" in out and "secret" not in out, out
