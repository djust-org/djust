"""What a client may put into a form's submitted data over a WebSocket is bounded (#3834).

Django bounds an HTTP POST (``DATA_UPLOAD_MAX_NUMBER_FIELDS`` = 1000, ``DATA_UPLOAD_MAX_MEMORY_SIZE`` =
2.5 MB). A WebSocket event is a JSON frame the client wrote, bounded only by ``max_message_size`` (64 KB;
``0`` removes it), and a form's data is not one frame: ``FormMixin`` keeps it in ``form_data`` for the life
of the view, adds one client-named key per ``validate_field`` event, merges every key of a ``submit_form``
event, and binds all of it to the form on every event.

The first class is the evidence: through the real ``WebsocketCommunicator`` path, with the new limits
switched off (which is what the code did before), a frame well under the frame limit puts thousands of
fields into ``form_data`` and a run of small events grows it without end. The rest assert what the new
limits do, on every path that lets a client name a key of a bound form: ``FormMixin``, ``WizardMixin``,
the admin form view and a ``params_form``. Counts and sizes, never time.

Refs #3834, #3830, #3833, #1646, #1468.
"""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from django import forms
from django.conf import global_settings
from django.test import RequestFactory, override_settings

from djust import LiveView, _form_data_bounds
from djust.config import config as djust_config
from djust.forms import FormMixin
from djust.wizard import WizardMixin

pytestmark = [pytest.mark.django_db, pytest.mark.pr_tier]

MOD = __name__


def limits(fields=None, size=None, **extra):
    """A LIVEVIEW_CONFIG that sets the two limits (``None`` keeps the default) and no rate limit."""
    cfg: Dict[str, Any] = {"rate_limit": {"rate": 10**9, "burst": 10**9}, **extra}
    if fields is not None:
        cfg["max_form_fields"] = fields
    if size is not None:
        cfg["max_form_data_size"] = size
    return override_settings(LIVEVIEW_CONFIG=cfg)


UNLIMITED = {"max_form_fields": 0, "max_form_data_size": 0}
#: ``FormMixin.mount()`` seeds ``form_data`` with one key for each field the form declares, and those
#: count: ``Contact`` declares one.
DECLARED = {"name": ""}


class Contact(forms.Form):
    name = forms.CharField(required=False)


class WsContact(FormMixin, LiveView):
    form_class = Contact
    last = None
    template = (
        f'<div dj-view="{MOD}.WsContact" dj-id="0"><form dj-submit="submit_form"></form>'
        "<p>{{ form_data|length }}</p></div>"
    )

    def mount(self, request: Any, **kwargs: Any) -> None:
        super().mount(request, **kwargs)
        type(self).last = self


_TERMINAL = {"patch", "html_update", "error", "noop"}


async def _session(events: List[Dict[str, Any]], max_message_size: int = 65536) -> List[Any]:
    """Mount ``WsContact``, send each event, wait for its terminal frame; every frame, in order."""
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    connected, _ = await comm.connect()
    assert connected
    await comm.receive_json_from()
    answers: List[Any] = []
    try:
        await comm.send_json_to({"type": "mount", "view": f"{MOD}.WsContact", "url": "/x/"})
        answers.append(await comm.receive_json_from())
        for ref, event in enumerate(events, 1):
            await comm.send_json_to({"type": "event", "ref": ref, **event})
            frames = []
            while True:
                frame = await comm.receive_json_from(timeout=10)
                frames.append(frame)
                if frame.get("type") in _TERMINAL:
                    break
            answers.append(frames)
    finally:
        await comm.disconnect()
    return answers


def drive(events, **kw):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_session(events, **kw))
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _env():
    pytest.importorskip("channels")
    WsContact.last = None
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD], DEBUG=False):
        yield


def frame_of(count, width=0):
    """A ``submit_form`` event whose params are ``count`` distinct keys."""
    return {
        "event": "submit_form",
        "params": {"k%05d" % i: "v" * width for i in range(count)},
    }


