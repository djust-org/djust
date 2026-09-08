"""#2731 — a bridged Django tag resolves an OBJECT operand, on every path.

``{% regroup rows by attr %}`` returned a single group with ``grouper=None``
and an empty ``list`` on the LiveView render path whenever the rows were plain
Python objects rather than dicts, and ``{% url 'v' rows.0.pk %}`` raised
``NoReverseMatch`` on the same input. Both failed **silently** in the sense
that matters: no exception from ``regroup``, no warning — a page that grouped a
queryset rendered one empty heading, and Django model instances are objects, so
that is the *normal* use of the tag.

The cause was one line at one sink. A bridged Django tag does not resolve its
operands through the renderer: it is handed a flat Python dict and Django's own
``Variable._resolve_lookup`` walks it. ``build_py_context``
(``crates/djust_templates/src/registry.rs``) built that dict with
``IntoPyObject for Value``, which turns a non-temporal ``Value::Encoded`` — how
an arbitrary Python object crosses the PyO3 boundary — into ``e.display``, its
``str()``. So ``_resolve_lookup`` was walking a **string**: every dotted
segment missed and the tag produced its "resolved to nothing" answer.

``{{ r.group }}`` over the same state resolved correctly because the RENDERER
answers a dotted lookup through ``Context::walk_live`` — the ADR-027 live
handle carried on the ``Encoded`` — which never reaches this sink. The fix
converges the sink onto that handle: ``value_into_handler_pyobject`` hands a
handler the live object, floor-protected through the same
``protect_sidecar_strict`` the walk uses (#1646).

The class is exactly "a bridged Python tag handler", and the sweep that found
it is pinned below as :class:`TestThreeEngineParity` — the cheap net the issue
asked for. ``{% ifchanged %}``, ``{% cycle %}``, ``{% with %}`` and
``{% firstof %}`` were swept too and are CLEAN: they are native Rust nodes and
resolve through the renderer, so they never touch this sink. They stay in the
matrix as controls.
"""

from __future__ import annotations

import os
import re
import tempfile

import django
import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "djust",
        ],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "APP_DIRS": True,
                "DIRS": [],
                "OPTIONS": {"context_processors": []},
            }
        ],
        SECRET_KEY="djust-2731",
        USE_TZ=False,
        ROOT_URLCONF=__name__,
    )
    django.setup()

from django.http import HttpResponse  # noqa: E402
from django.template import Context, Template as DjangoTemplate  # noqa: E402
from django.test import RequestFactory  # noqa: E402
from django.urls import path  # noqa: E402

from djust._rust import RustLiveView, render_template  # noqa: E402

urlpatterns = [
    path("thing/<int:pk>/", lambda request, pk: HttpResponse(""), name="djust-2731-thing"),
]


# --------------------------------------------------------------------------
# Row shapes
# --------------------------------------------------------------------------


class Nested:
    def __init__(self, k: str) -> None:
        self.k = k


class Row:
    """An ordinary user object: no ``__slots__``, a nested attribute."""

    def __init__(self, i: int) -> None:
        self.id = i
        self.pk = i
        self.group = f"g{i % 2}"
        self.nested = Nested(f"n{i % 2}")


class SlottedRow:
    """The issue's own shape — ``__slots__``, so no ``__dict__`` to dump."""

    __slots__ = ("group", "id", "pk")

    def __init__(self, i: int) -> None:
        self.id = i
        self.pk = i
        self.group = f"g{i % 2}"


def objects() -> list:
    return [Row(i) for i in range(4)]


def slotted() -> list:
    return [SlottedRow(i) for i in range(4)]


def dicts() -> list:
    return [
        {"id": i, "pk": i, "group": f"g{i % 2}", "nested": {"k": f"n{i % 2}"}} for i in range(4)
    ]


SHAPES = {"objects": objects, "slotted": slotted, "dicts": dicts}

