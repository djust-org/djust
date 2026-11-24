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


def _values() -> dict:
    return {"rows": User.objects.order_by("pk").values("username")}


def _values_flat() -> dict:
    return {"rows": User.objects.order_by("pk").values_list("username", flat=True)}


def _values_pairs() -> dict:
    return {"rows": User.objects.order_by("pk").values_list("pk", "username")}


#: ``.values()`` / ``.values_list()`` held in state (review B1): no model rows, so no handle.
PROJECTIONS = [
    ("values-loop", "{% for r in rows %}{{ r.username }};{% endfor %}", _values),
    ("values-length", "{{ rows|length }}", _values),
    ("values-index", "{{ rows.0.username }}", _values),
    ("values-flat-loop", "{% for r in rows %}{{ r }};{% endfor %}", _values_flat),
    ("values-flat-length", "{{ rows|length }}", _values_flat),
    ("values-flat-index", "{{ rows.0 }}", _values_flat),
    ("values-pairs-loop", "{% for a, b in rows %}{{ b }};{% endfor %}", _values_pairs),
    ("values-pairs-length", "{{ rows|length }}", _values_pairs),
]


def _perms_sliced() -> dict:
    from django.contrib.auth.models import Permission

    return {"perms": Permission.objects.order_by("pk")[:5]}


def _perms() -> dict:
    from django.contrib.auth.models import Permission

    return {"perms": Permission.objects.order_by("pk")}


def _perm() -> dict:
    from django.contrib.auth.models import Permission

    return {"p": Permission.objects.order_by("pk").first()}


#: A forward FK (``Permission.content_type``) read as one segment and dotted (review B2): a legacy
#: JIT re-queries the QuerySet with ``select_related``; a handle walked the state rows lazily.
FORWARD_FK = [
    ("fk-single", "{% for p in perms %}{{ p.content_type }};{% endfor %}", _perms_sliced),
    ("fk-if", "{% for p in perms %}{% if p.content_type %}y{% endif %}{% endfor %}", _perms_sliced),
    ("fk-single-unsliced", "{% for p in perms %}{{ p.content_type }};{% endfor %}", _perms),
    ("fk-dotted", "{% for p in perms %}{{ p.content_type.model }};{% endfor %}", _perms_sliced),
    ("fk-model", "{{ p.content_type }}|{{ p.content_type.app_label }}", _perm),
]

PROBES = (
    PROJECTIONS
    + FORWARD_FK
    + [
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
        # review S4: the QuerySet spelled through ``.all`` and through a ``{% with %}`` alias
        ("qs-top-all", "{% for u in users.all %}{{ u.username }};{% endfor %}", _qs),
        ("qs-top-all-count", "{{ users.all.count }}", _qs),
        ("qs-with-count", "{% with x=users %}{{ x.count }}{% endwith %}", _qs),
        (
            "qs-with-loop-walk",
            "{% with x=users %}{% for u in x %}{{ u.groups.count }};{% endfor %}{% endwith %}",
            _qs,
        ),
    ]
)


def _measure(source: str, factory, policy: str, flag: bool, twice: bool = False) -> tuple[str, int]:
    """The rendered body and the number of queries; ``twice`` renders again on the same view (a
    second sync, as after an event)."""
    from django.test import override_settings

    from djust.testing import LiveViewTestClient
    from python.tests.test_state_models_as_handles_3936 import FLAG_OFF, FLAG_ON, _body, _view

    if not twice:
        with CaptureQueriesContext(connection) as ctx:
            out = _render(source, factory, policy, flag=flag)
        return out, len(ctx.captured_queries)
    with override_settings(LIVEVIEW_CONFIG=FLAG_ON if flag else FLAG_OFF):
        with CaptureQueriesContext(connection) as ctx:
            client = LiveViewTestClient(_view(source, factory, policy))
            client.mount()
            client.render()
            out = _body(client.render())
    return out, len(ctx.captured_queries)


@pytest.mark.parametrize("twice", [False, True], ids=["once", "twice"])
@pytest.mark.parametrize("policy", ["explicit", "legacy"])
@pytest.mark.parametrize("name,source,factory", [pytest.param(*p, id=p[0]) for p in PROBES])
def test_the_overlay_answers_and_queries_as_flag_off_does(
    world, policy, name, source, factory, twice
):
    on_out, on_queries = _measure(source, factory, policy, flag=True, twice=twice)
    off_out, off_queries = _measure(source, factory, policy, flag=False, twice=twice)
    assert on_out == off_out, (on_out, off_out)
    assert on_queries <= off_queries, (on_queries, off_queries)


def _annotated() -> dict:
    from django.db.models import Count

    return {"users": User.objects.annotate(n=Count("groups")).order_by("pk")}


@pytest.mark.parametrize("twice", [False, True], ids=["once", "twice"])
@pytest.mark.parametrize("policy", ["explicit", "legacy"])
def test_an_annotated_queryset_reads_its_annotation(world, policy, twice):
    """Review S4: an annotation is read as Django reads it (explicit's flag-off dict lacks it and
    printed ``;;;``; the overlay improves that), with no added query."""
    source = "{% for u in users %}{{ u.n }};{% endfor %}"
    on_out, on_queries = _measure(source, _annotated, policy, flag=True, twice=twice)
    _, off_queries = _measure(source, _annotated, policy, flag=False, twice=twice)
    assert on_out == "2;1;0;"
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