# ---------------------------------------------------------------------------------------------
class TestWhatTheSocketAllowedBefore:
    """The evidence. The limits are switched OFF here, which is the behaviour before #3834."""

    def test_one_frame_under_the_frame_limit_carries_thousands_of_fields(self):
        event = frame_of(4000)
        assert len(json.dumps(event)) < 65536  # the default max_message_size admits it
        with limits(**UNLIMITED):
            answers = drive([event])
        assert answers[0]["type"] == "mount", answers[0]
        assert not any(f.get("type") == "message_too_large" for f in answers[1])
        # Django's POST limit would have stopped it at 1000
        assert len(WsContact.last.form_data) == 4000 + len(DECLARED)
        # ... and that is what the form the template reads (``form.data``) is bound to
        assert len(WsContact.last._form_instance.data) == 4000 + len(DECLARED)

    def test_a_run_of_small_events_grows_form_data_without_end(self):
        events = [
            {"event": "validate_field", "params": {"field": "f%d" % i, "value": "x"}}
            for i in range(150)
        ]
        with limits(**UNLIMITED):
            drive(events)
        assert len(WsContact.last.form_data) == 150 + len(DECLARED)  # one key per event, no ceiling


class TestTheLimitsHoldOverTheSocket:
    def test_a_frame_with_more_fields_than_the_limit_is_refused_whole(self):
        with limits(fields=1000):
            answers = drive([frame_of(4000)])
        assert WsContact.last.form_data == DECLARED  # nothing of it was stored
        assert WsContact.last.is_valid is False
        assert "more fields than the server accepts (1000)" in WsContact.last.form_errors[0]
        assert answers[1][-1]["type"] != "error"  # a refusal, not a crash

    def test_the_same_frame_is_accepted_when_it_fits(self):
        with limits(fields=1000):
            drive([frame_of(900)])
        assert len(WsContact.last.form_data) == 900 + len(DECLARED)

    def test_events_that_each_add_a_field_stop_at_the_limit(self):
        events = [
            {"event": "validate_field", "params": {"field": "f%d" % i, "value": "x"}}
            for i in range(40)
        ]
        with limits(fields=25):
            drive(events)
        data = WsContact.last.form_data
        # the declared field is not the client's to add, so 25 added fields fit beside it
        assert len(data) == 26
        assert set(data) == {"name", *("f%d" % i for i in range(25))}
        assert "more fields" in WsContact.last.form_errors[0]

    def test_a_field_sent_again_replaces_and_never_counts_twice(self):
        events = [
            {"event": "validate_field", "params": {"field": "name", "value": "v%d" % i}}
            for i in range(60)
        ]
        with limits(fields=3):
            drive(events)
        assert WsContact.last.form_data["name"] == "v59"
        assert WsContact.last.form_errors in ({}, [])  # never refused

    def test_the_size_limit_holds_across_events(self):
        events = [
            {"event": "validate_field", "params": {"field": "f%d" % i, "value": "y" * 100}}
            for i in range(20)
        ]
        with limits(size=1000):
            drive(events)
        data = WsContact.last.form_data
        assert 0 < len(data) < 20
        assert sum(len(k) + len(v) for k, v in data.items()) <= 1000
        assert "larger than the server accepts (1000" in WsContact.last.form_errors[0]


class TestEmptyMembersAreNotFree:
    """``[""] * 15000`` is ~60 KB of JSON, under the default frame limit, and used to weigh ONE
    character (an empty string was 0): 150 such events at the default limits stored 2,250,000 empty
    members and made every later event a second slow (#3842 review). Every member costs at least one
    character now, so the size limit bounds how many are kept."""

    def test_events_of_empty_strings_stop_at_the_size_limit_over_the_socket(self):
        payload = [""] * 15_000
        assert len(json.dumps(payload)) < 65_536
        events = [
            {"event": "validate_field", "params": {"field": "f%d" % i, "value": payload}}
            for i in range(30)
        ]
        # The member limit (#3844) would stop these first (13 x 15,002 members is past 100,000): it has
        # its own tests; this one is about the characters.
        with limits(size=200_000, max_form_members=0):
            answers = drive(events)
        stored = sum(len(v) for v in WsContact.last.form_data.values() if isinstance(v, list))
        assert 0 < stored <= 200_000, stored  # 13 events fit; the other 17 were refused
        assert "larger than the server accepts (200000" in WsContact.last.form_errors[0]
        assert all(frame[-1]["type"] != "error" for frame in answers[1:])  # refusals, not crashes


# ---------------------------------------------------------------------------------------------
def mounted(cls=WsContact):
    view = cls()
    view.mount(RequestFactory().get("/x/"))
    return view


