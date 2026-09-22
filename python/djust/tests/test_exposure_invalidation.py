"""ADR-038 E2-8: explicit context stays reactive, and no update is dropped.

D3: "Derived context must remain reactive. Track declared state/component
dependencies and explicit invalidation. For opaque dependencies, render
conservatively or require a documented invalidation mechanism; never silently
drop an update."

How explicit views meet that today, pinned here over the real runtime:

* The per-turn change detector (``websocket._snapshot_assigns``, which the
  runtime imports rather than copies) walks the whole instance ``__dict__`` for
  explicit views too. That is the conservative fallback: a plain attribute
  read by ``get_context_data`` is an opaque dependency, and narrowing the walk
  to declared ``state()`` fields would turn its change into a ``noop``.
* The detector names storage attributes (``_state_count``), not context keys.
  ``_sync_state_to_rust`` bridges the two by comparing every context key it
  was not told about against the previous render (value, structural
  fingerprint or identity), so a derived key such as ``doubled`` still reaches
  Rust and the partial render.
* A provider's ``ProviderContract.tracked`` keys are view attributes, so the
  same walk sees them, and the provider's rendered keys are re-derived.
* ``set_changed_keys()`` is the documented invalidation mechanism for what no
  snapshot can see (an attribute write on an opaque object, an external/DB
  change). It works the same under both policies.

Every explicit case has a legacy control that must produce the same frames.
"""

import json

import pytest
from asgiref.sync import sync_to_async
from django import forms
from django.test import override_settings

from djust import LiveView, event_handler
from djust.decorators import state
from djust.runtime import ViewRuntime
from djust.tests.test_exposure_runtime import make_request
from djust.tests.test_runtime_state_save_tt_1894 import MockTransport
from djust.wizard import WizardMixin

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

MODULE = __name__
RENDER_FRAMES = {"patch", "html_update", "embedded_update"}


class Service:
    """An opaque (non-container) object: compared by identity, never walked."""

    def __init__(self, label):
        self.label = label


def _body(policy):
    """One view body per policy, so the two classes differ only in policy."""

    def mount(self, request, **kwargs):
        self.count = 1
        self.service = Service("OPAQUE_A")

    def get_context_data(self, **kwargs):
        context = super(type(self), self).get_context_data(**kwargs)
        # ``doubled`` is derived from the declared field; ``label`` is derived
        # from a plain attribute the contract knows nothing about.
        context["doubled"] = f"DOUBLED_{self.count * 2}"
        context["label"] = f"LABEL_{self.service.label}"
        return context

    @event_handler()
    def bump(self):
        self.count += 1

    @event_handler()
    def swap_service(self):
        self.service = Service("OPAQUE_B")

    @event_handler()
    def write_in_place(self, hatch: str = ""):
        self.service.label = "OPAQUE_C"
        if hatch == "zero":
            self.set_changed_keys()
        elif hatch == "named":
            self.set_changed_keys("service")

    @event_handler()
    def nothing(self):
        pass

    return {
        "exposure_policy": policy,
        "template": "<div dj-root><p>{{ doubled }}</p><p>{{ label }}</p></div>",
        "count": state(0),
        "mount": mount,
        "get_context_data": get_context_data,
        "bump": bump,
        "swap_service": swap_service,
        "write_in_place": write_in_place,
        "nothing": nothing,
    }


ExplicitDerived = type("ExplicitDerived", (LiveView,), {"__module__": MODULE, **_body("explicit")})
LegacyDerived = type("LegacyDerived", (LiveView,), {"__module__": MODULE, **_body("legacy")})
DERIVED = {"explicit": ExplicitDerived, "legacy": LegacyDerived}


class StepOne(forms.Form):
    answer = forms.CharField(required=False)


class StepTwo(forms.Form):
    other = forms.CharField(required=False)


def _wizard_body(policy):
    return {
        "__module__": MODULE,
        "exposure_policy": policy,
        "wizard_steps": [
            {"name": "one", "title": "STEP_ONE_SENTINEL", "form_class": StepOne},
            {"name": "two", "title": "STEP_TWO_SENTINEL", "form_class": StepTwo},
        ],
        "template": (
            "<div dj-root><h1>{{ current_step.title }}</h1><p>{{ form_data.answer }}</p></div>"
        ),
    }


ExplicitWizard = type("ExplicitWizard", (WizardMixin, LiveView), _wizard_body("explicit"))
LegacyWizard = type("LegacyWizard", (WizardMixin, LiveView), _wizard_body("legacy"))
WIZARDS = {"explicit": ExplicitWizard, "legacy": LegacyWizard}


@pytest.fixture(autouse=True)
def staged(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)


