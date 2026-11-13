"""A bare operand on a raw ``@register.tag`` component tag is refused (#3676).

The Python component tags that are RAW ``@register.tag`` functions parse
``key=value`` only: ``{% alert val %}`` is a ``TemplateSyntaxError`` under Django
("Unexpected argument 'val'. Use key=value format."). The native Rust handlers did
not refuse it. After the engine resolved the operand, a bare variable whose VALUE
was ``title=PWNED class=evil`` arrived as the text ``title=PWNED class=evil``,
which the handler's ``key=value`` parser reads as a keyword. So a context value
could set any parameter of a tag it was merely passed to (``title``, ``class``,
``id``, event names), in a template Django itself refuses.

The fix was one mechanism and no per-tag list: at template COMPILATION the engine
hands the tokens to Django's own compile function for that tag
(``DjangoCheckedTagHandler``). Since 2.0 (one render path per component tag) every
tag except the core layout ones renders through the ``{% load %}`` bridge, which
compiles the tag with Django's own function anyway; the core layout handlers keep
the wrapper. Either way Django says what it accepts and what it refuses, with its
own message, so the Rust engine refuses exactly the templates Django refuses.

Everything here is derived from the registries and from Django's behaviour, not
from a table: the tag set is enumerated from the library, the expected outcome of
every call is whatever Django does with it, and the count is whatever the
enumeration finds.

What each class proves:

* ``TestEnumeration``: the sweep derived the tags it claims to, and every one is
  registered with the Rust engine (so the sweep is about the Rust engine, not about
  Django alone).
* ``TestBareOperandRefusedLikeDjango``: for EVERY raw tag, every shape of bare
  operand (variable, literal, number, attribute path, filtered variable) is refused
  at compile time with Django's message when Django refuses it, and accepted when
  Django accepts it. Keyword shapes agree with Django too.
* ``TestInjection``: the issue's payload. A variable whose value is a set of
  ``key=value`` pairs never reaches a handler as keywords.
* ``TestTagsThatTakeNoArguments``: the tags Django accepts a bare token on are
  exactly the tags whose compile function never reads its arguments, and the
  Rust handler of such a tag ignores them too.
* ``TestTheMechanism``: a raw tag of our own that LEGITIMATELY takes a positional
  argument keeps working (the mechanism asks Django, it does not assume
  ``key=value``), for an inline and a block tag.

Refs #3676, #3664, #3673, #1468, #1646, #1825.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from django.template import Context, Engine, TemplateSyntaxError
from django.template.library import import_library

from scripts.lib.template_library_parity import harness, variants

LABEL = "djust_components"
DOTTED = "djust.components.templatetags.djust_components"
RAW = "raw"

#: Every key is set to this, so a handler that read ANY of them shows it in its output.
SENTINEL = "PWNED"


# --------------------------------------------------------------------------- #
# The tags: enumerated from the library, never typed in


_MECHANISMS = frozenset({"with_positional_args", "DjangoCheckedTagHandler"})


def _mechanism_names(source: str) -> set[str]:
    """The mechanism names a module's source refers to, however it reaches them.

    Parsed, not matched as text: a call, a name passed around, an import under another
    name (``from djust._positional_args import with_positional_args as wrap``) and an
    attribute of the module (``_positional_args.with_positional_args``) all count.
    """
    import ast

    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.alias):
            found.add(node.name.rsplit(".", 1)[-1])
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # ``getattr(module, "with_positional_args")``
            found.add(node.value)
    return found & _MECHANISMS


def _handlers() -> dict[str, Any]:
    """Every tag the Rust engine serves for the library (all of them since S3: the bridge
    renders each one's own node), by name -> its compile function."""
    library = import_library(DOTTED)
    return dict(library.tags)


def _raw_tags() -> list[dict[str, Any]]:
    """Every raw ``@register.tag`` of the components library."""
    from djust.template_libraries import _classify, _consumes_body

    library = import_library(DOTTED)
    out = []
    for name in sorted(_handlers()):
        func = library.tags[name]
        if _classify(func) != RAW:
            continue
        if name == "split_pane":
            # Its body must hold a ``{% pane %}``: the sweep's ``x`` body is refused for that,
            # whatever the arguments, so the sweep would measure the body and not the operand.
            continue
        keys, params = harness.discover(func)
        out.append(
            {
                "name": name,
                "func": func,
                "keys": sorted(keys | params),
                "block": _consumes_body(func),
            }
        )
    return out


TAGS = _raw_tags()
IDS = [t["name"] for t in TAGS]


# --------------------------------------------------------------------------- #
# Engines


@pytest.fixture(scope="module")
def engines():
    from djust.components import rust_handlers as component_handlers
    from djust.template.backend import DjustTemplateBackend

    component_handlers.register_with_rust_engine()
    rust = DjustTemplateBackend(
        {"NAME": "raw-bare-3676", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}}
    )
    django = Engine(
        dirs=[str(Path(component_handlers.__file__).resolve().parent / "templates")],
        libraries={LABEL: DOTTED},
    )
    return rust, django