class TestRefusal:
    def test_the_defaults_are_djangos(self):
        assert (
            _form_data_bounds.DEFAULT_MAX_FORM_FIELDS
            == global_settings.DATA_UPLOAD_MAX_NUMBER_FIELDS
        )
        assert (
            _form_data_bounds.DEFAULT_MAX_FORM_DATA_SIZE
            == global_settings.DATA_UPLOAD_MAX_MEMORY_SIZE
        )
        assert _form_data_bounds.limits() == (1000, 2_621_440)
        assert djust_config.get("max_form_fields") == 1000
        assert djust_config.get("max_form_data_size") == 2_621_440

    def test_exactly_at_the_limit_passes_and_one_past_is_refused(self):
        with limits(fields=3):
            assert _form_data_bounds.refusal({"a": 1, "b": 2}, {"c": 3}) is None
            assert _form_data_bounds.refusal({"a": 1, "b": 2}, {"c": 3, "d": 4}) is not None
            assert _form_data_bounds.refusal({"a": 1, "b": 2, "c": 3}, {"a": 9}) is None

    def test_a_flood_is_refused_on_its_length_without_being_merged(self):
        class Boom(dict):
            def __iter__(self):
                raise AssertionError("a flood is refused on len(), before any key is looked at")

            def items(self):
                raise AssertionError("the size pass must not run once the count has refused")

        with limits(fields=10):
            assert _form_data_bounds.refusal({}, Boom({str(i): i for i in range(11)})) is not None

    def test_the_size_counts_keys_and_values_and_what_survives(self):
        with limits(size=10):
            assert _form_data_bounds.refusal({"ab": "cd"}, {"ef": "g"}) is None  # 4 + 3
            assert _form_data_bounds.refusal({"ab": "cd"}, {"ef": "ghijk"}) is not None  # 4 + 7
            # replacing a value frees its old size
            assert _form_data_bounds.refusal({"ab": "cdefgh"}, {"ab": "c"}) is None
            # nested JSON is counted
            assert _form_data_bounds.refusal({}, {"k": ["abcdef", ["ghijk"]]}) is not None

    @pytest.mark.parametrize("bad", [-1, "1000", 1.5, None, True, [1]])
    def test_a_value_that_is_not_a_non_negative_integer_is_the_default_never_unlimited(self, bad):
        with limits(fields=bad, size=bad):
            assert _form_data_bounds.limits() == (1000, 2_621_440)

    def test_zero_removes_a_limit(self):
        with limits(fields=0, size=0):
            assert _form_data_bounds.limits() == (0, 0)
            assert _form_data_bounds.refusal({}, {str(i): "x" * 50 for i in range(5000)}) is None

    def test_an_invalid_setting_is_reported_once_at_load(self, caplog):
        with caplog.at_level(logging.WARNING, logger="djust.config"):
            with limits(fields=-5):
                pass
        assert any("max_form_fields" in r.getMessage() for r in caplog.records)

    def test_the_message_never_carries_what_the_client_sent(self):
        with limits(fields=1):
            message = _form_data_bounds.refusal({}, {"secret-key-a": "v", "secret-key-b": "v"})
        assert "secret" not in message


class TestFormMixinHandlers:
    def test_validate_field_refuses_the_field_past_the_limit_and_keeps_the_rest(self):
        view = mounted()
        with limits(fields=2):
            view.validate_field(field="a", value="1")
            view.validate_field(field="b", value="2")
            view.validate_field(field="c", value="3")
        assert view.form_data == {**DECLARED, "a": "1", "b": "2"}
        assert view.is_valid is False and view.form_errors

    def test_submit_form_is_all_or_nothing(self):
        view = mounted()
        with limits(fields=3):
            view.submit_form(a="1", b="2", c="3", d="4")
        assert view.form_data == DECLARED
        assert view.is_valid is False

    def test_a_refusal_does_not_poison_the_next_event(self):
        view = mounted()
        with limits(fields=2):
            view.submit_form(a="1", b="2", c="3")
            view.submit_form(a="1", b="2")
        assert view.form_data == {**DECLARED, "a": "1", "b": "2"}

    def test_the_prefix_is_stripped_before_it_is_counted(self):
        class Prefixed(WsContact):
            prefix = "u"

        view = mounted(Prefixed)
        with limits(fields=3):
            view.submit_form(**{"u-a": "1", "u-b": "2"})
            view.submit_form(**{"u-a": "9"})
        assert view.form_data == {**DECLARED, "a": "9", "b": "2"}

    def test_a_refusal_is_logged_without_the_keys(self, caplog):
        view = mounted()
        with caplog.at_level(logging.WARNING, logger="djust.forms"):
            with limits(fields=1):
                view.submit_form(**{"secret-a": "1", "secret-b": "2"})
        text = " ".join(r.getMessage() for r in caplog.records)
        assert "refused form data" in text and "secret" not in text