async def mount(view_class):
    request = await sync_to_async(make_request)()
    transport = MockTransport()
    transport.build_request = lambda: request

    async def fresh(view):
        return await sync_to_async(make_request)(request.session.session_key)

    transport.explicit_event_request = fresh
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust"]):
        await runtime.dispatch_mount(
            {"type": "mount", "view": f"{MODULE}.{view_class.__name__}", "url": request.path}
        )
    assert not transport.errors, transport.errors
    assert any(frame.get("type") == "mount" for frame in transport.sent), transport.sent
    policy = "explicit" if view_class.exposure_policy == "explicit" else "legacy"
    from djust._exposure import uses_legacy_exposure

    # The policy is fixed from mount (never flipped mid-session).
    assert uses_legacy_exposure(runtime.view_instance) is (policy == "legacy")
    transport.sent.clear()
    return runtime, transport


async def event(runtime, transport, name, **params):
    transport.sent.clear()
    await runtime.dispatch_event({"type": "event", "event": name, "params": params})
    assert not transport.errors, transport.errors
    return [frame.get("type") for frame in transport.sent], json.dumps(transport.sent)


def _render_types(types):
    return [t for t in types if t in RENDER_FRAMES]


# ---------------------------------------------------------------------------
# Rust view render (root views, ViewRuntime event spine).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("policy", ["explicit", "legacy"])
async def test_derived_context_rerenders_when_its_declared_state_changes(policy):
    runtime, transport = await mount(DERIVED[policy])
    types, frames = await event(runtime, transport, "bump")
    assert _render_types(types), types
    assert "DOUBLED_4" in frames
    assert "noop" not in types


async def test_declared_state_change_is_detected_under_its_storage_name():
    """The detector names ``_state_count``; the render key is ``doubled``.

    Pins the bridge: the changed-keys set never contains a context key here,
    yet the frame above carries the derived value (``_sync_state_to_rust``'s
    derived-value comparison, not a name match, carries it).
    """
    from djust.websocket import _compute_changed_keys, _snapshot_assigns

    runtime, _ = await mount(ExplicitDerived)
    view = runtime.view_instance
    before = _snapshot_assigns(view)
    view.count += 1
    changed = _compute_changed_keys(before, _snapshot_assigns(view))
    assert changed == {"_state_count"}
    assert "doubled" not in changed


@pytest.mark.parametrize("policy", ["explicit", "legacy"])
async def test_opaque_attribute_dependency_rerenders_conservatively(policy):
    """A plain attribute read in ``get_context_data`` is not declared anywhere.

    The explicit detector keeps walking ``__dict__`` so its reassignment still
    renders; narrowing the walk to declared fields would drop this update.
    """
    runtime, transport = await mount(DERIVED[policy])
    types, frames = await event(runtime, transport, "swap_service")
    assert _render_types(types), types
    assert "LABEL_OPAQUE_B" in frames


@pytest.mark.parametrize("policy", ["explicit", "legacy"])
@pytest.mark.parametrize("hatch", ["zero", "named"])
async def test_set_changed_keys_is_the_invalidation_mechanism(policy, hatch):
    runtime, transport = await mount(DERIVED[policy])
    types, frames = await event(runtime, transport, "write_in_place", hatch=hatch)
    assert _render_types(types), types
    assert "LABEL_OPAQUE_C" in frames
    assert not runtime.view_instance._force_full_html  # consumed by the render


@pytest.mark.parametrize("policy", ["explicit", "legacy"])
async def test_in_place_opaque_write_without_hatch_is_the_documented_limit(policy):
    """Identity comparison cannot see an attribute write on an opaque object
    under either policy (the documented case ``set_changed_keys`` exists for).
    Explicit mode must not be worse than legacy: the next render carries it."""
    runtime, transport = await mount(DERIVED[policy])
    types, frames = await event(runtime, transport, "write_in_place")
    assert types == ["noop"], types
    types, frames = await event(runtime, transport, "bump")
    assert "LABEL_OPAQUE_C" in frames and "DOUBLED_4" in frames


@pytest.mark.parametrize("policy", ["explicit", "legacy"])
async def test_noop_turn_stays_a_noop(policy):
    runtime, transport = await mount(DERIVED[policy])
    types, _ = await event(runtime, transport, "nothing")
    assert types == ["noop"], types
    # Also after a real render, so the derived-value baseline is warm.
    await event(runtime, transport, "bump")
    types, _ = await event(runtime, transport, "nothing")
    assert types == ["noop"], types


async def test_policies_produce_identical_render_frames():
    """Same change, same frames: the explicit detector is no coarser and no
    finer than legacy for any turn above."""
    results = {}
    for policy, view_class in DERIVED.items():
        runtime, transport = await mount(view_class)
        seen = []
        for name, params in (
            ("nothing", {}),
            ("bump", {}),
            ("swap_service", {}),
            ("write_in_place", {}),
            ("write_in_place", {"hatch": "zero"}),
            ("nothing", {}),
        ):
            types, _ = await event(runtime, transport, name, **params)
            patches = [
                frame.get("patches") or frame.get("html")
                for frame in transport.sent
                if frame.get("type") in RENDER_FRAMES
            ]
            seen.append((types, patches))
        results[policy] = seen
    assert results["explicit"] == results["legacy"]


# ---------------------------------------------------------------------------
# Provider tracked keys (WizardMixin is the provider that declares them).
# ---------------------------------------------------------------------------


