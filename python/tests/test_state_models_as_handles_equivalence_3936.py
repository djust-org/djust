"""#3936 equivalence differential: ``state_models_as_handles`` on vs off.

Every template of the model-state corpus (the #3777 traversal CASES over a QuerySet, plus
single-model reads) renders byte-identically with the flag on and off, on both exposure
policies, except an explicit allowlist of the declared behaviour changes. The non-vacuity case
(``{% with q=u|default:u %}{{ q.groups.count }}``) MUST differ on an explicit view, so the
harness proves the flag is live.
"""

# ruff: noqa: F811
from __future__ import annotations

import pytest
from django.contrib.auth.models import User

from python.tests.test_queryset_all_traversal_3777 import CASES, world  # noqa: F401
from python.tests.test_state_models_as_handles_3936 import _render

SINGLE_MODEL = [
    ("model-column", "{{ u.username }}"),
    ("model-str", "{{ u }}"),
    ("model-relation-count", "{{ u.groups.count }}"),
    ("model-relation-loop", "{% for g in u.groups.all %}{{ g.name }},{% endfor %}"),
    ("model-if", "{% if u %}y{% endif %}{% if u.groups.all %}g{% endif %}"),
    ("model-with", "{% with q=u %}{{ q.username }}{{ q.groups.count }}{% endwith %}"),
    ("model-method", "{{ u.get_full_name }}|{{ u.get_username }}"),
    ("model-password", "{{ u.password }}|{{ u.groups.first.user_set.first.password }}"),
    ("model-pk-compare", "{% if u.pk == 1 %}one{% endif %}{{ u.id }}"),
]

#: Declared behaviour changes (ADR-027 addendum): a filtered rebinding reads the model.
MUST_DIFFER = {
    "filtered-rebinding": "{% with q=u|default:u %}{{ q.groups.count }}{% endwith %}",
}


def _single() -> dict:
    return {"u": User.objects.order_by("pk").first()}


@pytest.mark.parametrize("policy", ["explicit", "legacy"])
@pytest.mark.parametrize("name,source,factory", [pytest.param(*c, id=c[0]) for c in CASES])
def test_queryset_corpus_is_unchanged(world, policy, name, source, factory):
    assert _render(source, factory, policy, flag=True) == _render(
        source, factory, policy, flag=False
    )


@pytest.mark.parametrize("policy", ["explicit", "legacy"])
@pytest.mark.parametrize("name,source", SINGLE_MODEL, ids=[n for n, _ in SINGLE_MODEL])
def test_single_model_corpus_is_unchanged(world, policy, name, source):
    assert _render(source, _single, policy, flag=True) == _render(
        source, _single, policy, flag=False
    )


def test_the_harness_sees_the_flag(world):
    source = MUST_DIFFER["filtered-rebinding"]
    on = _render(source, _single, "explicit", flag=True)
    off = _render(source, _single, "explicit", flag=False)
    assert on != off and on == "2", (on, off)
