"""The raw-Python sidecar is attached on every render path (#2501, PR 1).

``python/tests/test_object_attribute_resolution_2501.py`` is the reproducer —
the reporter's matrix, measured against live Django. This file is the fix's own
coverage, and it exists because that matrix samples ONE axis: a top-level name,
on a value that happens to be a `Component` or a plain object. The surface has
FOUR axes and a suite that walks one of them is false confidence, not coverage
(v1.0.0rc4 finding #1):

* **path** — `render_template`, `render_template_with_dirs`,
  `DjustTemplateBackend`, and the `RustLiveView` path that had the sidecar all
  along (the fourth column, which is what makes "these paths now agree" a
  measurement rather than a claim);
* **carrier** — a value that crosses as `Value::Encoded` (a `Component`; its
  instance dict is entirely `_`-prefixed) and one that crosses as
  `Value::Object` (a plain object with a public instance attribute). Both are
  defective on the same axis and a fix aimed at one leaves the other, which is
  what `TestCarrierPremises` in the reproducer pins;
* **depth** — top level, inside a list (the walk's integer-index arm) and
  inside a dict (its item-access arm);
* **binding construct** — a top-level name, a `{% for %}` loop variable, a
  `{% with %}` name. This axis was MISSING from the first version of this
  file, and its absence was the finding as much as the behaviour was: the rare
  spelling (`{{ rows.0.cls_attr }}`) resolved while the common one — iterating
  a list of objects — silently did not, under docstrings claiming nested
  coverage. See `TestBindingConstructsReachTheSidecar`.

Everything below is measured against LIVE Django, never transcribed.

What the fix is
---------------
`Context::resolve`'s sidecar walk (`crates/djust_core/src/context.rs:884`) is
the ONE implementation of `Variable._resolve_lookup` in this codebase — item
access, `getattr`, the auto-call with both of Django's guards, then the integer
index — and it carries the serialization floor (`protect_sidecar`, applied
after the root bind and after every segment). Only the LiveView path attached
it. PR 1 attaches the same walk at the other three entry points, building the
sidecar in Rust from the context dict they already receive
(`entry_sidecar` → `djust.serialization.build_render_sidecar`), so a caller
reaching `_rust.render_template` directly gets it without passing anything.

A name BOUND by `{% for %}` / `{% with %}` reaches the same walk through
`Context::aliases` (#2375), which already states the correspondence the sidecar
needs — `r` IS `rows.<i>` — and whose registration guards are XSS boundaries.
Two operand shapes those guards refuse (a filtered operand, a dict view) stay
out of reach and are tracked at #2504.

Each segment of the walk also catches EXACTLY Django's exception set (#2506);
it previously discarded any exception and rendered the empty string, so an
attribute implementing an access check by raising failed silently and open.

Neither carrier gains an attribute path, and the pinned `Encoded` wire tuple is
untouched.

Refs #2501, #2506, #2505, #2504, #2502, #2489, #2485, #2481, #2478, #2418,
#2375, #1986, #1646, #1468, #1104, #1079, ADR-024.
"""

from __future__ import annotations

import pytest

pytest.importorskip("django")

from django.contrib.auth.models import User  # noqa: E402
from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402
from djust.components.base import Component  # noqa: E402


# ---------------------------------------------------------------------------
# The four paths
# ---------------------------------------------------------------------------
def django_render(source: str, context: dict) -> str:
    return DjangoTemplate(source).render(DjangoContext(dict(context)))


def render_template(source: str, context: dict) -> str:
    return _rust.render_template(source, dict(context))


def render_template_with_dirs(source: str, context: dict) -> str:
    return _rust.render_template_with_dirs(source, dict(context), [])


