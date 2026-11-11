"""Native Rust component handlers take what the Python tags take, and refuse what they refuse
(#3666, #3667).

Two halves of one question, "does the template render the same under the Rust engine as under
Django", found by the audit behind #3655.

**#3666: objects.** The Rust engine hands a native handler its operands as strings and JSON,
so a ``BoundField`` arrives as its widget HTML, a ``Form`` as its ``str``, a ``datetime`` as
``str(datetime)``, an element of a list as whatever ``str`` gives. ``{% field_error
field=form.name %}`` rendered nothing. The tags that take an object are not a list anyone
maintains: ``scripts/lib/template_library_parity/objects.py`` finds them by rendering every
parameter of every tag with a value that records how it is used. They have no native handler;
the Python tag's own node renders them (``rust_handlers.OBJECT_ARGUMENT_TAGS``).

**#3667: invalid input.** Where the Python tag raised (``int("abc")``, ``int(None)``, a missing
required parameter) the native handler rendered. Now it raises the same error, and where it
raised and Python rendered (``notification_center`` with ``None``) it renders.

What each class proves:

* ``TestObjectArguments``: the measured set equals the source set, in both directions; every
  object shape renders identically under both engines AND carries the object's data (so the
  comparison is not between two empty strings); the tags work in a template that never loads
  the library.
* ``TestInvalidInput``: the only variant either engine rejects alone is the documented
  unknown-keyword leniency; missing required parameters, non-numeric values and ``None`` raise
  the same error in both engines; no output of either engine contains a Python repr.
* ``TestMissingArguments``: ``missing_arguments`` against Django's real ``parse_bits`` over a
  grid of signatures and calls, quirk included.

* ``TestTypeDispatch`` (#3756): the tags that branch on a value's TYPE (``json_viewer``:
  ``isinstance``) read no attribute, so the recorder cannot see them. ``typed_values.py``
  finds them by rendering the Python tag on a value and on the stand-in the engine would hand
  a handler; the tests pin ``json_viewer`` against Django over every type, a seeded random
  tree of mixed types, and the set of tags still wrong for a realistic type.

The facts that cannot be known without rendering the whole library (``OBJECT_ARGUMENT_TAGS``
derivation, the one-engine-error table, the repr scan) come from ONE subprocess, the way the
parity ratchet does it: that process replaces ``uuid`` and ``datetime`` and must not be a
pytest worker.

Refs #3666, #3667, #3756, #3655, #3663, #3664, #3676, #1468, #1646, #1125.
"""

from __future__ import annotations

import datetime
import decimal
import json
import os
import pathlib
import random
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from django.template import Context, Engine, TemplateSyntaxError
from django.template.library import import_library

from scripts.lib.template_library_parity import harness, variants

# The fixture below runs the library-wide measurement once (~10 s in a subprocess), charged
# to whichever test asks first. `.test_durations` sums setup + call + teardown per test, so
# a recorded > 1 s would let tests/slow_tier.py deselect the tests that assert the contract
# under `-m "not slow"`; `pr_tier` is the explicit opt-out (see test_template_library_parity_3663).
pytestmark = pytest.mark.pr_tier

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "template-library-parity.py"
COMPONENTS = "djust.components.templatetags.djust_components"
LIBRARIES = {
    "djust_components": COMPONENTS,
    "theme_tags": "djust.theming.templatetags.theme_tags",
}


# --------------------------------------------------------------------------- #
# Engines and facts


@pytest.fixture(scope="module")
def engines():
    from djust.components import rust_handlers as component_handlers
    from djust.template.backend import DjustTemplateBackend
    from djust.theming import rust_handlers as theme_handlers

    component_handlers.register_with_rust_engine()
    theme_handlers.register_with_rust_engine()
    rust = DjustTemplateBackend(
        {"NAME": "fidelity-3666", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}}
    )
    django = Engine(
        dirs=[str(Path(component_handlers.__file__).resolve().parent / "templates")],
        libraries=dict(LIBRARIES),
    )
    return rust, django


@pytest.fixture(scope="module")
def facts(tmp_path_factory) -> dict[str, Any]:
    """What a fresh process measures over the whole library (``--fidelity``)."""
    out = tmp_path_factory.mktemp("fidelity3666") / "facts.json"
    env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT / "python"), env.get("PYTHONPATH")]))
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--fidelity", str(out)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    return json.loads(out.read_text(encoding="utf-8"))


def _render(engines, which: str, src: str, context: dict[str, Any]) -> tuple[str, ...]:
    """``("ok", html)`` or ``("err", ExceptionType, message)``."""
    rust, django = engines
    try:
        if which == "rust":
            out = rust.from_string(src).render(dict(context))
        else:
            out = django.from_string(src).render(Context(dict(context)))
    except Exception as exc:  # noqa: BLE001 - the exception IS the observation
        return ("err", type(exc).__name__, str(exc))
    return ("ok", re.sub(r"\s+", " ", str(out)).strip())


def _bound_form():
    return harness._bound_form()


class FakeUser:
    """What ``avatar_group`` reads of a user: ``get_full_name()`` and ``str()``."""

    def __init__(self, first: str, last: str) -> None:
        self.first, self.last = first, last

    def get_full_name(self) -> str:
        return f"{self.first} {self.last}"

    def __str__(self) -> str:
        return self.first.lower()


class Item:
    """A list item that is an object, not a dict (a dataclass row, a model instance)."""

    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)


# --------------------------------------------------------------------------- #


