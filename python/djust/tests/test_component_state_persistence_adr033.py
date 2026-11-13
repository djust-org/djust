"""ADR-033 S2 (D6) — a plain component's state persists like descriptor state.

``self.rating.value = 5`` used to be lost on reconnect: the session save
normalised the component to its rendered HTML and the signed back-navigation
snapshot dropped it as non-serialisable. Now every state round trip writes
``{"__djust_component__": "<module>.<class>", "state": {...}}`` and reads it
back as a component, rebuilt through its own constructor from that state.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.sessions",
        ],
        SECRET_KEY="test-secret-key-adr033-persist",
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
from djust.serialization import (  # noqa: E402
    STATE_COMPONENT_TAG,
    StateRoundtripJSONEncoder,
    decode_state_roundtrip,
    normalize_django_value,
)

_ALLOWED = "djust.tests.test_component_state_persistence_adr033"


class RatingView(LiveView):
    exposure_policy = "legacy"
    template = '<div dj-root dj-id="0">{{ rating }}</div>'

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.rating = Rating(value=4, max_stars=5)

    @event_handler()
    def set_rating(self, value: Any, **kwargs: Any) -> None:
        self.rating.value = int(value)


class Price(Component):
    template = "<i>{{ amount }}</i>"


class Strict(Component):
    """A constructor that does not swallow unknown kwargs."""

    template = "<i>{{ value }}</i>"

    def __init__(self, value: int = 0) -> None:
        super().__init__(value=value)


def _full_stars(html: str) -> int:
    return html.count("rating-star-full")


def _roundtrip(value: Any) -> Any:
    return decode_state_roundtrip(json.loads(json.dumps(value, cls=StateRoundtripJSONEncoder)))


# ------------------------------------------------------------------ #
# The tagged form and its inverse
# ------------------------------------------------------------------ #


class TestTaggedForm:
    def test_encoder_writes_state_not_html(self):
        raw = json.loads(json.dumps(Rating(value=4), cls=StateRoundtripJSONEncoder))
        assert raw[STATE_COMPONENT_TAG] == "djust.components.components.rating.Rating"
        assert raw["state"]["value"] == 4
        assert "rating-star" not in json.dumps(raw)

    def test_decode_rebuilds_the_component(self):
        back = _roundtrip(Rating(value=4))
        assert isinstance(back, Rating)
        assert back.state["value"] == 4
        assert _full_stars(back.render()) == 4

    def test_an_attribute_write_survives(self):
        r = Rating(value=4)
        r.value = 5
        assert _full_stars(_roundtrip(r).render()) == 5

    def test_nested_inside_containers_and_with_a_decimal(self):
        back = _roundtrip({"rows": [Rating(value=1), Price(amount=Decimal("19.99"))]})
        assert isinstance(back["rows"][0], Rating)
        assert back["rows"][1].state["amount"] == Decimal("19.99")

    def test_explicit_id_survives(self):
        back = _roundtrip(Rating(value=2, id="stars"))
        assert back.id == "stars"

    def test_normalize_for_the_session_carries_state_but_the_wire_carries_html(self):
        r = Rating(value=3)
        assert normalize_django_value({"rating": r}, state_roundtrip=True)["rating"][
            STATE_COMPONENT_TAG
        ]
        assert "rating-star" in normalize_django_value({"rating": r})["rating"]

    def test_an_unimported_or_foreign_class_stays_a_dict(self, caplog):
        for path in ("no.such.module.Thing", "djust.live_view.LiveView", "builtins.dict"):
            tagged = {STATE_COMPONENT_TAG: path, "state": {"value": 1}}
            assert decode_state_roundtrip(tagged) == tagged, path
        assert "leaving the tag as a dict" in caplog.text

    def test_a_refusing_constructor_stays_a_dict(self):
        tagged = {STATE_COMPONENT_TAG: f"{_ALLOWED}.Strict", "state": {"nope": 1}}
        assert decode_state_roundtrip(tagged) == tagged
        assert isinstance(decode_state_roundtrip({**tagged, "state": {"value": 3}}), Strict)

    def test_the_base_class_and_reserved_keys_are_refused(self):
        """Review 🟡5: a payload may not instantiate the abstract base with its
        own template, nor set private / configuration attributes."""
        base = {
            STATE_COMPONENT_TAG: "djust.components.base.Component",
            "state": {"template": "<i>x</i>"},
        }
        assert decode_state_roundtrip(base) == base
        back = decode_state_roundtrip(
            {
                STATE_COMPONENT_TAG: "djust.components.components.rating.Rating",
                "state": {"value": 2, "_explicit_id": "evil", "template": "<i>{{ value }}</i>"},
            }
        )
        assert isinstance(back, Rating)
        assert back._explicit_id is None and type(back).template is None
        assert "_explicit_id" not in back.state and "template" not in back.state

    def test_any_constructor_failure_is_fail_soft_and_decodes_nested_tags(self):
        """Review 🟡4: a ValueError used to propagate into the session restore."""

        class Picky(Component):
            template = "<i></i>"

            def __init__(self, value=0, **kwargs):
                if value < 0:
                    raise ValueError("no")
                super().__init__(value=value, **kwargs)

        import sys

        sys.modules[__name__].Picky = Picky  # resolvable, like a module-level class
        tagged = {
            STATE_COMPONENT_TAG: f"{_ALLOWED}.Picky",
            "state": {"value": -1, "amount": {"__djust_decimal__": "1.5"}},
        }
        back = decode_state_roundtrip(tagged)
        assert back[STATE_COMPONENT_TAG] == tagged[STATE_COMPONENT_TAG]
        assert back["state"]["amount"] == Decimal("1.5")

    def test_a_non_serialisable_state_value_skips_the_attribute_in_the_snapshot(self):
        """Review 🟡6: the snapshot path mirrors a view's own rule — skip, never ``str()``."""

        class Holder(Component):
            template = "<i></i>"

        view = RatingView()
        view.mount(None)
        view.holder = Holder(fn=lambda: 1)
        snap = view._capture_snapshot_state()
        assert "rating" in snap and "holder" not in snap

    def test_a_user_dict_that_merely_has_a_state_key_is_untouched(self):
        plain = {"state": {"a": 1}, "other": 2}
        assert decode_state_roundtrip(plain) == plain


# ------------------------------------------------------------------ #
# The three persistence paths
# ------------------------------------------------------------------ #


class TestSnapshotPaths:
    def test_a_captured_component_is_rebuilt_by_the_public_state_restore(self):
        from djust._state_restore import restore_public_state

        view = RatingView()
        view.mount(None)
        view.rating.value = 5
        snap = view._capture_snapshot_state()
        assert snap["rating"][STATE_COMPONENT_TAG].endswith(".Rating")
        fresh = RatingView()  # mount() is skipped on a session restore
        restore_public_state(fresh, decode_state_roundtrip(json.loads(json.dumps(snap))))
        assert isinstance(fresh.rating, Rating)
        assert _full_stars(fresh.render_with_diff()[0]) == 5

    def test_get_state_accepts_a_component(self):
        view = RatingView()
        view.mount(None)
        assert view.get_state()["rating"] is view.rating


class _ScopeSession:
    def __init__(self, key: str) -> None:
        self.session_key = key


async def _mount(communicator, view_path, url):
    await communicator.send_json_to({"type": "mount", "view": view_path, "url": url})
    for _ in range(8):
        frame = await communicator.receive_json_from()
        if frame.get("type") == "mount":
            return frame
    raise AssertionError("no mount frame")