def backend_render(source: str, context: dict) -> str:
    from djust.template_backend import DjustTemplateBackend

    backend = DjustTemplateBackend(
        params={"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
    )
    return backend.from_string(source).render(context=dict(context), request=None)


def liveview_render(source: str, context: dict) -> str:
    """The path that has had the sidecar since ADR-024 — the reference column.

    Not a fourth implementation to keep in step: the whole argument for #2501's
    shape is that there is ONE walk and three paths were not attaching it, so
    the assertion worth making is that the three now answer what this one
    already answered (#1646).
    """
    view = _rust.RustLiveView(source, [])
    view.set_raw_py_values(dict(context))
    return view.render()


#: The three paths #2501 fixes. Asserting all three is what keeps "which paths
#: are affected" a measurement — they are three separate bindings, and until
#: this PR `render_template` had no safety channel and no sidecar at all.
FIXED_PATHS = [
    pytest.param(render_template, id="render_template"),
    pytest.param(render_template_with_dirs, id="render_template_with_dirs"),
    pytest.param(backend_render, id="DjustTemplateBackend"),
]

ALL_PATHS = [*FIXED_PATHS, pytest.param(liveview_render, id="RustLiveView")]


# ---------------------------------------------------------------------------
# The two carriers
# ---------------------------------------------------------------------------
class Card(Component):
    """Crosses as `Value::Encoded`: its instance dict is entirely `_`-prefixed,
    so `has_public_dict_attrs` is False and `opaque_gate` does not decline it."""

    template = None

    cls_attr = "card-class-level"

    def _render_custom(self) -> str:
        return "<b>card</b>"

    def label(self) -> str:
        return "card-method"

    @property
    def kind(self) -> str:
        return "card-property"


class Presenter:
    """Crosses as `Value::Object`: a public instance attribute puts it on the
    `__dict__` bulk-dump arm, which answers `inst_attr` and nothing else."""

    cls_attr = "presenter-class-level"

    def __init__(self, user: object = None) -> None:
        self.inst_attr = "presenter-in-dict"
        self.user = user

    def label(self) -> str:
        return "presenter-method"

    @property
    def kind(self) -> str:
        return "presenter-property"


class RaisingAttributes:
    """Properties that RAISE, one per exception class Django treats
    differently at its three lookup steps (#2506)."""

    @property
    def runtime(self) -> str:
        raise RuntimeError("authz check failed")

    @property
    def attrerr(self) -> str:
        raise AttributeError("inner attribute error")

    @property
    def keyerr(self) -> str:
        raise KeyError("inner key error")

    @property
    def typeerr(self) -> str:
        raise TypeError("inner type error")

    @property
    def valueerr(self) -> str:
        raise ValueError("inner value error")

    @property
    def ok(self) -> str:
        return "fine"


class RaisingGetItem:
    """A `__getitem__` outside Django's step-1 catch set. Every template
    lookup tries item access FIRST, so this raises on the very first segment
    for ANY name."""

    def __getitem__(self, key: object) -> object:
        raise RuntimeError("getitem authz failed")

    ok = "fine"


class NumericSegmentRaisingGetattr:
    """The one shape that reaches Django's step-2 catch INDEPENDENTLY of the
    `dir()` re-raise branch, and the reason both mechanisms are kept.

    Its `__getattr__` raises out-of-set for a name that is NOT in `dir()`, and
    the segment PARSES AS AN INTEGER — so without the step-2 narrowing the walk
    falls through to the integer-index step, whose `KeyError` IS in the
    step-3 set, and the render comes out empty where Django propagates. The
    public instance attribute is load-bearing too: without it the object
    crosses as a `Value::String` and `{{ o.0 }}` is answered by
    `Context::string_index` before the sidecar is ever consulted.
    """

    def __init__(self) -> None:
        self.pub = "public"

    def __getattr__(self, name: str) -> object:
        # Scoped to the numeric segment under test. A blanket raise also fires
        # for the names `serialize_context` probes on the backend path
        # (`url`, and friends), which raises before the render starts and
        # measures the serializer instead of the walk.
        if name.isdigit():
            raise RuntimeError(f"getattr authz failed: {name}")
        raise AttributeError(name)

    def __getitem__(self, key: object) -> object:
        raise KeyError(key)


class KeyErrorGetItem:
    """A `__getitem__` INSIDE the step-1 set, so the walk falls through to
    `getattr` as Django does — the control for `RaisingGetItem`."""

    def __getitem__(self, key: object) -> object:
        raise KeyError(key)

    ok = "fine"


#: The four cells #2501 calls EMPTY, spelled once per carrier. `render` is the
#: `Component` half of the reporter's matrix; `cls_attr` / `label` / `kind` are
#: Django's step 2 over a class attribute, a nullary method and a property.
CARRIERS = [
    pytest.param(Card, ("render", "cls_attr", "label", "kind"), id="Encoded-Component"),
    pytest.param(Presenter, ("cls_attr", "label", "kind"), id="Object-plain"),
]


class TestBothCarriersOnEveryPath:
    """Axis 1 x axis 2: every path, both carriers, every EMPTY cell."""

    @pytest.mark.parametrize("render", FIXED_PATHS)
    @pytest.mark.parametrize(("factory", "attrs"), CARRIERS)
    def test_every_empty_cell_now_matches_django(self, render, factory, attrs):
        for attr in attrs:
            source = "{{ o.%s|safe }}" % attr
            expected = django_render(source, {"o": factory()})
            assert expected != "", f"premise: Django renders {source}"
            assert render(source, {"o": factory()}) == expected, source

    @pytest.mark.parametrize(("factory", "attrs"), CARRIERS)
    def test_the_premise_that_the_two_carriers_differ(self, factory, attrs):
        """Not decoration: if both fixtures crossed the same way, every test in
        this class would measure one carrier twice and the other not at all —
        which is the exact failure the reproducer's `TestCarrierPremises`
        corrected in #2501's own analysis."""
        crosses_encoded = _rust.crosses_as_encoded(factory())
        assert crosses_encoded is (factory is Card)

    @pytest.mark.parametrize("render", FIXED_PATHS)
    def test_the_three_paths_now_answer_what_the_liveview_path_answered(self, render):
        """The #1646 statement: this was drift, and it is gone."""
        for factory, attrs in [
            (Card, ("render", "cls_attr", "label", "kind")),
            (Presenter, ("cls_attr", "label", "kind")),
        ]:
            for attr in attrs:
                source = "{{ o.%s|safe }}" % attr
                assert render(source, {"o": factory()}) == liveview_render(
                    source, {"o": factory()}
                ), source


class TestDepth:
    """Axis 3. The walk reaches a nested object through its own item-access and
    integer-index arms, so a list element and a dict value need their own
    cases — the container is what enters the sidecar, and whether containers
    enter at all was the one genuine judgement call in this fix."""

    @pytest.mark.parametrize("render", ALL_PATHS)
    @pytest.mark.parametrize(
        ("source", "context"),
        [
            pytest.param("{{ rows.0.cls_attr }}", {"rows": [Presenter()]}, id="list-index"),
            pytest.param("{{ d.x.cls_attr }}", {"d": {"x": Presenter()}}, id="dict-item"),
            pytest.param("{{ rows.0.label }}", {"rows": [Presenter()]}, id="list-index-method"),
            pytest.param("{{ d.x.kind }}", {"d": {"x": Presenter()}}, id="dict-item-property"),
            pytest.param(
                "{{ rows.1.cls_attr }}",
                {"rows": [Presenter(), Card()]},
                id="list-index-second-element",
            ),
        ],
    )
    def test_a_nested_object_resolves(self, render, source, context):
        expected = django_render(source, dict(context))
        assert expected != "", "premise: Django renders it"
        assert render(source, dict(context)) == expected

    @pytest.mark.parametrize("render", FIXED_PATHS)
    def test_an_out_of_range_index_renders_empty_like_django(self, render):
        """Django's `string_if_invalid` default. The boundary cases the walk's
        integer-index arm has to answer (#1199)."""
        ctx = {"rows": [Presenter()]}
        for source in ("{{ rows.1.cls_attr }}", "{{ rows.9.cls_attr }}", "{{ rows.0.nope }}"):
            assert django_render(source, dict(ctx)) == ""
            assert render(source, dict(ctx)) == ""


class TestTheGuardsAreLoadBearing:
    """A template lookup must never become a way to invoke a mutating method.

    Both guards live in `Context::maybe_call` and PR 1 routes onto it rather
    than writing a second copy — that is the whole security argument for
    attaching the existing walk instead of giving a carrier an attribute path.
    Each test below is CROSSED against an unstamped sibling on the same object
    and the same path: without the crossed half, "the stamped method was not
    called" would also pass if NOTHING were ever called, which is the state
    these cells were in before this PR (#1200/#1468).
    """

    class Probe:
        def __init__(self) -> None:
            self.mutated = False
            self.plain_called = False

        def mutate(self) -> str:
            self.mutated = True
            return "MUTATED"

        mutate.alters_data = True  # type: ignore[attr-defined]

        def keep(self) -> str:
            self.kept_called = True
            return "KEPT"

        keep.do_not_call_in_templates = True  # type: ignore[attr-defined]

        def plain(self) -> str:
            self.plain_called = True
            return "PLAIN"

    @pytest.mark.parametrize("render", ALL_PATHS)
    def test_the_alters_data_guard_is_load_bearing(self, render):
        stamped = self.Probe()
        assert render("{{ o.mutate }}", {"o": stamped}) == ""
        assert stamped.mutated is False, "alters_data method was CALLED by a lookup"
        # The crossed half: the same object, the same path, one attribute
        # WITHOUT the stamp. It IS called — so the empty cell above is the
        # guard refusing, not the walk failing to reach the name.
        unstamped = self.Probe()
        assert render("{{ o.plain }}", {"o": unstamped}) == "PLAIN"
        assert unstamped.plain_called is True

    @pytest.mark.parametrize("render", ALL_PATHS)
    def test_the_do_not_call_guard_is_load_bearing(self, render):
        stamped = self.Probe()
        render("{{ o.keep }}", {"o": stamped})
        assert getattr(stamped, "kept_called", False) is False, (
            "do_not_call_in_templates method was CALLED by a lookup"
        )
        unstamped = self.Probe()
        assert render("{{ o.plain }}", {"o": unstamped}) == "PLAIN"
        assert unstamped.plain_called is True

    @pytest.mark.parametrize("render", FIXED_PATHS)
    def test_django_agrees_the_mutating_cell_is_empty(self, render):
        """Parity, not just safety: Django renders `alters_data` empty too."""
        assert django_render("{{ o.mutate }}", {"o": self.Probe()}) == ""
        assert render("{{ o.mutate }}", {"o": self.Probe()}) == ""


class TestTheSerializationFloorStillHolds:
    """SECURE_DEFAULTS Pattern 1. The sidecar is a SECOND channel onto live
    objects, so the floor has to hold on it — the whole of #1986 — and PR 1
    widens which paths have that channel.

    Two sinks, and both are covered by ONE Python function:
    `build_render_sidecar` routes every value through `_protect_sidecar_value`
    at build time (which is what protects the custom-tag bridge, which injects
    these objects straight into a handler's Python context), and
    `Context::protect_sidecar` re-applies it after the root bind and after
    every segment (which is what protects a model reached MID-walk, where no
    build-time pass could have seen it).
    """

    @staticmethod
    def _user() -> User:
        return User(username="alice", password="pbkdf2-secret", is_superuser=True)

    @pytest.mark.parametrize("render", FIXED_PATHS)
    @pytest.mark.parametrize("field", ["password", "is_superuser"])
    def test_a_floor_field_is_refused_through_the_sidecar(self, render, field):
        """Reached through a NON-model intermediary, which is the shape only
        the sidecar can resolve — #1986 vector 6. A direct `{{ user.password }}`
        would prove less: the eager serializer refuses that one too, so it
        cannot tell which mechanism did the work."""
        ctx = {"p": Presenter(user=self._user())}
        assert render("{{ p.user.%s }}" % field, dict(ctx)) == ""

    @pytest.mark.parametrize("render", FIXED_PATHS)
    def test_a_safe_field_still_renders_through_the_same_walk(self, render):
        """The crossed half. Without it, every assertion above would also pass
        if the walk resolved nothing at all through a `Presenter` — which is
        exactly what it did before this PR."""
        ctx = {"p": Presenter(user=self._user())}
        assert render("{{ p.user.username }}", dict(ctx)) == "alice"

    def test_the_build_time_pass_wraps_a_model(self):
        """The custom-tag sink, pinned at the mechanism rather than through a
        render: `djust_templates::registry` injects sidecar objects into the
        Python context a `{% tag %}` handler receives, OVERWRITING the
        serialized entry, and that injection never goes through the walk. So
        the build-time wrap is the only thing standing between a handler and a
        raw model."""
        from djust.serialization import _SidecarModelProxy, build_render_sidecar

        built = build_render_sidecar({"user": self._user(), "p": Presenter()})
        assert isinstance(built["user"], _SidecarModelProxy)
        with pytest.raises(AttributeError):
            built["user"].password
        assert built["user"].username == "alice"

    @pytest.mark.django_db
    def test_a_manager_and_a_queryset_are_still_wrapped_after_the_import_hoist(self):
        """`_protect_sidecar_value` is now on the render hot path (once per
        non-scalar top-level value per render), so its function-local
        `from django.db.models import Manager, QuerySet` was replaced by the
        module-scope `models.Manager` / `models.QuerySet` — measured at 0.40us
        per call against 0.14us hoisted.

        The hoist is only correct because the two spellings name the SAME
        objects; asserted here rather than assumed, alongside the behaviour
        that would break if they ever diverged. Without this the hoist has no
        coverage at all — it is a perf change, and a perf change that silently
        stopped wrapping a `QuerySet` is a floor bypass (#1986).
        """
        from django.db import models as django_models
        from django.db.models import Manager, QuerySet

        from djust.serialization import _protect_sidecar_value, _SidecarQuerySetProxy

        assert django_models.Manager is Manager
        assert django_models.QuerySet is QuerySet

        assert isinstance(_protect_sidecar_value(User.objects), _SidecarQuerySetProxy)
        assert isinstance(_protect_sidecar_value(User.objects.all()), _SidecarQuerySetProxy)
        # And the negative half, so the assertions above cannot pass by
        # wrapping everything.
        assert _protect_sidecar_value(Presenter()).__class__ is Presenter

    @pytest.mark.parametrize("render", FIXED_PATHS)
    @pytest.mark.parametrize("source", ["{{ p._private }}", "{{ p.__class__ }}", "{{ p._meta }}"])
    def test_an_underscore_segment_is_refused_before_the_walk_can_run(self, render, source):
        """`crates/djust_templates/src/parser.rs` refuses `_`-prefixed
        variables and segments at PARSE time (#2418), so the sidecar cannot be
        asked for one however it is built — the refusal is a raise, ahead of
        any context, and does not depend on the floor holding downstream."""
        with pytest.raises(Exception, match="may not begin with underscores"):
            render(source, {"p": Presenter()})


class TestWhatEntersTheSidecar:
    """The membership rule, stated as tests rather than as a comment."""

    def test_none_and_scalars_stay_out(self):
        from djust.serialization import build_render_sidecar

        built = build_render_sidecar(
            {
                "nothing": None,
                "n": 1,
                "f": 1.5,
                "b": True,
                "s": "text",
                "raw": b"bytes",
                "obj": Presenter(),
                "rows": [Presenter()],
                "d": {"x": Presenter()},
            }
        )
        assert sorted(built) == ["d", "obj", "rows"]

    @pytest.mark.parametrize("render", FIXED_PATHS)
    def test_a_scalars_cells_are_unchanged(self, render):
        """The consequence of the rule above, at the render. Keeping scalars
        out is what confines this change to the objects #2501 is about — a
        `str` in the sidecar would make `{{ s.upper }}` start rendering
        `ABC`, which is Django's behaviour but nobody's bug report (#1079)."""
        assert render("{{ s.upper }}", {"s": "abc"}) == ""
        assert render("{{ s }}", {"s": "abc"}) == "abc"


class TestBindingConstructsReachTheSidecar:
    """Axis 4 — the one the first version of this file did not have.

    The three axes above (path x carrier x depth) all spell the object's
    lookup path from a TOP-LEVEL context name: `{{ o.cls_attr }}`,
    `{{ rows.0.cls_attr }}`. The sidecar is KEYED by top-level name, so every
    construct that binds a value to a NEW name — `{% for %}`, `{% with %}`,
    `{% include … with %}` — put a `Value` in a frame and left the raw object
    unreachable under that name. `grep -n "{% for" ` returned nothing in this
    file or the reproducer, which is the finding as much as the behaviour was:
    the rare spelling (`rows.0.`) resolved while the common one — iterating a
    list of objects — silently did not.

    The fix consults `Context::aliases` (#2375) when the key's own head names
    no sidecar entry. That mechanism already states the correspondence — `r`
    IS `rows.<i>`, `q` IS `p` — and its registration guards are XSS boundaries
    (`Context::is_safe` resolves a safety decision through the same alias), so
    reusing it asserts nothing new and is strictly stricter than this use
    needs. What it refuses, this cannot reach: see the two `xfail`s below.
    """

    #: Measured against live Django, all four paths.
    BINDING_SPELLINGS = [
        pytest.param(
            "{% for r in rows %}{{ r.cls_attr }},{% endfor %}",
            {"rows": [Presenter(), Presenter()]},
            id="for-class-attribute",
        ),
        pytest.param(
            "{% for r in rows %}{{ r.label }},{% endfor %}",
            {"rows": [Presenter(), Presenter()]},
            id="for-nullary-method",
        ),
        pytest.param(
            "{% for r in rows %}{{ r.kind }},{% endfor %}",
            {"rows": [Presenter(), Presenter()]},
            id="for-property",
        ),
        pytest.param(
            "{% for r in rows reversed %}{{ r.cls_attr }},{% endfor %}",
            {"rows": [Presenter(), Presenter()]},
            id="for-reversed",
        ),
        pytest.param(
            "{% for r in rows %}{% for q in rows %}{{ q.label }}{% endfor %}{% endfor %}",
            {"rows": [Presenter(), Presenter()]},
            id="for-nested",
        ),
        pytest.param(
            "{% for k, v in pairs %}{{ v.cls_attr }},{% endfor %}",
            {"pairs": [("a", Presenter())]},
            id="for-unpacked",
        ),
        pytest.param(
            "{% for c in cards %}{{ c.cls_attr }},{% endfor %}",
            {"cards": [Card(), Card()]},
            id="for-the-other-carrier",
        ),
        pytest.param(
            "{% with q=o %}{{ q.cls_attr }}{% endwith %}",
            {"o": Presenter()},
            id="with-top-level-name",
        ),
        pytest.param(
            "{% with q=rows.0 %}{{ q.label }}{% endwith %}",
            {"rows": [Presenter()]},
            id="with-list-element",
        ),
        pytest.param(
            "{% with q=d.x %}{{ q.kind }}{% endwith %}",
            {"d": {"x": Presenter()}},
            id="with-dict-value",
        ),
    ]

    @pytest.mark.parametrize("render", ALL_PATHS)
    @pytest.mark.parametrize(("source", "context"), BINDING_SPELLINGS)
    def test_a_bound_name_resolves_like_django(self, render, source, context):
        expected = django_render(source, dict(context))
        assert expected.strip(","), f"premise: Django renders {source} -> {expected!r}"
        assert render(source, dict(context)) == expected, source

    @pytest.mark.parametrize("render", FIXED_PATHS)
    @pytest.mark.parametrize(
        ("source", "context"),
        [
            pytest.param(
                "{% for r in rows|slice:':1' %}{{ r.cls_attr }},{% endfor %}",
                {"rows": [Presenter(), Presenter()]},
                id="filtered-operand",
            ),
            pytest.param(
                "{% for r in dd.values %}{{ r.cls_attr }},{% endfor %}",
                {"dd": {"a": Presenter()}},
                id="dict-view-operand",
            ),
        ],
    )
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "#2504 — the alias mechanism refuses these two operand shapes, and "
            "its refusal is an XSS boundary rather than an oversight: `slice` "
            "shifts indices and a dict's marks are spelled BY KEY while the "
            "loop asserts an INDEX (#2334). Reaching them needs a "
            "correspondence nothing currently asserts."
        ),
    )
    def test_an_alias_less_operand_does_not_reach_the_sidecar_yet(self, render, source, context):
        expected = django_render(source, dict(context))
        assert expected.strip(","), "premise: Django renders it"
        assert render(source, dict(context)) == expected

    @pytest.mark.parametrize("render", FIXED_PATHS)
    def test_the_bound_names_own_value_still_wins(self, render):
        """The miss-only property, on this axis. A loop variable whose value
        ANSWERS the lookup must keep answering it out of the value stack — the
        alias expansion is consulted only after `Context::get` has missed."""
        ctx = {"rows": [{"cls_attr": "from-the-dict"}]}
        assert django_render("{% for r in rows %}{{ r.cls_attr }}{% endfor %}", dict(ctx)) == (
            "from-the-dict"
        )
        assert render("{% for r in rows %}{{ r.cls_attr }}{% endfor %}", dict(ctx)) == (
            "from-the-dict"
        )

    @pytest.mark.parametrize("render", FIXED_PATHS)
    def test_the_alias_expansion_is_index_accurate(self, render):
        """Not "an object of the right shape" — THE element the loop is on.

        Two elements with DISTINCT values, so a fix that resolved every
        iteration against `rows.0` (or against whichever raw object it found
        first) renders the same value twice and fails here. Without this the
        `{% for %}` cases above all pass against an off-by-any-amount walk,
        since every `Presenter()` answers `cls_attr` identically (#2129).
        """
        rows = [Presenter(), Presenter()]
        rows[0].cls_attr = "first"  # type: ignore[attr-defined]
        rows[1].cls_attr = "second"  # type: ignore[attr-defined]
        source = "{% for r in rows %}{{ r.cls_attr }},{% endfor %}"
        # An INSTANCE attribute here, so the value stack answers it and this
        # measures the ORDER rather than the sidecar. The sidecar-only twin is
        # below.
        assert render(source, {"rows": rows}) == django_render(source, {"rows": rows})

    @pytest.mark.parametrize("render", FIXED_PATHS)
    def test_the_alias_expansion_is_index_accurate_through_the_sidecar(self, render):
        """The same measurement on a name only the SIDECAR can answer — a
        per-instance property, invisible to the `__dict__` bulk-dump arm."""

        class Ordinal(Presenter):
            def __init__(self, tag: str) -> None:
                super().__init__()
                self._tag = tag

            @property
            def kind(self) -> str:  # type: ignore[override]
                return f"kind-{self._tag}"

        rows = [Ordinal("first"), Ordinal("second"), Ordinal("third")]
        source = "{% for r in rows %}{{ r.kind }},{% endfor %}"
        expected = django_render(source, {"rows": rows})
        assert expected == "kind-first,kind-second,kind-third,"
        assert render(source, {"rows": rows}) == expected

    @pytest.mark.parametrize("render", FIXED_PATHS)
    def test_a_nested_loops_inner_variable_composes_through_the_outer_alias(self, render):
        """`Context::alias_path` collapses the chain at registration time, so
        `q` inside `{% for r in rows %}{% for q in r.subs %}` expands to
        `rows.<i>.subs.<j>` in one hop. Distinct values per cell so a
        composition that lost the OUTER index still fails."""

        class Group:
            def __init__(self, tag: str) -> None:
                self.subs = [Ordinal_(f"{tag}-0"), Ordinal_(f"{tag}-1")]

        class Ordinal_:
            def __init__(self, tag: str) -> None:
                self.inst = tag

            @property
            def kind(self) -> str:
                return f"k-{self.inst}"

        ctx = {"groups": [Group("a"), Group("b")]}
        source = "{% for g in groups %}{% for s in g.subs %}{{ s.kind }},{% endfor %}{% endfor %}"
        expected = django_render(source, dict(ctx))
        assert expected == "k-a-0,k-a-1,k-b-0,k-b-1,"
        assert render(source, dict(ctx)) == expected