# Every case a bridged handler resolves an operand for, plus the native-Rust
# tags the issue asked to be swept, kept as controls.
CASES = {
    "regroup": (
        "{% regroup rows by group as gs %}{% for g in gs %}[{{ g.grouper }}:"
        "{% for r in g.list %}{{ r.id }}{% endfor %}]{% endfor %}"
    ),
    "regroup-dotted": (
        "{% regroup rows by nested.k as gs %}{% for g in gs %}[{{ g.grouper }}]{% endfor %}"
    ),
    "url-positional": "{% url 'djust-2731-thing' rows.0.pk %}",
    "url-kwarg": "{% url 'djust-2731-thing' pk=rows.0.pk %}",
    # Native Rust nodes — controls. They resolve through the renderer, so they
    # never reach the tag-bridge sink and were never broken.
    "ifchanged": "{% for r in rows %}{% ifchanged r.group %}[{{ r.group }}]{% endifchanged %}{% endfor %}",
    "cycle-var": "{% for r in rows %}{% cycle r.group 'z' %}{% endfor %}",
    "with": "{% with g=rows.0.group %}[{{ g }}]{% endwith %}",
    "firstof": "{% firstof missing rows.0.group %}",
    "for-attr": "{% for r in rows %}{{ r.group }}{% endfor %}",
    "if-attr": "{% if rows.0.group == 'g0' %}yes{% else %}no{% endif %}",
    "filter-attr": "{{ rows.0.group|upper }}",
}

# ``regroup-dotted`` needs a nested attribute; ``SlottedRow`` deliberately has
# none, so that pair is skipped rather than asserted against a shape it cannot
# express.
SKIP = {("regroup-dotted", "slotted")}

_DJ_ID = re.compile(r' dj-id="[^"]*"')
# `<!--dj-if-->` markers are a deliberate LiveView feature, not output: the
# stress benchmark strips them before comparing and so does this net.
_DJ_IF = re.compile(r"<!--/?dj-if[^>]*-->")


@pytest.fixture(autouse=True)
def _builtins_registered():
    from djust.template_tags import reregister_builtins

    reregister_builtins()
    yield


@pytest.fixture(autouse=True)
def _urlconf():
    """Point ``{% url %}`` at this module's own patterns.

    ``override_settings`` rather than the ``settings.configure`` block above:
    a shared conftest may already have configured settings, in which case that
    block never ran and ``ROOT_URLCONF`` is somebody else's.
    """
    from django.test import override_settings
    from django.urls import clear_url_caches

    with override_settings(ROOT_URLCONF=__name__):
        clear_url_caches()
        yield
    clear_url_caches()


@pytest.fixture(scope="module")
def template_dir():
    with tempfile.TemporaryDirectory(prefix="djust2731-") as directory:
        yield directory


def _repoint_template_engines() -> None:
    """Make Django AND djust re-read ``settings.TEMPLATES[0]['DIRS']``.

    Both cache it: Django on ``engines`` (``_engines`` plus the ``templates``
    cached_property) and djust in ``_get_template_dirs_cached``.
    """
    from django.template import engines

    from djust.utils import _get_template_dirs_cached

    engines._engines = {}
    engines.__dict__.pop("templates", None)
    _get_template_dirs_cached.cache_clear()


def _django(source: str, rows: list) -> str:
    return DjangoTemplate(source).render(Context({"rows": rows}))


def _stateless(source: str, rows: list) -> str:
    return render_template(source, {"rows": rows})


def _rust_live_view(source: str, rows: list) -> str:
    """``RustLiveView.set_state`` + ``render`` — the issue's own repro."""
    view = RustLiveView(source)
    view.set_state("rows", rows)
    return view.render()


def _live_view(source: str, rows: list, directory: str) -> str:
    """A REAL ``LiveView`` through the Python bridge — the path a page takes.

    Reproduction fidelity: the issue's repro drives ``RustLiveView`` directly,
    which never populates the raw-Python sidecar. A real view does populate it
    (``_sync_state_to_rust``), and it STILL reproduces, because that builder
    skips every JSON-friendly type — a ``list`` included — so the list of rows
    is not in the sidecar either.
    """
    from djust.live_view import LiveView

    name = "t2731_%x.html" % (abs(hash(source)) & 0xFFFFFFF)
    with open(os.path.join(directory, name), "w") as handle:
        handle.write("<div>" + source + "</div>")

    view_cls = type(
        "ParityView",
        (LiveView,),
        {
            "template_name": name,
            "mount": lambda self, request, **kwargs: setattr(self, "rows", rows),
        },
    )
    original_dirs = settings.TEMPLATES[0]["DIRS"]
    settings.TEMPLATES[0]["DIRS"] = [directory]
    try:
        _repoint_template_engines()
        view = view_cls()
        request = RequestFactory().get("/")
        view.mount(request)
        view._initialize_rust_view(request)
        view._sync_state_to_rust()
        html, _patches, _version = view.render_with_diff()
    finally:
        settings.TEMPLATES[0]["DIRS"] = original_dirs
        _repoint_template_engines()
    html = _DJ_IF.sub("", _DJ_ID.sub("", html))
    match = re.search(r"<div>(.*)</div>", html, re.S)
    return match.group(1) if match else html


