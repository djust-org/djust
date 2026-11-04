"""#3772 — a template that NAMES a mutating callable must not run it.

``{{ users.delete }}`` over a QuerySet in the context deleted every row under
the Rust engine. Django refuses: ``Model.delete`` / ``QuerySet.delete`` carry
``alters_data = True`` and ``Variable._resolve_lookup`` never calls such a
callable.

**Root cause (measured, not the cited path).** The sidecar walk
(``Context::maybe_call``) already refused, and ``_rust.render_template`` —
which has no JIT step — is inert on every shape below. The destructive call is
made BEFORE the template engine runs: ``serialize_object_with_paths`` (the Rust
queryset serializer behind ``DjustTemplateBackend.render`` and the LiveView
queryset JIT) walks the paths the template names and ``call0()``-ed every
callable leaf with no ``alters_data`` / ``do_not_call_in_templates`` check. The
Python codegen path had the guard; the Rust twin did not (#1646).

Reproduction fidelity (#1650 / #1638): a REAL table, the REAL ``QuerySet`` /
``Model`` / related-manager callables (not a stub that records a call), and the
assertion is what is in the database afterwards, so a callable that mutates
without being the one a test author thought of is still caught.
"""

from __future__ import annotations

import re
from typing import Any, Callable

import pytest
from django.contrib.auth.models import Group, User
from django.db.models.signals import post_delete, post_save
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate

from djust import _rust
from djust.live_view import LiveView
from djust.testing import LiveViewTestClient