class TestDjangosExceptionSetsAtEverySegment:
    """#2506 — the walk used to discard ANY exception from a lookup and render
    the empty string.

    `Variable._resolve_lookup` catches an explicit set at each of its three
    steps: `(TypeError, AttributeError, KeyError, ValueError, IndexError)` on
    item access, `(TypeError, AttributeError)` on `getattr` — and even then
    re-raises when `bit in dir(current)`, its "raised by a @property" branch —
    and `(IndexError, ValueError, KeyError, TypeError)` on the integer index.
    Anything outside propagates.

    The security reading, and why it is fixed here rather than filed: an
    attribute or `__getitem__` implementing an access check that signals
    refusal by RAISING had that refusal swallowed, rendering blank and
    indistinguishable from "no such attribute" — a silent failure, and for a
    raise-is-deny check a silent failure OPEN. `maybe_call` already propagates
    a real exception raised inside a nullary method; this makes the getattr
    half agree.
    """

    @pytest.mark.parametrize("render", FIXED_PATHS)
    @pytest.mark.parametrize(
        ("attr", "exc"),
        [
            pytest.param("runtime", RuntimeError, id="RuntimeError-the-authz-shape"),
            pytest.param("attrerr", AttributeError, id="AttributeError-via-the-dir-branch"),
            pytest.param("keyerr", KeyError, id="KeyError"),
            pytest.param("typeerr", TypeError, id="TypeError"),
            pytest.param("valueerr", ValueError, id="ValueError"),
        ],
    )
    def test_a_raising_property_propagates_instead_of_rendering_empty(self, render, attr, exc):
        source = "{{ r.%s }}" % attr
        # Premise, measured: Django raises here.
        with pytest.raises(Exception) as django_exc:
            django_render(source, {"r": RaisingAttributes()})
        assert isinstance(django_exc.value, exc)

        with pytest.raises(Exception) as djust_exc:
            render(source, {"r": RaisingAttributes()})
        # The crate wraps a `PyErr` as `RuntimeError("Python error: <T>: <msg>")`
        # at the boundary — its existing convention, shared with `maybe_call`'s
        # propagation — so the ORIGINAL type is asserted by name in the message
        # rather than by class. What matters is that it raises at all.
        assert exc.__name__ in str(djust_exc.value), str(djust_exc.value)

    @pytest.mark.parametrize("render", FIXED_PATHS)
    def test_step_2s_catch_set_is_independently_reachable(self, render):
        """The gate-off that keeps step 2 from being decoration (#2135).

        Removing the step-2 narrowing left every OTHER case in this class
        green, because the `dir()` re-raise branch below it answered them all
        — two mechanisms shadowing each other, which is one fix plus one
        decoration and no test can tell them apart while both exist. This is
        the case only step 2 answers: a name absent from `dir()` (so the
        re-raise branch declines) that PARSES AS AN INTEGER (so the walk would
        otherwise fall through to the index step, whose `KeyError` is a legal
        miss) — rendering empty where Django propagates.
        """
        source = "{{ o.0 }}"
        with pytest.raises(Exception) as django_exc:
            django_render(source, {"o": NumericSegmentRaisingGetattr()})
        assert isinstance(django_exc.value, RuntimeError)

        with pytest.raises(Exception) as djust_exc:
            render(source, {"o": NumericSegmentRaisingGetattr()})
        assert "getattr authz failed: 0" in str(djust_exc.value)

    def test_the_numeric_segment_premise(self):
        """The fixture must reach the sidecar at all: an object with no public
        instance attribute crosses as a `Value::String`, and `{{ o.0 }}` is
        then answered by `Context::string_index` — Django's step 3 over the
        `repr` — before the walk is consulted. This measures the carrier
        rather than assuming it."""
        assert _rust.crosses_as_encoded(NumericSegmentRaisingGetattr()) is False
        assert "pub" in vars(NumericSegmentRaisingGetattr())
        assert "0" not in dir(NumericSegmentRaisingGetattr())

    @pytest.mark.parametrize("render", FIXED_PATHS)
    def test_a_raising_getitem_propagates_too(self, render):
        """Step 1's set, which is WIDER than step 2's — `KeyError` there is a
        miss, not an error, so only the out-of-set exception propagates."""
        source = "{{ i.anything }}"
        with pytest.raises(Exception):
            django_render(source, {"i": RaisingGetItem()})
        with pytest.raises(Exception) as djust_exc:
            render(source, {"i": RaisingGetItem()})
        assert "RuntimeError" in str(djust_exc.value)

    @pytest.mark.parametrize("render", FIXED_PATHS)
    @pytest.mark.parametrize(
        ("source", "context"),
        [
            pytest.param("{{ r.nosuch }}", {"r": RaisingAttributes()}, id="absent-attribute"),
            pytest.param("{{ d.missing }}", {"d": {"a": 1}}, id="absent-dict-key"),
            pytest.param("{{ rows.9 }}", {"rows": [1, 2]}, id="index-out-of-range"),
            pytest.param(
                "{{ i.anything }}", {"i": KeyErrorGetItem()}, id="getitem-raising-KeyError"
            ),
            pytest.param(
                "{{ i.ok }}", {"i": KeyErrorGetItem()}, id="getitem-KeyError-then-getattr"
            ),
        ],
    )
    def test_an_ordinary_miss_still_renders_empty(self, render, source, context):
        """The other half, and the one that makes the narrowing a NARROWING
        rather than a widening: every in-set exception is still Django's
        `VariableDoesNotExist` and still renders `string_if_invalid` (`""`).

        Without these, a fix that propagated everything would pass every test
        above and break every template with a missing name.
        """
        expected = django_render(source, dict(context))
        assert render(source, dict(context)) == expected

    def test_the_getitem_KeyError_premise(self):
        """`KeyErrorGetItem` must actually reach step 2 — otherwise the two
        rows above measure the absent-name path twice and the step-1 catch set
        is untested."""
        obj = KeyErrorGetItem()
        with pytest.raises(KeyError):
            obj["ok"]
        assert obj.ok == "fine"


