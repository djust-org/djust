"""#2667 — ``submit_form()`` must not raise ``AttributeError: no attribute
'form_data'`` on a ``FormMixin`` view whose ``FormMixin.mount()`` never ran.

The reporter's symptom is an error frame on the FIRST submit of an empty form,
with the workaround "declare ``form_data = {}`` in ``mount()``". Reproduced
through the real path: a ``WebsocketCommunicator`` against ``LiveViewConsumer``,
one ``mount`` then one ``submit_form`` event.

Two ordinary authoring mistakes skip ``FormMixin.mount()``, and NEITHER raises
at mount time — the page renders, and only the first event blows up:

* ``OwnMountNoSuper`` — a ``mount()`` override without
  ``super().mount(request, **kwargs)``
* ``ReversedMro``     — bases declared ``(LiveView, FormMixin)``, so
  ``LiveView.mount`` wins the MRO

``validate_field`` already carried an inline ``hasattr(self, "form_data")``
guard; ``submit_form`` and the getters had drifted without one (#1646). The fix
routes every reader through one ``_ensure_form_state()`` chokepoint.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

import pytest
from django import forms
from django.test import override_settings

from djust import LiveView
from djust.forms import FormMixin

pytestmark = [pytest.mark.django_db]

MOD = __name__


class Contact2667Form(forms.Form):
    name = forms.CharField(max_length=100, required=True)
    email = forms.EmailField(required=True)
    topic = forms.ChoiceField(choices=[("a", "A"), ("b", "B")], required=False)


def _tpl(name: str) -> str:
    return (
        f'<div dj-view="{MOD}.{name}" dj-id="0"><form dj-submit="submit_form">'
        '<input name="name" value="{{ form_data.name }}"/>'
        '<button type="submit">Go</button></form>'
        "<p>{{ error_message }}</p></div>"
    )


class PlainContact2667(FormMixin, LiveView):
    """Control: the correct shape — no user ``mount()`` at all."""

    form_class = Contact2667Form
    template = _tpl("PlainContact2667")

    def form_invalid(self, form: Any) -> None:
        self.error_message = "please fix the errors"


class OwnMountNoSuper2667(FormMixin, LiveView):
    """A ``mount()`` override that forgets ``super().mount()``."""

    form_class = Contact2667Form
    template = _tpl("OwnMountNoSuper2667")

    def mount(self, request: Any, **kwargs: Any) -> None:
        # No super().mount(...) — this is the reporter's shape.
        self.title = "Contact"

    def form_invalid(self, form: Any) -> None:
        self.error_message = "please fix the errors"


class ReversedMro2667(LiveView, FormMixin):
    """Bases in the wrong order — ``LiveView.mount`` wins the MRO."""

    form_class = Contact2667Form
    template = _tpl("ReversedMro2667")

    def form_invalid(self, form: Any) -> None:
        self.error_message = "please fix the errors"


UNMOUNTED = ["OwnMountNoSuper2667", "ReversedMro2667"]
ALL_VIEWS = ["PlainContact2667", *UNMOUNTED]

_TERMINAL = {"patch", "html_update", "error", "noop"}


async def _drive(cls_name: str) -> Dict[str, Any]:
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    connected, _ = await comm.connect()
    assert connected
    await comm.receive_json_from(timeout=5)
    out: Dict[str, Any] = {}
    try:
        await comm.send_json_to({"type": "mount", "view": f"{MOD}.{cls_name}", "url": "/x/"})
        out["mount"] = await comm.receive_json_from(timeout=5)
        await comm.send_json_to(
            {"type": "event", "event": "submit_form", "params": {}, "ref": 2667}
        )
        frames: List[Dict[str, Any]] = []
        for _ in range(6):
            frame = await comm.receive_json_from(timeout=5)
            frames.append(frame)
            if frame.get("type") in _TERMINAL:
                break
        out["frames"] = frames
    finally:
        await comm.disconnect()
    return out


def _run(cls_name: str) -> Dict[str, Any]:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_drive(cls_name))
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _env() -> Any:
    pytest.importorskip("channels")
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD], DEBUG=False):
        yield


class TestSubmitFormWithoutMount:
    """The reported bug, through the real WebSocket event path."""

    @pytest.mark.parametrize("cls_name", ALL_VIEWS)
    def test_submit_empty_form_does_not_error(self, cls_name: str) -> None:
        out = _run(cls_name)
        assert out["mount"].get("type") == "mount", out["mount"]
        kinds = [f.get("type") for f in out["frames"]]
        assert "error" not in kinds, (
            f"{cls_name}: submitting an empty form produced an error frame "
            f"({out['frames']}) — this is #2667 ('no attribute form_data')."
        )

    @pytest.mark.parametrize("cls_name", UNMOUNTED)
    def test_unmounted_view_warns(self, cls_name: str, caplog: pytest.LogCaptureFixture) -> None:
        """Self-healing is not silent."""
        with caplog.at_level(logging.WARNING, logger="djust.forms"):
            _run(cls_name)
        warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("FormMixin.mount() never ran" in m and cls_name in m for m in warnings), (
            f"expected a FormMixin.mount warning naming {cls_name}; got {warnings}"
        )

    def test_the_two_shapes_get_DIFFERENT_advice(self, caplog: pytest.LogCaptureFixture) -> None:
        """One generic sentence is wrong for whichever view is reading it.

        ``ReversedMro2667`` has no ``mount()`` at all, so "call super().mount()
        from your mount()" sends its author hunting for a method they never
        wrote. The first version of this fix emitted a byte-identical warning
        for both shapes and three artifacts claimed it "names the actual
        mistake" — it did not.
        """
        msgs = {}
        for cls_name in UNMOUNTED:
            caplog.clear()
            with caplog.at_level(logging.WARNING, logger="djust.forms"):
                _instantiate(cls_name).submit_form()
            msgs[cls_name] = " ".join(
                r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING
            )

        own, mro = msgs["OwnMountNoSuper2667"], msgs["ReversedMro2667"]
        assert own != mro, f"both shapes got the SAME advice: {own!r}"

        # The class that DOES override mount() is told to call super().
        assert "super().mount(request, **kwargs)" in own, own
        assert "OwnMountNoSuper2667 overrides mount()" in own, own

        # The class that does NOT override mount() must NOT be told to edit one,
        # and the advice must name the bases the author actually TYPED — an
        # earlier version named ComponentMixin, the MRO winner, which does not
        # appear anywhere in their source.
        assert "overrides mount()" not in mro, mro
        assert "does not define mount()" in mro, mro
        assert "must come FIRST" in mro, mro
        assert "(LiveView, FormMixin) to (FormMixin, LiveView)" in mro, mro

    @pytest.mark.parametrize("cls_name", ALL_VIEWS)
    def test_every_reactive_attribute_exists_after_submit(self, cls_name: str) -> None:
        """The repair fills the whole documented set, not just ``form_data``."""
        view = _instantiate(cls_name)
        view.submit_form()
        for attr in (
            "form_data",
            "form_choices",
            "form_errors",
            "field_errors",
            "is_valid",
            "success_message",
            "error_message",
        ):
            assert hasattr(view, attr), f"{cls_name}: missing {attr} after submit_form()"
        # form_choices is the one reset_form() does not write — pin it here.
        assert view.form_choices["topic"] == [("a", "A"), ("b", "B")]

    @pytest.mark.parametrize(
        "call",
        [
            lambda v: v.submit_form(),
            lambda v: v.validate_field(field="name", value="x"),
            lambda v: v.reset_form(),
            lambda v: v.get_field_value("name"),
            lambda v: v.get_field_errors("name"),
            lambda v: v.has_field_errors("name"),
        ],
        ids=[
            "submit_form",
            "validate_field",
            "reset_form",
            "get_field_value",
            "get_field_errors",
            "has_field_errors",
        ],
    )
    def test_each_entry_point_survives_an_unmounted_view(self, call: Any) -> None:
        """Every FormMixin entry point that reads form state, not just the cited one.

        ``validate_field`` had the guard and ``submit_form`` did not; the point
        of the fix is that no entry point is left out (#1646).
        """
        view = _instantiate("OwnMountNoSuper2667")
        call(view)  # must not raise AttributeError

        # `form_data` alone does NOT cover every row: `reset_form()` assigns
        # `self.form_data = {}` itself, so that assertion passes whether or not
        # the chokepoint ran — the review found removing reset_form's call left
        # this file 14/14 green. `form_choices` is the attribute reset_form
        # never writes, so it is what actually proves the call happened.
        for attr in ("form_data", "form_choices", "field_errors", "form_errors"):
            assert hasattr(view, attr), f"missing {attr}"
        assert view.form_choices["topic"] == [("a", "A"), ("b", "B")]


def _instantiate(cls_name: str) -> Any:
    """A view built the way the framework builds one, with mount() as declared."""
    from django.test import RequestFactory

    cls = globals()[cls_name]
    view = cls()
    view.mount(RequestFactory().get("/x/"))
    return view


class TestResetFormChokepoint:
    """`reset_form()`'s `_ensure_form_state()` call, isolated (#2690 review).

    `reset_form` reassigns every reactive attribute EXCEPT `form_choices`, so it
    cannot raise on an unmounted view and every "did it not blow up" assertion
    passes with the chokepoint removed. This is the one thing that goes red.
    """

    def test_reset_form_on_an_unmounted_view_populates_form_choices(self) -> None:
        view = _instantiate("OwnMountNoSuper2667")
        view.reset_form()
        assert hasattr(view, "form_choices"), (
            "reset_form() left form_choices missing — it writes every other "
            "reactive attribute itself, so its _ensure_form_state() call is the "
            "only thing that can populate this one."
        )
        assert view.form_choices["topic"] == [("a", "A"), ("b", "B")]

    def test_reset_form_choices_match_a_correctly_mounted_view(self) -> None:
        """The repaired value is the real one, not merely present."""
        healthy = _instantiate("PlainContact2667")
        repaired = _instantiate("OwnMountNoSuper2667")
        repaired.reset_form()
        assert repaired.form_choices == healthy.form_choices
