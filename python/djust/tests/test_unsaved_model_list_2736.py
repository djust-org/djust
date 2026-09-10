"""#2736 — a plain ``list`` of model instances serializes its fields on the
LiveView path, saved or not, exactly as a QuerySet of the same rows does.

The issue's symptom was a list of UNSAVED instances rendering identity-only:
``{{ rows.0.username }}`` empty, ``{% regroup rows by username %}`` giving
``[None]``, ``{% url 'v' rows.0.pk %}`` raising ``NoReverseMatch``. Tracing it
symptom-up found NO branch keyed on ``pk`` / ``_state.adding``: the eager
serializer (``_serialize_model_safely``) already carries an unsaved row's
concrete field values. Two upstream causes, both at the JIT serialization
boundary, neither about being unsaved:

1. **list-vs-QuerySet drift** (``mixins/context.py``). With no field path
   attributed to ``rows`` — every whole-object use, ``{% regroup %}`` included —
   the QuerySet branch emitted the full dict while the list branch called
   ``_jit_serialize_model`` per element, whose no-paths answer is the
   least-exposure IDENTITY map (finding #19, right for one ``{{ user }}``).
   A SAVED list failed the same way; an unsaved row's identity is
   ``pk: None``, which is what made it read as saved-vs-unsaved.
2. **an index segment in a path** (``parser.rs::extract_template_variables``).
   ``{{ rows.0.username }}`` was attributed to ``rows`` as the attribute path
   ``0.username``; codegen serialized attribute ``0`` and shipped ``[{}]`` —
   for a list AND a QuerySet, saved or not.

Both are fixed at the boundary (#1646) — one producer for the no-paths list
case, one chokepoint for index segments — so every sink is fixed at once.
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
        SECRET_KEY="djust-2736",
        USE_TZ=False,
        ROOT_URLCONF=__name__,
    )
    django.setup()

from django.contrib.auth.models import User  # noqa: E402
from django.http import HttpResponse  # noqa: E402
from django.template import Context, Template as DjangoTemplate  # noqa: E402
from django.test import RequestFactory  # noqa: E402
from django.urls import path  # noqa: E402

from djust._rust import extract_template_variables  # noqa: E402

urlpatterns = [
    path("u/<int:pk>/", lambda request, pk: HttpResponse(""), name="djust-2736-user"),
]

_DJ_ID = re.compile(r'\s*dj-id="[^"]*"')
_DJ_IF = re.compile(r"<!--dj-if:[^>]*-->")

HASH = "pbkdf2_sha256$600000$salt$hash"


# --------------------------------------------------------------------------
# Row shapes — the axis the issue drew (saved / unsaved) and the one that
# actually mattered (list / QuerySet)
# --------------------------------------------------------------------------


def unsaved() -> list:
    return [User(username="alice"), User(username="bob"), User(username="alice")]


def saved() -> list:
    User.objects.all().delete()
    return [
        User.objects.create(username="alice"),
        User.objects.create(username="bob"),
        User.objects.create(username="alice2"),
    ]


def queryset():
    saved()
    return User.objects.order_by("pk")


SHAPES = {"unsaved-list": unsaved, "saved-list": saved, "queryset": queryset}

# Every sink the issue and the #2734 review listed as failing identically.
CASES = {
    "index-field": "{{ rows.0.username }}",
    "index-if": "{% if rows.0.username == 'alice' %}Y{% else %}N{% endif %}",
    "index-with": "{% with u=rows.0.username %}{{ u }}{% endwith %}",
    "index-firstof": "{% firstof rows.0.nope rows.0.username %}",
    "regroup": (
        "{% regroup rows by username as gs %}{% for g in gs %}[{{ g.grouper }}:"
        "{{ g.list|length }}]{% endfor %}"
    ),
    "for": "{% for r in rows %}{{ r.username }},{% endfor %}",
    "length": "{{ rows|length }}",
}


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


@pytest.fixture
def template_dir():
    with tempfile.TemporaryDirectory(prefix="djust2736-") as directory:
        yield directory


def _repoint_template_engines() -> None:
    from django.template import engines

    from djust.utils import _get_template_dirs_cached

    engines._engines = {}
    engines.__dict__.pop("templates", None)
    _get_template_dirs_cached.cache_clear()


def _django(source: str, rows) -> str:
    return DjangoTemplate(source).render(Context({"rows": rows}))


class _Mounted:
    """A REAL ``LiveView`` through the Python bridge, kept alive so a test can
    mutate its state and re-render (the change-detection check)."""

    def __init__(self, source: str, rows, directory: str) -> None:
        from djust.live_view import LiveView

        name = "t2736_%x.html" % (abs(hash(source)) & 0xFFFFFFF)
        with open(os.path.join(directory, name), "w") as handle:
            handle.write("<div>" + source + "</div>")

        view_cls = type(
            "UnsavedRowsView",
            (LiveView,),
            {
                "template_name": name,
                "mount": lambda self, request, **kwargs: setattr(self, "rows", rows),
            },
        )
        self._directory = directory
        self._original_dirs = settings.TEMPLATES[0]["DIRS"]
        settings.TEMPLATES[0]["DIRS"] = [directory]
        _repoint_template_engines()
        self.view = view_cls()
        request = RequestFactory().get("/")
        self.view.mount(request)
        self.view._initialize_rust_view(request)

    def render(self) -> str:
        self.view._sync_state_to_rust()
        html, _patches, _version = self.view.render_with_diff()
        html = _DJ_IF.sub("", _DJ_ID.sub("", html))
        match = re.search(r"<div>(.*)</div>", html, re.S)
        return match.group(1) if match else html

    def close(self) -> None:
        settings.TEMPLATES[0]["DIRS"] = self._original_dirs
        _repoint_template_engines()


def _live_view(source: str, rows, directory: str) -> str:
    mounted = _Mounted(source, rows, directory)
    try:
        return mounted.render()
    finally:
        mounted.close()


# --------------------------------------------------------------------------
# The net: every sink × every shape, LiveView against Django
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestListParity:
    """Before the fix: every ``index-*`` cell was red for all three shapes
    (cause 2), and ``regroup`` was red for BOTH lists (cause 1) — the QuerySet
    and ``for`` cells were the only green ones, which is why it read as a
    tag bug and then as an unsaved-instance bug."""

    @pytest.mark.parametrize("case", sorted(CASES))
    @pytest.mark.parametrize("shape", sorted(SHAPES))
    def test_live_view_matches_django(self, case, shape, template_dir):
        source = CASES[case]
        expected = _django(source, SHAPES[shape]())
        assert expected, "control: the case must resolve to something under Django"
        assert _live_view(source, SHAPES[shape](), template_dir) == expected

    def test_url_over_a_saved_list_element(self, template_dir):
        """The issue's ``NoReverseMatch``: ``rows.0.pk`` reaches the tag."""
        rows = saved()
        source = "{% url 'djust-2736-user' rows.0.pk %}"
        assert _live_view(source, rows, template_dir) == "/u/%d/" % rows[0].pk

    def test_unsaved_row_serializes_its_fields_not_just_identity(self, template_dir):
        """The issue's own statement of the gap, asserted directly on the
        context the renderer receives."""
        mounted = _Mounted(CASES["regroup"], unsaved(), template_dir)
        try:
            context = mounted.view.get_context_data()
        finally:
            mounted.close()
        row = context["rows"][0]
        assert row["username"] == "alice"
        assert row["pk"] is None and row["id"] is None


