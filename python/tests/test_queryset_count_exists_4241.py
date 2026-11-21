"""#4241 -- ``{{ qs.count }}`` / ``{{ qs.exists }}`` on a top-level QuerySet.

A QuerySet in the context rendered ``{{ qs|length }}`` on the native (Rust)
renderer but ``{{ qs.count }}`` and ``{{ qs.exists }}`` rendered empty. Django's
own engine is the oracle; every native entry point is checked against it: the
plain ``DjustTemplateBackend`` and a LiveView under explicit exposure (the 2.0
default), with the QuerySet supplied by ``get_context_data`` and by a
``state()`` field, on REAL ``User`` rows (#1650 / #1638).
"""

from __future__ import annotations

import html as html_lib
import re

import pytest
from django.contrib.auth.models import User
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate

from djust import LiveView
from djust.testing import LiveViewTestClient


def _django_render(source: str, context: dict) -> str:
    return DjangoTemplate(source).render(DjangoContext(dict(context)))


def _backend_render(source: str, context: dict) -> str:
    from djust.template import DjustTemplateBackend

    backend = DjustTemplateBackend(
        params={"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
    )
    return str(backend.from_string(source).render(context=dict(context), request=None))


def _body(html: str) -> str:
    match = re.search(r"<div dj-root[^>]*>(.*)</div>", html, re.S)
    assert match is not None, html
    return match.group(1)


def _liveview_render(source: str, context: dict) -> str:
    """Explicit exposure (the 2.0 default): the QuerySet comes from get_context_data."""

    class _V(LiveView):
        exposure_policy = "explicit"

        def get_context_data(self, **kwargs):
            ctx = super().get_context_data(**kwargs)
            ctx.update(context)
            return ctx

    _V.template = f"<div dj-root>{source}</div>"
    client = LiveViewTestClient(_V)
    client.mount()
    return _body(client.render())


def _liveview_before_super_render(source: str, context: dict) -> str:
    """Explicit exposure, the QuerySet handed to ``super().get_context_data`` (JIT path)."""

    class _V(LiveView):
        exposure_policy = "explicit"

        def get_context_data(self, **kwargs):
            kwargs.update(context)
            return super().get_context_data(**kwargs)

    _V.template = f"<div dj-root>{source}</div>"
    client = LiveViewTestClient(_V)
    client.mount()
    return _body(client.render())


def _liveview_serialize_for_template_render(source: str, context: dict) -> str:
    """Explicit exposure, the QuerySet passed through ``serialize_for_template`` (JIT path)."""

    class _V(LiveView):
        exposure_policy = "explicit"

        def get_context_data(self, **kwargs):
            ctx = super().get_context_data(**kwargs)
            for name, value in context.items():
                ctx[name] = self.serialize_for_template(value, name)
            return ctx

    _V.template = f"<div dj-root>{source}</div>"
    client = LiveViewTestClient(_V)
    client.mount()
    return _body(client.render())


def _liveview_legacy_render(source: str, context: dict) -> str:
    """The deprecated 1.x attribute walk: the QuerySet is a view attribute (JIT path)."""

    class _V(LiveView):
        exposure_policy = "legacy"  # legacy subject: #4241 parity under the legacy arm

        def mount(self, request, **kwargs):
            for name, value in context.items():
                setattr(self, name, value)

    _V.template = f"<div dj-root>{source}</div>"
    client = LiveViewTestClient(_V)
    client.mount()
    return _body(client.render())


NATIVE = [
    pytest.param(_backend_render, id="DjustTemplateBackend"),
    pytest.param(_liveview_render, id="LiveView-explicit-after-super"),
    pytest.param(_liveview_before_super_render, id="LiveView-explicit-before-super"),
    pytest.param(
        _liveview_serialize_for_template_render, id="LiveView-explicit-serialize_for_template"
    ),
    pytest.param(_liveview_legacy_render, id="LiveView-legacy"),
]

SOURCES = {
    "count": "[{{ qs.count }}]",
    "exists": "[{{ qs.exists }}]",
    "length": "[{{ qs|length }}]",
    "if-exists": "[{% if qs.exists %}yes{% else %}no{% endif %}]",
    "count-and-loop": "[{{ qs.count }}:{% for u in qs %}{{ u.username }},{% endfor %}]",
    "exists-and-loop": "[{{ qs.exists }}:{% for u in qs %}{{ u.username }},{% endfor %}]",
    "count-and-exists": "[{{ qs.count }}/{{ qs.exists }}]",
    # The rest of the QuerySet-method set #3801 answers for a nested ``.all``.
    "first-last": "[{{ qs.first.username }}/{{ qs.last.username }}]",
}


@pytest.fixture
def users(db):
    for name in ("u0", "u1", "u2"):
        User.objects.create(username=name)


def _full() -> dict:
    return {"qs": User.objects.all().order_by("pk")}


def _empty() -> dict:
    return {"qs": User.objects.none()}


SHAPES = {"three-rows": _full, "empty": _empty}


@pytest.mark.django_db
class TestQuerySetCountExists4241:
    @pytest.mark.parametrize("render", NATIVE)
    @pytest.mark.parametrize("shape", list(SHAPES))
    @pytest.mark.parametrize("name", list(SOURCES))
    def test_agrees_with_django(self, users, render, shape, name) -> None:
        source = SOURCES[name]
        expected = _django_render(source, SHAPES[shape]())
        got = html_lib.unescape(render(source, SHAPES[shape]())).replace("<!--dj-if-->", "")
        assert got == expected, f"{name} {shape}: Django {expected!r}, got {got!r}"

    def test_django_oracle_is_what_the_issue_expects(self, users) -> None:
        assert _django_render("{{ qs.count }}/{{ qs.exists }}", _full()) == "3/True"
        assert _django_render("{{ qs.count }}/{{ qs.exists }}", _empty()) == "0/False"


LIVEVIEWS = [p for p in NATIVE if p.id.startswith("LiveView")]


@pytest.mark.django_db
class TestNoExtraQuery4241:
    """``count`` / ``exists`` are answered from the rows the render already
    fetched: the page costs the queries ``{{ qs|length }}`` costs, no more.
    ``first`` / ``last`` cost what Django's own methods cost; pinned below."""

    @pytest.mark.parametrize("render", LIVEVIEWS)
    @pytest.mark.parametrize("shape", list(SHAPES))
    @pytest.mark.parametrize("name", ["count", "exists", "count-and-exists", "count-and-loop"])
    def test_same_queries_as_length(self, users, render, shape, name) -> None:
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as baseline:
            render(SOURCES["length"], SHAPES[shape]())
        with CaptureQueriesContext(connection) as measured:
            render(SOURCES[name], SHAPES[shape]())
        assert len(measured) == len(baseline), [q["sql"] for q in measured.captured_queries]

    @pytest.mark.parametrize("render", LIVEVIEWS)
    @pytest.mark.parametrize("shape", list(SHAPES))
    @pytest.mark.parametrize("method", ["count", "exists"])
    def test_count_exists_cost_zero_extra(self, users, render, shape, method) -> None:
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as baseline:
            render("[{% for u in qs %}{{ u.username }},{% endfor %}]", SHAPES[shape]())
        with CaptureQueriesContext(connection) as measured:
            render(
                "[{{ qs.%s }}:{%% for u in qs %%}{{ u.username }},{%% endfor %%}]" % method,
                SHAPES[shape](),
            )
        assert len(measured) - len(baseline) == 0, [q["sql"] for q in measured.captured_queries]

    @pytest.mark.parametrize("render", LIVEVIEWS)
    @pytest.mark.parametrize(
        "qs_factory, method, extra",
        [
            # Django's own behaviour, pinned: on an ordered QuerySet ``first``
            # slices ``self``, which the filled result cache answers (no
            # query); ``last`` runs on ``reverse()``, a fresh clone (one
            # query). Unordered, both run on an ``order_by("pk")`` clone.
            pytest.param(lambda: User.objects.all().order_by("pk"), "first", 0, id="ordered-first"),
            pytest.param(lambda: User.objects.all().order_by("pk"), "last", 1, id="ordered-last"),
            pytest.param(lambda: User.objects.all(), "first", 1, id="unordered-first"),
            pytest.param(lambda: User.objects.all(), "last", 1, id="unordered-last"),
        ],
    )
    def test_first_last_query_as_django_does(
        self, users, render, qs_factory, method, extra
    ) -> None:
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        loop = "[{% for u in qs %}{{ u.username }},{% endfor %}]"
        with CaptureQueriesContext(connection) as baseline:
            render(loop, {"qs": qs_factory()})
        with CaptureQueriesContext(connection) as measured:
            render("[{{ qs.%s.username }}]" % method + loop, {"qs": qs_factory()})
        assert len(measured) - len(baseline) == extra, [q["sql"] for q in measured.captured_queries]


@pytest.mark.django_db
class TestSidecarProtections4241:
    """The QuerySet now held in the render sidecar keeps the guarded walk's
    protections: the serialization floor hides ``password`` on every row it
    yields, and ``alters_data`` methods such as ``delete`` are refused."""

    @pytest.fixture
    def secret_users(self, db):
        for name in ("u0", "u1"):
            User.objects.create_user(username=name, password="s3cret-pw")

    @pytest.mark.parametrize("render", NATIVE)
    @pytest.mark.parametrize(
        "source", ["[{{ qs.first.password }}]", "[{{ qs.0.password }}]", "[{{ qs.last.password }}]"]
    )
    def test_password_is_floored(self, secret_users, render, source) -> None:
        got = html_lib.unescape(render(source + "{{ qs.count }}", _full()))
        assert "[]" in got, got
        assert "pbkdf2" not in got and "s3cret" not in got, got

    @pytest.mark.parametrize("render", NATIVE)
    def test_delete_is_refused(self, secret_users, render) -> None:
        render("[{{ qs.delete }}][{{ qs.count }}]", _full())
        assert User.objects.count() == 2

    @pytest.mark.parametrize("render", LIVEVIEWS)
    def test_query_attribute_does_not_render_sql(self, secret_users, render) -> None:
        """Only a QuerySet the template reads a #4241 method on is held for the
        sidecar walk, so ``{{ qs.query }}`` alone cannot reach the SQL."""
        got = html_lib.unescape(render("[{{ qs.query }}]", _full()))
        assert "SELECT" not in got.upper(), got
        assert "auth_user" not in got, got
