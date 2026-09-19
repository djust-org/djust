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
import pytest
from asgiref.sync import sync_to_async
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
    template = '<div dj-root dj-id="0">{{ rating }}</div>'
    enable_state_snapshot = True

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

    def test_a_user_dict_that_merely_has_a_state_key_is_untouched(self):
        plain = {"state": {"a": 1}, "other": 2}
        assert decode_state_roundtrip(plain) == plain


# ------------------------------------------------------------------ #
# The three persistence paths
# ------------------------------------------------------------------ #


class TestSnapshotPaths:
    def test_signed_snapshot_captures_and_restores(self):
        view = RatingView()
        view.mount(None)
        view.rating.value = 5
        snap = view._capture_snapshot_state(strict=True)
        assert snap["rating"][STATE_COMPONENT_TAG].endswith(".Rating")
        fresh = RatingView()  # mount() is skipped on a signed-snapshot restore
        fresh._restore_snapshot(decode_state_roundtrip(json.loads(json.dumps(snap))))
        assert isinstance(fresh.rating, Rating)
        assert _full_stars(fresh.render_with_diff()[0]) == 5

    def test_get_state_accepts_a_component(self):
        view = RatingView()
        view.mount(None)
        assert view.get_state()["rating"] is view.rating

    def test_session_component_state_is_the_state_dict(self):
        view = RatingView()
        view.mount(None)
        view.rating.value = 2
        saved = view._extract_component_state(view.rating)
        assert saved == view.rating.state
        other = RatingView()
        other.mount(None)
        other._restore_component_state(other.rating, json.loads(json.dumps(saved)))
        assert other.rating.state["value"] == 2
        assert _full_stars(other.rating.render()) == 2


class _ScopeSession:
    def __init__(self, key: str) -> None:
        self.session_key = key


async def _mount(communicator, view_path, url):
    await communicator.send_json_to({"type": "mount", "view": view_path, "url": url})
    for _ in range(8):
        frame = await communicator.receive_json_from(timeout=3)
        if frame.get("type") == "mount":
            return frame
    raise AssertionError("no mount frame")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_reconnect_restores_the_written_value(generous_save_timeout):
    """ADR-033 verification: reconnect after ``self.rating.value = 5`` and the
    restored view renders five stars. Two sockets over one session; the
    second mount is a session restore, so ``mount()`` does not run and the
    component must come back from the saved state."""
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import override_settings

    from djust.websocket import LiveViewConsumer

    def _create_session():
        s = SessionStore()
        s.create()
        return s.session_key

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED]):
        session_key = await sync_to_async(_create_session)()

        first = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        first.scope["session"] = _ScopeSession(session_key)
        assert (await first.connect())[0]
        await first.receive_json_from(timeout=2)
        mounted = await _mount(first, f"{_ALLOWED}.RatingView", "/rating/")
        assert _full_stars(mounted["html"]) == 4
        await first.send_json_to(
            {"type": "event", "event": "set_rating", "params": {"value": "5"}, "ref": 1}
        )
        updated = await first.receive_json_from(timeout=5)
        assert updated.get("type") in ("patch", "html_update", "html_recovery"), updated
        await first.disconnect()

        saved = await SessionStore(session_key=session_key).aget("liveview_/rating/", None)
        assert saved and saved["rating"][STATE_COMPONENT_TAG].endswith(".Rating"), saved
        assert saved["rating"]["state"]["value"] == 5

        second = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        second.scope["session"] = _ScopeSession(session_key)
        assert (await second.connect())[0]
        await second.receive_json_from(timeout=2)
        remounted = await _mount(second, f"{_ALLOWED}.RatingView", "/rating/")
        try:
            assert _full_stars(remounted.get("html", "")) == 5, remounted
        finally:
            await second.disconnect()
