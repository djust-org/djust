"""#4069 -- a loop row's relation walk over a supplied QuerySet was empty on an
explicit view.

``exposure_policy = "explicit"`` (the default) does no JIT serialization: the
native render normalizes a supplied QuerySet into column dicts, and kept the
raw value for the render sidecar only when the template read ``count`` /
``exists`` / ``first`` / ``last`` off the name itself (#4241). A loop row's
``{{ u.groups.all }}`` reaches the sidecar through the alias ``u -> users.<N>``
and found no entry, so it rendered empty (``[][][]``, ``0;0;0;``) where legacy
and Django agree.

The fix keeps the EVALUATED ROWS (never the QuerySet) for a name whose rows the
template walks past their columns. Every row is floored like any other sidecar
value. Expected strings are derived from Django (#2727), with real ``User`` /
``Group`` rows (#1650).
"""

from __future__ import annotations

import html as html_lib
import re

import pytest
from django.contrib.auth.models import Group, User
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate

from djust.live_view import LiveView
from djust.testing import LiveViewTestClient


def _body(html: str) -> str:
    match = re.search(r"<div dj-root[^>]*>(.*)</div>", html, re.S)
    assert match is not None, html
    return html_lib.unescape(match.group(1))


def _render(source: str, context_factory) -> str:
    class _V(LiveView):
        exposure_policy = "explicit"

        def get_context_data(self, **kwargs):
            ctx = super().get_context_data(**kwargs)
            ctx.update(context_factory())
            return ctx

    _V.template = f"<div dj-root>{source}</div>"
    client = LiveViewTestClient(_V)
    client.mount()
    return _body(client.render())


def _django(source: str, context: dict) -> str:
    return DjangoTemplate(source).render(DjangoContext(dict(context)))


@pytest.fixture
def world(db):
    staff = Group.objects.create(name="staff")
    ops = Group.objects.create(name="ops")
    for name, groups in (("u0", (staff, ops)), ("u1", (staff,)), ("u2", ())):
        User.objects.create(username=name, password="pbkdf2_sha256$secret$hash").groups.add(*groups)


def _users() -> dict:
    return {"users": User.objects.all().order_by("pk")}


def _user_list() -> dict:
    return {"users": list(User.objects.all().order_by("pk"))}


def _groups() -> dict:
    return {"groups": Group.objects.all().order_by("pk")}


SHAPES = [
    pytest.param(
        "{% for u in users %}[{% for g in u.groups.all %}{{ g.name }},{% endfor %}]{% endfor %}",
        _users,
        id="for-over-relation-all",
    ),
    pytest.param(
        "{% for u in users %}{{ u.groups.all|length }};{% endfor %}", _users, id="all-length"
    ),
    pytest.param("{% for u in users %}{{ u.groups.count }};{% endfor %}", _users, id="count"),
    pytest.param("{% for u in users %}{{ u.groups }};{% endfor %}", _users, id="bare-manager"),
    pytest.param("{% for u in users %}{{ u.username }};{% endfor %}", _users, id="column"),
    pytest.param("{{ users.0.groups.all.0.name }}", _users, id="index-walk"),
    pytest.param(
        "{% for g in groups %}{{ g.name }}={% for u in g.user_set.all %}{{ u.username }},"
        "{% endfor %};{% endfor %}",
        _groups,
        id="reverse-accessor",
    ),
    pytest.param(
        "{% for u in users %}{% if u.groups.all %}y{% else %}n{% endif %}{% endfor %}",
        _users,
        id="if-relation",
    ),
    pytest.param("{% for u in users %}{{ u.groups.count }};{% endfor %}", _user_list, id="list"),
]


@pytest.mark.parametrize("source,factory", SHAPES)
def test_explicit_relation_walk_matches_django(world, source, factory):
    expected = _django(source, factory())
    assert expected  # non-vacuous oracle
    assert _render(source, factory) == expected