class TestThreeEngineParity:
    """The net: every case × every shape, through Django and BOTH djust paths.

    This is the comparison the stress benchmark makes, as a test. Before the
    fix it went red on 4 of these cells (``regroup`` / ``regroup-dotted`` /
    both ``url`` spellings, ``objects`` and ``slotted``) and green on every
    dict cell — which is exactly why the defect shipped: the existing
    ``{% regroup %}`` suite only ever grouped dicts.
    """

    @pytest.mark.parametrize("case", sorted(CASES))
    @pytest.mark.parametrize("shape", sorted(SHAPES))
    def test_all_three_engines_agree(self, case, shape, template_dir):
        if (case, shape) in SKIP:
            pytest.skip("shape cannot express this case")
        source = CASES[case]
        expected = _django(source, SHAPES[shape]())
        assert _stateless(source, SHAPES[shape]()) == expected, "stateless path diverged"
        assert _rust_live_view(source, SHAPES[shape]()) == expected, "RustLiveView diverged"
        assert _live_view(source, SHAPES[shape](), template_dir) == expected, (
            "LiveView bridge path diverged"
        )


class TestLiveHandleArm:
    """Arm 1 — an ``Encoded`` carrying an ADR-027 live handle crosses as the
    object itself.

    Gate this arm off (return the ``TemplateObject`` wrapper unconditionally)
    and these go red: ``Encoded::attrs`` is EMPTY for an ordinary user object,
    so the wrapper cannot answer ``by group`` at all.
    """

    def test_regroup_over_objects_groups_by_the_attribute(self):
        source = CASES["regroup"]
        assert _rust_live_view(source, objects()) == "[g0:0][g1:1][g0:2][g1:3]"

    def test_regroup_over_slotted_objects_groups_by_the_attribute(self):
        """The issue's verbatim repro: ``__slots__``, so no ``__dict__``."""
        source = CASES["regroup"]
        assert _rust_live_view(source, slotted()) == "[g0:0][g1:1][g0:2][g1:3]"

    def test_regroup_by_a_dotted_grouper_walks_the_nested_object(self):
        assert _rust_live_view(CASES["regroup-dotted"], objects()) == "[n0][n1][n0][n1]"

    def test_url_resolves_an_object_argument(self):
        """The second tag the sweep found at the same sink — same fix."""
        assert _rust_live_view(CASES["url-positional"], objects()) == "/thing/0/"
        assert _rust_live_view(CASES["url-kwarg"], objects()) == "/thing/0/"

    def test_the_group_list_holds_the_objects_not_their_repr(self):
        """The binding round-trips: a handler's returned rows are the SAME
        ``Value``s, so ``{{ r.id }}`` over ``g.list`` still resolves.

        This is what the ``TemplateObject`` unwrap arm in
        ``impl FromPyObject for Value`` buys — without it the wrapper would be
        re-measured as a NEW object and ``r.id`` would read the wrapper's
        attributes.
        """
        source = "{% regroup rows by group as gs %}{% for g in gs %}{% for r in g.list %}{{ r.id }}.{{ r.group }};{% endfor %}{% endfor %}"
        assert _rust_live_view(source, objects()) == "0.g0;1.g1;2.g0;3.g1;"