class TestObjectArguments:
    def test_the_object_taking_tags_are_the_ones_the_source_lists(self, facts):
        """Measured from the Python tags, compared with the source in BOTH directions: a new
        tag that reads an attribute of its argument may not get a native handler, and a tag
        listed that no longer does is reported (the list would otherwise only grow)."""
        from djust.components import rust_handlers

        measured = {key.split("|")[0].split(":", 1)[1] for key in facts["object_parameters"]}
        assert measured, "the measurement found no object-taking tag: it is not looking"
        assert measured == set(rust_handlers.OBJECT_ARGUMENT_TAGS), (
            "tags the Python library reads an attribute of, not in OBJECT_ARGUMENT_TAGS: "
            f"{sorted(measured - rust_handlers.OBJECT_ARGUMENT_TAGS)}; listed but measured as "
            f"taking no object: {sorted(rust_handlers.OBJECT_ARGUMENT_TAGS - measured)}"
        )

    def test_no_object_taking_tag_keeps_a_native_handler_class(self):
        from djust.components import rust_handlers

        registered = {name for name, _ in rust_handlers.INLINE_HANDLERS} | {
            name for name, _end, _ in rust_handlers.BLOCK_HANDLERS
        }
        assert not registered & rust_handlers.OBJECT_ARGUMENT_TAGS, sorted(
            registered & rust_handlers.OBJECT_ARGUMENT_TAGS
        )

    def test_every_object_tag_is_registered_with_the_engine_and_every_one_exists(self):
        """Derived from the library: each name is a real tag of it, and registering puts a
        handler in the engine for it (the bridge, not the removed native class)."""
        from djust import _rust
        from djust.components import rust_handlers

        library = import_library(COMPONENTS)
        assert set(rust_handlers.OBJECT_ARGUMENT_TAGS) <= set(library.tags)
        for tag in rust_handlers.OBJECT_ARGUMENT_TAGS:
            _rust.unregister_tag_handler(tag)
            assert not _rust.has_tag_handler(tag)
        rust_handlers.register_with_rust_engine()
        assert all(_rust.has_tag_handler(t) for t in rust_handlers.OBJECT_ARGUMENT_TAGS)

    def test_the_tags_work_in_a_template_that_never_loads_the_library(self):
        """Every other native tag does; the bridge registered by ``{% load %}`` would not
        exist yet in a process whose first template is this one. Through the engine's own
        entry point, so no ``{% load %}`` and no Django loader is involved."""
        from djust import _rust
        from djust.components import rust_handlers

        # Every tag the SHAPES table below exercises, not only those the source lists, so a
        # tag dropped from the list is still unregistered here and fails.
        used = {re.search(r"\{% (\w+)", body).group(1) for _l, body, _c, _m in self.SHAPES}
        for tag in used | set(rust_handlers.OBJECT_ARGUMENT_TAGS):
            _rust.unregister_tag_handler(tag)
        rust_handlers.register_with_rust_engine()
        problems = []
        for label, body, make_context, marker in self.SHAPES:
            try:
                out = _rust.render_template(body, make_context())
            except Exception as exc:  # noqa: BLE001 - the exception is the observation
                problems.append(f"{label}: {type(exc).__name__}: {str(exc)[:120]}")
                continue
            if marker not in out:
                problems.append(f"{label}: {marker!r} not in {out[:120]!r}")
        assert not problems, "\n".join(problems)

    #: (id, template body, a context factory, text the OBJECT contributes). Every shape here
    #: is one the audit named or the derivation measured; the "marker" is what proves the
    #: object reached the tag, so the equality below is never between two empty strings.
    SHAPES = [
        (
            "field_error: BoundField",
            "{% field_error field=bound_form.name %}",
            lambda: {"bound_form": _bound_form()},
            "This field is required.",
        ),
        (
            "field_error: positional BoundField",
            "{% field_error bound_form.name %}",
            lambda: {"bound_form": _bound_form()},
            "This field is required.",
        ),
        (
            "form_errors: Form",
            "{% form_errors form=bound_form %}",
            lambda: {"bound_form": _bound_form()},
            "Form-level",
        ),
        (
            "combobox: BoundField",
            '{% combobox name="x" field=bound_form.name options=opts %}',
            lambda: {"bound_form": _bound_form(), "opts": [{"value": "a", "label": "A"}]},
            'id="id_name"',
        ),
        (
            "dj_radio_group: BoundField",
            "{% dj_radio_group field=bound_form.name options=opts %}",
            lambda: {"bound_form": _bound_form(), "opts": [{"value": "a", "label": "A"}]},
            'id="id_name"',
        ),
        (
            "rich_select: BoundField",
            '{% rich_select name="x" field=bound_form.name options=opts %}',
            lambda: {"bound_form": _bound_form(), "opts": [{"value": "a", "label": "A"}]},
            'id="id_name"',
        ),
        (
            "relative_time: datetime",
            "{% relative_time datetime=when %}",
            lambda: {
                "when": datetime.datetime(2024, 3, 15, 11, 0, 0, tzinfo=datetime.timezone.utc)
            },
            "2024-03-15T11:00:00+00:00",
        ),
        (
            "relative_time: date",
            "{% relative_time datetime=when %}",
            lambda: {"when": datetime.date(2024, 3, 15)},
            "2024-03-15",
        ),
        (
            "avatar_group: user objects",
            "{% avatar_group users=people %}",
            lambda: {"people": [FakeUser("Al", "Bo"), FakeUser("Cy", "Di")]},
            'title="Al Bo"',
        ),
        (
            "avatar_group: dicts",
            "{% avatar_group users=people %}",
            lambda: {"people": [{"name": "Al Bo"}]},
            'title="Al Bo"',
        ),
        (
            "conversation_thread: message objects",
            "{% conversation_thread messages=msgs %}",
            lambda: {"msgs": [Item(sender="user", name="Pat", text="hello there", time="9:00")]},
            "hello there",
        ),
        (
            "conversation_thread: dicts",
            "{% conversation_thread messages=msgs %}",
            lambda: {"msgs": [{"sender": "user", "name": "Pat", "text": "hello there"}]},
            "hello there",
        ),
        (
            "notification_popover: notification objects",
            '{% notification_popover id="np" notifications=notes open=True %}',
            lambda: {"notes": [Item(id="n1", title="Deploy done", message="ok", time="t")]},
            "Deploy done",
        ),
        (
            "toast_container: toast objects",
            "{% toast_container toasts %}",
            lambda: {"toasts": [Item(id="t1", type="info", message="Saved it")]},
            "Saved it",
        ),
        (
            "json_viewer: datetime",
            "{% json_viewer data=when %}",
            lambda: {
                "when": datetime.datetime(2024, 3, 15, 11, 0, 0, tzinfo=datetime.timezone.utc)
            },
            '<span class="dj-json__value">2024-03-15 11:00:00+00:00</span>',
        ),
        (
            "json_viewer: date",
            "{% json_viewer data=when %}",
            lambda: {"when": datetime.date(2024, 3, 15)},
            '<span class="dj-json__value">2024-03-15</span>',
        ),
        (
            "json_viewer: Decimal",
            "{% json_viewer data=amount %}",
            lambda: {"amount": decimal.Decimal("1.5")},
            '<span class="dj-json__value">1.5</span>',
        ),
        (
            "json_viewer: nested in a dict",
            "{% json_viewer data=row %}",
            lambda: {"row": {"when": datetime.date(2024, 3, 15), "n": decimal.Decimal("2")}},
            '<span class="dj-json__value">2024-03-15</span>',
        ),
        (
            "toast_container: dicts",
            "{% toast_container toasts=toasts %}",
            lambda: {"toasts": [{"id": "t1", "type": "info", "message": "Saved it"}]},
            "Saved it",
        ),
    ]

    def test_every_object_shape_renders_the_same_under_both_engines(self, engines):
        """The audit's repro and every other shape the derivation names, in one test so the
        report lists every mismatch together. Each case must also CONTAIN what the object
        carries; an engine pair that both rendered nothing would otherwise pass."""
        problems = []
        for label, body, make_context, marker in self.SHAPES:
            src = "{% load djust_components %}" + body
            django = _render(engines, "django", src, make_context())
            rust = _render(engines, "rust", src, make_context())
            if django[0] != "ok" or marker not in django[1]:
                problems.append(f"{label}: Django did not render {marker!r}: {django}")
            elif rust != django:
                problems.append(f"{label}:\n  django {django}\n  rust   {rust}")
        assert not problems, "\n".join(problems)

    def test_the_shapes_cover_every_object_tag(self):
        """The table above is not the derivation, but it must not fall behind it."""
        from djust.components import rust_handlers

        covered = {
            re.search(r"\{% (\w+)", body).group(1) for _label, body, _ctx, _marker in self.SHAPES
        }
        assert set(rust_handlers.OBJECT_ARGUMENT_TAGS) <= covered, sorted(
            set(rust_handlers.OBJECT_ARGUMENT_TAGS) - covered
        )