def test_explicit_capture_holds_rows_not_queryset(world):
    source = "{% for u in users %}{{ u.groups.count }}{% endfor %}|{{ users.query }}|"
    out = _render(source, _users)
    assert out.startswith("210|"), out
    assert "SELECT" not in out.upper(), out


def test_explicit_relation_walk_keeps_the_floor(world):
    source = (
        "{% for u in users %}<{{ u.password }}{{ u.groups.first.user_set.first.password }}"
        "{{ u.groups.count }}>{% endfor %}"
    )
    out = _render(source, _users)
    assert "pbkdf2" not in out and "secret" not in out, out
    assert out == "<2><1><0>", out


def test_explicit_relation_walk_query_count(world):
    """The outer QuerySet is evaluated once: capturing its rows reuses the
    result cache the normalize pass fills. A column-only loop over the same
    QuerySet (no capture) is the baseline; the walk adds only the per-row
    relation queries Django itself issues."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    def _outer_selects(source: str) -> int:
        with CaptureQueriesContext(connection) as ctx:
            _render(source, _users)
        return sum(
            1
            for q in ctx.captured_queries
            if 'FROM "auth_user"' in q["sql"] and "auth_user_groups" not in q["sql"]
        )

    baseline = _outer_selects("{% for u in users %}{{ u.username }}{% endfor %}")
    walked = _outer_selects("{% for u in users %}{{ u.groups.count }}{% endfor %}")
    assert walked == baseline, (baseline, walked)


# -- train/2.0-ah review S1: the shapes the row capture alone did not answer ----------------------
#
# A filtered operand has no sidecar loop alias (#2504) and a QuerySet inside a container has no
# top-level capture, so an explicit view rendered these empty. The explicit sync now runs the JIT
# over the walked names (as legacy does), so each row dict carries the relation values.


def _nested_users() -> dict:
    return {"page": {"users": User.objects.all().order_by("pk")}}


def _rows_of_dicts() -> dict:
    return {"rows": [{"label": "all", "users": User.objects.all().order_by("pk")}]}


FILTERED_SHAPES = [
    pytest.param(
        "{% for u in users|slice:':2' %}{{ u.groups.count }};{% endfor %}", _users, id="slice"
    ),
    pytest.param(
        "{% for u in users|dictsort:'username' %}{{ u.groups.count }};{% endfor %}",
        _users,
        id="dictsort",
    ),
    pytest.param(
        "{% with first=users|first %}{{ first.groups.count }}{% endwith %}", _users, id="with-first"
    ),
    pytest.param(
        "{% for u in users|slice:':2' %}{{ u.groups.count }};{% endfor %}",
        _user_list,
        id="slice-list",
    ),
    pytest.param(
        "{% for u in page.users %}[{% for g in u.groups.all %}{{ g.name }},{% endfor %}]"
        "{% endfor %}",
        _nested_users,
        id="queryset-in-dict",
    ),
    pytest.param(
        "{% for r in rows %}{% for u in r.users %}{{ u.groups.count }};{% endfor %}{% endfor %}",
        _rows_of_dicts,
        id="queryset-in-list-of-dicts",
    ),
]


@pytest.mark.parametrize("source,factory", FILTERED_SHAPES)
def test_explicit_filtered_and_nested_shapes_match_django(world, source, factory):
    expected = _django(source, factory())
    assert expected.strip(";[],") not in ("", "0")  # non-vacuous oracle
    assert _render(source, factory) == expected


def test_explicit_jit_rows_keep_the_floor(world):
    """The JIT rows on an explicit view are floored as legacy's are: no hash through a
    filtered operand's relation walk. A pin, no gate-off: the JIT's floor is legacy's,
    pinned by test_queryset_all_followups_3788 (now explicit)."""
    source = (
        "{% for u in users|slice:':2' %}{{ u.password }}|"
        "{{ u.groups.first.user_set.first.password }};{% endfor %}"
    )
    got = _render(source, _users)
    assert "pbkdf2" not in got and "secret" not in got, got