def _call(tag: dict[str, Any], args: str) -> str:
    return variants.wrap(LABEL, tag["name"], args, tag["block"])


_ID = re.compile(r"(?<=[-_])[0-9a-f]{6,12}\b")


#: The kept native twins (``rust_handlers.NATIVE_INLINE_HANDLERS`` / ``NATIVE_BLOCK_HANDLERS``).
def _native_names() -> frozenset[str]:
    from djust.components import rust_handlers as rh

    return frozenset(
        [n for n, _h in rh.NATIVE_INLINE_HANDLERS] + [n for n, _e, _h in rh.NATIVE_BLOCK_HANDLERS]
    )


_NATIVE_LAYOUT = _native_names()


def _norm(html: str) -> str:
    # ``tooltip`` numbers its tip from a process-wide counter: one per render, either engine.
    html = re.sub(r"(?<=-tip-)\d+", "N", html)
    return re.sub(r"\s+", " ", _ID.sub("ID", html)).strip()


def _compile(engines, which: str, src: str) -> tuple[str, ...]:
    """What COMPILING ``src`` does: ``("ok",)`` or ``("err", type, message)``.

    Only compilation: the point of the fix is that the refusal comes before any
    context is available, so a bad call in an untaken branch is caught too.
    """
    rust, django = engines
    try:
        if which == "rust":
            rust.from_string(src)
        else:
            django.from_string(src)
    except Exception as exc:  # noqa: BLE001 - the exception IS the observation
        return ("err", type(exc).__name__, str(exc))
    return ("ok",)


def _render(engines, which: str, src: str, context: dict[str, Any]) -> tuple[str, ...]:
    rust, django = engines
    try:
        if which == "rust":
            out = rust.from_string(src).render(dict(context))
        else:
            out = django.from_string(src).render(Context(dict(context)))
    except Exception as exc:  # noqa: BLE001
        return ("err", type(exc).__name__, str(exc))
    return ("ok", _norm(str(out)))


def _payloads(tag: dict[str, Any]) -> list[str]:
    """One value per parameter the handler reads: ``key=PWNED``.

    A handler splits an argument at its FIRST ``=``, so one variable sets one
    parameter; the sweep tries each parameter in turn rather than a single value
    that sets only the alphabetically first.
    """
    return [f"{key}={SENTINEL}" for key in tag["keys"] or ["title", "class", "id"]]


# --------------------------------------------------------------------------- #

#: Bare operands: a token with no ``=``. Every one is positional to Django.
BARE = {
    "variable": "val",
    "attribute": "obj.attr",
    "filtered": "val|upper",
    "double-quoted literal": '"x"',
    "single-quoted literal": "'x'",
    "number": "3",
    "translated literal": '_("x")',
}

#: Calls built from keywords only, and mixed ones. Whatever Django does with them,
#: the Rust engine must do too.
KEYWORD = {
    "no arguments": "",
    "literal keyword": 'k="x"',
    "variable keyword": "k=val",
    "filtered keyword": "k=val|upper",
    "keyword with a quoted space": 'k="a b"',
    "two keywords": 'a="x" b=val',
    "empty value": "k=",
    "bare literal after a keyword": 'k="x" "y"',
    "bare variable after a keyword": 'k="x" val',
    "bare variable before a keyword": 'val k="x"',
    "quoted literal containing =": '"a=b"',
    "filter argument containing =": 'val|default:"a=b"',
}