class TestWrapperArm:
    """Arm 2 — an ``Encoded`` the floor does not govern crosses as a
    ``TemplateObject`` reading ``Encoded::attrs`` / ``Encoded::items``.

    Reached for an ITERABLE the floor returned unchanged, which the live arm
    deliberately refuses: ``_protect_sidecar_value`` is the LEAF floor and this
    sink has no next segment to re-protect at, so handing one over would carry
    raw models past it (see :class:`TestFloorHoldsForEveryCarrier`).

    Gate this arm off — fall back to ``e.display`` — and this goes red: the
    handler receives the text ``frozenset({<... object at 0x...>})`` and
    iterates its characters.
    """

    def test_regroup_over_a_frozenset_source_matches_django(self):
        source = "{% regroup tags by k as gs %}{% for g in gs %}[{{ g.grouper }}]{% endfor %}"

        class Tag:
            def __init__(self, k):
                self.k = k

            def __hash__(self):
                return hash(self.k)

            def __eq__(self, other):
                return isinstance(other, Tag) and self.k == other.k

        tags = frozenset({Tag("a")})
        expected = DjangoTemplate(source).render(Context({"tags": tags}))
        assert expected == "[a]"

        view = RustLiveView(source)
        view.set_state("tags", tags)
        assert view.render() == expected

    def test_an_ungoverned_iterable_is_NOT_handed_over_live(self):
        """The floor gate, pinned — it has no output of its own.

        A live handle the floor returned UNCHANGED and that is iterable is
        refused by ``value_into_handler_pyobject`` and takes the wrapper
        instead, because ``_protect_sidecar_value`` is the LEAF floor: it
        proxies a ``Model`` and returns a ``frozenset`` unchanged, so handing
        one over would carry raw models to Python code that never walks another
        segment. The tree pass that would fix that is O(n) per bridged TAG
        CALL.

        Gate the guard off (hand every live handle over) and this goes red
        while every rendering test above stays green, which is exactly why the
        guard needs a pin rather than an output assertion — and why shipping it
        as an ``isinstance`` allowlist of five builtins let five other carriers
        through (:class:`TestFloorHoldsForEveryCarrier`).
        """
        import djust.template_tags.regroup as regroup_module

        seen = {}
        original = regroup_module.RegroupTagHandler.render

        def spy(self, args, context, autoescape=True):
            seen["tags"] = context.get("tags")
            return original(self, args, context, autoescape)

        class Tag:
            def __init__(self, k):
                self.k = k

            def __hash__(self):
                return hash(self.k)

            def __eq__(self, other):
                return isinstance(other, Tag) and self.k == other.k

        regroup_module.RegroupTagHandler.render = spy
        try:
            view = RustLiveView(
                "{% regroup tags by k as gs %}{% for g in gs %}[{{ g.grouper }}]{% endfor %}"
            )
            view.set_state("tags", frozenset({Tag("a")}))
            view.render()
        finally:
            regroup_module.RegroupTagHandler.render = original

        handed = seen["tags"]
        assert type(handed).__name__ == "TemplateObject", (
            "a container live handle must reach a handler wrapped, not raw — "
            f"got {type(handed).__name__}"
        )
        assert not isinstance(handed, frozenset)

    def test_a_wrapper_returned_in_a_binding_still_spells_its_object(self):
        """A wrapper that comes BACK through a binding keeps the object's
        spelling and length.

        This is the return direction, and it is reachable: ``{% regroup %}``
        over a ``frozenset`` of ``frozenset``s hands the items out through
        ``TemplateObject.__iter__``, each inner container is a wrapper again,
        and ``gs`` carries them back into the renderer. There is deliberately
        no unwrap arm in ``impl FromPyObject for Value`` — the wrapper answers
        from the same facts ``opaque_value`` measures, so re-measuring it
        reconstructs the same ``Encoded``. This test is what says so.

        Both engines are handed the SAME object, so frozenset iteration order
        cannot make it flaky.
        """
        source = (
            "{% regroup outer by nope as gs %}{% for g in gs %}"
            "{% for s in g.list %}<{{ s }}|{{ s|length }}>{% endfor %}{% endfor %}"
        )
        outer = frozenset({frozenset({1, 2}), frozenset({3})})

        expected = DjangoTemplate(source).render(Context({"outer": outer}))
        assert "|2>" in expected and "|1>" in expected, "the fixture must exercise length"

        view = RustLiveView(source)
        view.set_state("outer", outer)
        assert view.render() == expected