class TestTheSidecarOnlyEverADDSResolutions:
    """The design's own claim, which is what makes PR 1 additive by
    construction: `resolve_without_builtins` consults the sidecar ONLY after
    `Context::get`, `dict_view` and `string_index` have all missed."""

    @pytest.mark.parametrize("render", FIXED_PATHS)
    def test_a_dict_key_still_wins_over_the_objects_attribute(self, render):
        """Django's step 1 before its step 2 — a dict that HAS a key named
        `items` resolves to that key's value, not to `dict.items`."""
        ctx = {"d": {"items": "the-key", "x": 1}}
        assert django_render("{{ d.items }}", dict(ctx)) == "the-key"
        assert render("{{ d.items }}", dict(ctx)) == "the-key"

    @pytest.mark.parametrize("render", FIXED_PATHS)
    def test_the_dict_bulk_dump_arm_still_answers_a_public_instance_attribute(self, render):
        """The regression #2478 declined to risk. `{{ o.inst_attr }}` resolves
        through the value stack, ahead of the sidecar, exactly as before."""
        assert render("{{ o.inst_attr }}", {"o": Presenter()}) == "presenter-in-dict"

    @pytest.mark.parametrize("render", FIXED_PATHS)
    def test_the_bare_object_spelling_is_untouched(self, render):
        """`{{ o }}` still renders the `__dict__` arm's mapping — a divergence
        from Django, scoped out with the rest of that arm's defects (#2502)."""
        rendered = render("{{ o }}", {"o": Presenter()})
        assert "presenter-in-dict" in rendered
        assert rendered != django_render("{{ o }}", {"o": Presenter()})