class TestEnumeration:
    def test_the_sweep_derived_a_real_set(self):
        assert TAGS, "no raw native component tags were derived"
        assert {"alert", "card", "accordion"} <= set(IDS), (
            "the tags the issue names must be in the derived set"
        )

    def test_the_set_covers_inline_and_block_tags(self):
        """Both parse paths (``validate_tag_arguments`` is called for block and inline
        tags separately in the parser) are exercised, so neither can be left behind."""
        assert any(t["block"] for t in TAGS)
        assert any(not t["block"] for t in TAGS)

    def test_every_derived_tag_is_registered_with_the_rust_engine(self, engines):
        """Otherwise the sweep would be a statement about Django alone, not about the Rust
        engine (which renders each tag through the ``{% load %}`` bridge, or natively for the
        core layout tags)."""
        from djust import _rust

        missing = [
            t["name"]
            for t in TAGS
            if not (
                _rust.has_block_tag_handler(t["name"]) or _rust.has_raw_block_tag_handler(t["name"])
                if t["block"]
                else _rust.has_tag_handler(t["name"])
            )
        ]
        assert not missing, missing

    def test_every_native_handler_of_the_library_is_accounted_for(self):
        """The enumeration drops nothing silently: a native handler is either a raw tag
        (swept here) or a simple/inclusion tag (swept by #3664's test)."""
        from djust.template_libraries import _classify

        library = import_library(DOTTED)
        kinds: dict[str, int] = {}
        for name in _handlers():
            func = library.tags.get(name)
            kinds[_classify(func) if func else "not-in-library"] = (
                kinds.get(_classify(func) if func else "not-in-library", 0) + 1
            )
        assert kinds.get("not-in-library", 0) == 0, kinds
        # ``split_pane`` is the one raw tag the sweep leaves out (see ``_raw_tags``).
        assert kinds[RAW] == len(TAGS) + 1, (kinds, len(TAGS))


class TestBareOperandRefusedLikeDjango:
    # One test per SHAPE, looping over the tags and reporting every mismatch together:
    # a test id per tag x shape added ~2,600 ids with no entry in .test_durations and
    # used over half the unrecorded-duration budget of tests/test_ci_python_test_shards.py.
    @pytest.mark.parametrize("shape", sorted(BARE), ids=sorted(BARE))
    def test_a_bare_operand_is_refused_at_compile_time_with_djangos_message(self, engines, shape):
        problems = []
        for tag in TAGS:
            src = _call(tag, BARE[shape])
            django = _compile(engines, "django", src)
            rust = _compile(engines, "rust", src)
            if rust != django:
                problems.append(
                    f"{{% {tag['name']} {BARE[shape]} %}}: Django compiles to {django}, "
                    f"the Rust engine to {rust}"
                )
            elif django[0] == "err" and (
                django[1] != "TemplateSyntaxError" or "Use key=value format" not in django[2]
            ):
                problems.append(f"{{% {tag['name']} {BARE[shape]} %}}: {django}")
        assert not problems, "\n".join(problems)

    @pytest.mark.parametrize("shape", sorted(KEYWORD), ids=sorted(KEYWORD))
    def test_every_other_shape_compiles_as_it_does_under_django(self, engines, shape):
        """Keyword form unchanged: what the Rust engine accepted before is accepted now,
        and the one thing it accepts that Django refuses is the bare operand."""
        problems = [
            f"{{% {tag['name']} {KEYWORD[shape]} %}}"
            for tag in TAGS
            if _compile(engines, "rust", _call(tag, KEYWORD[shape]))
            != _compile(engines, "django", _call(tag, KEYWORD[shape]))
        ]
        assert not problems, "Rust differs from Django for:\n" + "\n".join(problems)

    def test_the_sweep_sees_both_outcomes(self, engines):
        """Vacuity guard for the two classes above: Django refuses a bare variable on
        some tags and accepts it on others. A sweep that saw only one outcome would
        pass on a handler that refuses everything (or nothing)."""
        outcomes = {_compile(engines, "django", _call(tag, "val"))[0] for tag in TAGS}
        assert outcomes == {"ok", "err"}, outcomes

    @pytest.mark.parametrize("tag", TAGS, ids=IDS)
    def test_a_keyword_call_still_renders(self, engines, tag):
        """The refusal is of the bare operand, not of the tag: a keyword call (one per
        key the tag reads) compiles in the Rust engine and renders, or raises, exactly as
        under Django (a tag that iterates ``messages=1`` raises in both)."""
        for key in tag["keys"][:3] or ["title"]:
            src = _call(tag, f"{key}=1")
            out = _render(engines, "rust", src, {})
            if tag["name"] in _NATIVE_LAYOUT:
                # A kept native twin renders its own markup (its divergence from the Python tag is
                # listed in test_component_single_render_path.py). The call compiles and renders.
                assert out[0] == "ok", (tag["name"], key, out)
                continue
            assert out == _render(engines, "django", src, {}), (tag["name"], key, out)


