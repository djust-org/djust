"""The ui twins draw what the simple classes need (#4002, slice 3 follow-up).

The simple ``Alert``, ``Modal`` and ``Dropdown`` render through ``AlertComponent``,
``ModalComponent`` and ``DropdownComponent``. Three things the simple classes drew were lost on
that path and are now in the twins:

* ``DropdownItem`` takes an optional ``url`` (checked and escaped like any URL) and ``disabled``.
* ``ModalComponent(standalone=True, show=False)`` draws the hidden Bootstrap modal, so
  ``data-bs-toggle="modal"`` can open it, instead of nothing.
* ``standalone=True`` makes the Bootstrap close controls of ``AlertComponent`` /
  ``ModalComponent`` Bootstrap's ``data-bs-dismiss``: no view receives ``dismiss`` for a
  component rendered on its own.

A registered component's output is unchanged: ``standalone`` defaults to ``False``, a hidden
``ModalComponent`` still renders ``""`` (its ``hide()`` / ``dismiss`` close it that way), and an
item without ``url`` / ``disabled`` draws what it drew.
"""

from __future__ import annotations

import warnings
from typing import Iterator

import pytest

from djust.components.ui import (
    Alert,
    AlertComponent,
    Dropdown,
    DropdownComponent,
    DropdownItem,
    Modal,
    ModalComponent,
)
from djust.config import config

FRAMEWORKS = ("bootstrap5", "tailwind", "plain")


@pytest.fixture
def framework(request: pytest.FixtureRequest) -> Iterator[str]:
    before = config.get("css_framework", "bootstrap5")
    config.set("css_framework", request.param)
    try:
        yield request.param
    finally:
        config.set("css_framework", before)


def _dropdown(*items: DropdownItem) -> str:
    return str(DropdownComponent(component_id="d", label="M", items=list(items)).render())


# --- DropdownItem url / disabled ------------------------------------------------------------


@pytest.mark.parametrize("framework", FRAMEWORKS, indirect=True)
def test_item_url_is_the_link(framework: str) -> None:
    assert 'href="/docs/?a=1&amp;b=2"' in _dropdown(DropdownItem(text="A", url="/docs/?a=1&b=2"))


@pytest.mark.parametrize("framework", FRAMEWORKS, indirect=True)
@pytest.mark.parametrize("url", ["javascript:alert(1)", " JavaScript:x", "data:text/html,x"])
def test_item_url_with_a_non_navigation_scheme_becomes_hash(framework: str, url: str) -> None:
    html = _dropdown(DropdownItem(text="A", url=url))
    assert 'href="#"' in html
    assert "javascript" not in html.lower() and "data:" not in html


@pytest.mark.parametrize("framework", FRAMEWORKS, indirect=True)
def test_item_url_cannot_leave_its_attribute(framework: str) -> None:
    html = _dropdown(DropdownItem(text="A", url='/p" onmouseover="y'))
    assert '" onmouseover="' not in html
    assert 'href="/p&quot; onmouseover=&quot;y"' in html


@pytest.mark.parametrize("framework", FRAMEWORKS, indirect=True)
def test_disabled_item_is_marked_and_has_no_click(framework: str) -> None:
    html = _dropdown(DropdownItem(text="A", action="go", disabled=True))
    assert 'aria-disabled="true"' in html
    assert 'dj-click="go' not in html


@pytest.mark.parametrize("framework", FRAMEWORKS, indirect=True)
def test_an_item_without_url_or_disabled_draws_what_it_drew(framework: str) -> None:
    html = _dropdown(DropdownItem(text="A", action="go"))
    assert 'href="#"' in html and "aria-disabled" not in html
    assert 'dj-click="go' in html


def test_an_item_without_url_or_disabled_is_byte_identical_under_bootstrap() -> None:
    assert _dropdown(DropdownItem(text="A", action="go", variant="danger")) == (
        '<div class="btn-group dropdown" id="d"><button type="button" class="btn btn-secondary'
        ' dropdown-toggle" data-bs-toggle="dropdown" aria-expanded="false">M</button>'
        '<ul class="dropdown-menu"><li><a class="dropdown-item text-danger" href="#"'
        ' dj-click="go">A</a></li></ul></div>'
    )


def test_to_dict_carries_url_and_disabled_only_when_set() -> None:
    assert DropdownItem(text="A").to_dict() == {"text": "A", "action": ""}
    assert DropdownItem(text="A", url="/a", disabled=True).to_dict() == {
        "text": "A",
        "action": "",
        "url": "/a",
        "disabled": True,
    }


def test_simple_dropdown_item_url_and_disabled_map_onto_the_item() -> None:
    html = str(
        Dropdown(
            "M", items=[{"label": "A", "url": "/a"}, {"label": "B", "disabled": True}]
        ).render()
    )
    assert '<a class="dropdown-item" href="/a">A</a>' in html
    assert '<a class="dropdown-item disabled" href="#" aria-disabled="true">B</a>' in html


# --- ModalComponent standalone ------------------------------------------------------------------


@pytest.mark.parametrize("framework", FRAMEWORKS, indirect=True)
def test_a_registered_hidden_modal_still_renders_nothing(framework: str) -> None:
    assert ModalComponent(component_id="m", title="T", body="B").render() == ""


def test_a_standalone_hidden_modal_is_the_hidden_bootstrap_markup() -> None:
    html = str(ModalComponent(component_id="m", title="T", body="B", standalone=True).render())
    assert '<div class="modal fade" id="m" tabindex="-1" aria-hidden="true">' in html
    assert "d-block" not in html and "modal-backdrop" not in html and "show" not in html
    assert "dj-click" not in html
    assert html.count('data-bs-dismiss="modal"') == 2


