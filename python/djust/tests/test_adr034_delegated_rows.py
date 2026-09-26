"""ADR-034 D5: the delegated-row example, run as written in the ADR.

Repeated rows that only choose an operation on a record pass the record id to
an ordinary view handler: no component per row, no stateful collection. The
ADR's ``RowActionsView`` block is extracted with the doc-snippet checker's own
extractor, and its HTML block is the template. It is driven through the HTTP
fallback with the ids the page renders, before and after a reorder, and with
forged ids and actions.
"""

import importlib.util
import json
import pathlib
import re
import sys
import types

import pytest
from django.test import RequestFactory

ROOT = pathlib.Path(__file__).resolve().parents[3]
ADR = ROOT / "docs/adr/034-component-scoped-events-and-bindings.md"


def _example():
    spec = importlib.util.spec_from_file_location(
        "_doc_snippets", ROOT / "scripts/check-doc-snippets.py"
    )
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    code = next(
        code
        for _line, code, _m in checker.extract_python_blocks(ADR)
        if "class RowActionsView" in code
    )
    text = ADR.read_text()
    start = text.index("class RowActionsView")
    html = re.search(r"```html\n(.*?)\n```", text[start:], re.S).group(1)
    module = types.ModuleType("djust_adr034_rows")
    sys.modules[module.__name__] = module
    exec(compile(code, "<adr034-rows>", "exec"), module.__dict__)
    view = module.RowActionsView
    view.template_name = None
    view.template = "<div dj-root>" + html + "</div>"
    return view


RowActionsView = _example()


def _post(initial, params, event="project_action"):
    request = RequestFactory().post(
        initial.path,
        data=json.dumps(params),
        content_type="application/json",
        HTTP_X_DJUST_EVENT=event,
    )
    request.user, request.session, request.tenant = initial.user, initial.session, None
    view = RowActionsView()
    return view.post(request), view


@pytest.mark.django_db
def test_rows_carry_their_record_id_and_forged_ids_are_refused():
    from djust.tests.test_exposure_runtime import make_request

    initial = make_request()
    page = RowActionsView().get(initial).content.decode()
    # Each row's buttons carry that row's id, typed; no component per row.
    assert page.count('dj-value-project-id:int="42"') == 2
    assert page.count('dj-value-project-id:int="87"') == 2
    assert "data-component-id" not in page

    response, view = _post(initial, {"project_id": "87", "action": "activity"})
    assert response.status_code == 200, response.content
    assert (view.selected_project, view.selected_action) == (87, "activity")

    for params in (
        {"project_id": "99", "action": "details"},
        {"project_id": "42", "action": "drop"},
    ):
        response, view = _post(initial, params)
        assert response.status_code >= 400, params
        assert (view.selected_project, view.selected_action) == (87, "activity")


@pytest.mark.django_db
def test_a_reordered_list_still_sends_each_rows_own_id():
    from djust.tests.test_exposure_runtime import make_request

    view = RowActionsView()
    request = make_request()
    view.get(request)
    view.projects = list(reversed(view.projects))
    view.request = request
    html, _patches, _version = view.render_with_diff(request)
    beta, alpha = html.index("Beta"), html.index("Alpha")
    assert beta < alpha
    assert html.index('dj-value-project-id:int="87"', beta) < alpha
    assert html.index('dj-value-project-id:int="42"', alpha) > alpha
