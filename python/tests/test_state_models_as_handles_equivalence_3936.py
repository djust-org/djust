"""#3936 equivalence differential: ``state_models_as_handles`` on vs off.

Every template of the model-state corpus (the #3777 traversal CASES over a QuerySet, single-model
reads, held lists, and the #3888 model-list corpus of 55 loop-operand x relation-read templates)
renders byte-identically, through the full render and through ``render_with_diff`` after a
change, with the flag on and off, on both exposure policies, except an explicit allowlist of the declared behaviour changes. The non-vacuity case
(``{% with q=u|default:u %}{{ q.groups.count }}``) MUST differ on an explicit view, so the
harness proves the flag is live.
"""

# ruff: noqa: F811
from __future__ import annotations

import re

import pytest
from django.contrib.auth.models import User

from python.tests.test_queryset_all_traversal_3777 import CASES, world  # noqa: F401
from python.tests.test_sliced_list_of_models_3888 import FILTERED, READS, UNFILTERED
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


#: The #3888 model-list corpus (review N5): every loop operand, filtered or not, by every relation
#: read through the loop variable -- 55 templates over a list of models and over a QuerySet.
ROW_CORPUS = [
    (f"{op}-{read}", operand.replace("<R>", READS[read]))
    for op, operand in {**FILTERED, **UNFILTERED}.items()
    for read in READS
]


def _user_list() -> dict:
    return {"users": list(User.objects.order_by("pk"))}


def _user_qs() -> dict:
    return {"users": User.objects.order_by("pk")}


@pytest.mark.parametrize("policy", ["explicit", "legacy"])
@pytest.mark.parametrize("factory", [_user_list, _user_qs], ids=["list", "queryset"])
@pytest.mark.parametrize("name,source", ROW_CORPUS, ids=[n for n, _ in ROW_CORPUS])
def test_row_corpus_is_unchanged(world, policy, factory, name, source):
    assert _render(source, factory, policy, flag=True) == _render(
        source, factory, policy, flag=False
    )


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


# -- the diff entry (train/2.0-ah review B3) ---------------------------------------------------
#
# ``_render`` above is the full render (``render_result``), which never reads the loop render
# cache. ``render_with_diff`` does, and a handle hashed as ``str(model)`` replayed a stale item
# there. So every template also renders, changes the rows, and renders WITH DIFF, flag on vs
# off; the held-list rows read a column ``__str__`` does not spell.

HELD_LIST = [
    ("held-list-column", "{% for x in us %}<li>{{ x.email }}</li>{% endfor %}"),
    ("held-list-relation", "{% for x in us %}<li>{{ x.groups.count }}</li>{% endfor %}"),
    ("held-list-filtered", "{% for x in us|slice:':2' %}<li>{{ x.groups.count }}</li>{% endfor %}"),
]


def _held() -> dict:
    return {"us": list(User.objects.order_by("pk"))}


def _change_rows() -> None:
    from django.contrib.auth.models import Group

    User.objects.update(email="changed@example.com")
    Group.objects.filter(name="staff").update(name="staff-renamed")
    User.objects.get(username="u2").groups.add(Group.objects.get(name="ops"))


def _render_change_diff(source: str, factory, policy: str, flag: bool) -> str:
    from django.test import override_settings

    from djust.testing import LiveViewTestClient
    from python.tests.test_state_models_as_handles_3936 import FLAG_ON, _body, _view

    def body(html: str) -> str:
        return _body(
            re.sub(r"<div [^>]*dj-root[^>]*>", "<div dj-root>", html.replace(" dj-id=", " data-x="))
        )

    config = dict(FLAG_ON if flag else {}, loop_render_cache_enabled=True)
    from django.db import transaction

    # The change is rolled back, so the flag-on and flag-off runs start from the same rows.
    with override_settings(LIVEVIEW_CONFIG=config), transaction.atomic():
        client = LiveViewTestClient(_view(source, factory, policy))
        client.mount()
        view = client.view_instance
        view.render_with_diff()
        _change_rows()
        got = re.sub(r' data-x="[^"]*"', "", body(view.render_with_diff()[0]))
        transaction.set_rollback(True)
    return got


def _diff_corpus():
    for name, source, factory in CASES:
        yield pytest.param(source, factory, id=name)
    for name, source in SINGLE_MODEL:
        yield pytest.param(source, _single, id=name)
    for name, source in HELD_LIST:
        yield pytest.param(source, _held, id=name)
    for name, source in ROW_CORPUS:
        yield pytest.param(source, _user_list, id="rows-" + name)


@pytest.mark.parametrize("policy", ["explicit", "legacy"])
@pytest.mark.parametrize("source,factory", list(_diff_corpus()))
def test_render_with_diff_after_a_change_is_unchanged(world, policy, source, factory):
    on = _render_change_diff(source, factory, policy, flag=True)
    off = _render_change_diff(source, factory, policy, flag=False)
    assert on == off, (on, off)


def test_the_diff_harness_sees_a_change(world):
    """Non-vacuity: the held-list column follows the change through the diff entry."""
    got = _render_change_diff(HELD_LIST[0][1], _held, "explicit", flag=True)
    assert got.count("changed@example.com") == 3, got  # explicit re-reads the rows
    got = _render_change_diff(HELD_LIST[1][1], _held, "explicit", flag=True)
    assert got.endswith("<li>1</li>"), got  # u2 gained ops
