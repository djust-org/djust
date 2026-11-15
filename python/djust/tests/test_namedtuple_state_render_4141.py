"""A namedtuple held in LiveView state keeps its field names on the render path (#4141).

``normalize_django_value`` turned every tuple into a list before the template saw it, so a
namedtuple held on the view lost its fields: ``{{ cols.0.key }}`` was empty and a
``{% data_table %}`` whose columns are namedtuples drew them as lists once the view rendered from
state (after an event). A direct render was right, which is why it read as a tag bug (#3895).

Every page below is compared with Django's own rendering of the same template and the same
context, on the first paint, after an event over the WebSocket, after a reconnect, and after a
fallback POST; with both exposure policies.
"""

from __future__ import annotations

import collections
import json
import re
from pathlib import Path
from typing import Any, List

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.template import Context, Engine
from django.test import RequestFactory

import djust.components
from djust import LiveView, _automatic_state, event_handler
from djust.serialization import normalize_django_value
from djust.tests import _per_turn_harness as harness

MODULE = __name__

Col = collections.namedtuple("Col", "key label width")
Point = collections.namedtuple("Point", ["x", "y"])

BODY = (
    "<p>{{ cols.0.key }}|{{ cols.1.label }}|{{ cols.0.0 }}|{{ cols|length }}"
    "|{% for c in cols %}{{ c.key }}:{{ c.label }},{% endfor %}"
    "|{{ point.x }},{{ point.y }}|{{ point.1 }}|{% for v in point %}{{ v }};{% endfor %}"
    "|{{ nested.first.label }}|{{ count }}</p>"
    "{% data_table rows=rows columns=cols %}"
)


def _cols() -> List[Col]:
    return [Col("name", "Name", "10px"), Col("age", "Age", "")]


def _context(count: int) -> dict:
    cols = _cols()
    return {
        "cols": cols,
        "rows": [{"id": 1, "name": "Ann", "age": 3}],
        "point": Point(4, 5),
        "nested": {"first": cols[0]},
        "count": count,
    }


class _Columns(LiveView):
    # A shared base, never routed: each subclass below states the policy it tests. Stated here
    # too so the module-level exposure guard (tests/unit/test_exposure_policy_pin_guard.py) holds.
    exposure_policy = "explicit"
    template = "<div dj-root>" + BODY + "</div>"

    def mount(self, request, **kwargs):
        for name, value in _context(0).items():
            setattr(self, name, value)

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1


class ExplicitColumns(_Columns):
    exposure_policy = "explicit"

    def get_context_data(self, **kwargs):
        return {name: getattr(self, name) for name in _context(0)}


class LegacyColumns(_Columns):
    exposure_policy = "legacy"


VIEWS = [ExplicitColumns, LegacyColumns]


_DJANGO = Engine(
    libraries={"djust_components": "djust.components.templatetags.djust_components"},
    dirs=[str(Path(djust.components.__file__).parent / "templates")],
)


def _django(count: int) -> str:
    return _DJANGO.from_string("{% load djust_components %}" + BODY).render(
        Context(_context(count))
    )


def _paragraph(html: str) -> str:
    found = re.search(r"<p[^>]*>(.*?)</p>", html, re.S)
    assert found, html[:600]
    return found.group(1)


def _header(html: str) -> List[str]:
    return [
        re.sub(r"<[^>]+>|\s+", "", th) for th in re.findall(r"<th\b[^>]*>(.*?)</th>", html, re.S)
    ]


def _expected(count: int) -> tuple:
    page = _django(count)
    return _paragraph(page), _header(page)


def test_the_reference_reads_the_fields():
    """Control: Django renders the field names, so an agreeing page is not vacuous."""
    paragraph, header = _expected(0)
    assert paragraph.startswith("name|Age|name|2|name:Name,age:Age,|4,5|5|4;5;|Name|0"), paragraph
    assert header == ["Name", "Age"], header


@pytest.fixture(autouse=True)
def _one_tab(monkeypatch):
    monkeypatch.setattr(_automatic_state, "new_tab_id", lambda: harness.TAB)


