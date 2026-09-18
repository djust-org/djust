"""#2899 — ``{{ v }}`` on a dict SUBCLASS spells the object, as Django does.

Django calls ``str(o)``; djust modelled every ``dict`` as an engine map and
displayed the map, so a subclass with its own ``__str__`` / ``__repr__``
(``QueryDict``, ``OrderedDict``, ``defaultdict``, a user class) rendered as
``{...}`` — the dict-side counterpart of #2704. A subclass that inherits the
dict spelling (``TypedState``, a bare ``class D(dict)``) keeps crossing as a
map: its ``str()`` IS the map repr.

Every cell here is measured against Django's engine on the same value, on
the bare ``render_template`` path and on the real LiveView HTTP path.
"""

from __future__ import annotations

import collections
from typing import Any, Dict

import django
import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.sessions",
        ],
        SECRET_KEY="test-2899",
        SESSION_ENGINE="django.contrib.sessions.backends.cache",
        USE_TZ=True,
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {"builtins": ["djust.templatetags.live_tags"]},
            }
        ],
    )
    django.setup()

from django.contrib.sessions.backends.cache import SessionStore  # noqa: E402
from django.http import QueryDict  # noqa: E402
from django.template import Context, Engine  # noqa: E402
from django.test import RequestFactory  # noqa: E402
from django.utils.safestring import mark_safe  # noqa: E402

from djust import LiveView  # noqa: E402
from djust._rust import crosses_as_encoded, render_template  # noqa: E402
from djust.components.mixins.base import TypedState  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402


class DictLike(dict):
    def __str__(self) -> str:
        return mark_safe("<b>DICTLIKE</b>")


class Strs(dict):
    def __str__(self) -> str:
        return "STRS"


class ReprOnly(dict):
    def __repr__(self) -> str:
        return "ReprOnly(" + dict.__repr__(self) + ")"


class Inherits(dict):
    """No spelling of its own — ``str()`` is the map repr."""


class TS(TypedState):
    active: str = "a"


def _django(template: str, ctx: Dict[str, Any]) -> str:
    return Engine().from_string(template).render(Context(ctx))


def _rust(template: str, ctx: Dict[str, Any]) -> str:
    return render_template(template, ctx)


VALUES = {
    "dictlike": lambda: DictLike(a=1),
    "strs": lambda: Strs(a=1),
    "repr-only": lambda: ReprOnly(a=1),
    "querydict": lambda: QueryDict("q=djust&page=2&tag=rust"),
    "ordered": lambda: collections.OrderedDict(a=1, b=2),
    "defaultdict": lambda: collections.defaultdict(int, a=1),
    "inherits": lambda: Inherits(a=1),
    "typedstate": lambda: TS(active="z"),
    "plain": lambda: {"a": 1},
}


@pytest.mark.parametrize("name", sorted(VALUES))
def test_bare_display_matches_django(name):
    make = VALUES[name]
    assert _rust("{{ x }}", {"x": make()}) == _django("{{ x }}", {"x": make()})


@pytest.mark.parametrize("name", sorted(VALUES))
def test_item_lookup_length_and_truth_match_django(name):
    """A carrier must still answer the mapping cells Django answers."""
    make = VALUES[name]
    key = "q" if name == "querydict" else ("active" if name == "typedstate" else "a")
    template = "{{ x." + key + " }}|{{ x|length }}|{% if x %}T{% else %}F{% endif %}"
    assert _rust(template, {"x": make()}) == _django(template, {"x": make()})


@pytest.mark.parametrize("name", sorted(VALUES))
def test_json_script_spells_the_mapping_like_djangos_encoder(name):
    """``json_script`` is ``json.dumps(o, cls=DjangoJSONEncoder)``, which
    serializes a dict subclass as the MAPPING — the carrier must not hand it
    the display string."""
    make = VALUES[name]
    t = '{{ x|json_script:"d" }}'
    assert _rust(t, {"x": make()}) == _django(t, {"x": make()})


def test_querydict_last_value_and_arity_cells_hold():
    """#2556's cells survive the QueryDict crossing as itself."""
    qd = QueryDict("a=1&a=2&page=3")
    t = "{{ qd.a }}|{{ qd.page|add:1 }}|{% if qd.page == '3' %}yes{% endif %}"
    assert _rust(t, {"qd": qd}) == _django(t, {"qd": qd}) == "2|4|yes"


def test_predicate_agrees_with_conversion():
    assert crosses_as_encoded(DictLike(a=1)) is True
    assert crosses_as_encoded(collections.OrderedDict(a=1)) is True
    assert crosses_as_encoded(Inherits(a=1)) is False
    assert crosses_as_encoded(TS()) is False
    assert crosses_as_encoded({"a": 1}) is False


def test_normalize_keeps_a_self_spelling_subclass_for_the_render_path():
    assert normalize_django_value(Inherits(a=1)) == {"a": 1}
    assert normalize_django_value(TS(active="z")) == {"active": "z"}
    kept = normalize_django_value(DictLike(a=1))
    assert isinstance(kept, DictLike)
    # The session boundary cannot take the object.
    saved = normalize_django_value(DictLike(a=1), state_roundtrip=True)
    assert type(saved) is dict and saved == {"a": 1}
    # A MultiValueDict keeps #2556's last-value map on this path in BOTH
    # modes — it must survive a state-backend round trip. Raw, it still spells
    # itself (the `request.GET` cell of the LiveView test below).
    assert normalize_django_value(QueryDict("a=1&a=2")) == {"a": "2"}
    assert normalize_django_value(QueryDict("a=1&a=2"), state_roundtrip=True) == {"a": "2"}


class Page(LiveView):
    template = (
        '<div dj-root dj-id="0">[{{ box }}][{{ box.a }}][{{ od }}][{{ od.b }}]'
        "[{{ request.GET }}][{{ request.GET.q }}][{{ ts }}][{{ ts.active }}]</div>"
    )

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.box = DictLike(a=1)
        self.od = collections.OrderedDict(a=1, b=2)
        self.ts = TS(active="z")


def test_liveview_http_path_matches_django():
    """``{{ request.GET }}`` — the issue's "ordinary thing to write" — reaches
    the renderer raw through the sidecar and spells itself. (A QueryDict
    assigned to view STATE keeps #2556's last-value map: it has to survive a
    state-backend round trip.)"""
    request = RequestFactory().get("/2899/?q=djust")
    request.session = SessionStore()
    body = Page.as_view()(request).content.decode()
    ctx = {
        "box": DictLike(a=1),
        "od": collections.OrderedDict(a=1, b=2),
        "request": request,
        "ts": TS(active="z"),
    }
    expected = _django(
        "[{{ box }}][{{ box.a }}][{{ od }}][{{ od.b }}][{{ request.GET }}][{{ request.GET.q }}]"
        "[{{ ts }}][{{ ts.active }}]",
        ctx,
    )
    assert expected in body, body