# --------------------------------------------------------------------------- #


def _native_simple_tags() -> list[dict[str, Any]]:
    """Every simple/inclusion tag of the two libraries that has a native handler, with the
    parameters Django requires of it. Read from Django's own signatures."""
    from djust import _rust
    from djust.components import rust_handlers
    from djust.template_libraries import _classify
    from djust.theming import rust_handlers as theme_handlers

    rust_handlers.register_with_rust_engine()
    theme_handlers.register_with_rust_engine()
    tags = []
    for label, dotted in LIBRARIES.items():
        library = import_library(dotted)
        for name in sorted(library.tags):
            if not _rust.has_tag_handler(name) or name in ("pane",):
                continue
            func = library.tags[name]
            if _classify(func) not in ("simple_tag", "inclusion_tag"):
                continue
            import inspect

            sig = inspect.signature(inspect.unwrap(func))
            params = [
                p
                for p in sig.parameters.values()
                if p.kind is p.POSITIONAL_OR_KEYWORD and p.name != "context"
            ]
            tags.append(
                {
                    "label": label,
                    "name": name,
                    "required": [p.name for p in params if p.default is p.empty],
                }
            )
    return tags


class TestInvalidInput:
    def test_the_only_variant_one_engine_alone_rejects_is_the_documented_leniency(self, facts):
        """Every variant of every tag the harness renders, and every one that exactly one
        engine rejected. After #3667 that is the unknown keyword the native handler accepts
        on purpose, and only that: it is what ``rust_only_keys`` passes by construction."""
        rows = facts["one_engine_errors"]
        assert rows, "no one-engine error at all: the measurement stopped seeing the leniency"
        unexpected = [
            r
            for r in rows
            if (r["kind"], r["variant"], r["side"])
            != ("unexpected-keyword", "rust_only_keys", "python")
        ]
        assert not unexpected, "\n".join(
            f"{r['tag']} {r['variant']}: {r['side']} raised {r['kind']}: {r['error'][:120]}"
            for r in unexpected
        )

    def test_a_missing_required_parameter_is_refused_like_django(self, engines):
        """For EVERY native simple/inclusion tag with a required parameter and EVERY one of
        its required parameters, given everything else but that one: both engines refuse,
        with the same exception and the same message."""
        problems = []
        checked = 0
        for tag in _native_simple_tags():
            for omitted in tag["required"]:
                others = " ".join(
                    "%s=%s" % (p, variants.token(p, "typical"))
                    for p in tag["required"]
                    if p != omitted
                )
                src = "{% load " + tag["label"] + " %}{% " + tag["name"] + " " + others + " %}"
                django = _render(engines, "django", src, {})
                rust = _render(engines, "rust", src, {})
                checked += 1
                if not (django[:2] == ("err", "TemplateSyntaxError") and rust == django):
                    problems.append(
                        f"{tag['name']} without {omitted}:\n  django {django}\n  rust   {rust}"
                    )
        # One case per required parameter of every tag, derived: a tag whose parameters all
        # have defaults has nothing to omit, which is most of the library.
        expected = sum(len(t["required"]) for t in _native_simple_tags())
        assert checked == expected and checked > 0, (checked, expected)
        assert not problems, "\n".join(problems)

    def test_the_issue_names_are_among_the_missing_required_cases(self):
        names = {t["name"] for t in _native_simple_tags() if t["required"]}
        assert {"data_table", "toast_container"} <= names

    #: (tag call, why Python refuses it). Each is refused by the Python tag when it renders;
    #: the engines must agree on the exception type and message, whichever it is.
    BAD_VALUES = [
        ('{% slider name="s" value="abc" %}', "int('abc')"),
        ('{% slider name="s" value=1 value_end="abc" %}', "int('abc') of the range end"),
        ("{% activity_feed events=ev max=none %}", "int(None)"),
        ('{% activity_feed events=ev max="abc" %}', "int('abc')"),
        ("{% animated_number value=5 separator=none %}", "str.replace(',', None)"),
        ("{% pagination page=1 total_pages=none %}", "None + 1"),
        ("{% progress value=none %}", "int(None)"),
        ('{% progress value="abc" %}', "int('abc')"),
    ]

    def test_invalid_values_raise_the_same_error_in_both_engines(self, engines):
        problems = []
        # ``none`` is a context variable holding None, as in the parity harness; Django's own
        # ``None`` literal is a different parse path than this test is about.
        context = {"ev": [], "none": None}
        for call, why in self.BAD_VALUES:
            src = "{% load djust_components %}" + call
            django = _render(engines, "django", src, context)
            rust = _render(engines, "rust", src, context)
            if django[0] != "err":
                problems.append(f"{call}: Django no longer refuses it ({why}): {django}")
            elif rust != django:
                problems.append(f"{call} ({why}):\n  django {django}\n  rust   {rust}")
        assert not problems, "\n".join(problems)

    def test_valid_values_are_still_accepted(self, engines):
        """The refusal is of the invalid value, not of the tag: the neighbouring valid calls
        render, and render the same numbers in both engines where Django does."""
        calls = [
            '{% slider name="s" value=3 %}',
            '{% slider name="s" value="3" value_end=7 %}',
            "{% slider name='s' %}",
            "{% activity_feed events=ev max=5 %}",
            "{% animated_number value=1234 separator='.' %}",
            "{% pagination page=2 total_pages=5 %}",
            "{% progress value=40 %}",
            "{% progress value=250 %}",
        ]
        for call in calls:
            src = "{% load djust_components %}" + call
            rust = _render(engines, "rust", src, {"ev": []})
            assert rust[0] == "ok", (call, rust)

    def test_notification_center_with_none_renders_like_python(self, engines):
        """The one row that ran the other way: Python treats ``None`` as no notifications,
        the native handler iterated it."""
        src = "{% load djust_components %}{% notification_center notifications=none %}"
        django = _render(engines, "django", src, {})
        rust = _render(engines, "rust", src, {})
        assert django[0] == "ok" and rust[0] == "ok", (django, rust)
        # Both draw the bell and no notification rows. (The markup is the handler's own and
        # differs from the Python tag's; the baseline records that. What matters here is
        # that the call is no longer an error.)
        assert "notif" in rust[1]

    def test_a_list_of_strings_for_notification_popover_leaks_no_repr(self, engines):
        """The leaked ``<built-in method title of str object at 0x...>`` of #3667: a ``str``
        has a ``title`` too, the method. Cause: an item that is not a dict was read with
        ``getattr(item, 'title', '')`` and the method came back as the value."""
        src = (
            '{% load djust_components %}{% notification_popover id="np" '
            "notifications=names open=True %}"
        )
        context = {"names": ["alpha", "beta"]}
        for which in ("django", "rust"):
            out = _render(engines, which, src, context)
            assert out[0] == "ok", (which, out)
            assert "built-in method" not in out[1] and "object at 0x" not in out[1], (which, out)
        assert _render(engines, "rust", src, context) == _render(engines, "django", src, context)

    def test_no_output_of_either_engine_contains_a_python_repr(self, facts):
        """The notification popover was the only one; the scan is over every variant of every
        tag the harness renders, in both engines, so the next one is found by name."""
        assert facts["repr_leaks"] == []
        assert len(facts["measured_tags"]) > 100, "the scan measured too little to mean anything"


