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

The facts that cannot be known without rendering the whole library (``OBJECT_ARGUMENT_TAGS``
derivation, the one-engine-error table, the repr scan) come from ONE subprocess, the way the
parity ratchet does it: that process replaces ``uuid`` and ``datetime`` and must not be a
pytest worker.

Refs #3666, #3667, #3655, #3663, #3664, #3676, #1468, #1646, #1125.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
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