# ---------------------------------------------------------------------------
# The entries a template takes
# ---------------------------------------------------------------------------
def plain_render(source: str, context: dict) -> str:
    from djust.template import DjustTemplateBackend

    backend = DjustTemplateBackend(
        params={"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
    )
    return str(backend.from_string(source).render(context=dict(context), request=None))


def raw_render(source: str, context: dict) -> str:
    return _rust.render_template(source, dict(context))


def liveview_render(source: str, context: dict) -> str:
    """A LiveView whose values are VIEW ATTRIBUTES, the way an app writes them.

    Attributes — not keys added after ``super().get_context_data()`` — are what
    the LiveView JIT queryset serializer (``mixins/context.py``) walks, so this
    is the shape that reaches ``serialize_queryset`` on the LiveView path.
    """

    class _V(LiveView):
        def mount(self, request, **kwargs):
            for name, value in context.items():
                setattr(self, name, value)

    _V.template = f"<div dj-root>{source}</div>"
    client = LiveViewTestClient(_V)
    client.mount()
    html = client.render()
    match = re.search(r"<div dj-root[^>]*>(.*)</div>", html, re.S)
    assert match is not None, html
    return match.group(1)


def django_render(source: str, context: dict) -> str:
    return DjangoTemplate(source).render(DjangoContext(dict(context)))


ENGINES = [
    pytest.param(plain_render, id="DjustTemplateBackend"),
    pytest.param(liveview_render, id="LiveView"),
    pytest.param(raw_render, id="render_template"),
]


# ---------------------------------------------------------------------------
# The detector: everything a destructive callable could change
# ---------------------------------------------------------------------------
class Probe:
    """Row counts for both tables and the M2M, plus every save/delete signal.

    Counts alone miss a ``Model.save()`` on an unchanged row (no row changes);
    the signals catch it. Connected AFTER the fixtures, so the setup's own
    saves are not counted.
    """

    def __init__(self) -> None:
        self.saves = 0
        self.deletes = 0
        post_save.connect(self._saved, weak=False)
        post_delete.connect(self._deleted, weak=False)

    def _saved(self, **kwargs: Any) -> None:
        self.saves += 1

    def _deleted(self, **kwargs: Any) -> None:
        self.deletes += 1

    def close(self) -> None:
        post_save.disconnect(self._saved)
        post_delete.disconnect(self._deleted)

    def snapshot(self) -> tuple:
        return (
            User.objects.count(),
            Group.objects.count(),
            User.groups.through.objects.count(),
            self.saves,
            self.deletes,
        )


@pytest.fixture
def world():
    """Three users, all in one group — so every relation has something to lose."""
    group = Group.objects.create(name="staff")
    for i in range(3):
        User.objects.create(username=f"u{i}").groups.add(group)
    probe = Probe()
    try:
        yield probe
    finally:
        probe.close()


# ---------------------------------------------------------------------------
# Every shape: (id, template, context factory). The factory runs INSIDE the
# test so each engine gets a fresh, unevaluated QuerySet.
# ---------------------------------------------------------------------------
Case = tuple[str, str, Callable[[], dict]]

CASES: list[Case] = [
    # -- a top-level QuerySet (the issue's shape) -------------------------
    ("qs.delete", "{{ users.delete }}", lambda: {"users": User.objects.all()}),
    (
        "qs.delete-in-if",
        "{% if users.delete %}x{% endif %}",
        lambda: {"users": User.objects.all()},
    ),
    (
        "qs.delete-filtered",
        "{{ users.delete|length }}",
        lambda: {"users": User.objects.all()},
    ),
    (
        "qs.delete-in-with",
        "{% with d=users.delete %}{{ d }}{% endwith %}",
        lambda: {"users": User.objects.all()},
    ),
    ("qs.0.delete", "{{ users.0.delete }}", lambda: {"users": User.objects.all()}),
    (
        "for-u.delete",
        "{% for u in users %}{{ u.delete }}{% endfor %}",
        lambda: {"users": User.objects.all()},
    ),
    (
        "for-u.save",
        "{% for u in users %}{{ u.save }}{% endfor %}",
        lambda: {"users": User.objects.all()},
    ),
    (
        "for-u.groups.clear",
        "{% for u in users %}{{ u.groups.clear }}{% endfor %}",
        lambda: {"users": User.objects.all()},
    ),
    (
        "for-u.groups.create",
        "{% for u in users %}{{ u.groups.create }}{% endfor %}",
        lambda: {"users": User.objects.all()},
    ),
    (
        "for-u.groups.get_or_create",
        "{% for u in users %}{{ u.groups.get_or_create }}{% endfor %}",
        lambda: {"users": User.objects.all()},
    ),
    (
        "for-u.groups.update_or_create",
        "{% for u in users %}{{ u.groups.update_or_create }}{% endfor %}",
        lambda: {"users": User.objects.all()},
    ),
    (
        "for-u-for-g.delete",
        "{% for u in users %}{% for g in u.groups.all %}{{ g.delete }}{% endfor %}{% endfor %}",
        lambda: {"users": User.objects.all()},
    ),
    (
        "for-g.user_set.clear",
        "{% for g in groups %}{{ g.user_set.clear }}{% endfor %}",
        lambda: {"groups": Group.objects.all()},
    ),
    (
        "for-g.user_set.create",
        "{% for g in groups %}{{ g.user_set.create }}{% endfor %}",
        lambda: {"groups": Group.objects.all()},
    ),
    # -- the queryset under a container / behind a callable ---------------
    (
        "nested-dict.users.delete",
        "{{ data.users.delete }}",
        lambda: {"data": {"users": User.objects.all()}},
    ),
    # -- a single instance ------------------------------------------------
    ("user.delete", "{{ user.delete }}", lambda: {"user": User.objects.first()}),
    ("user.save", "{{ user.save }}", lambda: {"user": User.objects.first()}),
    (
        "user.groups.clear",
        "{{ user.groups.clear }}",
        lambda: {"user": User.objects.first()},
    ),
    (
        "user.groups.create",
        "{{ user.groups.create }}",
        lambda: {"user": User.objects.first()},
    ),
    # -- a list[Model] ----------------------------------------------------
    ("list.delete", "{{ users.delete }}", lambda: {"users": list(User.objects.all())}),
    (
        "list-for-u.delete",
        "{% for u in users %}{{ u.delete }}{% endfor %}",
        lambda: {"users": list(User.objects.all())},
    ),
    # -- a top-level Manager ---------------------------------------------
    ("mgr.create", "{{ mgr.create }}", lambda: {"mgr": User.objects}),
    ("mgr.get_or_create", "{{ mgr.get_or_create }}", lambda: {"mgr": User.objects}),
    ("mgr.all.delete", "{{ mgr.all.delete }}", lambda: {"mgr": User.objects}),
    ("mgr.filter.delete", "{{ mgr.filter.delete }}", lambda: {"mgr": User.objects}),
    ("mgr.all.update", "{{ mgr.all.update }}", lambda: {"mgr": User.objects}),
]
CASE_IDS = [c[0] for c in CASES]


@pytest.mark.django_db
class TestANamedMutatorIsNeverCalled3772:
    @pytest.mark.parametrize("render", ENGINES)
    @pytest.mark.parametrize(("name", "source", "make_ctx"), CASES, ids=CASE_IDS)
    def test_the_database_is_untouched(self, render, name, source, make_ctx, world) -> None:
        before = world.snapshot()
        render(source, make_ctx())
        assert world.snapshot() == before, (
            f"{name!r} through {render.__name__} changed the database: "
            f"(users, groups, memberships, saves, deletes) {before} -> {world.snapshot()}"
        )

    @pytest.mark.parametrize(("name", "source", "make_ctx"), CASES, ids=CASE_IDS)
    def test_django_is_the_control(self, name, source, make_ctx, world) -> None:
        """The premise: Django's own engine leaves every one of these alone, so
        an unchanged database is the contract and not an artefact of the
        harness."""
        before = world.snapshot()
        django_render(source, make_ctx())
        assert world.snapshot() == before


@pytest.mark.django_db
class TestTheDetectorIsLive3772:
    """Non-vacuity: a detector that cannot see a mutation passes every test
    above for the wrong reason. Each mutation is performed DIRECTLY, and the
    snapshot must move."""

    def test_a_queryset_delete_moves_the_snapshot(self, world) -> None:
        before = world.snapshot()
        User.objects.all().delete()
        assert world.snapshot() != before

    def test_a_model_save_moves_the_snapshot(self, world) -> None:
        before = world.snapshot()
        User.objects.first().save()
        assert world.snapshot() != before

    def test_a_related_manager_clear_moves_the_snapshot(self, world) -> None:
        before = world.snapshot()
        User.objects.first().groups.clear()
        assert world.snapshot() != before

    def test_a_related_manager_create_moves_the_snapshot(self, world) -> None:
        before = world.snapshot()
        User.objects.first().groups.create()
        assert world.snapshot() != before


def _Pets(marked: bool) -> Any:
    """A to-many attribute with its own ``all`` — the call the serializer makes
    itself for a ``{{ u.pets.0.name }}`` path, which no template NAMED.

    ``marked`` stamps ``alters_data`` on it. The destructive body is real
    (it deletes every user), so the assertion is the table, not a flag. A fresh
    class per call, not a mutated shared one (#1109).
    """

    def all(self):  # noqa: A001 — the name is the point
        User.objects.all().delete()
        return []

    if marked:
        all.alters_data = True  # type: ignore[attr-defined]
    return type("Pets", (), {"all": all})()


def _destroyer(marker: str | None) -> Callable:
    """A zero-argument callable that really deletes every user, carrying
    ``marker`` (``alters_data`` / ``do_not_call_in_templates``) or nothing."""

    def destroy():
        User.objects.all().delete()
        return "DESTROYED"

    if marker:
        setattr(destroy, marker, True)
    return destroy


@pytest.mark.django_db
class TestEachMarkerIsHonouredByTheSerializer3772:
    """The two markers Django reads, each on a callable ATTRIBUTE of a row.

    ``alters_data`` is what ``Model.delete`` carries; ``do_not_call_in_templates``
    is what a Model class or an ``enums.Choices`` carries (Django uses the
    object as-is). Each has an unmarked twin that IS called, so a refusal of
    all callables could not pass.
    """

    @pytest.mark.parametrize("render", ENGINES[:2])
    @pytest.mark.parametrize("marker", ["alters_data", "do_not_call_in_templates"])
    def test_a_marked_callable_is_not_called(self, render, marker, world, monkeypatch) -> None:
        fn = _destroyer(marker)
        monkeypatch.setattr(User, "widget", property(lambda self: fn), raising=False)
        before = world.snapshot()
        render("{% for u in users %}{{ u.widget }}{% endfor %}", {"users": User.objects.all()})
        assert world.snapshot() == before

    @pytest.mark.parametrize("render", ENGINES[:2])
    def test_the_unmarked_twin_is_called(self, render, world, monkeypatch) -> None:
        fn = _destroyer(None)
        monkeypatch.setattr(User, "widget", property(lambda self: fn), raising=False)
        before = world.snapshot()
        render("{% for u in users %}{{ u.widget }}{% endfor %}", {"users": User.objects.all()})
        assert world.snapshot() != before, "the harness never reached the callable"


@pytest.mark.django_db
class TestTheSerializersOwnAllCallIsGuarded3772:
    """``serialize_object_with_paths``'s List arm calls ``attr.all()`` on a
    numeric-index path. The guard that stops ``{{ u.delete }}`` must stop this
    one too: it is the same ``call0()`` family, reached without a name.

    Driven through ``_rust.serialize_queryset`` directly, and that is a measured
    limit rather than a shortcut: ``extract_template_variables`` never emits a
    numeric segment (``{{ u.pets.0.name }}`` yields ``pets.name``), so no
    template reaches this arm today. The pyfunction is public and documents
    ``active_leases.0.property.name`` as its own input, so the call is guarded
    on the function's contract, not on what the extractor happens to emit."""

    def test_an_alters_data_all_is_not_called(self, world, monkeypatch) -> None:
        pets = _Pets(True)
        monkeypatch.setattr(User, "pets", property(lambda self: pets), raising=False)
        before = world.snapshot()
        _rust.serialize_queryset(list(User.objects.all()), ["pets.0.name"])
        assert world.snapshot() == before

    def test_the_unmarked_twin_IS_called(self, world, monkeypatch) -> None:
        """Non-vacuity: the same call with the marker absent reaches ``all()``,
        so the test above asserts the guard and not that the arm is
        unreachable."""
        pets = _Pets(False)
        monkeypatch.setattr(User, "pets", property(lambda self: pets), raising=False)
        before = world.snapshot()
        _rust.serialize_queryset(list(User.objects.all()), ["pets.0.name"])
        assert world.snapshot() != before


@pytest.mark.django_db
class TestOrdinaryRowsStillRender3772:
    """The refusal must not take the legitimate path with it: a field, a
    ``get_*`` method and a related manager's rows still render."""

    @pytest.mark.parametrize("render", ENGINES)
    def test_fields_and_methods_still_resolve(self, render, world) -> None:
        out = render(
            "{% for u in users %}[{{ u.username }}|{{ u.get_full_name }}]{% endfor %}",
            {"users": User.objects.order_by("username")},
        )
        assert out == "[u0|][u1|][u2|]"

    @pytest.mark.parametrize("render", ENGINES)
    def test_a_related_manager_non_mutating_call_still_resolves(self, render, world) -> None:
        """``count`` is a nullary callable on the same related manager whose
        ``clear`` / ``create`` are refused. The guard is the marker, not the
        manager: an unmarked callable is still called."""
        out = render(
            "{% for u in users %}{{ u.groups.count }};{% endfor %}",
            {"users": User.objects.order_by("username")},
        )
        assert out == "1;1;1;"

    @pytest.mark.parametrize("render", ENGINES)
    def test_an_unmarked_custom_method_is_still_called(self, render, world) -> None:
        """Django calls a zero-arg method with no marker; so must the
        serializer — otherwise the fix is a blanket refusal of callables and
        every ``{{ obj.display_name }}`` method regresses."""
        out = render(
            "{% for u in users %}{{ u.get_username }};{% endfor %}",
            {"users": User.objects.order_by("username")},
        )
        assert out == "u0;u1;u2;"