class TestSerializationFloorAtThisSink:
    """The floor (SECURE_DEFAULTS Pattern 1) still holds where it held before.

    A bare ``Model`` never becomes an ``Encoded`` — it routes through
    ``normalize_django_value`` and reaches a handler as a denylist-filtered
    dict — so handing live handles to handlers does not open it.
    """

    def test_a_model_reaches_a_handler_without_its_floor_fields(self):
        from django.contrib.auth.models import User

        import djust.template_tags.regroup as regroup_module

        seen = {}
        original = regroup_module.RegroupTagHandler.render

        def spy(self, args, context, autoescape=True):
            seen["rows"] = context.get("rows")
            return original(self, args, context, autoescape)

        regroup_module.RegroupTagHandler.render = spy
        try:
            user = User(username="alice", password="pbkdf2_sha256$SECRET")
            source = (
                "{% regroup rows by username as gs %}{% for g in gs %}[{{ g.grouper }}]{% endfor %}"
            )
            assert _rust_live_view(source, [user]) == "[alice]"
        finally:
            regroup_module.RegroupTagHandler.render = original

        row = seen["rows"][0]
        assert isinstance(row, dict), "a Model must still reach a handler as a filtered dict"
        assert "password" not in row
        assert "is_staff" not in row
        assert row["username"] == "alice"


# --------------------------------------------------------------------------
# The carriers the LEAF floor cannot see inside (PR #2734 review, 🔴 2)
# --------------------------------------------------------------------------

SECRET = "pbkdf2_sha256$THIS-MUST-NOT-RENDER"


def _a_user():
    """A ``User`` with a pk, so it is hashable for the set / dict-key rows."""
    from django.contrib.auth.models import User

    return User(pk=1, username="alice", password=SECRET, is_staff=True)


class _CustomSeq:
    """A sequence that is none of the five builtins — the shape an
    ``isinstance`` allowlist is structurally unable to cover."""

    def __init__(self, items):
        self._items = list(items)

    def __len__(self):
        return len(self._items)

    def __iter__(self):
        return iter(self._items)


CARRIERS = {
    "list": lambda: [_a_user()],
    "tuple": lambda: (_a_user(),),
    "set": lambda: {_a_user()},
    "frozenset": lambda: frozenset({_a_user()}),
    "dict_values": lambda: {1: _a_user()}.values(),
    "dict_keys": lambda: {_a_user(): 1}.keys(),
    "deque": lambda: __import__("collections").deque([_a_user()]),
    "generator": lambda: (u for u in [_a_user()]),
    "custom_sequence": lambda: _CustomSeq([_a_user()]),
}

_BY_FLOOR_FIELD = (
    "{% regroup rows by password as gs %}{% for g in gs %}[{{ g.grouper }}]{% endfor %}"
)
_BY_ORDINARY_FIELD = (
    "{% regroup rows by username as gs %}{% for g in gs %}[{{ g.grouper }}]{% endfor %}"
)


class TestFloorHoldsForEveryCarrier:
    """The serialization floor holds for a model in EVERY carrier shape, at
    this sink, on the ``RustLiveView`` render path these tests drive.

    The first version of this fix gated the live arm on an ``isinstance``
    allowlist — ``list`` / ``tuple`` / ``dict`` / ``set`` / ``frozenset`` — and
    shipped five leaks (PR #2734 review): ``collections.deque``, ``dict_keys``,
    ``dict_values``, a generator and any custom ``__len__``/``__iter__`` class
    are none of those, so each took the LIVE arm, and
    ``{% regroup rows by password %}`` rendered the hash.

    The gate is now the PROPERTY the floor actually has — take the live arm
    only when the object is NOT ITERABLE — because an allowlist of container
    types is always one shape short. Each row below carries its own
    non-vacuity control, so a cell that is "safe" because nothing resolved at
    all cannot pass.
    """

    @pytest.mark.parametrize("carrier", sorted(CARRIERS))
    def test_a_floor_field_does_not_render_from_any_carrier(self, carrier):
        rendered = _rust_live_view(_BY_FLOOR_FIELD, CARRIERS[carrier]())
        assert "THIS-MUST-NOT-RENDER" not in rendered, (
            f"the serialization floor leaked through a {carrier} carrier: {rendered!r}"
        )

    @pytest.mark.parametrize("carrier", sorted(CARRIERS))
    def test_non_vacuity_an_ordinary_field_DOES_render(self, carrier):
        """The control for the row above: the carrier resolves at all.

        Without this, a carrier that silently resolved to nothing would look
        like the floor holding.
        """
        assert _rust_live_view(_BY_ORDINARY_FIELD, CARRIERS[carrier]()) == "[alice]"

    @pytest.mark.parametrize("carrier", ["deque", "dict_values", "generator", "custom_sequence"])
    def test_a_non_builtin_carrier_still_groups_exactly_as_django(self, carrier):
        """Safe is not enough — the carriers must still be CORRECT.

        A gate that sent everything to the wrapper would pass the floor rows
        above while breaking these.
        """
        source = "{% regroup rows by group as gs %}{% for g in gs %}[{{ g.grouper }}]{% endfor %}"
        rows = list(objects())
        wrapped = {
            "deque": lambda: __import__("collections").deque(rows),
            "dict_values": lambda: dict(enumerate(rows)).values(),
            "generator": lambda: (r for r in rows),
            "custom_sequence": lambda: _CustomSeq(rows),
        }[carrier]
        expected = DjangoTemplate(source).render(Context({"rows": wrapped()}))
        assert expected == "[g0][g1][g0][g1]"
        assert _rust_live_view(source, wrapped()) == expected