class TwoStep(forms.Form):
    first = forms.CharField(required=False)


class Wizard(WizardMixin, LiveView):
    wizard_steps = [{"name": "one", "title": "One", "form_class": TwoStep}]
    template = "<div dj-root></div>"


class TestWizard:
    @pytest.mark.parametrize("event", ["update_step_field", "validate_field"])
    def test_a_step_stops_at_the_limit(self, event):
        view = mounted(Wizard)
        with limits(fields=3):
            for i in range(10):
                getattr(view, event)(field="f%d" % i, value="x")
        assert sorted(view.wizard_step_data["one"]) == ["f0", "f1", "f2"]
        assert "more fields" in view.wizard_step_errors["one"]["__all__"][0]

    def test_a_field_sent_again_is_a_replacement(self):
        view = mounted(Wizard)
        with limits(fields=1):
            for i in range(5):
                view.update_step_field(field="first", value="v%d" % i)
        assert view.wizard_step_data["one"] == {"first": "v4"}
        assert "one" not in view.wizard_step_errors


class TestAdminFormView:
    def _view(self):
        from djust.admin_ext.forms import AdminFormMixin

        view = AdminFormMixin.__new__(AdminFormMixin)
        view.form_data = {}
        view.field_errors = {}
        view.form_errors = {}
        view._create_form = lambda data: Contact(data)
        return view

    def test_validate_field_and_submit_form_are_bounded(self):
        view = self._view()
        with limits(fields=2):
            view.validate_field(field="a", value="1")
            view.validate_field(field="b", value="2")
            view.validate_field(field="c", value="3")
            assert view.form_data == {"a": "1", "b": "2"}
            view.submit_form(c="3", d="4")
        assert view.form_data == {"a": "1", "b": "2"}
        assert view.is_valid is False


class TestParamsForm:
    def test_a_url_change_with_more_parameters_than_the_limit_is_invalid(self):
        from djust._params_form import bind_params

        view = SimpleNamespace()
        with limits(fields=5):
            form, values = bind_params(view, Contact, {"k%d" % i: "x" for i in range(6)})
        assert values is None
        assert "more fields" in view.params_errors["__all__"][0]
        assert "k0" not in form.data  # the form was bound to nothing

    def test_within_the_limit_binds_as_before(self):
        from djust._params_form import bind_params

        view = SimpleNamespace()
        with limits(fields=5):
            form, values = bind_params(view, Contact, {"name": "x"})
        assert values == {"name": "x"} and view.params_errors == {}

    def test_a_non_mapping_is_still_bound_as_absent(self):
        from djust._params_form import bind_params

        view = SimpleNamespace()
        form, values = bind_params(view, Contact, "nonsense")
        assert values is not None or view.params_errors is not None


class TestConfigValidation:
    def test_defaults_survive_a_reload_with_nothing_set(self):
        with override_settings(LIVEVIEW_CONFIG={}):
            assert djust_config.get("max_form_fields") == 1000

    def test_the_unlimited_warning_is_for_production_only(self, caplog):
        with caplog.at_level(logging.WARNING, logger="djust.config"):
            with override_settings(DEBUG=False, LIVEVIEW_CONFIG=UNLIMITED):
                pass
        assert any(
            "max_form_fields" in r.getMessage() and "no limit" in r.getMessage()
            for r in caplog.records
        )


# ---------------------------------------------------------------------------------------------
def nest(value, depth):
    for _ in range(depth):
        value = [value]
    return value


DEPTHS = [0, 1, 7, 8, 9, 20, 50, 500]


