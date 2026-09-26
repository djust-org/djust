"""ADR-034 D7 scenarios: interactive components behave as documented."""

import re

from djust.tests import _doc_examples as H

from . import scenario


@scenario("project-menu")
def project_menu(example):
    page = H.Page(H.load(example))
    (menu,) = page.ids()
    assert page.event("toggle", component_id=menu) == 200
    # The disabled item and an unknown value never reach the callback.
    if '"disabled": True' in example.code:
        assert page.event("select", component_id=menu, value="delete") >= 400
    assert page.event("select", component_id=menu, value="nope") >= 400
    assert page.event("select", component_id=menu, value="edit") == 200
    assert page.view.selected_action == "edit"


@scenario("toolbar")
def toolbar(example):
    page = H.Page(H.load(example))
    project, account = page.ids()
    assert page.event("select", component_id=account, value="settings") == 200
    assert page.view.show_account_settings and page.view.selected_action == ""
    assert not page.view.project_menu.open
    assert page.event("select", component_id=project, value="archive") == 200
    assert page.view.selected_action == "archive"


@scenario("help-observe")
def help_observe(example):
    page = H.Page(H.load(example))
    (menu,) = page.ids()
    lifetime = re.search(r'data-dj-observe-lifetime="([^"]+)"', page.html).group(1)
    report = {"component_id": menu, "lifetime": lifetime}
    assert page.event("observe_toggle", open=True, sequence=1, **report) == 200
    assert page.view.help_opened == 1
    assert page.event("observe_toggle", open=True, sequence=1, **report) == 200  # duplicate
    assert page.event("observe_toggle", open=False, sequence=2, **report) == 200
    assert page.view.help_opened == 1


@scenario("row-actions")
def row_actions(example):
    page = H.Page(H.load(example))
    assert page.ids() == []
    assert page.html.count('dj-value-project-id:int="42"') == 1
    assert page.event("project_action", project_id="87", action="details") == 200
    assert (page.view.selected_project, page.view.selected_action) == (87, "details")
    assert page.event("project_action", project_id="99", action="details") >= 400


@scenario("project-list")
def project_list(example):
    page = H.Page(H.load(example))
    alpha, beta = page.ids()
    assert page.event("select", component_id=beta, value="details") == 200
    assert page.view.selected == "87:details"
    assert page.event("select", component_id=alpha, value="archive") == 200
    assert page.view.selected == "42:archive"
    assert [m.key for m in page.view.row_menus] == ["87"]
    assert page.view.row_menus.get("42") is None
    # The archived row's identity is refused from now on.
    assert page.event("select", component_id=alpha, value="details") >= 400