def test_gate_off_without_the_relation_check_a_legacy_fk_loop_is_n_plus_one(world, monkeypatch):
    """Gate-off (review B2): without ``_paths_read_a_relation`` the legacy handles walk each row's
    forward FK lazily."""
    from djust.mixins import rust_bridge

    source = "{% for p in perms %}{{ p.content_type }};{% endfor %}"
    monkeypatch.setattr(rust_bridge, "_paths_read_a_relation", lambda *a, **k: False)
    _, on_queries = _measure(source, _perms_sliced, "legacy", flag=True)
    _, off_queries = _measure(source, _perms_sliced, "legacy", flag=False)
    assert on_queries > off_queries, (on_queries, off_queries)


def test_gate_off_restoring_the_shadow_turns_the_method_probe_red(world, monkeypatch):
    """Gate-off: with the skip removed (every state model bound), ``{{ users.count }}`` renders
    empty again -- the B-H1 shadow this file pins."""
    from djust.mixins import rust_bridge

    monkeypatch.setattr(rust_bridge, "_overlay_skip_keys", lambda *a, **k: frozenset())
    assert _render("{{ users.count }}", _qs, "explicit", flag=True) == ""
    assert _render("{{ users.count }}", _qs, "explicit", flag=False) == "3"


@pytest.mark.parametrize("policy", ["explicit", "legacy"])
def test_a_projection_is_never_an_empty_handle(world, policy):
    """Review B1: a projection's rows render (they rendered ``''`` / ``0`` with the overlay on)."""
    assert _render("{{ rows|length }}", _values_flat, policy, flag=True) == "3"
    assert _render("{% for r in rows %}{{ r }};{% endfor %}", _values_flat, policy) == "u0;u1;u2;"


def test_gate_off_capturing_a_projection_renders_it_empty(world, monkeypatch):
    """Gate-off: with the projection check removed, the overlay binds the floor's empty answer."""
    from djust.mixins import context

    monkeypatch.setattr(context, "_is_projection", lambda qs: False)
    assert _render("{{ rows|length }}", _values_flat, "explicit", flag=True) == "0"


def test_the_world_is_what_the_answers_assume(world):
    assert list(User.objects.order_by("pk").values_list("username", flat=True)) == [
        "u0",
        "u1",
        "u2",
    ]
    assert Group.objects.count() == 2


# -- review S1: a method-read QuerySet whose rows are walked keeps its filtered rebindings ------
#
# The overlay skips such a key (the dict path answers ``count`` and the walk). On an explicit view
# its rows were the plain column dicts, so a filtered rebinding of a row read the relation empty --
# #3936's own symptom, switched back on by an unrelated ``{{ users.count }}``. Its rows now go
# through the JIT as legacy's do, so each row dict carries the relation values.

S1_CASES = [
    (
        "count+with-first-rebind",
        "{{ users.count }}|{% with q=users|first %}{% with r=q|default:q %}"
        "{{ r.groups.count }}{% endwith %}{% endwith %}",
        "3|2",
    ),
    (
        "count+loop-rebind",
        "{{ users.count }}|{% for x in users %}{% with r=x|default:x %}"
        "{{ r.groups.count }}{% endwith %},{% endfor %}",
        "3|2,1,0,",
    ),
]


@pytest.mark.parametrize("flag", [True, False], ids=["handles", "flag-off"])
@pytest.mark.parametrize("policy", ["explicit", "legacy"])
@pytest.mark.parametrize("name,source,want", S1_CASES, ids=[c[0] for c in S1_CASES])
def test_a_method_read_key_keeps_its_filtered_rebindings(world, policy, flag, name, source, want):
    from django.template import Context as DjangoContext
    from django.template import Template as DjangoTemplate

    assert DjangoTemplate(source).render(DjangoContext(_qs())) == want
    out, queries = _measure(source, _qs, policy, flag=flag)
    assert out == want
    # the outer SELECT once, the relation once (prefetched), no per-row query
    assert queries <= 3, queries


def test_gate_off_without_the_jit_an_explicit_rebinding_reads_empty(world, monkeypatch):
    from djust.mixins import rust_bridge

    monkeypatch.setattr(rust_bridge, "_method_read_rows_take_the_jit", lambda ctx: False)
    _, source, _ = S1_CASES[1]
    assert _render(source, _qs, "explicit") == "3|,,,"


# -- review N4: an explicit QuerySet loop reading a forward FK as one segment -------------------
#
# Pre-existing, with the overlay on or off: ``{% for p in perms %}{{ p.content_type }}`` on an
# explicit view walked each row's FK through the sidecar alias (2N+1 queries). Its rows now go
# through the JIT, which selects the relation with them, as legacy's do.

FK_ONE_SEGMENT = [
    "{% for p in perms %}{{ p.content_type }};{% endfor %}",
    "{% for p in perms %}{% if p.content_type %}y{% endif %}{% endfor %}",
]


@pytest.mark.parametrize("flag", [True, False], ids=["handles", "flag-off"])
@pytest.mark.parametrize("source", FK_ONE_SEGMENT)
def test_an_explicit_fk_loop_is_one_query(world, source, flag):
    from django.template import Context as DjangoContext
    from django.template import Template as DjangoTemplate

    want = DjangoTemplate(source).render(DjangoContext(_perms()))
    out, queries = _measure(source, _perms, "explicit", flag=flag)
    assert out == want
    assert queries == 1, queries


def test_gate_off_an_explicit_fk_loop_walks_each_row(world, monkeypatch):
    from djust.mixins import rust_bridge

    monkeypatch.setattr(rust_bridge, "_explicit_fk_rows_take_the_jit", lambda *a: False)
    # #4290 would hand the rows to the JIT as plain-column rows; its gate off too.
    monkeypatch.setattr(rust_bridge, "_plain_rows_take_the_jit", lambda *a: False)
    _, queries = _measure(FK_ONE_SEGMENT[0], _perms_sliced, "explicit", flag=False)
    assert queries > 5, queries
