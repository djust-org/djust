"""Native Rust tag handlers take the same POSITIONAL parameters as the Python tags (#3664).

``{% dj_tag "Python" %}`` is valid Django: ``dj_tag`` is a ``simple_tag`` whose
first parameter is ``label``. The native handler reads ``key=value`` strings and
used to skip every argument without an ``=``, so the Rust engine rendered
``<span class="tag tag-default"></span>`` (empty) for a template that works under
Django. Worse, a positional VALUE containing ``=`` (``{% dj_tag name %}`` with
``name = "variant=primary"``) was parsed as a keyword.

The fix is one mechanism (``djust._positional_args``): at template compilation the
engine's ``rewrite_args_at_parse`` hook names each positional token after the
parameter the Python tag declares at that position, and from there the engine
treats it exactly like the keyword it stands for. These tests derive everything
from the Django tags' own signatures; there is no per-tag list to forget a new tag.

What each class proves:

* ``TestEveryTagTakesItsPositionalParameters``: for EVERY native simple/inclusion
  tag and EVERY prefix of its positional parameters, the Rust engine renders the
  positional form identically to the keyword form, and Django renders the two
  identically too. Where the keyword form already agrees with Django, the
  positional form agrees with Django. The sweep is not vacuous: a tag whose
  handler reads none of the values it is given is reported, not skipped.
* ``TestPositionalValuesAreValues``: a positional token is a value (literal,
  variable, filter chain, a string containing ``=``), never a keyword.
* ``TestErrors``: too many positional arguments, a positional after a keyword and
  a parameter given twice are refused with Django's messages.
* ``TestTagsWithoutPositionalParameters``: raw ``@register.tag`` tags and
  ``*args`` tags are not rewritten.

Refs #3664, #3663, #3655, #3666, #1468, #1646, #1125.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import Any

import pytest
from django.template import Context, Engine
from django.template.library import import_library

from scripts.lib.template_library_parity import variants

LIBRARIES = {
    "djust_components": "djust.components.templatetags.djust_components",
    "theme_tags": "djust.theming.templatetags.theme_tags",
}

SIMPLE = ("simple_tag", "inclusion_tag")


# --------------------------------------------------------------------------- #
# Engines


@pytest.fixture(scope="module")
def engines():
    from djust.components import rust_handlers as component_handlers
    from djust.template.backend import DjustTemplateBackend
    from djust.theming import rust_handlers as theme_handlers

    component_handlers.register_with_rust_engine()
    theme_handlers.register_with_rust_engine()
    rust = DjustTemplateBackend(
        {"NAME": "positional-3664", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}}
    )
    django = Engine(
        dirs=[str(Path(component_handlers.__file__).resolve().parent / "templates")],
        libraries=dict(LIBRARIES),
    )
    return rust, django


def _context() -> dict[str, Any]:
    from scripts.lib.template_library_parity import harness

    ctx = harness.make_context(with_form=False)
    ctx["request"] = None
    ctx["name"] = "Bob"
    ctx["eq"] = "variant=primary"
    ctx["eq_event"] = "event=pwn"
    from djust.theming.presets import list_presets

    ctx["preset_names"] = [list_presets()[0]["name"]]
    ctx["a_dict"] = {"success": "badge-success"}
    ctx["records"] = [
        {"id": "r1", "message": "Saved", "type": "success", "level": "success", "title": "Saved",
         "label": "L1", "name": "n1", "value": 1},
        {"id": "r2", "message": "Failed", "type": "error", "level": "error", "title": "Failed",
         "label": "L2", "name": "n2", "value": 2},
    ]  # fmt: skip
    return ctx


_ID = re.compile(r"(?<=[-_])[0-9a-f]{6,12}\b")


def _norm(html: str) -> str:
    return re.sub(r"\s+", " ", _ID.sub("ID", html)).strip()


def _render(engines, which: str, src: str) -> tuple[str, ...]:
    """``("ok", html)`` or ``("err", ExceptionType, message)``."""
    rust, django = engines
    try:
        if which == "rust":
            out = rust.from_string(src).render(_context())
        else:
            out = django.from_string(src).render(Context(_context()))
    except Exception as exc:  # noqa: BLE001 - the exception IS the observation
        return ("err", type(exc).__name__, str(exc))
    return ("ok", _norm(str(out)))


# --------------------------------------------------------------------------- #
# The tags: derived from Django's own signatures, independently of the mechanism


def _native_names() -> dict[str, set[str]]:
    from djust.components import rust_handlers as component_handlers
    from djust.theming import rust_handlers as theme_handlers

    return {
        "djust_components": {name for name, _ in component_handlers.INLINE_HANDLERS}
        | {name for name, _end, _ in component_handlers.BLOCK_HANDLERS},
        "theme_tags": set(theme_handlers._THEME_TAG_NAMES),
    }


def _tags() -> list[dict[str, Any]]:
    from djust.template_libraries import _classify

    out = []
    for label, dotted in LIBRARIES.items():
        library = import_library(dotted)
        for name in sorted(_native_names()[label] & set(library.tags)):
            func = library.tags[name]
            kind = _classify(func)
            if kind not in SIMPLE:
                continue
            sig = inspect.signature(inspect.unwrap(func))
            params = [
                p
                for p in sig.parameters.values()
                if p.kind is p.POSITIONAL_OR_KEYWORD and p.name != "context"
            ]
            out.append(
                {
                    "label": label,
                    "name": name,
                    "kind": kind,
                    "params": [p.name for p in params],
                    "defaults": {p.name: p.default for p in params},
                    "required": [p.name for p in params if p.default is p.empty],
                    "varargs": any(p.kind is p.VAR_POSITIONAL for p in sig.parameters.values()),
                }
            )
    return out


TAGS = _tags()
#: The tags that have a positional parameter to map (``theme_mode`` takes only ``context``).
WITH_PARAMS = [t for t in TAGS if t["params"]]
IDS = [f"{t['label']}:{t['name']}" for t in WITH_PARAMS]

#: Tags for which no argument this sweep can pass changes the Rust output, and why. Without
#: this table the vacuity check below would be silenced for them; with it each entry is a
#: claim ``test_the_exemption_is_still_true`` re-proves, so it cannot outlive its reason.
NO_OBSERVABLE_ARGUMENT = {
    "form_errors": "takes a Form object, which never reaches a native handler (#3666)",
    "field_error": "takes a BoundField object, which never reaches a native handler (#3666)",
    "theme_framework_overrides": "renders nothing unless a CSS-framework theme pack is active",
    "toast_container": (
        "its handler ignores its arguments, keyword or positional, and reads the context "
        "variable `toasts` (a keyword-form difference from the Python tag, #3663)"
    ),
}

#: Values to try, in order, for a parameter whose ``variants.token`` the Django tag rejects
#: or ignores (a slider's ``value`` is a number, an icon's ``name`` a known icon, a
#: skeleton's ``skeleton_type`` a known type, a toast container's ``toasts`` a list, a
#: preset selector's ``presets`` registered preset names, a ``variant_map`` a dict).
_CANDIDATES = (
    "3", '"check"', '"card"', '"text"', "lst_dict", "lst_str", "preset_names", "a_dict", "True", '"3"',
)  # fmt: skip

_VALUES: dict[tuple[str, str], dict[str, str]] = {}


def _values(engines, tag: dict[str, Any]) -> dict[str, str]:
    """A template expression for every parameter of ``tag`` that Django accepts and
    that changes what Django renders, found by asking Django rather than by a per-tag
    table.

    Parameters are settled in order, each against the call built from the ones
    settled before it (a ``frozen_left`` is only wrong once there are ``rows``), the
    required ones first. For each, the first candidate wins that (a) makes Django's
    keyword call render and (b) changes its output. A parameter nothing changes gets
    the first candidate that renders, so the tag is still exercised.
    """
    key = (tag["label"], tag["name"])
    if key in _VALUES:
        return _VALUES[key]
    head = "{% load " + tag["label"] + " %}{% " + tag["name"] + " "
    chosen: dict[str, str] = {}

    def django(extra: str = "") -> tuple[str, ...]:
        args = " ".join(f"{p}={v}" for p, v in chosen.items())
        return _render(engines, "django", head + args + " " + extra + " %}")

    order = [*tag["required"], *(p for p in tag["params"] if p not in tag["required"])]
    for param in order:
        default = tag["defaults"][param]
        flipped = ["False" if default else "True"] if isinstance(default, bool) else []
        # A required parameter is the data the tag is about: try a list of records first.
        data = ["records"] if param in tag["required"] else []
        candidates = [*flipped, *data, variants.token(param, "typical"), *_CANDIDATES]
        baseline = django()
        first_ok = None
        pick = None
        for cand in candidates:
            out = django(f"{param}={cand}")
            if out[0] != "ok":
                continue
            first_ok = first_ok or cand
            if out != baseline or baseline[0] != "ok":
                pick = cand
                break
        chosen[param] = pick or first_ok or variants.token(param, "typical")
    _VALUES[key] = chosen
    return chosen


def _value(engines, tag: dict[str, Any], param: str) -> str:
    return _values(engines, tag)[param]


def _call(engines, tag: dict[str, Any], n: int, *, positional: bool) -> str:
    chosen = tag["params"][:n]
    values = _values(engines, tag)
    if positional:
        args = " ".join(values[p] for p in chosen)
    else:
        args = " ".join(f"{p}={values[p]}" for p in chosen)
    return "{% load " + tag["label"] + " %}{% " + tag["name"] + (" " + args if args else "") + " %}"


OBSERVABLE = [t for t in WITH_PARAMS if t["name"] not in NO_OBSERVABLE_ARGUMENT]


def _prefixes(tag: dict[str, Any]) -> list[int]:
    """Every prefix length Django accepts: it must cover the required parameters."""
    first = max((tag["params"].index(p) + 1 for p in tag["required"]), default=0)
    return list(range(max(first, 1), len(tag["params"]) + 1))


def _noargs(tag: dict[str, Any]) -> str:
    return "{% load " + tag["label"] + " %}{% " + tag["name"] + " %}"


# --------------------------------------------------------------------------- #


def test_the_sweep_sees_the_tags_the_issue_names():
    """The sweep is derived, so pin that it did not derive an empty or partial set."""
    names = {t["name"] for t in TAGS}
    named_in_the_issue = {
        "code_snippet", "copy_button", "currency_input", "dj_tag", "fab", "gauge",
        "kanban_board", "multi_select", "number_stepper", "otp_input", "rating",
        "search_input", "split_button", "tag_input", "tree_view", "theme_mode_toggle",
        "theme_preset_selector",
    }  # fmt: skip
    assert named_in_the_issue <= names, sorted(named_in_the_issue - names)
    assert len(TAGS) >= 60, len(TAGS)


class TestEveryTagTakesItsPositionalParameters:
    @pytest.mark.parametrize("tag", WITH_PARAMS, ids=IDS)
    def test_positional_equals_keyword_in_the_rust_engine(self, engines, tag):
        """The call is the same call. This is the issue: the positional form used to
        render the tag with every argument ignored."""
        for n in _prefixes(tag):
            positional = _render(engines, "rust", _call(engines, tag, n, positional=True))
            keyword = _render(engines, "rust", _call(engines, tag, n, positional=False))
            assert positional == keyword, (
                f"{tag['name']}: first {n} parameter(s) {tag['params'][:n]} by position "
                f"render differently from the same by keyword"
            )

    @pytest.mark.parametrize("tag", WITH_PARAMS, ids=IDS)
    def test_positional_equals_keyword_in_django(self, engines, tag):
        """The oracle: Django maps position to parameter name exactly as claimed. If
        this failed, the sweep's values would be unfit and the Rust comparison above
        would prove nothing."""
        for n in _prefixes(tag):
            positional = _render(engines, "django", _call(engines, tag, n, positional=True))
            keyword = _render(engines, "django", _call(engines, tag, n, positional=False))
            assert positional[0] == "ok", positional
            assert positional == keyword, (tag["name"], n)

    @pytest.mark.parametrize("tag", WITH_PARAMS, ids=IDS)
    def test_positional_agrees_with_django_wherever_the_keyword_form_does(self, engines, tag):
        """Positional is not a second way to differ from Django. Where the keyword
        call already renders what Django renders, the positional call does too; where
        the keyword call differs (the rest of #3663), positional inherits that
        difference and no other."""
        for n in _prefixes(tag):
            keyword_rust = _render(engines, "rust", _call(engines, tag, n, positional=False))
            keyword_django = _render(engines, "django", _call(engines, tag, n, positional=False))
            if keyword_rust != keyword_django:
                continue
            positional_rust = _render(engines, "rust", _call(engines, tag, n, positional=True))
            assert positional_rust == _render(
                engines, "django", _call(engines, tag, n, positional=True)
            )

    @pytest.mark.parametrize(
        "tag", OBSERVABLE, ids=[f"{t['label']}:{t['name']}" for t in OBSERVABLE]
    )
    def test_the_sweep_is_not_vacuous_for_this_tag(self, engines, tag):
        """A handler that reads none of the values it is handed would pass every check
        above (the positional and the keyword form both render the defaults). Some
        parameter must change the Rust output, and that change must be reachable by
        position."""
        baseline = _render(engines, "rust", _noargs(tag))
        changed_by_keyword = [
            n
            for n in _prefixes(tag)
            if _render(engines, "rust", _call(engines, tag, n, positional=False)) != baseline
        ]
        assert changed_by_keyword, f"{tag['name']}: the handler read none of {tag['params']}"
        n = changed_by_keyword[0]
        assert _render(engines, "rust", _call(engines, tag, n, positional=True)) != baseline, (
            f"{tag['name']}: its first {n} parameter(s) change the output by keyword and "
            f"are ignored by position"
        )

    @pytest.mark.parametrize("name", sorted(NO_OBSERVABLE_ARGUMENT))
    def test_the_exemption_is_still_true(self, engines, name):
        """Each exemption is a claim about the Rust output. When a handler starts reading
        the argument (#3666 fixed, a pack configured), this fails and the entry must go."""
        tag = next(t for t in TAGS if t["name"] == name)
        baseline = _render(engines, "rust", _noargs(tag))
        for n in _prefixes(tag):
            assert _render(engines, "rust", _call(engines, tag, n, positional=False)) == baseline


class TestPositionalValuesAreValues:
    def test_the_issue_repro(self, engines):
        src = '{% load djust_components %}{% dj_tag "Python" %}'
        assert _render(engines, "rust", src) == (
            "ok",
            '<span class="tag tag-default">Python</span>',
        )
        assert _render(engines, "rust", src) == _render(engines, "django", src)

    @pytest.mark.parametrize(
        "args, expected",
        [
            ('"Python" "primary"', '<span class="tag tag-primary">Python</span>'),
            ("name", '<span class="tag tag-default">Bob</span>'),
            ('name|upper "primary"', '<span class="tag tag-primary">BOB</span>'),
            ('"a=b"', '<span class="tag tag-default">a=b</span>'),
            ("'single'", '<span class="tag tag-default">single</span>'),
            ('"Py" variant="primary"', '<span class="tag tag-primary">Py</span>'),
        ],
    )
    def test_a_positional_token_is_a_value(self, engines, args, expected):
        src = "{% load djust_components %}{% dj_tag " + args + " %}"
        assert _render(engines, "rust", src) == ("ok", expected)
        assert _render(engines, "django", src) == ("ok", expected)

    def test_a_positional_value_containing_an_equals_sign_is_not_a_keyword(self, engines):
        """The hole under the issue: the engine hands a handler the RESOLVED text, so
        ``{% dj_tag eq %}`` with ``eq = "variant=primary"`` reached it as
        ``variant=primary`` and was read as the keyword ``variant``. A context value
        (user-controlled text in practice) could set any parameter of the tag it was
        passed to, including ``event``."""
        src = "{% load djust_components %}{% dj_tag eq %}"
        out = _render(engines, "rust", src)
        assert out == ("ok", '<span class="tag tag-default">variant=primary</span>'), out
        assert out == _render(engines, "django", src)

    def test_a_positional_value_cannot_inject_an_event_name(self, engines):
        """The same hole one parameter over: ``event`` decides which handler a click
        calls on the server."""
        src = "{% load djust_components %}{% dj_button eq_event %}"
        rust = _render(engines, "rust", src)
        assert rust[0] == "ok", rust
        assert "event=pwn" in rust[1]
        assert 'dj-click="pwn"' not in rust[1]
        assert rust == _render(engines, "django", src)

    def test_as_var_is_not_a_positional_argument(self, engines):
        """``{% dj_tag "Py" as x %}`` takes ``as x`` first, as Django does; neither word
        is a positional argument (``as`` would be the ``variant``, ``x`` the
        ``dismissible`` flag). The Rust handlers do not bind ``x`` (they render inline),
        which is a separate, older difference; what is pinned is that the call is read
        as ``{% dj_tag "Py" %}``."""
        with_as = _render(engines, "rust", '{% load djust_components %}{% dj_tag "Py" as x %}')
        without = _render(engines, "rust", '{% load djust_components %}{% dj_tag "Py" %}')
        assert with_as == without == ("ok", '<span class="tag tag-default">Py</span>')

    def test_theme_tags_take_positional_parameters(self, engines):
        """``theme_tags`` have their own handler class; its docstring used to say
        positional arguments are ignored by design."""
        src = '{% load theme_tags %}{% theme_mode_toggle "my-toggle" False %}'
        out = _render(engines, "rust", src)
        assert out[0] == "ok", out
        assert "my-toggle" in out[1]
        assert out == _render(engines, "django", src)
        keyword = (
            '{% load theme_tags %}{% theme_mode_toggle button_class="my-toggle" show_label=False %}'
        )
        assert out == _render(engines, "rust", keyword)


class TestErrors:
    """Django's ``parse_bits`` messages, for the positional half of its checks."""

    @pytest.mark.parametrize("tag", [t for t in TAGS if not t["varargs"]], ids=lambda t: t["name"])
    def test_too_many_positional_arguments(self, engines, tag):
        extra = len(tag["params"]) + 1
        args = " ".join(['"x"'] * extra)
        src = "{% load " + tag["label"] + " %}{% " + tag["name"] + " " + args + " %}"
        django = _render(engines, "django", src)
        assert django[:2] == ("err", "TemplateSyntaxError"), django
        assert "received too many positional arguments" in django[2]
        assert _render(engines, "rust", src) == django

    @pytest.mark.parametrize("tag", WITH_PARAMS, ids=lambda t: t["name"])
    def test_positional_after_keyword(self, engines, tag):
        first = tag["params"][0]
        value = _call_value(engines, tag, first)
        src = _src(tag, [f"{first}={value}", value])
        django = _render(engines, "django", src)
        assert django[:2] == ("err", "TemplateSyntaxError"), django
        assert "positional argument(s) after some keyword argument(s)" in django[2]
        assert _render(engines, "rust", src) == django

    @pytest.mark.parametrize("tag", WITH_PARAMS, ids=lambda t: t["name"])
    def test_keyword_given_twice(self, engines, tag):
        first = tag["params"][0]
        value = _call_value(engines, tag, first)
        src = _src(tag, [f"{first}={value}", f"{first}={value}"])
        django = _render(engines, "django", src)
        assert django[:2] == ("err", "TemplateSyntaxError"), django
        assert "received multiple values for keyword argument" in django[2]
        assert _render(engines, "rust", src) == django

    @pytest.mark.parametrize("tag", WITH_PARAMS, ids=lambda t: t["name"])
    def test_parameter_given_by_position_and_by_keyword(self, engines, tag):
        """Django notices this at the Python call, so it is a ``TypeError`` when the
        node renders; djust refuses it when the template compiles, as a
        ``TemplateSyntaxError`` carrying ``parse_bits``' own wording for a parameter
        given twice. Both refuse, and neither renders a value."""
        first = tag["params"][0]
        value = _call_value(engines, tag, first)
        others = [f"{r}={_value(engines, tag, r)}" for r in tag["required"] if r != first]
        src = _src(tag, [value, f"{first}={value}", *others])
        django = _render(engines, "django", src)
        assert django[:2] == ("err", "TypeError"), django
        assert f"multiple values for argument '{first}'" in django[2]
        rust = _render(engines, "rust", src)
        assert rust[:2] == ("err", "TemplateSyntaxError"), rust
        assert rust[2] == f"'{tag['name']}' received multiple values for keyword argument '{first}'"


def _call_value(engines, tag: dict[str, Any], param: str) -> str:
    return _value(engines, tag, param)


def _src(tag: dict[str, Any], args: list[str]) -> str:
    return "{% load " + tag["label"] + " %}{% " + tag["name"] + " " + " ".join(args) + " %}"


class TestTagsWithoutPositionalParameters:
    def test_a_varargs_tag_is_not_rewritten(self, engines):
        """``{% kbd "Ctrl" "K" %}`` is ``kbd(*keys)``: there is no name to give, and its
        handler reads the raw tokens."""
        src = '{% load djust_components %}{% kbd "Ctrl" "K" %}'
        assert _render(engines, "rust", src) == _render(engines, "django", src)

    def test_the_named_parameters_of_a_tag_with_kwargs_are_positional(self, engines):
        """``icon(name, size, set, **kwargs)``: the first three are positional."""
        src = '{% load djust_components %}{% icon "check" "lg" %}'
        out = _render(engines, "rust", src)
        assert out[0] == "ok" and "dj-icon--lg" in out[1], out
        assert out == _render(engines, "django", src)

    def test_raw_tags_have_no_positional_parameters(self, engines):
        """A raw ``@register.tag`` parses ``key=value`` only; Django refuses a bare
        token. djust leaves those tags alone (a bare literal is still ignored, a bare variable still read as key=value), so the
        premise that they have nothing to map is what is pinned here: no raw tag is
        given a positional spec, and none of them accepts a positional token that
        the Rust handler then renders differently."""
        from djust._positional_args import positional_spec
        from djust.template_libraries import _classify

        library = import_library(LIBRARIES["djust_components"])
        raw = [
            name
            for name in sorted(_native_names()["djust_components"] & set(library.tags))
            if _classify(library.tags[name]) not in SIMPLE
        ]
        assert len(raw) >= 100, len(raw)
        for name in raw:
            assert positional_spec(name, library.tags[name]) is None, name
        from djust import _rust

        for name in raw:
            src = variants.wrap("djust_components", name, '"x"', _rust.has_block_tag_handler(name))
            django = _render(engines, "django", src)
            if django[0] == "ok":
                # No arguments at all (``menu_divider``): Django ignores the token.
                continue
            assert django[1] == "TemplateSyntaxError", (name, django)
            assert "Use key=value format" in django[2], (name, django)


class TestPositionalSpec:
    """``positional_spec`` / ``rewrite_positional`` against a library of our own, so the
    shapes the real tags do not have (keyword-only parameters, ``takes_context``
    together with ``*args``) are covered too."""

    @staticmethod
    def _library():
        from django import template

        library = template.Library()

        @library.simple_tag(takes_context=True)
        def with_context(context, a, b=2, *rest, flag=False, **extra):
            return ""

        @library.simple_tag
        def plain(a, b=2):
            return ""

        @library.inclusion_tag("x.html")
        def inclusion(a, b=2):
            return {}

        @library.tag
        def raw(parser, token):
            return None

        return library

    def test_the_spec_follows_django_signature_handling(self):
        from djust._positional_args import positional_spec

        tags = self._library().tags
        spec = positional_spec("with_context", tags["with_context"])
        assert spec.params == ("a", "b")  # ``context`` is not positional; ``flag`` is keyword-only
        assert spec.has_varargs and spec.takes_as_var and spec.name == "with_context"
        assert positional_spec("plain", tags["plain"]).params == ("a", "b")
        inclusion = positional_spec("inclusion", tags["inclusion"])
        assert inclusion.params == ("a", "b") and not inclusion.takes_as_var
        assert positional_spec("raw", tags["raw"]) is None

    @pytest.mark.parametrize(
        "args, expected",
        [
            (['"x"'], ['a="x"']),
            (['"x"', "y"], ['a="x"', "b=y"]),
            (['"x"', 'b="y"'], ['a="x"', 'b="y"']),
            (['a="x"'], ['a="x"']),
            (['"k=v"'], ['a="k=v"']),
            (["x|default:'k=v'"], ["a=x|default:'k=v'"]),
            # ``name=`` with nothing after it is not a keyword to Django (kwarg_re needs a
            # character after the ``=``), so it is a positional value.
            (["name="], ["a=name="]),
            (['"x"', "as", "out"], ['a="x"', "as", "out"]),
            (['"x"', "y", "z", "w"], ['a="x"', "b=y", "z", "w"]),  # ``*rest`` keeps the extras
        ],
    )
    def test_rewrite(self, args, expected):
        from djust._positional_args import positional_spec, rewrite_positional

        spec = positional_spec("with_context", self._library().tags["with_context"])
        assert rewrite_positional(spec, args) == expected


class TestTheEngineHook:
    """``rewrite_args_at_parse`` is the engine's contract, not a djust-components one."""

    @pytest.fixture
    def tag(self):
        from djust import _rust

        seen: list[list[str]] = []

        class Handler:
            def __init__(self, rewrite):
                if rewrite is not None:
                    self.rewrite_args_at_parse = rewrite

            def render(self, args, context):
                seen.append(list(args))
                return "ok"

        def register(rewrite):
            _rust.register_tag_handler("zz_hook_3664", Handler(rewrite))
            return seen

        yield register
        _rust.unregister_tag_handler("zz_hook_3664")

    def _render(self, src):
        from djust._rust import render_template

        return render_template(src, {"v": "k=v"})

    def test_the_handler_gets_the_tokens_the_hook_returns(self, tag):
        seen = tag(lambda args: ["first=" + args[0], *args[1:]])
        assert self._render('{% zz_hook_3664 "x" other=1 %}') == "ok"
        assert seen == [['first="x"', "other=1"]]

    def test_the_hook_sees_the_tokens_unresolved(self, tag):
        """The point of the hook: ``v`` is still the name the template wrote, not the
        resolved ``k=v`` that would look like a keyword."""
        received: list[list[str]] = []

        def hook(args):
            received.append(list(args))
            return ["label=" + args[0]]

        seen = tag(hook)
        self._render("{% zz_hook_3664 v %}")
        assert received == [["v"]]
        assert seen == [["label=k=v"]]

    def test_a_handler_without_the_hook_is_unchanged(self, tag):
        seen = tag(None)
        self._render('{% zz_hook_3664 "x" other=1 %}')
        assert seen == [["x", "other=1"]]

    def test_a_hook_that_raises_refuses_the_template(self, tag):
        from django.template import TemplateSyntaxError

        def hook(args):
            raise TemplateSyntaxError("nope")

        tag(hook)
        with pytest.raises(TemplateSyntaxError, match="nope"):
            self._render('{% zz_hook_3664 "x" %}')

    def test_a_hook_that_returns_the_wrong_shape_is_an_error(self, tag):
        tag(lambda args: 5)
        with pytest.raises(Exception, match="(?i)list|sequence|str"):
            self._render('{% zz_hook_3664 "x" %}')