class TestInjection:
    """The issue. The payload is a variable's VALUE, so the compile-time refusal must not
    depend on it."""

    @pytest.mark.parametrize("tag", TAGS, ids=IDS)
    def test_a_variable_holding_key_value_pairs_is_not_read_as_keywords(self, engines, tag):
        src = _call(tag, "val")
        baseline = _render(engines, "rust", _call(tag, ""), {})
        for payload in _payloads(tag):
            context = {"val": payload}
            django = _render(engines, "django", src, context)
            rust = _render(engines, "rust", src, context)
            if django[0] == "err":
                # A template Django refuses is refused, whatever the variable holds.
                assert rust == django, (tag["name"], payload, rust, django)
                continue
            # A template Django accepts ignores the token; so must the Rust handler. The
            # variable is the only thing that differs from the no-argument call.
            assert rust == baseline, f"{tag['name']}: a bare variable changed the output"
            assert SENTINEL not in rust[1]

    @pytest.mark.parametrize("tag", TAGS, ids=IDS)
    def test_the_refusal_does_not_depend_on_the_value(self, engines, tag):
        """Compilation has no context at all, so the outcome cannot depend on what the
        variable holds: the same call compiles (or not) whatever ``val`` is later."""
        src = _call(tag, "val")
        expected = _compile(engines, "django", src)
        assert _compile(engines, "rust", src) == expected
        if expected[0] == "ok":
            baseline = _render(engines, "rust", _call(tag, ""), {})
            for value in (*_payloads(tag), "plain", ""):
                assert _render(engines, "rust", src, {"val": value}) == baseline

    def test_the_issue_example(self, engines):
        src = "{% load djust_components %}{% alert val %}body{% endalert %}"
        context = {"val": "title=PWNED type=danger"}
        rust, _ = engines
        with pytest.raises(TemplateSyntaxError, match=r"Unexpected argument 'val'. Use key=value"):
            rust.from_string(src).render(context)

    def test_card_and_accordion_cannot_be_given_a_class_or_an_id(self, engines):
        rust, _ = engines
        for tag, value in (("card", "class=evil-class title=PWN"), ("accordion", "id=hijacked-id")):
            src = "{% load djust_components %}{% " + tag + " val %}x{% end" + tag + " %}"
            with pytest.raises(TemplateSyntaxError, match="Use key=value format"):
                rust.from_string(src).render({"val": value})


class TestTagsThatTakeNoArguments:
    """Django accepts a bare token on a raw tag only when the tag's compile function
    never looks at its arguments. That is the entire allowlist, and it is DERIVED from
    Django's behaviour; this class proves the reason."""

    @staticmethod
    def _accepting(engines) -> list[dict[str, Any]]:
        return [t for t in TAGS if _compile(engines, "django", _call(t, "val"))[0] == "ok"]

    def test_there_are_some(self, engines):
        assert self._accepting(engines), "expected tags whose compile function ignores arguments"

    def test_each_of_them_never_reads_its_arguments(self, engines):
        import inspect

        for tag in self._accepting(engines):
            source = inspect.getsource(tag["func"])
            assert "split_contents" not in source, tag["name"]
            assert "token.contents" not in source, tag["name"]
            assert "bits" not in source, tag["name"]

    def test_every_other_tag_reads_them(self, engines):
        """The converse: a tag Django refuses a bare token on does read its arguments (it
        calls ``_parse_kv_args`` or an equivalent), so the refusal is the tag's own
        contract and not an accident of the sweep."""
        import inspect

        accepting = {t["name"] for t in self._accepting(engines)}
        for tag in TAGS:
            if tag["name"] in accepting:
                continue
            source = inspect.getsource(tag["func"])
            assert "split_contents" in source or "token.contents" in source, tag["name"]

    def test_their_rust_handlers_ignore_the_token_too(self, engines):
        for tag in self._accepting(engines):
            baseline = _render(engines, "rust", _call(tag, ""), {})
            for shape, args in BARE.items():
                out = _render(
                    engines,
                    "rust",
                    _call(tag, args),
                    {"val": _payloads(tag)[0], "obj": {"attr": 1}},
                )
                assert out == baseline, (tag["name"], shape)