# --------------------------------------------------------------------------
# Cause 2 pinned at its chokepoint: an index segment is not a field
# --------------------------------------------------------------------------


class TestIndexSegmentExtraction:
    def test_index_segment_is_dropped_from_the_path(self):
        assert extract_template_variables("{{ rows.0.username }}") == {"rows": ["username"]}

    def test_a_bare_index_is_a_whole_element_use(self):
        assert extract_template_variables("{{ rows.0 }}") == {"rows": []}

    def test_nested_index_segments_and_dedup(self):
        source = "{{ rows.0.profile.1.name }}{{ rows.2.profile.0.name }}"
        assert extract_template_variables(source) == {"rows": ["profile.name"]}

    def test_a_digit_leading_attribute_is_not_an_identifier(self):
        """``0abc`` is not all digits, so it is kept — the rule is INDEX, not
        'starts with a digit'."""
        assert extract_template_variables("{{ rows.0abc }}") == {"rows": ["0abc"]}


# --------------------------------------------------------------------------
# The serialization floor holds — a list is not a way past it
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestFloorHoldsForUnsavedList:
    """The no-paths list case now emits the FULL dict, so this is the check
    that "full" still means "floor-protected" (``_ALWAYS_EXCLUDED_FIELDS``),
    for an unsaved row exactly as for a saved one."""

    def _rows(self):
        return [User(username="alice", password=HASH, is_staff=True)]

    def test_serialized_row_carries_username_but_not_password(self, template_dir):
        mounted = _Mounted(CASES["regroup"], self._rows(), template_dir)
        try:
            row = mounted.view.get_context_data()["rows"][0]
        finally:
            mounted.close()
        assert row["username"] == "alice"
        assert "password" not in row
        assert "is_staff" not in row
        assert HASH not in repr(row)

    @pytest.mark.parametrize(
        "source",
        [
            "{{ rows.0.password }}",
            "{% regroup rows by password as gs %}{% for g in gs %}[{{ g.grouper }}]{% endfor %}",
            "{% for r in rows %}{{ r.password }}{% endfor %}",
        ],
    )
    def test_no_sink_renders_the_password(self, source, template_dir):
        html = _live_view(source, self._rows(), template_dir)
        assert HASH not in html
        assert "pbkdf2" not in html

    def test_non_vacuity_control(self, template_dir):
        """The same rows render the non-floor field — so a floor cell is green
        because the field was refused, not because nothing resolved."""
        source = (
            "{% regroup rows by username as gs %}{% for g in gs %}[{{ g.grouper }}]{% endfor %}"
        )
        assert _live_view(source, self._rows(), template_dir) == "[alice]"


# --------------------------------------------------------------------------
# Change detection: a field mutation on an unsaved row in a list re-renders
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestChangeDetectionOnUnsavedRows:
    """An unsaved instance has no stable ``pk`` and ``deep_fingerprint`` keys
    a ``Model`` leaf by ``id()``; what makes a container compare see a field
    change is that the row reaches change detection as a DICT (the eager
    serialization above, plus ``_normalize_db_values``). This pins that an
    in-place mutation on an unsaved row is rendered, for the no-paths list
    case this PR re-routed and for the index-path case it fixed."""

    @pytest.mark.parametrize("case", ["index-field", "regroup", "for"])
    def test_in_place_field_mutation_is_rendered(self, case, template_dir):
        rows = unsaved()
        mounted = _Mounted(CASES[case], rows, template_dir)
        try:
            first = mounted.render()
            assert "alice" in first
            rows[0].username = "zed"
            second = mounted.render()
        finally:
            mounted.close()
        assert second == _django(CASES[case], rows)
        assert second != first
