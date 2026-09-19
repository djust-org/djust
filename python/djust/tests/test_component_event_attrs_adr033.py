"""ADR-033 S3 (D4/D5) — values are typed on the wire; an instance carries a name.

``Component.event_attrs`` is the one place a component renders the attributes
an element emits an event with: ``dj-<trigger>`` plus typed ``dj-value-*``
params the client already parses (``08-event-parsing.js``), and the
instance's ``name`` so one handler serves any number of instances.
"""

from __future__ import annotations

from typing import Any

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
        SECRET_KEY="test-secret-key-adr033-events",
        SESSION_ENGINE="django.contrib.sessions.backends.db",
        USE_TZ=True,
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
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

from djust import Component, LiveView  # noqa: E402
from djust.components.components.rating import Rating  # noqa: E402
from djust.decorators import event_handler  # noqa: E402
from djust.validation import validate_handler_params  # noqa: E402


class Chip(Component):
    template = "<i></i>"


class TestEventAttrs:
    def test_int_value_is_typed(self):
        assert Chip().event_attrs("pick", value=4) == 'dj-click="pick" dj-value-value:int="4"'

    def test_every_type_has_its_suffix(self):
        out = Chip().event_attrs(
            "pick", on=True, off=False, ratio=0.5, label="a", tags=["x", 1], skip=None
        )
        assert 'dj-value-on:bool="true"' in out
        assert 'dj-value-off:bool="false"' in out
        assert 'dj-value-ratio:float="0.5"' in out
        assert 'dj-value-label="a"' in out
        assert 'dj-value-tags:json="[&quot;x&quot;,1]"' in out
        assert "skip" not in out

    def test_underscores_become_hyphens(self):
        assert 'dj-value-item-id:int="7"' in Chip().event_attrs("pick", item_id=7)

    def test_values_and_event_are_escaped(self):
        out = Chip().event_attrs('x"><script>', value='"<b>&')
        assert "<script>" not in out and "<b>" not in out
        assert 'dj-value-value="&quot;&lt;b&gt;&amp;"' in out

    def test_a_falsy_event_renders_nothing(self):
        assert Chip().event_attrs("", value=1) == ""
        assert Chip().event_attrs(None) == ""

    def test_trigger(self):
        assert Chip().event_attrs("changed", trigger="change") == 'dj-change="changed"'

    def test_name_rides_along_only_when_set(self):
        assert "dj-value-name" not in Rating(value=1).event_attrs("set_rating", value=1)
        out = Rating(value=1, name="service").event_attrs("set_rating", value=2)
        assert out == 'dj-click="set_rating" dj-value-value:int="2" dj-value-name="service"'

    def test_a_form_field_name_is_not_an_identity(self):
        """Review 🔴1: 26 components mean the HTML field name by ``name`` and
        default it non-empty; stamping that on every trigger would hand an
        unexpected param to every pre-existing handler without ``**kwargs``."""
        from djust.components.components.date_picker import DatePicker

        html = DatePicker().render()
        assert "dj-click" in html and "dj-value-name" not in html
        assert "dj-value-name" not in DatePicker(name="when").render()

    def test_non_json_values_render_as_their_text(self):
        """Review 🔴3: a UUID / date / Decimal id rendered ``str()`` before."""
        import datetime
        import uuid
        from decimal import Decimal

        u = uuid.UUID(int=7)
        out = Chip().event_attrs("pick", id=u, day=datetime.date(2026, 9, 19), amt=Decimal("1.5"))
        assert f'dj-value-id="{u}"' in out
        assert 'dj-value-day="2026-09-19"' in out and 'dj-value-amt="1.5"' in out
        assert ":json" not in out

    def test_state_is_a_reserved_kwarg(self):
        with pytest.raises(TypeError, match="reserved"):
            Chip(state={"a": 1})

    def test_a_str_annotated_handler_still_receives_text(self):
        """Compat: a handler written for the untyped wire keeps working."""

        def pick(self: Any, value: str) -> None:
            pass

        result = validate_handler_params(pick, {"value": 4}, "pick")
        assert result["valid"], result
        assert result["coerced_params"] == {"value": "4"}
        result = validate_handler_params(pick, {"value": True}, "pick")
        assert result["coerced_params"] == {"value": "true"}

    def test_an_int_id_written_back_still_opens_the_item(self):
        """Review 🔴2: ``str(id) == self.active`` never matched an int."""
        from djust.components.components.accordion import Accordion

        acc = Accordion(items=[{"id": 1, "title": "a", "content": "x"}], active=None)
        acc.active = 1
        assert "dj-accordion__item--open" in acc.render() or "open" in acc.render()

    def test_name_is_state(self):
        r = Rating(value=1, name="service")
        assert r.state["name"] == "service"
        assert r.name == "service"

    def test_the_handler_convention_accepts_the_typed_params(self):
        def set_rating(self: Any, value: int, name: str | None = None, **kwargs: Any) -> None:
            pass

        result = validate_handler_params(set_rating, {"value": 4, "name": "service"}, "set_rating")
        assert result["valid"], result
        assert result["coerced_params"] == {"value": 4, "name": "service"}


# ------------------------------------------------------------------ #
# The formset case: one handler, three instances, identified by name
# ------------------------------------------------------------------ #


class RowsView(LiveView):
    template = (
        '<div dj-root dj-id="0">{% for r in ratings %}<section>{{ r }}</section>{% endfor %}</div>'
    )

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.ratings = [Rating(value=1, name=f"row-{i}") for i in (1, 2, 3)]

    @event_handler()
    def set_rating(self, value: int, name: str | None = None, **kwargs: Any) -> None:
        for r in self.ratings:
            if r.name == name:
                r.value = value


def _stars_per_rating(html: str) -> list[int]:
    return [chunk.count("rating-star-full") for chunk in html.split('class="rating"')[1:]]


@pytest.mark.django_db
class TestOneHandlerManyInstances:
    def test_a_click_on_the_second_changes_only_the_second(self):
        view = RowsView()
        view.mount(None)
        html = view.render_with_diff()[0]
        assert _stars_per_rating(html) == [1, 1, 1]
        view.set_rating(value=5, name="row-2")
        html = view.render_with_diff()[0]
        assert _stars_per_rating(html) == [1, 5, 1]