class TestTheMechanism:
    """A raw tag of our own, so the shapes the component library does not have are
    covered: a tag that LEGITIMATELY takes a positional argument, an inline and a block
    one, and a handler that already has a validator."""

    @pytest.fixture
    def library(self):
        from django import template

        library = template.Library()

        @library.tag("zz_positional_3676")
        def positional(parser, token):
            bits = token.split_contents()
            if len(bits) != 2:
                raise template.TemplateSyntaxError("zz_positional_3676 takes one argument")
            return template.Node()

        @library.tag("zz_block_3676")
        def block(parser, token):
            bits = token.split_contents()
            if len(bits) != 2:
                raise template.TemplateSyntaxError("zz_block_3676 takes one argument")
            parser.parse(("endzz_block_3676",))
            parser.delete_first_token()
            return template.Node()

        @library.tag("zz_keyword_3676")
        def keyword(parser, token):
            for bit in token.split_contents()[1:]:
                if "=" not in bit:
                    raise template.TemplateSyntaxError(
                        f"Unexpected argument '{bit}'. Use key=value format."
                    )
            return template.Node()

        return library

    @pytest.fixture
    def registered(self, library):
        from djust import _rust
        from djust._positional_args import with_argument_contract

        class Inline:
            def render(self, args, context):
                return "|".join(args)

        class Block:
            def render(self, args, content, context):
                return "[" + "|".join(args) + "]" + content

        seen = []
        _rust.register_tag_handler(
            "zz_positional_3676", with_argument_contract("zz_positional_3676", Inline(), library)
        )
        _rust.register_tag_handler(
            "zz_keyword_3676", with_argument_contract("zz_keyword_3676", Inline(), library)
        )
        _rust.register_block_tag_handler(
            "zz_block_3676",
            "endzz_block_3676",
            with_argument_contract("zz_block_3676", Block(), library),
        )
        yield seen
        _rust.unregister_tag_handler("zz_positional_3676")
        _rust.unregister_tag_handler("zz_keyword_3676")
        _rust.unregister_block_tag_handler("zz_block_3676")

    @staticmethod
    def _render(src, context=None):
        from djust._rust import render_template

        return render_template(src, context or {})

    def test_a_raw_tag_that_takes_a_positional_argument_keeps_working(self, registered):
        assert self._render('{% zz_positional_3676 "x" %}', {}) == "x"
        assert self._render("{% zz_positional_3676 v %}", {"v": "hello"}) == "hello"

    def test_it_is_still_held_to_its_own_arity(self, registered):
        with pytest.raises(TemplateSyntaxError, match="takes one argument"):
            self._render("{% zz_positional_3676 %}")
        with pytest.raises(TemplateSyntaxError, match="takes one argument"):
            self._render('{% zz_positional_3676 "a" "b" %}')

    def test_a_block_tag_that_takes_a_positional_argument_keeps_working(self, registered):
        out = self._render("{% zz_block_3676 v %}body{% endzz_block_3676 %}", {"v": "x"})
        assert out == "[x]body"

    def test_a_block_tags_argument_is_checked_before_its_body(self, registered):
        with pytest.raises(TemplateSyntaxError, match="takes one argument"):
            self._render("{% zz_block_3676 %}body{% endzz_block_3676 %}")

    def test_a_keyword_only_tag_refuses_a_bare_variable(self, registered):
        with pytest.raises(TemplateSyntaxError, match=r"Unexpected argument 'v'. Use key=value"):
            self._render("{% zz_keyword_3676 v %}", {"v": "title=PWNED"})
        assert self._render("{% zz_keyword_3676 title=1 %}", {}) == "title=1"

    def test_the_wrapper_forwards_what_the_engine_reads_off_a_handler(self, library):
        from djust._positional_args import with_argument_contract

        class Handler:
            RESOLVE_ARG_POSITIONS = frozenset({0})
            LAZY_BODY = True

            def render(self, args, context):
                return ""

        wrapped = with_argument_contract("zz_keyword_3676", Handler(), library)
        assert wrapped.RESOLVE_ARG_POSITIONS == frozenset({0})
        assert wrapped.LAZY_BODY is True
        assert not hasattr(wrapped, "rewrite_args_at_parse")

    def test_a_handlers_own_validator_still_runs(self, library):
        from djust._positional_args import with_argument_contract

        calls: list[list[str]] = []

        class Handler:
            def validate_at_parse(self, args):
                calls.append(list(args))
                raise TemplateSyntaxError("handler says no")

            def render(self, args, context):
                return ""

        wrapped = with_argument_contract("zz_keyword_3676", Handler(), library)
        with pytest.raises(TemplateSyntaxError, match="handler says no"):
            wrapped.validate_at_parse(['k="x"'])
        assert calls == [['k="x"']]

    def test_with_argument_contract_is_the_only_caller_of_the_mechanisms(self):
        """The one-entry-point claim is a pin, not prose: nothing outside
        ``_positional_args`` names ``with_positional_args`` or ``DjangoCheckedTagHandler``,
        so a future raw theme tag cannot be registered unwrapped by copying a neighbouring
        call.

        Found by parsing, not by a regular expression over the text, so the name is caught
        however it is reached: called, passed around, imported under another name
        (``from djust._positional_args import with_positional_args as wrap``) or read off the
        module (``_positional_args.with_positional_args``). The scan EXCLUDES a test
        directory by the path RELATIVE to the package, never the absolute one: a checkout
        under a directory called ``tests`` has every file under it, and an absolute-path
        filter would skip them all and pass.
        """
        import pathlib

        import djust

        root = pathlib.Path(djust.__file__).resolve().parent
        scanned = [
            path
            for path in sorted(root.rglob("*.py"))
            if "tests" not in path.relative_to(root).parts and path.name != "_positional_args.py"
        ]
        # The scan must be able to see something: a filter that matched every file would
        # pass an empty list of callers for the wrong reason.
        assert len(scanned) > 50, f"scanned only {len(scanned)} files under {root}"
        assert root / "components" / "rust_handlers.py" in scanned

        callers = sorted(
            str(path.relative_to(root))
            for path in scanned
            if _mechanism_names(path.read_text(encoding="utf-8"))
        )
        assert callers == [], callers

    @pytest.mark.parametrize(
        "source",
        [
            "with_positional_args('t', h, lib)",
            "from djust._positional_args import with_positional_args as wrap\nwrap('t', h, lib)",
            "import djust._positional_args as m\nm.DjangoCheckedTagHandler(h, 't', f)",
            "handler_class = DjangoCheckedTagHandler",
            "import djust._positional_args as m\ngetattr(m, 'with_positional_args')('t', h, lib)",
        ],
    )
    def test_the_pin_sees_every_way_to_reach_a_mechanism(self, source):
        """Gate-off for the pin above, through the same function it uses: each way of reaching
        a mechanism is reported, and a module that only reaches ``with_argument_contract`` is not."""
        assert _mechanism_names(source)
        assert not _mechanism_names("from djust._positional_args import with_argument_contract")

    def test_simple_and_inclusion_tags_keep_the_positional_mechanism(self):
        """One entry point, two mechanisms, each tag gets exactly one: a ``simple_tag``
        is wrapped by the positional rewrite (#3664), not by the compile check."""
        from djust._positional_args import PositionalTagHandler, with_argument_contract

        from django import template

        library = template.Library()

        @library.simple_tag
        def zz_simple_3676(label, variant="a"):
            return ""

        class Handler:
            def render(self, args, context):
                return ""

        wrapped = with_argument_contract("zz_simple_3676", Handler(), library)
        assert isinstance(wrapped, PositionalTagHandler)
        assert not hasattr(wrapped, "validate_at_parse")

    def test_a_simple_block_tag_is_left_alone(self):
        """A ``simple_block_tag`` reads its arguments AFTER its body (``parse_bits``
        follows ``parser.parse``), which the stop-at-body check cannot reach, so it is
        not wrapped. No native handler in the components library is one; this pins the
        limit rather than leaving it to be discovered."""
        from django import template

        if not hasattr(template.Library, "simple_block_tag"):
            pytest.skip("Django < 5.2 has no simple_block_tag")
        from djust._positional_args import with_argument_contract

        library = template.Library()

        @library.simple_block_tag
        def zz_sblock_3676(content, label):
            return content

        class Block:
            def render(self, args, content, context):
                return content

        handler = Block()
        assert with_argument_contract("zz_sblock_3676", handler, library) is handler

    def test_a_tag_the_library_does_not_have_is_unchanged(self, library):
        from djust._positional_args import with_argument_contract

        class Handler:
            def render(self, args, context):
                return ""

        handler = Handler()
        assert with_argument_contract("not_a_tag", handler, library) is handler