class TestNestingIsNotAWayAround:
    """``_weigh`` once charged a flat 8 for any container at depth 8, so ``[[...["x" * 60000]...]]`` nine
    lists deep was free: 1,000 such events stored 59,943,890 characters (23x the limit)."""

    @pytest.mark.parametrize("depth", DEPTHS)
    def test_the_weight_does_not_depend_on_the_depth(self, depth):
        payload = {"k": nest("x" * 3000, depth)}
        assert _form_data_bounds._weigh(payload, 10**9) >= 3000

    @pytest.mark.parametrize("depth", DEPTHS)
    def test_a_nested_payload_is_refused_like_a_flat_one(self, depth):
        with limits(size=1000):
            assert _form_data_bounds.refusal({}, {"k": nest("x" * 3000, depth)}) is not None

    @pytest.mark.parametrize("depth", DEPTHS)
    def test_nested_in_a_mapping_a_tuple_and_a_set_too(self, depth):
        deep: Any = "x" * 3000
        for i in range(depth):
            deep = [{"a": deep}] if i % 2 else (deep,)
        with limits(size=1000):
            assert _form_data_bounds.refusal({}, {"k": deep}) is not None

    def test_bytes_are_their_length(self):
        with limits(size=1000):
            assert _form_data_bounds.refusal({}, {"k": b"x" * 3000}) is not None
            assert _form_data_bounds.refusal({}, {"k": [bytearray(3000)]}) is not None

    def test_a_value_nested_past_the_stack_bound_is_refused_not_recursed_into(self):
        deep: Any = "x"
        for _ in range(5000):
            deep = [deep]
        with limits(size=10**9):
            assert _form_data_bounds.refusal({}, {"k": deep}) is not None

    def test_a_cycle_runs_out_of_budget(self):
        cycle: list = []
        cycle.append(cycle)
        with limits(size=1000):
            assert _form_data_bounds.refusal({}, {"k": cycle}) is not None

    def test_the_work_is_bounded_by_the_budget_not_the_value(self):
        pulled = []

        class Endless(list):
            def __iter__(self):
                def gen():
                    for i in range(10**9):
                        pulled.append(i)
                        yield "ab"

                return gen()

        weight = _form_data_bounds._weigh({"k": Endless()}, 1000)
        assert weight > 1000
        assert len(pulled) < 1000  # a few hundred members, not a billion

    @pytest.mark.parametrize("depth", [9, 50])
    def test_form_mixin_handlers(self, depth):
        view = mounted()
        payload = nest("x" * 3000, depth)
        with limits(size=10_000):
            for i in range(10):
                view.validate_field(field="f%d" % i, value=payload)
            view2 = mounted()
            view2.submit_form(k=payload, j=payload, l=payload, m=payload)
        stored = sum(len(k) for k in view.form_data) + 3000 * (len(view.form_data) - 1)
        assert stored <= 10_000 + 3000
        assert len(view.form_data) < 10
        assert view2.form_data == DECLARED  # all four together are over: refused whole

    @pytest.mark.parametrize("depth", [9, 50])
    def test_wizard(self, depth):
        view = mounted(Wizard)
        with limits(size=10_000):
            for i in range(10):
                view.update_step_field(field="f%d" % i, value=nest("x" * 3000, depth))
        assert len(view.wizard_step_data["one"]) < 10
        assert "larger than the server accepts" in view.wizard_step_errors["one"]["__all__"][0]

    @pytest.mark.parametrize("depth", [9, 50])
    def test_admin_form_view(self, depth):
        view = TestAdminFormView()._view()
        with limits(size=10_000):
            for i in range(10):
                view.validate_field(field="f%d" % i, value=nest("x" * 3000, depth))
            view.submit_form(**{"g%d" % i: nest("x" * 3000, depth) for i in range(5)})
        assert len(view.form_data) < 10

    @pytest.mark.parametrize("depth", [9, 50])
    def test_params_form(self, depth):
        from djust._params_form import bind_params

        view = SimpleNamespace()
        with limits(size=1000):
            form, values = bind_params(view, Contact, {"name": nest("x" * 3_000_000, depth)})
        assert values is None
        assert "larger than the server accepts" in view.params_errors["__all__"][0]

    def test_through_the_real_socket(self):
        """The payload of the review: distinct names, each a string wrapped nine lists deep."""
        events = [
            {
                "event": "validate_field",
                "params": {"field": "f%d" % i, "value": nest("x" * 20_000, 9)},
            }
            for i in range(30)
        ]
        with limits(size=100_000):
            drive(events)
        data = WsContact.last.form_data
        chars = sum(len(k) for k in data) + sum(20_000 for k in data if k != "name")
        assert chars <= 100_000 + 20_000
        assert 0 < len(data) < 30
        assert "larger than the server accepts" in WsContact.last.form_errors[0]