def _request(method: str, key: Any, event: str = "") -> Any:
    factory = RequestFactory()
    if method == "GET":
        request = factory.get(harness.PATH)
    else:
        request = factory.post(
            harness.PATH,
            data=json.dumps({}),
            content_type="application/json",
            HTTP_X_DJUST_EVENT=event,
            HTTP_X_DJUST_TAB=harness.TAB,
        )
    request.user = AnonymousUser()
    request.tenant = None
    request.session = SessionStore(key)
    if key is None:
        request.session.create()
    return request


@pytest.mark.django_db
@pytest.mark.parametrize("view_class", VIEWS, ids=lambda c: c.exposure_policy)
def test_the_first_paint_and_a_fallback_post_keep_the_fields(view_class):
    first = _request("GET", None)
    page = view_class().get(first)
    assert page.status_code == 200
    html = page.content.decode()
    assert (_paragraph(html), _header(html)) == _expected(0)
    key = first.session.session_key
    for count in (1, 2):
        answer = view_class().post(_request("POST", key, "inc"))
        assert answer.status_code == 200, answer.content
        body = json.loads(answer.content)
        html = body.get("html") or ""
        if not html:  # a patch answer: render the view's current state instead
            pytest.fail("fallback POST answered without html: %r" % sorted(body))
        # Legacy saves nothing between requests: each stateless POST mounts, then one event.
        expected = count if view_class is ExplicitColumns else 1
        assert (_paragraph(html), _header(html)) == _expected(expected)


async def _rendered_after(comm: Any) -> str:
    """The page as the view renders it now, through the socket's own full-HTML path."""
    from djust.tests._ws_frames import has_frame, receive_until

    await comm.send_json_to({"type": "request_html"})
    frames = await receive_until(comm, has_frame("html_recovery", "html_update"), what="html")
    return frames[-1]["html"]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("view_class", VIEWS, ids=lambda c: c.exposure_policy)
async def test_an_event_and_a_reconnect_keep_the_fields(view_class):
    key = await harness.new_session_key_async()
    with harness.ws_settings(MODULE):
        comm, mount = await harness.mount_view(view_class, key)
        try:
            assert (_paragraph(mount["html"]), _header(mount["html"])) == _expected(0)
            frame = await harness.event(comm, "inc")
            assert frame["type"] != "error", frame
            html = await _rendered_after(comm)
            assert (_paragraph(html), _header(html)) == _expected(1)
        finally:
            await comm.disconnect()
        again, mount = await harness.mount_view(view_class, key)
        try:
            # Explicit: the saved count (1) over mount(); legacy saves nothing (0).
            count = 1 if view_class is ExplicitColumns else 0
            assert (_paragraph(mount["html"]), _header(mount["html"])) == _expected(count)
            frame = await harness.event(again, "inc")
            assert frame["type"] != "error", frame
            html = await _rendered_after(again)
            assert (_paragraph(html), _header(html)) == _expected(count + 1)
        finally:
            await again.disconnect()


# ---------------------------------------------------------------------------
# What crosses for plain tuples and lists is unchanged
# ---------------------------------------------------------------------------


def test_a_namedtuple_keeps_its_type_and_fields_with_normalized_members():
    from decimal import Decimal
    from uuid import UUID

    out = normalize_django_value([Col("k", UUID(int=1), Decimal("1.5"))])
    assert type(out) is list and type(out[0]) is Col
    assert out[0] == Col("k", "00000000-0000-0000-0000-000000000001", Decimal("1.5"))


@pytest.mark.parametrize(
    "value",
    [(1, "a", [2, (3,)]), [1, (2, 3)], ((),), [], ()],
    ids=["tuple", "list", "nested-empty", "empty-list", "empty-tuple"],
)
def test_plain_tuples_and_lists_still_cross_as_lists(value):
    out = normalize_django_value(value)
    assert type(out) is list
    assert json.dumps(out) == json.dumps(value)


def test_a_namedtuple_at_the_state_boundary_is_still_a_list():
    out = normalize_django_value(Col("k", "L", ""), state_roundtrip=True)
    assert type(out) is list and out == ["k", "L", ""]


from django.urls import path as _path  # noqa: E402

urlpatterns = [_path("cols-%s/" % v.exposure_policy, v.as_view()) for v in VIEWS]