class TestTheScannersFindWhatTheyLookFor:
    """Canaries: ``repr_leaks`` and ``one_engine_errors`` report on a hand-made audit, so a
    scanner that found nothing (a broken pattern, a renamed field) cannot pass for a clean
    library. The tests above only ever see the real, clean output."""

    @staticmethod
    def _audit(python: tuple[str, str], rust: tuple[str, str]) -> harness.Audit:
        record = harness.VariantRecord("v", "src", "DIFFERENT", python, rust)
        return harness.Audit(detail={"lib:tag": [record]})

    @pytest.mark.parametrize(
        "leak",
        [
            "&lt;built-in method title of str object at 0xADDR&gt;",
            "<bound method Foo.bar of <Foo object at 0xADDR>>",
            "x <function <lambda> at 0xADDR> y",
            "&lt;function f at 0xADDR&gt;",
        ],
    )
    def test_repr_leaks_reports_each_shape_on_the_engine_that_has_it(self, leak):
        for side in ("python", "rust"):
            outputs = {"python": ("ok", "clean"), "rust": ("ok", "clean")}
            outputs[side] = ("ok", "<div>" + leak + "</div>")
            found = harness.repr_leaks(self._audit(outputs["python"], outputs["rust"]))
            assert [f["side"] for f in found] == [side], (leak, found)

    def test_repr_leaks_ignores_clean_output_and_a_rejected_render(self):
        assert harness.repr_leaks(self._audit(("ok", "<b>fine</b>"), ("ok", "<b>fine</b>"))) == []
        # An error message that mentions a method is an error, not leaked markup.
        assert harness.repr_leaks(self._audit(("err", "built-in method"), ("ok", "fine"))) == []

    @pytest.mark.parametrize(
        "message, kind",
        [
            (
                "TemplateSyntaxError: 'x' received unexpected keyword argument 'k'",
                "unexpected-keyword",
            ),
            (
                "TemplateSyntaxError: 'x' did not receive value(s) for the argument(s): 'a'",
                "missing-required",
            ),
            ("TypeError: int() argument must be a string", "invalid-value"),
        ],
    )
    def test_one_engine_errors_classifies_and_names_the_side(self, message, kind):
        [row] = harness.one_engine_errors(self._audit(("err", message), ("ok", "rendered")))
        assert (row["kind"], row["side"]) == (kind, "python")
        [row] = harness.one_engine_errors(self._audit(("ok", "rendered"), ("err", message)))
        assert row["side"] == "rust"
        assert harness.one_engine_errors(self._audit(("err", message), ("err", message))) == []
        assert harness.one_engine_errors(self._audit(("ok", "a"), ("ok", "b"))) == []


