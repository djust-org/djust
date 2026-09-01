"""`{{ component }}` escapes and `{{ component.render }}` is empty (#2501).

The reporter's matrix, measured against LIVE Django rather than transcribed::

    {{ c }}              django '<div class="alert">real</div>'   djust '&lt;div …'   ESCAPED
    {{ c|safe }}         django  same                             djust  same         ok
    {{ c.render }}       django  same                             djust ''            EMPTY
    {{ c.render|safe }}  django  same                             djust ''            EMPTY

All four are documented spellings — ``{{ counter.render }}`` at
``docs/website/guides/components.md:227`` and *"Components in templates render
via ``{{ component }}``"* at ``components.md:637``.

Two independent causes, and the SECOND is not the one #2501 states
-------------------------------------------------------------------
1. **ESCAPED.** ``Component.__str__`` returns a genuine ``SafeString``
   (``hasattr(str(c), "__html__")`` is ``True``), and the marker is lost
   crossing into the renderer: the object arrives as a ``Value::Encoded``
   whose ``display`` is a bare Rust ``String`` with no safety bit, and
   ``Context::safe_keys`` — the name-keyed safety channel — is never told.

   Path-dependent, and only the LiveView path escapes it: there,
   ``normalize_django_value`` converts a ``Component`` to ``str(component)``
   (a ``SafeString``) and ``_collect_safe_keys`` marks the key. Its twin
   ``djust.template.serialization.serialize_value`` — which the
   ``DjustTemplateBackend`` path runs — has no ``Component`` arm, so the same
   value takes the object path there. That is #1646 drift between two
   serializers, one of which grew a rule the other did not.

2. **EMPTY.** #2501 attributes this to the ``__dict__`` bulk-dump arm of
   ``impl FromPyObject for Value``. Measured, a real ``Component`` never
   reaches that arm: its ``__dict__`` keys are ``_rust_instance`` /
   ``_explicit_id`` / ``_component_key``, all ``_``-prefixed, so
   ``has_public_dict_attrs`` is ``False``, ``opaque_gate`` does NOT decline it
   and it crosses as a ``Value::Encoded`` with an EMPTY ``attrs`` map
   (``_rust.crosses_as_encoded(component)`` is ``True``).

   The ``__dict__`` arm IS the mechanism for the second table below — a plain
   object WITH a public instance attribute. So the defect spans BOTH carriers:
   neither performs Django's step 2 (``getattr`` on the live object) nor its
   auto-call, and a fix that served one carrier would leave the other.

What Django does, and what the guards are
------------------------------------------
``Variable._resolve_lookup`` per dotted segment: mapping item access →
``getattr`` (reaching class attributes, properties, descriptors) → auto-call
if callable → integer index. The auto-call honours
``do_not_call_in_templates`` (use as-is) and ``alters_data`` (refuse — render
empty, never call). ``TestAutoCallGuards`` pins both, in the direction that a
template lookup must never become a way to invoke a mutating method.

Refs #2501, #2495, #2485, #2481, #2478, #2477, #2448, #2418, #1646, #1079.
"""

from __future__ import annotations

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402
from djust.components.base import Component  # noqa: E402


class Alert(Component):
    """A real ``Component`` subclass — not a stand-in (#2501's own repro)."""

    template = None

    def _render_custom(self) -> str:
        return '<div class="alert">real</div>'


class Plain:
    """A plain object exercising every arm of Django's step 2."""

    cls_attr = "class-level"

    def __init__(self) -> None:
        self.inst_attr = "in-dict"

    def meth(self) -> str:
        return "called"

    @property
    def prop(self) -> str:
        return "prop"


class Mutating:
    """``alters_data`` and ``do_not_call_in_templates``, with a side-effect canary."""

    def __init__(self) -> None:
        self.mutated = False

    def mutate(self) -> str:
        self.mutated = True
        return "MUTATED"

    mutate.alters_data = True  # type: ignore[attr-defined]

    def keep(self) -> str:
        return "called"

    keep.do_not_call_in_templates = True  # type: ignore[attr-defined]