async def test_provider_tracked_keys_are_inside_the_change_detector():
    """Every ``ProviderContract.tracked`` key on an explicit view is watched,
    so a provider's rendered keys cannot go stale behind its own state."""
    from djust._exposure import provider_contracts_for
    from djust.websocket import _snapshot_assigns

    runtime, _ = await mount(ExplicitWizard)
    watched = set(_snapshot_assigns(runtime.view_instance))
    tracked = set().union(*(c.tracked for c in provider_contracts_for(ExplicitWizard)))
    assert tracked, "the wizard provider declares tracked keys"
    assert tracked <= watched, tracked - watched
    # The render bookkeeping of which keys providers wrote is reassigned on
    # every explicit render; it must not look like a state change itself.
    assert "_explicit_context_provider_keys" not in watched


@pytest.mark.parametrize("policy", ["explicit", "legacy"])
async def test_provider_tracked_key_change_rerenders_its_keys(policy):
    runtime, transport = await mount(WIZARDS[policy])
    types, frames = await event(runtime, transport, "next_step")
    assert _render_types(types), types
    assert "STEP_TWO_SENTINEL" in frames


@pytest.mark.parametrize("policy", ["explicit", "legacy"])
async def test_provider_tracked_key_in_place_mutation_rerenders(policy):
    runtime, transport = await mount(WIZARDS[policy])
    types, frames = await event(
        runtime, transport, "update_step_field", field="answer", value="ANSWER_SENTINEL"
    )
    assert _render_types(types), types
    assert "ANSWER_SENTINEL" in frames


# ---------------------------------------------------------------------------
# Django-template path: embedded children render through Django's engine.
# ---------------------------------------------------------------------------


def _child_body(policy):
    def mount(self, request, **kwargs):
        self.count = 1
        self.service = Service("CHILD_A")

    def get_context_data(self, **kwargs):
        context = super(type(self), self).get_context_data(**kwargs)
        context["doubled"] = f"DOUBLED_{self.count * 2}"
        context["label"] = f"LABEL_{self.service.label}"
        return context

    @event_handler()
    def bump(self):
        self.count += 1

    @event_handler()
    def swap_service(self):
        self.service = Service("CHILD_B")

    return {
        "__module__": MODULE,
        "exposure_policy": policy,
        "sticky": True,
        "sticky_id": "menu",
        "count": state(1),
        "template": "<div><p>{{ doubled }}</p><p>{{ label }}</p></div>",
        "mount": mount,
        "get_context_data": get_context_data,
        "bump": bump,
        "swap_service": swap_service,
    }


ExplicitChild = type("ExplicitChild", (LiveView,), _child_body("explicit"))
LegacyChild = type("LegacyChild", (LiveView,), _child_body("legacy"))


def _parent(name, policy, child):
    return type(
        name,
        (LiveView,),
        {
            "__module__": MODULE,
            "exposure_policy": policy,
            "template": (
                "<div dj-root>{% load live_tags %}{% live_render "
                f'"{MODULE}.{child.__name__}" sticky=True %}}</div>'
            ),
        },
    )


ExplicitParent = _parent("ExplicitParent", "explicit", ExplicitChild)
LegacyParent = _parent("LegacyParent", "legacy", LegacyChild)
PARENTS = {"explicit": ExplicitParent, "legacy": LegacyParent}


@pytest.fixture
def children(settings):
    settings.DJUST_LIVE_RENDER_ALLOWED_MODULES = [MODULE]


@pytest.mark.parametrize("policy", ["explicit", "legacy"])
async def test_child_derived_context_rerenders_on_the_django_path(children, policy):
    """Each successive child change reaches its ``embedded_update``: a declared
    field (``bump``) and an opaque plain attribute (``swap_service``)."""
    runtime, transport = await mount(PARENTS[policy])
    for name, sentinel in (
        ("bump", "DOUBLED_4"),
        ("swap_service", "LABEL_CHILD_B"),
        ("bump", "DOUBLED_6"),
    ):
        types, frames = await event(runtime, transport, name, view_id="menu")
        assert types.count("embedded_update") == 1, types
        assert sentinel in frames, (name, frames)


async def test_django_and_rust_render_the_same_change_identically(children):
    """Where both engines apply (an embedded child's own template), the same
    change renders the same markup through Django (the embedded_update frame)
    and through the Rust template engine, under both policies."""
    from djust._rust import render_template

    html = {}
    for policy, parent in PARENTS.items():
        runtime, transport = await mount(parent)
        await event(runtime, transport, "bump", view_id="menu")
        frame = next(f for f in transport.sent if f.get("type") == "embedded_update")
        child = runtime.view_instance._get_child_view("menu")
        context = {k: child.get_context_data()[k] for k in ("doubled", "label")}
        rust = render_template(child.template, context)
        assert frame["html"] == rust, (policy, frame["html"], rust)
        html[policy] = frame["html"]
    assert "DOUBLED_4" in html["explicit"]
    assert html["explicit"] == html["legacy"]
