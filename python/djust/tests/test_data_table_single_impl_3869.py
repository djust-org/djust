"""``{% data_table %}`` is one implementation on both engines (#3869).

The Rust engine draws the table with the native ``DataTableHandler``. The Django engine's
``data_table`` used to be an inclusion tag over ``djust_components/table.html``, a second
implementation that drew none of the native-only features (``expandable``, ``editable_columns``,
``exportable`` ...). It is now a simple tag that hands the handler the same operands
(``render_data_table``), so both engines draw the same bytes, and ``table.html`` is gone.

* ``TestBothEnginesDrawTheSameBytes``: three row shapes x three flag sets, byte for byte.
* ``test_expandable_is_wired_under_the_django_engine``: the feature the issue named.
* ``test_table_html_is_gone``: no template file, so no override of it can take effect.
* ``TestTheExposureFloor``: a model row under the Django engine is its floored dict.
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


def test_table_html_is_gone() -> None:
    """No template file, so an override of it (``djust_components/table.html``) has no effect."""
    templates = Path(djust.components.__file__).parent / "templates"
    assert not (templates / "djust_components" / "table.html").exists()
    with pytest.raises(TemplateDoesNotExist):
        Engine(dirs=[str(templates)]).get_template("djust_components/table.html")


class TestTheExposureFloor:
    def test_a_model_row_is_its_floored_dict_under_the_django_engine(self) -> None:
        src = "{% data_table rows=rows columns=cols %}"
        out = _django(src, {"rows": [_account()], "cols": MODEL_COLUMNS})
        assert "ann" in out
        assert "pbkdf2$secret" not in out and "secret" not in out, out

    def test_a_generator_of_models_is_floored_too(self) -> None:
        src = "{% data_table rows=rows columns=cols %}"
        out = _django(src, {"rows": (m for m in [_account()]), "cols": MODEL_COLUMNS})
        assert "ann" in out and "secret" not in out, out