def django_render(source: str, context: dict) -> str:
    return DjangoTemplate(source).render(DjangoContext(dict(context)))


def djust_render(source: str, context: dict) -> str:
    """``_rust.render_template`` — one of the two entry points the backend binds."""
    return _rust.render_template(source, dict(context))


def djust_render_with_dirs(source: str, context: dict) -> str:
    """The OTHER entry point ``DjustTemplateBackend`` binds."""
    return _rust.render_template_with_dirs(source, dict(context), [])


def djust_backend_render(source: str, context: dict) -> str:
    """The full ``DjustTemplateBackend`` path — what a user's project runs."""
    from djust.template_backend import DjustTemplateBackend

    backend = DjustTemplateBackend(
        params={"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
    )
    return backend.from_string(source).render(context=dict(context), request=None)


#: Every path a user's template can take. Asserting all three is what keeps
#: "which paths are affected" a measurement rather than a claim (#1646).
PATHS = [
    pytest.param(djust_render, id="render_template"),
    pytest.param(djust_render_with_dirs, id="render_template_with_dirs"),
    pytest.param(djust_backend_render, id="DjustTemplateBackend"),
]

#: The reporter's four spellings. Every one is documented.
COMPONENT_SPELLINGS = ["{{ c }}", "{{ c|safe }}", "{{ c.render }}", "{{ c.render|safe }}"]

#: Django's step 2 over a plain object: the instance ``__dict__`` (which the
#: bulk-dump arm already reaches), then a class attribute, a method reached
#: through the auto-call, and a property.
PLAIN_LOOKUPS = ["{{ o.inst_attr }}", "{{ o.cls_attr }}", "{{ o.meth }}", "{{ o.prop }}"]


class TestComponentSpellings:
    """All four documented spellings must render what Django renders."""

    @pytest.mark.parametrize("render", PATHS)
    @pytest.mark.parametrize("source", COMPONENT_SPELLINGS)
    def test_component_matches_django(self, render, source):
        component = Alert()
        assert hasattr(str(component), "__html__"), (
            "premise: Component.__str__ returns a marked SafeString"
        )
        assert render(source, {"c": component}) == django_render(source, {"c": component})


class TestPlainObjectLookups:
    """Django's step 2 reaches class attributes, methods and properties."""

    @pytest.mark.parametrize("render", PATHS)
    @pytest.mark.parametrize("source", PLAIN_LOOKUPS)
    def test_lookup_matches_django(self, render, source):
        assert render(source, {"o": Plain()}) == django_render(source, {"o": Plain()})


class TestCarrierPremises:
    """The two premises #2501 states about the carrier, checked not assumed."""

    def test_a_real_component_crosses_as_encoded_not_as_a_dict_dump(self):
        """#2501 says the ``__dict__`` bulk-dump arm; measured, it is ``Encoded``."""
        component = Alert()
        assert all(k.startswith("_") for k in component.__dict__), (
            "a Component's instance dict is entirely private, so the "
            "`__dict__` bulk-dump arm cannot be the mechanism"
        )
        assert _rust.crosses_as_encoded(component) is True

    def test_a_plain_object_with_a_public_attr_does_take_the_dict_arm(self):
        """The second table's objects DO take the arm #2501 names."""
        assert _rust.crosses_as_encoded(Plain()) is False


class TestAutoCallGuards:
    """A template lookup must never invoke a mutating method (#2501, ADR-024)."""

    @pytest.mark.parametrize("render", PATHS)
    def test_alters_data_is_never_called(self, render):
        obj = Mutating()
        rendered = render("{{ o.mutate }}", {"o": obj})
        assert obj.mutated is False, "alters_data method was CALLED by a template lookup"
        assert rendered == django_render("{{ o.mutate }}", {"o": Mutating()})

    @pytest.mark.parametrize("render", PATHS)
    def test_do_not_call_in_templates_is_used_as_is(self, render):
        obj = Mutating()
        assert render("{{ o.keep }}", {"o": obj}) == django_render("{{ o.keep }}", {"o": obj})
