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
