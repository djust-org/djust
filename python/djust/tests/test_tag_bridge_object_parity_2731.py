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
    """Arm 2 — an ``Encoded`` with NO usable live handle crosses as a
    ``TemplateObject`` reading ``Encoded::attrs`` / ``Encoded::items``.

    Reached for a CONTAINER handle, which the live arm deliberately refuses
    (the leaf serialization floor cannot see inside one). Gate this arm off —
    fall back to ``e.display`` — and this goes red: the handler receives the
    text ``frozenset({<... object at 0x...>})`` and iterates its characters.
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

    def test_a_container_handle_is_NOT_handed_over_live(self):
        """The container guard, pinned — it has no output of its own.

        A live handle that IS a container is refused by
        ``value_into_handler_pyobject`` and takes the wrapper instead, because
        ``_protect_sidecar_value`` is the LEAF floor: it proxies a ``Model``
        and returns a ``list`` unchanged, so a container would carry raw models
        past the floor to Python code that never walks another segment. Only an
        ``isinstance`` decides it — the tree pass that would fix it is O(n) per
        bridged TAG CALL.

        Gate the guard off (hand containers over live) and this goes red while
        every rendering test above stays green, which is exactly why the guard
        needs a pin rather than an output assertion.
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
