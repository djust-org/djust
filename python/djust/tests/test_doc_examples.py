"""Every marked documentation example runs its scenario (ADR-037 D2)."""

import pytest

from djust.tests import _doc_examples as H
from djust.tests.doc_scenarios import load_all

SCENARIOS = load_all()
RUNNABLE = [
    example
    for section in H.COVERED
    for example in H.examples(section)
    if example.marked and example.scenario
]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("example", RUNNABLE, ids=lambda e: e.id)
def test_documented_example(example):
    SCENARIOS[example.scenario](example)


from djust.tests.doc_scenarios import generated  # noqa: E402


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("example", generated.EXAMPLES, ids=lambda e: e.id)
def test_generated_example(example):
    SCENARIOS[example.scenario](example)


def test_the_schema_edit_example_is_the_guide_example():
    from djust.schema import BEST_PRACTICES

    (guide,) = [e for s in H.COVERED for e in H.examples(s) if e.id == "article-edit"]
    assert BEST_PRACTICES["forms"]["edit_example"] == guide.code


def test_model_form_mixin_is_an_optional_mixin():
    from djust.management.commands.djust_audit import KNOWN_MIXINS
    from djust.schema import OPTIONAL_MIXINS

    (entry,) = [m for m in OPTIONAL_MIXINS if m["name"] == "ModelFormMixin"]
    assert entry["import"] == "from djust.forms import ModelFormMixin"
    assert "ModelFormMixin" in KNOWN_MIXINS


def test_plain_form_scaffold_is_unchanged():
    from djust.mcp.server import create_server

    server = create_server()
    (tool,) = [t for t in server._tool_manager._tools.values() if t.name == "scaffold_view"]
    code = tool.fn(name="ContactView", features="form")
    assert "FormMixin" in code and "ModelFormMixin" not in code