class TestBridgedTagsAreDjangos:
    """What the object tags inherit by being the Python tag's own node (#3753 review)."""

    def test_unknown_keywords_are_refused_exactly_where_the_python_tag_refuses_them(self, engines):
        """A tag that was native accepted any keyword; the bridged tag accepts the keywords
        the Python tag does. Derived per tag from the Python tag's signature: a
        ``simple_tag`` / ``inclusion_tag`` without ``**kwargs`` refuses, a raw tag that reads
        ``key=value`` pairs ignores. Both engines agree for every one of the ten."""
        import inspect

        from djust.components import rust_handlers
        from djust.template_libraries import _classify

        library = import_library(COMPONENTS)
        derived_refusing = set()
        for tag in sorted(rust_handlers.OBJECT_ARGUMENT_TAGS):
            func = library.tags[tag]
            if _classify(func) in ("simple_tag", "inclusion_tag"):
                params = inspect.signature(inspect.unwrap(func)).parameters.values()
                if not any(p.kind is p.VAR_KEYWORD for p in params):
                    derived_refusing.add(tag)
        observed_refusing = set()
        problems = []
        for tag in sorted(rust_handlers.OBJECT_ARGUMENT_TAGS):
            src = "{% load djust_components %}{% " + tag + " zz_unknown_kw=1 %}"
            django = _render(engines, "django", src, {})
            rust = _render(engines, "rust", src, {})
            if django != rust:
                problems.append(f"{tag}:\n  django {django}\n  rust   {rust}")
            if django[0] == "err" and "unexpected keyword argument" in django[2]:
                observed_refusing.add(tag)
        assert not problems, "\n".join(problems)
        assert observed_refusing == derived_refusing, (observed_refusing, derived_refusing)
        assert observed_refusing, (
            "no object tag refuses an unknown keyword: the probe is not looking"
        )
        # The documentation names the refusing tags; it must not fall behind the measurement.
        docs = (ROOT / "docs" / "TEMPLATE_BACKEND.md").read_text(encoding="utf-8")
        section = docs[docs.index("**Unknown keywords on the bridged tags") :].split("\n\n", 1)[0]
        assert all(f"`{tag}`" in section for tag in observed_refusing), (
            sorted(observed_refusing),
            section[:300],
        )

    NO_APP_DIRS = r"""
import json, os, sys, types
import django
from django.conf import settings

os.environ.pop("DJANGO_SETTINGS_MODULE", None)
urls = types.ModuleType("u")
urls.urlpatterns = []
sys.modules["u"] = urls
settings.configure(
    DEBUG=False, SECRET_KEY="x", USE_TZ=True,
    INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth", "djust",
                    "djust.theming", "djust.components"],
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    TEMPLATES=[
        {"BACKEND": "django.template.backends.django.DjangoTemplates", "NAME": "django",
         "DIRS": [], "APP_DIRS": False, "OPTIONS": {}},
        {"BACKEND": "djust.template.backend.DjustTemplateBackend", "NAME": "djust",
         "DIRS": [], "APP_DIRS": False, "OPTIONS": {}},
    ],
    ROOT_URLCONF="u",
)
django.setup()
from django.template import engines

CASES = {
    "toast_container": ("{% toast_container toasts=t %}", {"t": [{"id": "1", "type": "info", "message": "m"}]}),
    "progress": ("{% progress value=3 %}", {}),
    "field_error": ("{% field_error field=none %}", {"none": None}),
    "avatar_group": ("{% avatar_group users=u %}", {"u": [{"name": "A B"}]}),
}
out = {}
for label, (body, ctx) in CASES.items():
    for name in ("django", "djust"):
        try:
            engines[name].from_string("{% load djust_components %}" + body).render(dict(ctx))
            out[label + "|" + name] = "ok"
        except Exception as exc:
            out[label + "|" + name] = type(exc).__name__
print(json.dumps(out))
"""

    def test_print_css_hides_the_classes_the_bridged_toast_emits(self):
        """The bridged ``toast_container`` emits ``dj-toast-container`` / ``dj-toast``, not the
        native handler's ``toast-container`` / ``toast``; the print stylesheet that hides toasts
        has to name the ones that are rendered. Read from the template, not typed here."""
        template = (
            ROOT / "python/djust/components/templates/djust_components/toast.html"
        ).read_text(encoding="utf-8")
        emitted = set(re.findall(r'class="(dj-toast(?:-container)?)[ "]', template))
        assert emitted == {"dj-toast", "dj-toast-container"}, emitted
        css = (ROOT / "python/djust/theming/static/djust_theming/css/print.css").read_text(
            encoding="utf-8"
        )
        hidden = set(
            re.findall(r"^\s*\.([\w-]+),?\s*$", css.split("display: none !important")[0], re.M)
        )
        assert emitted <= hidden, sorted(emitted - hidden)

    def test_an_inclusion_tag_needs_the_template_loader_in_both_engines(self, tmp_path):
        """``toast_container`` is the one inclusion tag of the ten: its node loads
        ``djust_components/toast.html``. With no ``APP_DIRS`` and no ``DIRS`` the Python tag
        fails with ``TemplateDoesNotExist`` under Django's own engine too, so this is the tag's
        contract, not something the bridge added; the native handlers (and the bridged tags that
        are not inclusion tags) need no template file. Measured in a fresh process because the
        engines are configured by settings."""
        script = tmp_path / "no_app_dirs.py"
        script.write_text(self.NO_APP_DIRS, encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
        env["PYTHONPATH"] = os.pathsep.join(
            filter(None, [str(ROOT / "python"), env.get("PYTHONPATH")])
        )
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, proc.stderr[-1500:]
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        assert result["toast_container|django"] == "TemplateDoesNotExist", result
        assert result["toast_container|djust"] == result["toast_container|django"], result
        for tag in ("progress", "field_error", "avatar_group"):
            assert result[tag + "|djust"] == "ok", result


# --------------------------------------------------------------------------- #


class TestMissingArguments:
    """``missing_arguments`` is a transcription of the end of ``parse_bits``; this compares it
    with the real thing over a grid, so a Django change to the quirk is seen here."""

    SIGNATURES = {
        "req": "def t(a, b): return ''",
        "default": "def t(a, b=1): return ''",
        "all_default": "def t(a=1, b=2): return ''",
        "kwonly": "def t(a, *, k): return ''",
        "kwonly_default": "def t(a, *, k=1): return ''",
        "varargs": "def t(a, *rest, b=2): return ''",
        "varkw": "def t(a, **extra): return ''",
        "none": "def t(): return ''",
    }
    CALLS = [
        "",
        "1",
        "1 2",
        "1 2 3",
        "a=1",
        "b=1",
        "k=1",
        "1 k=1",
        "1 b=2",
        "b=2 a=1",
        "1 2 k=3",
        "a=1 b=2 k=3",
        "zz=1",
    ]

    @staticmethod
    def _library(source: str):
        from django import template

        library = template.Library()
        namespace: dict[str, Any] = {}
        exec(source, namespace)  # noqa: S102 - a fixed table of signatures above
        library.simple_tag(namespace["t"], name="t")
        return library

    def test_missing_arguments_agrees_with_parse_bits(self):
        from django.template.base import Token, TokenType

        from djust._positional_args import missing_arguments, positional_spec
        from djust.template_libraries import _parser

        agreed = 0
        problems = []
        for label, source in self.SIGNATURES.items():
            library = self._library(source)
            compile_func = library.tags["t"]
            spec = positional_spec("t", compile_func)
            for call in self.CALLS:
                bits = call.split()
                missing = bool(missing_arguments(spec, bits))
                token = Token(TokenType.BLOCK, ("t " + call).strip())
                try:
                    compile_func(_parser([]), token)
                    refused = None
                except TemplateSyntaxError as exc:
                    refused = str(exc)
                django_missing = refused is not None and "did not receive value(s)" in refused
                other = refused is not None and not django_missing
                if other:
                    # Refused for another reason (unexpected keyword, too many positional
                    # arguments): the other checks own those, not this function.
                    continue
                agreed += 1
                if missing != django_missing:
                    problems.append(f"{label} {call!r}: ours {missing}, Django {refused}")
        assert agreed > 25, f"only {agreed} cases compared"
        assert not problems, "\n".join(problems)


# --------------------------------------------------------------------------- #
# #3756: tags that dispatch on a value's TYPE


#: The types a template realistically passes where a tag takes a scalar or an object. The
#: others measured (``tuple``, ``UUID``, ``bytes``, ``set``, ``frozenset``) are not in the set
#: that must be fixed or named: a tuple in a ``class=`` slot or a UUID in a ``count=`` slot is not
#: an input anyone writes. They are counted, though (#3768): ``RESIDUAL_STANDIN_LOSS`` pins what
#: is left of the 495 cells over every type, so the number cannot grow unseen.
REALISTIC_TYPES = frozenset(
    {
        "datetime",
        "naive datetime",
        "date",
        "time",
        "timedelta",
        "Decimal",
        "Enum",
        "IntEnum",
        "Path",
    }
)

#: ``tag`` -> why the Python tag still renders a realistic value differently from the Rust
#: handler, with the engines in agreement on the value's stand-in (so it IS the lost type).
#: Each is a temporal value in a CSS-class attribute slot (``map_picker``'s ``Path`` was the
#: fourth until #3768 put the type back). Equality in both directions: a tag that stops being
#: wrong must be removed from here, and a new one must be looked at rather than appended.
KNOWN_TYPE_LOSS = {
    "theme_mode_toggle": "button_class: a date / datetime / time as a CSS class",
    "theme_preset_selector": "dropdown_class: a date / datetime / time as a CSS class",
    "theme_switcher": "button_class, dropdown_class: a date / datetime / time as a CSS class",
}

#: What the stand-in measurement still finds over ALL fifteen value types, not only the realistic
#: ones (#3768): ``{type: {"tag:param": cells}}``. 495 cells before the fix; this is the rest,
#: and it can only shrink. Equality in both directions, so a cell that gets fixed must be removed
#: and a cell that appears must be looked at, not appended.
#:
#: * ``tuple`` (18): a tuple in a slot that takes a LIST. The Python tag is the strict one there
#:   (``if not isinstance(data, list)`` renders nothing for a tuple) and the native handler,
#:   handed a ``TupleValue`` (a list), renders the data, so the native engine is MORE lenient than
#:   Django's. Deliberately not made strict: a tuple is an ordinary thing to pass for a list
#:   (Django ``choices``), and a real ``tuple`` here emptied ``dependent_select`` on the first try
#:   (``test_standin_types_3768.py::TestAListSlotKeepsWorking``).
#: * ``set`` / ``frozenset`` / ``bytes`` (16, ``color_picker:swatches`` joined in #3669): iterable, and an iterable crosses to a handler as
#:   the engine's ``TemplateObject`` wrapper, not as itself, so the type cannot be recovered
#:   there. ``avatar_group`` (a ``{% load %}`` bridge tag) hits the same wrapper.
#: * ``date`` / ``datetime`` / ``time`` (16): ``theme_*`` handlers, a temporal value in a CSS-class
#:   slot; the same four as ``KNOWN_TYPE_LOSS``, once per type.
#: * ``UUID`` (1): ``notification_center`` ignores its ``unread_count`` argument, which no type
#:   recovery reaches.
#:
#: No input anyone writes on purpose, which is why they are recorded, not fixed.
RESIDUAL_STANDIN_LOSS: dict[str, dict[str, int]] = {
    "tuple": {
        "bar_chart:data": 1,
        "collab_selection:users": 1,
        "comparison_table:plans": 1,
        "cursors:users": 1,
        "export_dialog:formats": 1,
        "image_upload_preview:previews": 1,
        "lightbox:images": 1,
        "line_chart:series": 1,
        "log_viewer:lines": 1,
        "masonry_grid:items": 1,
        "mentions_input:users": 1,
        "meter:segments": 1,
        "presence_avatars:users": 1,
        "reactions:options": 1,
        "scroll_spy:sections": 1,
        "sparkline:data": 1,
        "terminal:output": 1,
        "treemap:data": 1,
    },
    "set": {
        "color_picker:swatches": 1,
        "multi_select:options": 1,
        "tag_input:suggestions": 1,
        "tag_input:tags": 1,
    },
    "frozenset": {
        "color_picker:swatches": 1,
        "multi_select:options": 1,
        "tag_input:suggestions": 1,
        "tag_input:tags": 1,
    },
    "bytes": {
        "avatar_group:users": 1,
        "color_picker:swatches": 1,
        "multi_select:options": 1,
        "tag_input:suggestions": 1,
        "tag_input:tags": 1,
    },
    "UUID": {"notification_center:unread_count": 1},
    **{
        kind: {
            "theme_mode_toggle:button_class": 1,
            "theme_preset_selector:dropdown_class": 1,
            "theme_switcher:button_class": 1,
            "theme_switcher:dropdown_class": 1,
        }
        for kind in ("date", "datetime", "naive datetime", "time")
    },
}

#: The cell count before #3768 (``typed_values.differences``: 208 tags, 15 types, 21,390 cells).
STANDIN_LOSS_BEFORE_3768 = 495


class TestTypeDispatch:
    """``json_viewer`` branches on ``isinstance`` and reads no attribute, so the recorder that
    found the nine attribute-reading tags could not see it (#3756)."""

    TYPES = {
        "datetime": lambda: datetime.datetime(2024, 3, 15, 11, 0, 0, tzinfo=datetime.timezone.utc),
        "naive datetime": lambda: datetime.datetime(2024, 3, 15, 11, 0, 0),
        "date": lambda: datetime.date(2024, 3, 15),
        "time": lambda: datetime.time(11, 30),
        "timedelta": lambda: datetime.timedelta(minutes=90),
        "Decimal": lambda: decimal.Decimal("1.5"),
        "UUID": lambda: uuid.UUID(int=7),
        "bytes": lambda: b"ab",
        "Path": lambda: pathlib.PurePosixPath("/a/b"),
        "tuple": lambda: (1, 2),
        "set": lambda: {1},
        "float": lambda: 2.5,
        "bool": lambda: True,
        "int": lambda: 3,
        "None": lambda: None,
        "str": lambda: "x",
    }

    @pytest.mark.parametrize("label", sorted(TYPES))
    def test_json_viewer_renders_every_type_like_django(self, engines, label):
        src = "{% load djust_components %}{% json_viewer data=v %}"
        django = _render(engines, "django", src, {"v": self.TYPES[label]()})
        rust = _render(engines, "rust", src, {"v": self.TYPES[label]()})
        assert django[0] == "ok", django
        assert rust == django, f"{label}:\n  django {django}\n  rust   {rust}"

    def test_the_reported_cells(self, engines):
        """The three the issue measured, stated outright: no type modifier for a non-JSON scalar."""
        src = "{% load djust_components %}{% json_viewer data=v %}"
        for label, expect in (
            ("datetime", '<span class="dj-json__value">2024-03-15 11:00:00+00:00</span>'),
            ("date", '<span class="dj-json__value">2024-03-15</span>'),
            ("Decimal", '<span class="dj-json__value">1.5</span>'),
        ):
            rust = _render(engines, "rust", src, {"v": self.TYPES[label]()})
            assert expect in rust[1], (label, rust[1][:300])

    def test_random_nested_values_render_like_django(self, engines):
        """Seeded random trees of dicts / lists / mixed scalar types: the curated table above
        samples top-level values, and the dispatch recurses."""
        rng = random.Random(3756)
        leaves = [self.TYPES[k] for k in ("datetime", "date", "Decimal", "UUID", "int", "float")]
        leaves += [lambda: "s<b>", lambda: None, lambda: True, lambda: 0, lambda: ""]

        def tree(depth: int) -> Any:
            roll = rng.random()
            if depth == 0 or roll < 0.4:
                return rng.choice(leaves)()
            if roll < 0.7:
                return [tree(depth - 1) for _ in range(rng.randint(0, 3))]
            return {f"k{i}": tree(depth - 1) for i in range(rng.randint(0, 3))}

        src = "{% load djust_components %}{% json_viewer data=v collapsed_depth=9 %}"
        bad = []
        for _ in range(150):
            value = tree(3)
            django = _render(engines, "django", src, {"v": value})
            rust = _render(engines, "rust", src, {"v": value})
            if rust != django:
                bad.append(f"{value!r}\n  django {django}\n  rust   {rust}")
        assert not bad, f"{len(bad)} mismatches:\n" + "\n".join(bad[:3])

    def test_json_viewer_is_native_and_declares_typed_args(self):
        """``json_viewer`` stays a native handler and asks the engine for its operands' TYPES
        (``TYPED_ARGS``). It is not on the ``{% load %}`` bridge: that walks raw objects with no
        floor (``test_typed_args_floor_3756.py``)."""
        from djust.components import rust_handlers

        handlers = dict(rust_handlers.INLINE_HANDLERS)
        assert "json_viewer" in handlers
        assert "json_viewer" not in rust_handlers.OBJECT_ARGUMENT_TAGS
        declared = {
            name
            for name, handler in rust_handlers.INLINE_HANDLERS
            if getattr(handler, "TYPED_ARGS", False)
        }
        assert declared == set(rust_handlers.TYPED_ARGUMENT_TAGS), (declared, "the set drifted")

    def test_the_engine_hands_a_typed_handler_the_value_not_its_stand_in(self):
        """The mechanism, without the tag: a handler declaring ``TYPED_ARGS`` receives the
        ``datetime`` / ``Decimal`` the engine resolved, under the reserved context key; one that
        does not declare it receives nothing there and pays nothing."""
        from djust import _rust
        from djust.components.rust_handlers import TYPED_ARGS_KEY

        seen: dict[str, Any] = {}

        class Typed:
            TYPED_ARGS = True

            def render(self, args, context):
                seen["typed"] = context.get(TYPED_ARGS_KEY)
                seen["args"] = list(args)
                return ""

        class Plain:
            def render(self, args, context):
                seen["plain"] = TYPED_ARGS_KEY in context
                return ""

        when = datetime.datetime(2024, 3, 15, 11, 0, 0, tzinfo=datetime.timezone.utc)
        amount = decimal.Decimal("1.5")
        _rust.register_tag_handler("typed_probe_3756", Typed())
        _rust.register_tag_handler("plain_probe_3756", Plain())
        try:
            ctx = {"when": when, "amount": amount, "row": {"n": amount}}
            _rust.render_template(
                '{% typed_probe_3756 a=when b=amount c=row d="lit" e=7 f=missing %}', ctx
            )
            _rust.render_template("{% plain_probe_3756 a=when %}", ctx)
        finally:
            _rust.unregister_tag_handler("typed_probe_3756")
            _rust.unregister_tag_handler("plain_probe_3756")
        typed = seen["typed"]
        assert typed["a"] == when and type(typed["a"]) is datetime.datetime
        assert typed["b"] == amount and type(typed["b"]) is decimal.Decimal
        assert typed["c"] == {"n": amount} and type(typed["c"]["n"]) is decimal.Decimal
        assert typed["e"] == 7
        assert "d" not in typed, "a quoted literal has no type to recover"
        assert "f" not in typed, "an unresolved operand falls back to the string argument"
        assert seen["plain"] is False

    def test_a_lone_surrogate_inside_a_dict_or_list_renders(self, engines):
        """User-supplied JSON can hold a lone surrogate (``json.loads('"\\ud83d"')``); the first
        version of this PR raised ``Handler 'json_viewer' render() must return a string`` for it,
        where Django and main render it."""
        src = "{% load djust_components %}{% json_viewer data=v %}"
        for raw in ('{"c": "\\ud83d"}', '["\\ud83d", {"k": "\\ude00"}]', '"\\ud83d"'):
            value = json.loads(raw)
            django = _render(engines, "django", src, {"v": value})
            rust = _render(engines, "rust", src, {"v": value})
            assert django[0] == "ok", django
            assert rust[0] == "ok", (raw, rust)

    def test_the_measurement_finds_the_tag_it_was_written_for(self, facts):
        """Looking is not the same as finding: the derivation must report ``json_viewer`` as
        type-sensitive for the issue's three types, or it would pass over an empty table."""
        typed = facts["typed_values"]
        assert typed["cells"] > 10000, typed["cells"]
        cells = {
            (d["tag"], d["param"], d["type"])
            for d in typed["dispatches"]
            if d["tag"].endswith("json_viewer")
        }
        for label in ("datetime", "date", "Decimal"):
            assert ("djust_components:json_viewer", "data", label) in cells, sorted(cells)

    def test_every_type_sensitive_tag_of_a_realistic_type_is_handled_or_known(self, facts):
        """Both directions. A tag whose Python output depends on a realistic value's type, where
        the engines agree on the stand-in and still disagree on the value, must be bridged
        (``OBJECT_ARGUMENT_TAGS``), typed (``TYPED_ARGUMENT_TAGS``) or be in ``KNOWN_TYPE_LOSS``.
        And ``KNOWN_TYPE_LOSS`` may only name tags that are still wrong: an entry that is no
        longer wrong fails here, and so does a new one that is not recorded."""
        from djust.components import rust_handlers

        handled = set(rust_handlers.OBJECT_ARGUMENT_TAGS) | set(rust_handlers.TYPED_ARGUMENT_TAGS)
        lost = {
            d["tag"].split(":", 1)[1]
            for d in facts["typed_values"]["dispatches"]
            if d["type"] in REALISTIC_TYPES and d["isolated"] and not d["rust_agrees"]
        }
        assert not lost & handled, f"handled and still wrong: {sorted(lost & handled)}"
        assert lost == set(KNOWN_TYPE_LOSS), (
            f"type loss not recorded: {sorted(lost - set(KNOWN_TYPE_LOSS))}; "
            f"recorded but no longer lost: {sorted(set(KNOWN_TYPE_LOSS) - lost)}"
        )

    def test_the_stand_in_loss_is_exactly_the_recorded_residual(self, facts):
        """#3768. Over every value type, the cells where a native handler is handed a stand-in and
        the Python tag tells it from the value: ``RESIDUAL_STANDIN_LOSS`` and nothing else, in
        both directions. The number can only go down: a cell that appears fails here until it is
        looked at, and one that is fixed fails here until it is removed."""
        measured: dict[str, dict[str, int]] = {}
        for d in facts["typed_values"]["dispatches"]:
            if d["isolated"] and not d["rust_agrees"]:
                cell = "%s:%s" % (d["tag"].split(":", 1)[1], d["param"])
                by_tag = measured.setdefault(d["type"], {})
                by_tag[cell] = by_tag.get(cell, 0) + 1
        assert measured == RESIDUAL_STANDIN_LOSS, (
            f"new: { {t: sorted(set(m) - set(RESIDUAL_STANDIN_LOSS.get(t, {}))) for t, m in measured.items()} }; "
            f"fixed: { {t: sorted(set(m) - set(measured.get(t, {}))) for t, m in RESIDUAL_STANDIN_LOSS.items()} }"
        )

    def test_the_fix_removed_the_cells_it_claims(self, facts):
        """The headline number, derived rather than restated (#2727): the recorded residual's size
        is computed from the pin, the before-count is the measurement the issue reported, and the
        measurement must still be the 21,390 cells those were counted over, plus the 135 cells
        #3669 added by making the native handlers read more parameters, plus the 210 cells #3961
        added (the 13 parameters of ``dj_radio_group`` and ``status_indicator``'s ``live``, over
        the same 15 types; re-measured, not estimated), plus the 45 cells #3962 added (the three
        parameters it gave ``dj_radio`` (``id``), ``rating`` (``name``) and ``toggle_group``
        (``form_field``), over the same 15 types; re-measured, not estimated)."""
        residual = sum(sum(m.values()) for m in RESIDUAL_STANDIN_LOSS.values())
        assert residual < STANDIN_LOSS_BEFORE_3768
        assert facts["typed_values"]["cells"] == 21390 + 135 + 15 * (13 + 1) + 15 * 3, (
            "the cell grid changed: re-derive both counts"
        )
        measured = sum(
            1 for d in facts["typed_values"]["dispatches"] if d["isolated"] and not d["rust_agrees"]
        )
        assert measured == residual

    def test_a_bridged_or_typed_tag_agrees_with_django_for_every_realistic_type(self, facts):
        """A bridged tag is Django's own node and a typed one is handed the real value, so
        neither differs from Django for a type a template realistically passes. This is the gate:
        it fails if ``json_viewer`` loses its typed arguments.

        Restricted to ``REALISTIC_TYPES`` because the bridge is not exact for an exotic ITERABLE:
        a ``bytes`` given to ``avatar_group users=`` reaches the node as the engine's wrapper,
        which cannot be sliced (``users[:5]``), where Django slices the real ``bytes``
        (tracked at #3768)."""
        from djust.components import rust_handlers

        handled = set(rust_handlers.OBJECT_ARGUMENT_TAGS) | set(rust_handlers.TYPED_ARGUMENT_TAGS)
        wrong = sorted(
            (d["tag"], d["param"], d["type"])
            for d in facts["typed_values"]["dispatches"]
            if d["tag"].split(":", 1)[1] in handled
            and d["type"] in REALISTIC_TYPES
            and not d["rust_agrees"]
        )
        assert not wrong, wrong[:10]

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "known gap (#3768): serialize_value rebuilds a top-level dict SUBCLASS (OrderedDict, "
            "defaultdict) as a plain dict, so a tuple / list / dict holding one spells `{...}` "
            "where Django spells `OrderedDict({...})`. Same cause and same output on main for "
            "`{{ v }}`; not json_viewer's. Remove this marker when serialize_value stops rebuilding."
        ),
    )
    def test_a_tuple_holding_an_ordereddict_spells_like_django(self, engines):
        import collections

        src = "{% load djust_components %}{% json_viewer data=v %}"
        value = (collections.OrderedDict(a=1),)
        django = _render(engines, "django", src, {"v": value})
        rust = _render(engines, "rust", src, {"v": value})
        assert django[0] == "ok" and rust == django