@pytest.mark.django_db
class TestTheLiveViewPathsComponentExclusion:
    """The fourth path had the sidecar but skipped `Component` values going
    into it (`python/djust/mixins/rust_bridge.py`, added by #802 with no
    recorded rationale). Un-excluded here so a component's names resolve on
    this path as they now do on the other three.

    #2501's Stage 4 plan says this is what makes `{{ c.render }}` resolve on
    the LiveView path. It is not, and the correction is worth stating because
    it changes what these tests are allowed to claim. Measured with the
    exclusion restored, `{{ c.render|safe }}` ALREADY rendered: the sync loop
    replaces a component in the eager context with `{"render": <html>}` and
    marks the key safe, so the dotted spelling hits `Context::get` and never
    reaches the sidecar. What the un-exclusion buys is every OTHER name —
    `{{ c.cls_attr }}` / `{{ c.label }}` / `{{ c.kind }}` rendered `||` before
    it. The same wrapper dict is why `{{ c }}` renders a dict repr here, which
    is #2503 and is pinned below in its diverging direction.

    Measured through the REAL LiveView render rather than through
    `set_raw_py_values`, because the exclusion lives in the Python builder and
    a test that hands Rust a sidecar directly would step over the code under
    test entirely (reproduction fidelity).
    """

    @staticmethod
    def _render(template: str) -> str:
        from djust import LiveView
        from djust.testing import LiveViewTestClient

        class _V(LiveView):
            def mount(self, request, **kwargs):
                self._card = Card()

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                ctx["c"] = self._card
                return ctx

        _V.template = template
        client = LiveViewTestClient(_V)
        client.mount()
        html, _, _ = client.render_with_patches()
        return html

    def test_a_components_documented_dotted_spelling_resolves(self):
        """Through the eager `{"render": <html>}` wrapper, not the sidecar —
        this one passed before the un-exclusion too, and it is here so the
        un-exclusion cannot regress it."""
        assert ">card</b>" in self._render("<div>{{ c.render|safe }}</div>")

    def test_the_components_other_names_resolve_too(self):
        """The cell the un-exclusion actually closes. Rendered `||` before it,
        because the wrapper dict carries `render` and nothing else."""
        html = self._render("<div>{{ c.cls_attr }}|{{ c.label }}|{{ c.kind }}</div>")
        assert "card-class-level|card-method|card-property" in html

    def test_the_bare_spelling_still_renders_the_wrapper_dicts_repr(self):
        """Pinned in its DIVERGING direction (#2503), so the fix for it turns
        this red rather than passing silently (#1859).

        Unchanged by the un-exclusion — the sidecar is consulted only on a
        `Context::get` miss and `c` is present in the eager context — which is
        what makes this a pre-existing defect of the wrapper shape rather than
        something #2501 introduced.
        """
        html = self._render("<div>{{ c }}</div>")
        assert "{&#x27;render&#x27;:" in html or "{'render':" in html