class TestFloorReachesAModelHeldINSIDEAnElement:
    """The floor is CONTINUOUS across ``OPAQUE_ITEM_CAP`` for a NESTED model.

    Every row in :class:`TestFloorHoldsForEveryCarrier` holds its model in
    LEAF position — the element *is* the model. This class holds it one level
    down, which is what separates the two ``__iter__`` branches:

    * under the cap, elements come from ``Encoded::items`` — already ``Value``s,
      and the ``Value`` conversion descends, so a model at any depth is the
      denylist-filtered dict ``normalize_django_value`` made;
    * past the cap (and for a one-shot generator) the elements are asked of the
      live object instead.

    The first version of that fallback applied ``_protect_sidecar_value`` — the
    LEAF floor — once per element, which cannot see inside one. So the floor
    was DISCONTINUOUS at the cap (PR #2734 review round 3): the identical
    template rendered ``""`` for 3 rows and the password hash for 100 001. The
    fallback now routes each element through the same ``Value`` conversion the
    enumerated branch uses, so both branches answer alike.

    Gate the conversion back to the leaf floor and the past-cap and generator
    rows below go red while every leaf-position row stays green — which is
    exactly why this class exists separately.
    """

    NESTED_FLOOR = (
        "{% regroup rows by 0.password as gs %}{% for g in gs %}[{{ g.grouper }}]{% endfor %}"
    )
    NESTED_CONTROL = (
        "{% regroup rows by 0.username as gs %}{% for g in gs %}[{{ g.grouper }}]{% endfor %}"
    )

    @staticmethod
    def _nested(n):
        """``n`` elements, each a LIST holding a model — one level down."""
        return [[_a_user()] for _ in range(n)]

    @pytest.mark.parametrize(
        "label,rows",
        [
            (
                "under the cap (enumerated items)",
                lambda: TestFloorReachesAModelHeldINSIDEAnElement._nested(3),
            ),
            (
                "past the cap (live fallback)",
                lambda: TestFloorReachesAModelHeldINSIDEAnElement._nested(100_001),
            ),
            (
                "one-shot generator (live fallback)",
                lambda: ([_a_user()] for _ in range(3)),
            ),
        ],
    )
    def test_a_nested_floor_field_does_not_render(self, label, rows):
        rendered = _rust_live_view(self.NESTED_FLOOR, rows())
        assert "THIS-MUST-NOT-RENDER" not in rendered, (
            f"the floor leaked through a model nested one level down, {label}: {rendered!r}"
        )

    @pytest.mark.parametrize(
        "label,rows",
        [
            (
                "under the cap (enumerated items)",
                lambda: TestFloorReachesAModelHeldINSIDEAnElement._nested(3),
            ),
            (
                "past the cap (live fallback)",
                lambda: TestFloorReachesAModelHeldINSIDEAnElement._nested(100_001),
            ),
            (
                "one-shot generator (live fallback)",
                lambda: ([_a_user()] for _ in range(3)),
            ),
        ],
    )
    def test_non_vacuity_a_nested_ordinary_field_DOES_render(self, label, rows):
        """The control: the nested lookup resolves at all on this shape.

        Without it, a nested carrier that resolved to nothing would look
        exactly like the floor holding.
        """
        assert _rust_live_view(self.NESTED_CONTROL, rows()) == "[alice]"

    def test_the_two_sides_of_the_cap_agree(self):
        """The discontinuity itself, pinned as one assertion.

        A future change that fixes one branch and not the other leaves these
        two equal-by-accident only if BOTH are wrong the same way; the floor
        row above then catches it.
        """
        under = _rust_live_view(self.NESTED_FLOOR, self._nested(3))
        past = _rust_live_view(self.NESTED_FLOOR, self._nested(100_001))
        assert under == past, (
            "the serialization floor must not depend on which side of "
            f"OPAQUE_ITEM_CAP the carrier falls: {under!r} vs {past!r}"
        )


