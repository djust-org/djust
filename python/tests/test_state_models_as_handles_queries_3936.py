"""#3936 overlay invariant: the handle overlay issues no query the flag-off render does not, and
answers every read the flag-off render answers (train/2.0-ai, B-H1/B-H2).

The overlay binds a floored handle for each state key holding a model ABOVE the state. For a
top-level QuerySet that handle is a list of row handles, and before this fix it shadowed every
answer the JIT and the render sidecar already give for that name with Django's semantics:
``{{ users.count }}`` / ``.exists`` / ``.first`` / ``.last`` rendered empty (a list has no
``count``), ``{% for g in u.groups.all %}`` walked each row live (one query per row, and
``queryset_all_row_cap`` was skipped), and a legacy single model was walked on the caller's
instance instead of the JIT's prefetched one. Such a key now keeps the flag-off path.

Each probe renders with the flag on and off, on both exposure policies; the output AND the number
of queries must match. Real ``User`` / ``Group`` rows (#1650).
"""

# ruff: noqa: F811
from __future__ import annotations

import pytest
from django.contrib.auth.models import Group, User
from django.db import connection
from django.test.utils import CaptureQueriesContext

from python.tests.test_queryset_all_traversal_3777 import world  # noqa: F401
from python.tests.test_state_models_as_handles_3936 import _render


def _qs() -> dict:
    return {"users": User.objects.order_by("pk")}


def _held() -> dict:
    return {"users": list(User.objects.order_by("pk"))}


def _one() -> dict:
    return {"u": User.objects.order_by("pk").first()}


PROBES = [
    ("qs-column", "{% for u in users %}{{ u.username }};{% endfor %}", _qs),
    (
        "qs-nested-all",
        "{% for u in users %}{% for g in u.groups.all %}{{ g.name }},{% endfor %};{% endfor %}",
        _qs,
    ),
    ("qs-row-count", "{% for u in users %}{{ u.groups.count }};{% endfor %}", _qs),
    (
        "qs-slice-walk",
        "{% for u in users|slice:':2' %}{{ u.groups.count }};{% endfor %}",
        _qs,
    ),
    ("qs-with-first", "{% with q=users|first %}{{ q.groups.count }}{% endwith %}", _qs),
    ("qs-count", "{{ users.count }}", _qs),
    ("qs-exists", "{% if users.exists %}y{% endif %}|{{ users.exists }}", _qs),
    ("qs-first-last", "{{ users.first.username }}|{{ users.last.username }}", _qs),
    (
        "qs-loop-and-method",
        "{% for u in users %}{% for g in u.groups.all %}{{ g.name }},{% endfor %}"
        "{{ u.groups.all.count }};{% endfor %}",
        _qs,
    ),
    (
        "qs-count-and-loop",
        "{{ users.count }}:{% for u in users %}{{ u.username }};{% endfor %}",
        _qs,
    ),
    (
        "held-nested-all",
        "{% for u in users %}{% for g in u.groups.all %}{{ g.name }},{% endfor %};{% endfor %}",
        _held,
    ),
    ("held-row-count", "{% for u in users %}{{ u.groups.count }};{% endfor %}", _held),
    ("model-relation-count", "{{ u.groups.count }}", _one),
    (
        "model-relation-loop",
        "{% for g in u.groups.all %}{{ g.name }},{% endfor %}",
        _one,
    ),
    ("model-column", "{{ u.username }}", _one),
]


def _measure(source: str, factory, policy: str, flag: bool) -> tuple[str, int]:
    with CaptureQueriesContext(connection) as ctx:
        out = _render(source, factory, policy, flag=flag)
    return out, len(ctx.captured_queries)


@pytest.mark.parametrize("policy", ["explicit", "legacy"])
@pytest.mark.parametrize("name,source,factory", [pytest.param(*p, id=p[0]) for p in PROBES])
def test_the_overlay_answers_and_queries_as_flag_off_does(world, policy, name, source, factory):
    on_out, on_queries = _measure(source, factory, policy, flag=True)
    off_out, off_queries = _measure(source, factory, policy, flag=False)
    assert on_out == off_out, (on_out, off_out)
    assert on_queries <= off_queries, (on_queries, off_queries)


#: The probes whose flag-off answer is Django's, pinned so the equality above is not two equal
#: wrong answers (u0: staff, ops; u1: staff; u2: none).
DJANGO_ANSWERS = {
    "qs-count": "3",
    "qs-exists": "y|True",
    "qs-first-last": "u0|u2",
    "qs-nested-all": "staff,ops,;staff,;;",
    "qs-row-count": "2;1;0;",
    "qs-loop-and-method": "staff,ops,2;staff,1;0;",
}


@pytest.mark.parametrize("policy", ["explicit", "legacy"])
@pytest.mark.parametrize("name", sorted(DJANGO_ANSWERS))
def test_the_overlay_keeps_djangos_answer(world, policy, name):
    source, factory = next((s, f) for n, s, f in PROBES if n == name)
    assert _render(source, factory, policy, flag=True) == DJANGO_ANSWERS[name]


@pytest.mark.parametrize("policy", ["explicit", "legacy"])
def test_the_overlay_keeps_the_row_cap(world, policy):
    """#3788: ``queryset_all_row_cap`` bounds a walked ``.all`` on the flag-on render too."""
    from djust.config import config

    source = (
        "{% for u in users %}[{% for g in u.groups.all %}{{ g.name }},{% endfor %}]{% endfor %}"
    )
    previous = config.get("queryset_all_row_cap")
    config.set("queryset_all_row_cap", 1)
    try:
        on = _render(source, _qs, policy, flag=True)
        off = _render(source, _qs, policy, flag=False)
    finally:
        config.set("queryset_all_row_cap", previous)
    assert on == off == "[staff,][staff,][]"


def test_the_overlay_still_serves_a_filtered_rebinding(world):
    """Non-vacuity: the keys the overlay keeps still get their handle (the declared change)."""
    source = "{% with q=u|default:u %}{{ q.groups.count }}{% endwith %}"
    assert _render(source, _one, "explicit", flag=True) == "2"
    assert _render(source, _one, "explicit", flag=False) == ""


def test_gate_off_restoring_the_shadow_turns_the_method_probe_red(world, monkeypatch):
    """Gate-off: with the skip removed (every state model bound), ``{{ users.count }}`` renders
    empty again -- the B-H1 shadow this file pins."""
    from djust.mixins import rust_bridge

    monkeypatch.setattr(rust_bridge, "_overlay_skip_keys", lambda *a, **k: frozenset())
    assert _render("{{ users.count }}", _qs, "explicit", flag=True) == ""
    assert _render("{{ users.count }}", _qs, "explicit", flag=False) == "3"


def test_the_world_is_what_the_answers_assume(world):
    assert list(User.objects.order_by("pk").values_list("username", flat=True)) == [
        "u0",
        "u1",
        "u2",
    ]
    assert Group.objects.count() == 2