@pytest.mark.parametrize("framework", ["tailwind", "plain"], indirect=True)
def test_a_standalone_hidden_modal_outside_bootstrap_renders_nothing_and_warns(
    framework: str,
) -> None:
    with pytest.warns(UserWarning, match="hidden standalone ModalComponent renders nothing"):
        assert ModalComponent(component_id="m", body="B", standalone=True).render() == ""


@pytest.mark.parametrize("framework", FRAMEWORKS, indirect=True)
def test_a_registered_hidden_modal_does_not_warn(framework: str) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert ModalComponent(component_id="m", body="B").render() == ""


@pytest.mark.parametrize("framework", ["tailwind", "plain"], indirect=True)
def test_a_standalone_shown_modal_outside_bootstrap_draws_no_dead_close_control(
    framework: str,
) -> None:
    html = str(ModalComponent(component_id="m", body="B", show=True, standalone=True).render())
    assert "dj-click" not in html and "data-component-id" not in html
    assert "B" in html


@pytest.mark.parametrize("framework", ["tailwind", "plain"], indirect=True)
def test_a_registered_shown_modal_outside_bootstrap_keeps_its_close_controls(
    framework: str,
) -> None:
    html = str(ModalComponent(component_id="m", body="B", show=True).render())
    assert 'dj-click="dismiss" data-component-id="m"' in html


@pytest.mark.parametrize("framework", FRAMEWORKS, indirect=True)
def test_registered_modal_escapes_its_id_the_same_in_header_and_footer(framework: str) -> None:
    """Both close controls write the id escaped, the same way in every framework (the
    Bootstrap footer's ``conditional_escape`` restored, as before #4002)."""
    html = str(ModalComponent(component_id='m"1', body="B", show=True).render())
    assert 'data-component-id="m"1"' not in html
    assert html.count('data-component-id="m&quot;1"') == 2


def test_a_standalone_shown_modal_closes_with_bootstrap() -> None:
    html = str(ModalComponent(component_id="m", body="B", show=True, standalone=True).render())
    assert 'class="modal fade show d-block"' in html and "modal-backdrop" in html
    assert "dj-click" not in html
    assert html.count('data-bs-dismiss="modal"') == 2


def test_a_registered_shown_modal_closes_through_dismiss() -> None:
    html = str(ModalComponent(component_id="m", body="B", show=True).render())
    assert "data-bs-dismiss" not in html
    assert html.count('dj-click="dismiss" data-component-id="m"') == 2


def test_simple_modal_is_standalone() -> None:
    hidden = str(Modal("B", id="m").render())
    assert 'class="modal fade" id="m"' in hidden and 'data-bs-dismiss="modal"' in hidden
    shown = str(Modal("B", id="m", show=True).render())
    assert "show d-block" in shown and "dj-click" not in shown


# --- AlertComponent standalone ------------------------------------------------------------------


def test_a_registered_alert_closes_through_dismiss() -> None:
    html = str(AlertComponent(component_id="a", message="m").render())
    assert 'dj-click="dismiss" data-component-id="a"' in html
    assert "data-bs-dismiss" not in html


def test_a_standalone_alert_closes_with_bootstrap() -> None:
    html = str(AlertComponent(component_id="a", message="m", standalone=True).render())
    assert (
        '<button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close">'
        in html
    )
    assert "dj-click" not in html


def test_simple_alert_is_standalone() -> None:
    html = str(Alert("m", dismissable=True).render())
    assert 'data-bs-dismiss="alert"' in html and "dj-click" not in html


@pytest.mark.parametrize("framework", ["tailwind", "plain"], indirect=True)
def test_a_standalone_alert_outside_bootstrap_draws_no_dead_close_button(framework: str) -> None:
    html = str(AlertComponent(component_id="a", message="m", standalone=True).render())
    assert "dj-click" not in html and "<button" not in html
    registered = str(AlertComponent(component_id="a", message="m").render())
    assert 'dj-click="dismiss" data-component-id="a"' in registered


# --- DropdownItem data values -------------------------------------------------------------------


@pytest.mark.parametrize("framework", FRAMEWORKS, indirect=True)
def test_data_values_are_kept_as_written(framework: str) -> None:
    """``dj-value-*`` attributes are event parameters the browser never follows: any value but
    a script scheme comes through as escaped text, whatever its key (#4002 review)."""
    html = _dropdown(
        DropdownItem(
            text="A",
            action="go",
            data={"action": "ns:del", "link": "/a?b=1&c=2", "item_id": 4, "note": 'x"y'},
        )
    )
    assert 'dj-value-action="ns:del"' in html
    assert 'dj-value-link="/a?b=1&amp;c=2"' in html
    assert 'dj-value-item_id="4"' in html and 'dj-value-note="x&quot;y"' in html


@pytest.mark.parametrize("framework", FRAMEWORKS, indirect=True)
@pytest.mark.parametrize("key", ["link", "action", "itemId", "nextUrl", "next", "target", "x"])
@pytest.mark.parametrize(
    "value",
    ["javascript:alert(1)", " JaVaScRiPt:alert(1)", "java\tscript:alert(1)", "vbscript:x",
     "data:text/html,<b>x</b>", "\x00javascript:x"],
)  # fmt: skip
def test_a_script_scheme_in_any_data_value_is_neutralised(
    framework: str, key: str, value: str
) -> None:
    from django.utils.safestring import mark_safe

    for claimed in (value, mark_safe(value)):
        html = _dropdown(DropdownItem(text="A", action="go", data={key: claimed}))
        assert f'dj-value-{key}="#"' in html
        assert "script:" not in html.lower() and "data:" not in html