class TestAboveTheItemCap:
    """A carrier past ``OPAQUE_ITEM_CAP`` renders, and renders CORRECTLY.

    ``opaque_value`` declines to enumerate a sequence whose stated length is
    past the cap (100 000), so ``Encoded::items`` is ``None``. The first
    version of this fix raised ``TypeError: 'list' object is not iterable`` out
    of ``render()`` — a page that regrouped a large list went from
    wrong-but-rendering to a 500 (PR #2734 review, 🔴 1).

    ``TemplateObject.__iter__`` now falls back to the live object, putting
    every element through the same floor, which is both what Django's own
    handler does and what makes the output correct rather than merely present.
    """

    def test_it_renders_byte_identically_to_django(self):
        source = "{% regroup rows by group as gs %}{% for g in gs %}[{{ g.grouper }}]{% endfor %}"
        rows = [SlottedRow(i) for i in range(100_001)]
        expected = DjangoTemplate(source).render(Context({"rows": rows}))
        assert _rust_live_view(source, [SlottedRow(i) for i in range(100_001)]) == expected

    def test_the_floor_still_holds_past_the_cap(self):
        rows = [_a_user() for _ in range(100_001)]
        assert "THIS-MUST-NOT-RENDER" not in _rust_live_view(_BY_FLOOR_FIELD, rows)


class TestWrapperPythonProtocol:
    """The wrapper stands in for a value that used to arrive as a ``str``.

    Both of these were new-surface regressions rather than defects in the fix
    (PR #2734 review): declaring ``__eq__`` makes PyO3 set ``__hash__ = None``,
    and a pyclass with no ``__reduce__`` is unpicklable — so a ``{% load %}``ed
    handler doing ``set(values)`` or ``copy.deepcopy(context)`` would have
    started raising where it used to work.
    """

    def _wrapper(self):
        import djust.template_tags.regroup as regroup_module

        seen = {}
        original = regroup_module.RegroupTagHandler.render

        def spy(self, args, context, autoescape=True):
            seen["tags"] = context.get("tags")
            return original(self, args, context, autoescape)

        regroup_module.RegroupTagHandler.render = spy
        try:
            view = RustLiveView("{% regroup tags by k as gs %}{{ gs|length }}")
            view.set_state("tags", frozenset({Row(1)}))
            view.render()
        finally:
            regroup_module.RegroupTagHandler.render = original
        wrapper = seen["tags"]
        assert type(wrapper).__name__ == "TemplateObject", "fixture must produce a wrapper"
        return wrapper

    def test_it_is_hashable(self):
        wrapper = self._wrapper()
        assert isinstance(hash(wrapper), int)
        assert len({wrapper, wrapper}) == 1

    def test_it_survives_deepcopy_and_pickle_as_the_string_it_replaced(self):
        import copy
        import pickle

        wrapper = self._wrapper()
        assert copy.deepcopy(wrapper) == str(wrapper)
        assert pickle.loads(pickle.dumps(wrapper)) == str(wrapper)

    def test_its_declared_module_path_resolves(self):
        from djust import _rust

        assert type(self._wrapper()) is _rust.TemplateObject
        assert type(self._wrapper()).__module__ == "djust._rust"