# ---------------------------------------------------------------------------------------------
Big = type("Big", (forms.Form,), {"f%d" % i: forms.CharField(required=False) for i in range(1200)})


class BigView(FormMixin, LiveView):
    form_class = Big
    template = "<div dj-root></div>"


class TestDeclaredFieldsAreNotTheClientsToAdd:
    """A form that declares 1,200 fields holds 1,200 keys from mount on; submitting them must work."""

    def test_the_whole_form_can_be_submitted_at_the_default_limit(self):
        view = mounted(BigView)
        assert len(view.form_data) == 1200
        view.submit_form(**{"f%d" % i: "v" for i in range(1200)})
        assert view.form_errors in ({}, [])
        assert view.form_data["f1199"] == "v"

    def test_what_the_client_adds_beyond_them_is_what_is_limited(self):
        view = mounted(BigView)
        with limits(fields=10):
            view.submit_form(
                **{"f%d" % i: "v" for i in range(1200)}, **{"x%d" % i: "v" for i in range(10)}
            )
            assert "x9" in view.form_data
            view.submit_form(x10="v")
        assert "x10" not in view.form_data
        assert "more fields" in view.form_errors[0]

    def test_a_flood_is_still_refused_on_its_length(self):
        view = mounted(BigView)
        with limits(fields=10):
            view.submit_form(**{"x%d" % i: "v" for i in range(2000)})
        assert not any(k.startswith("x") for k in view.form_data)


class TestARefusalDoesNotLinger:
    def test_the_message_is_cleared_by_the_next_accepted_event(self):
        view = mounted()
        with limits(fields=1):
            view.validate_field(field="a", value="1")
            view.validate_field(field="b", value="2")
            assert view.form_errors and "more fields" in view.form_errors[0]
            view.validate_field(field="a", value="3")  # a replacement: accepted
        assert view.form_errors == {}
        assert view.form_data["a"] == "3"

    def test_a_form_errors_value_somebody_else_set_is_left_alone(self):
        view = mounted()
        with limits(fields=1):
            view.validate_field(field="a", value="1")
            view.validate_field(field="b", value="2")
            view.form_errors = ["their own"]
            view.validate_field(field="a", value="3")
        assert view.form_errors == ["their own"]

    def test_the_wizard_step_message_is_cleared_too(self):
        view = mounted(Wizard)
        with limits(fields=1):
            view.update_step_field(field="a", value="1")
            view.update_step_field(field="b", value="2")
            assert "one" in view.wizard_step_errors
            view.update_step_field(field="a", value="3")
        assert "one" not in view.wizard_step_errors


Wide = type("Wide", (forms.Form,), {"w%d" % i: forms.CharField(required=False) for i in range(30)})


class WideWizard(WizardMixin, LiveView):
    wizard_steps = [{"name": "one", "title": "One", "form_class": Wide}]
    template = "<div dj-root></div>"


class TestDeclaredFieldsOnTheOtherPaths:
    """The wizard step form and the ``params_form`` declare their fields too; they are not counted."""

    def test_wizard_step_declared_fields_are_not_counted(self):
        view = mounted(WideWizard)
        with limits(fields=10):
            for i in range(30):
                view.update_step_field(field="w%d" % i, value="v")
            for i in range(10):
                view.update_step_field(field="x%d" % i, value="v")
            assert "one" not in view.wizard_step_errors
            view.update_step_field(field="x10", value="v")
        assert "x9" in view.wizard_step_data["one"] and "x10" not in view.wizard_step_data["one"]
        assert "more fields" in view.wizard_step_errors["one"]["__all__"][0]

    def test_params_form_declared_fields_are_not_counted(self):
        from djust._params_form import bind_params

        view = SimpleNamespace()
        with limits(fields=10):
            form, values = bind_params(view, Wide, {"w%d" % i: "v" for i in range(30)})
            assert values is not None and view.params_errors == {}
            form, values = bind_params(
                view,
                Wide,
                {**{"w%d" % i: "v" for i in range(30)}, **{"x%d" % i: "v" for i in range(11)}},
            )
        assert values is None
        assert "more fields" in view.params_errors["__all__"][0]