class TestTheAutoCallKillSwitch:
    """ADR-024's flag has to reach the new paths or they ignore a setting the
    LiveView path honours — `Context::from_dict` defaults `auto_call` to true,
    so it is not inherited from anywhere."""

    def test_a_direct_caller_gets_djangos_behaviour(self):
        assert _rust.render_template("{{ o.label }}", {"o": Presenter()}) == "presenter-method"

    def test_the_flag_off_stops_the_call(self):
        """Asserted POSITIVELY, on the output the flag-off walk produces and
        on a side-effect witness.

        The original spelling was `!= "presenter-method"`, which passes on any
        wrong output at all — a crash-to-empty included — and so certifies the
        flag works when it may only have broken the render.

        `== ""` is NOT the right positive form here, measured rather than
        assumed: ADR-024's kill-switch restores the *pre-ADR plain-getattr
        walk*, which binds the callable itself, so `{{ o.label }}` renders the
        escaped `<bound method …>` repr. `""` is Django's answer for a
        `alters_data` refusal, a different cell.
        """
        called: list[str] = []

        class Witness(Presenter):
            def label(self) -> str:  # type: ignore[override]
                called.append("yes")
                return "presenter-method"

        for out in (
            _rust.render_template("{{ o.label }}", {"o": Witness()}, False),
            _rust.render_template_with_dirs("{{ o.label }}", {"o": Witness()}, [], None, False),
        ):
            assert out.startswith("&lt;bound method"), out
            assert "Witness.label" in out, out
        assert called == [], "the flag was off; the method must not have been called"

        # Non-vacuity: the same expression WITH the flag on calls it. Without
        # this the assertions above would pass against a walk that resolves
        # nothing at all.
        assert _rust.render_template("{{ o.label }}", {"o": Witness()}) == "presenter-method"
        assert called == ["yes"]

    def test_the_backend_path_reads_the_project_config(self, monkeypatch):
        """The wiring, not the flag: `DjustTemplate.render` is the only caller
        that knows the project's `LIVEVIEW_CONFIG`."""
        from djust import config as djust_config

        class _FakeConfig:
            def get(self, key, default=None):
                return False if key == "template_auto_call" else default

        monkeypatch.setattr(djust_config, "get_config", lambda: _FakeConfig())
        # Positively, for the reason `test_the_flag_off_stops_the_call` gives:
        # `!=` passes on a crash-to-empty as readily as on a working
        # kill-switch.
        assert backend_render("{{ o.label }}", {"o": Presenter()}).startswith("&lt;bound method")
        # And the guard against a vacuous pass: ON is the default and renders.
        monkeypatch.undo()
        assert backend_render("{{ o.label }}", {"o": Presenter()}) == "presenter-method"
