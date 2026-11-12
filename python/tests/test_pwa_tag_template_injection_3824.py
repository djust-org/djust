"""A PWA tag's resolved argument is DATA, never template source (#3824).

The bug
-------
The four ``djust_pwa_*`` handlers in ``djust/template_tags/pwa.py`` bridge the
native engine to the Django tags in ``djust/templatetags/djust_pwa.py``. They
did it by writing the tag into Django template SOURCE and compiling that::

    {% load djust_pwa %} {% djust_pwa_head name="<resolved value>" %}

``<resolved value>`` is whatever the context held. A value carrying a quote and
``%}`` ended the tag early and everything after it was compiled as template
text, so a view variable — a tenant's configurable app name is enough —
decided the page's markup::

    name = 'a" %}<script>alert(1)</script>{% djust_pwa_head name="b'

rendered a live ``<script>alert(1)</script>`` (also for ``theme_color``, and for
every keyword of ``djust_pwa_manifest``, ``djust_sw_register`` and
``djust_offline_indicator``, and through a dotted operand ``u.name``). The
Django engine was never affected: it resolves ``name=var`` as a value.

The fix
-------
The handler builds its source from CONSTANTS (a tag name and keyword names that
were checked against ``_TAG_PARAMS``) and passes every value in the render
CONTEXT, so Django resolves it as data. The oracle for "what a PWA tag renders
for this operand" is therefore the Django engine itself, which these rows run
LIVE rather than transcribe: for every tag, keyword and hostile payload the
native engine must now render exactly what the Django engine renders.

What each class of row is for, and what removes it
--------------------------------------------------
* ``TestNativeEngineMatchesDjango`` — the whole surface, tag x keyword x
  payload x operand shape. Goes red if a value reaches the source again.
* ``TestTheSourceIsConstant`` — the build function in isolation: no value text
  can appear in the source it returns, whatever the keyword spelling.
* ``TestNothingButKnownNamesReachTheSource`` — the allowlist, separately from
  the context-variable mechanism (two mechanisms, each reachable on its own:
  #2129). A hostile keyword or tag name cannot be tested through a template,
  because the template tokenizer only produces identifiers; it is reachable
  through the function, so it is tested there.
* ``TestShapeChecks`` — ``theme_color`` and ``sw_url`` are refused when they
  are not colour / service-worker shaped. Escaping already makes them inert;
  these rows are the shape check on top, and are reachable independently of it
  (a refused value renders the DEFAULT, an escaped one renders itself).
* ``TestOrdinaryValuesAreUnchanged`` — a normal value renders the bytes it
  always did.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

pytest.importorskip("django")

from django.template import Context, Template  # noqa: E402

from djust import _rust  # noqa: E402
from djust.template_tags import get_registered_handlers  # noqa: E402
from djust.template_tags import pwa  # noqa: E402
from djust.templatetags import djust_pwa  # noqa: E402

#: tag -> the keywords it takes, read off the Django tag's OWN signature rather
#: than restated, minus the injected ``context`` and the list-typed ``icons``.
_DJANGO_TAGS = {
    "djust_pwa_head": djust_pwa.djust_pwa_head,
    "djust_pwa_manifest": djust_pwa.djust_pwa_manifest,
    "djust_sw_register": djust_pwa.djust_sw_register,
    "djust_offline_indicator": djust_pwa.djust_offline_indicator,
}


def _params(fn) -> frozenset[str]:
    # simple_tag / inclusion_tag return the function itself, undecorated.
    names = set(inspect.signature(fn).parameters) - {"context", "icons"}
    return frozenset(names)


TAG_ARGS = [(tag, arg) for tag, fn in _DJANGO_TAGS.items() for arg in sorted(_params(fn))]

#: The payload from the issue, then each metacharacter class on its own.
LIVE = "<script>alert(1)</script>"
INJECT = 'a" %}' + LIVE + '{% djust_pwa_head name="b'
PAYLOADS = {
    "issue-injection": INJECT,
    "double-quote": 'he said "hi"',
    "single-quote": "it's",
    "percent-brace": "a %} b",
    "double-brace": "{{ 7*7 }}",
    "block-open": "{% now 'Y' %}",
    "script-close": "</script>" + LIVE,
    "javascript-url": "javascript:alert(1)",
    "newline": "line1\nline2",
    "unicode": "café ☃ ‮",
    "ampersand": "Tom & Jerry",
    "comment-close": "--><img src=x onerror=alert(1)>",
}


def _native(tag: str, arg: str, value, dotted: bool) -> str:
    operand = "u.v" if dotted else "v"
    context = {"u": {"v": value}} if dotted else {"v": value}
    return str(_rust.render_template("{%% %s %s=%s %%}" % (tag, arg, operand), context))


def _django(tag: str, arg: str, value) -> str:
    return str(
        Template("{%% load djust_pwa %%}{%% %s %s=v %%}" % (tag, arg)).render(Context({"v": value}))
    )


# ---------------------------------------------------------------------------
# The regression, over the whole surface
# ---------------------------------------------------------------------------


class TestNativeEngineMatchesDjango:
    @pytest.mark.parametrize("dotted", [False, True], ids=["variable", "dotted"])
    @pytest.mark.parametrize("payload", list(PAYLOADS.values()), ids=list(PAYLOADS))
    @pytest.mark.parametrize("tag,arg", TAG_ARGS)
    def test_the_native_engine_renders_what_the_django_engine_renders(
        self, tag: str, arg: str, payload: str, dotted: bool
    ) -> None:
        got = _native(tag, arg, payload, dotted)
        assert got == _django(tag, arg, payload)

    @pytest.mark.parametrize("dotted", [False, True], ids=["variable", "dotted"])
    @pytest.mark.parametrize("tag,arg", TAG_ARGS)
    def test_the_issue_payload_does_not_inject_markup(
        self, tag: str, arg: str, dotted: bool
    ) -> None:
        """The literal exploit, asserted without the oracle.

        Parity with Django would still pass if both engines were wrong the same
        way, so the named failure is also checked directly: the page does not
        gain the injected element, and the injected second ``djust_pwa_head``
        does not run (its output would carry a second ``PWA Meta Tags``).
        """
        got = _native(tag, arg, INJECT, dotted)
        assert LIVE not in got, f"{tag} {arg}: a context value became live markup"
        assert got.count("PWA Meta Tags") <= 1, "an injected tag call ran"

    def test_the_rows_cover_every_keyword_of_every_tag(self) -> None:
        """A tag that grows a parameter is covered without editing this file."""
        assert {t for t, _ in TAG_ARGS} == set(_DJANGO_TAGS)
        assert len(TAG_ARGS) == sum(len(_params(f)) for f in _DJANGO_TAGS.values())
        assert len(TAG_ARGS) >= 15, "the surface collapsed: signature read is wrong"


# ---------------------------------------------------------------------------
# The build function, in isolation
# ---------------------------------------------------------------------------


class TestTheSourceIsConstant:
    @pytest.mark.parametrize("payload", list(PAYLOADS.values()), ids=list(PAYLOADS))
    @pytest.mark.parametrize("tag,arg", TAG_ARGS)
    def test_no_value_text_reaches_the_source(self, tag: str, arg: str, payload: str) -> None:
        source, values = pwa._tag_source_and_context(tag, {arg: payload})
        assert payload not in source
        assert source == "{%% load djust_pwa %%}{%% %s %s=kw_%s %%}" % (tag, arg, arg)
        assert values == {"kw_" + arg: payload}, "the value must travel in the context"

    def test_the_tables_agree_with_the_django_tags(self) -> None:
        assert set(pwa._TAG_PARAMS) == set(_DJANGO_TAGS)
        for tag, fn in _DJANGO_TAGS.items():
            assert pwa._TAG_PARAMS[tag] == _params(fn), (
                f"{tag}: pwa._TAG_PARAMS drifted from the Django tag's signature"
            )


class TestNothingButKnownNamesReachTheSource:
    HOSTILE = "x=1 %}" + LIVE + "{% djust_pwa_head name"

    def test_a_hostile_keyword_is_refused(self) -> None:
        with pytest.raises(ValueError):
            pwa._tag_source_and_context("djust_pwa_head", {self.HOSTILE: "v"})

    def test_a_hostile_keyword_renders_only_the_comment(self) -> None:
        out = pwa._render_django_tag("djust_pwa_head", {self.HOSTILE: "v"})
        assert out == "<!-- djust: djust_pwa_head render failed (check server logs) -->"
        assert LIVE not in out

    def test_a_hostile_tag_name_is_refused(self) -> None:
        hostile = "djust_pwa_head %}" + LIVE + "{% djust_pwa_head"
        with pytest.raises(ValueError):
            pwa._tag_source_and_context(hostile, {})
        assert LIVE not in pwa._render_django_tag(hostile, {})

    def test_a_known_tag_with_an_unknown_keyword_is_refused(self) -> None:
        with pytest.raises(ValueError):
            pwa._tag_source_and_context("djust_sw_register", {"name": "v"})

    def test_icons_is_not_a_bridge_keyword(self) -> None:
        """``icons`` takes a list of dicts; the bridge only ever builds ``str``s."""
        assert "icons" not in pwa._TAG_PARAMS["djust_pwa_manifest"]


def _template_source_sites(root: pathlib.Path) -> dict[str, set[str]]:
    """``{relative path: {why flagged}}`` for every template-compiling call.

    Flags, in every non-test module under ``root``: a call to ``Template`` /
    ``from_string`` / ``Engine(...)`` by its own name or an alias introduced by
    ``from ... import Template as T`` or ``T = Template``, an
    ``Engine(...).from_string`` chain, and ``getattr`` / ``import_module`` /
    ``__import__`` given a string that names one of them.
    """
    targets = {"Template", "from_string", "Engine"}
    found: dict[str, set[str]] = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if "/tests/" in "/" + rel or rel.startswith("tests/") or path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = set(targets)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in targets and alias.asname:
                        names.add(alias.asname)
            elif isinstance(node, ast.Assign) and isinstance(node.value, (ast.Name, ast.Attribute)):
                src = node.value.id if isinstance(node.value, ast.Name) else node.value.attr
                if src in targets:
                    names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            called = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            if called in names:
                found.setdefault(rel, set()).add("call:" + called)
            if called in {"getattr", "import_module", "__import__"}:
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        if arg.value in targets or arg.value.startswith("django.template"):
                            found.setdefault(rel, set()).add("dynamic:" + arg.value)
    return found


class TestOnlyKnownTemplateSourceBuildersExist:
    """A TRIPWIRE, not a guard: every template-compiling call site in ``djust``.

    #3824 was one such call fed a value. This pins the set of modules that
    contain one, across all of ``python/djust`` (not just the tag modules), so
    a new one is a decision made on purpose rather than a copy of the old shape.

    What it does NOT do: it cannot tell whether a value reaches the source at a
    known site, it will not see a call it has no name for (``render_to_string``
    of a name built from data, a ``Template`` reached through a variable it
    cannot trace, ``eval``), and it is not a substitute for reading the new
    site. The behaviour rows above are the guard; this is what makes you go and
    look.
    """

    #: file -> why a template-compiling call is legitimate there. Each is
    #: author-written source (a class attribute, a template file read from
    #: disk, a constant), never a resolved tag argument. (`template_libraries.py`
    #: only mentions `Template(...)` in a comment and builds a stub engine; it
    #: compiles nothing, so it is not a site.)
    KNOWN = {
        "template_tags/pwa.py": "source is constants; values go in the context (_tag_source_and_context)",
        "template_tags/_django_expr.py": "compiles the empty template, a constant",
        "templatetags/live_tags.py": "a child view's own `template` attribute, author source",
        "_template_bindings.py": "scans a view's declared `template` / `template_name`",
        "streaming.py": "the view's own `template` attribute",
        "websocket.py": "a child view's `get_template()` source",
        "management/commands/generate_sw.py": "the service-worker template, a string constant",
        "mcp/server.py": "template source an MCP caller asks to be checked (dev tool)",
        "theming/templatetags/theme_tags.py": "the preview template, a module constant",
        "components/gallery/views.py": "gallery example sources, module constants",
    }

    def test_the_construction_sites_are_exactly_the_known_ones(self) -> None:
        root = pathlib.Path(pwa.__file__).resolve().parents[1]
        found = _template_source_sites(root)
        assert set(found) == set(self.KNOWN), (
            "the set of modules compiling template source changed. Confirm no "
            "resolved argument reaches the source at the new site, then update "
            f"KNOWN. New: {sorted(set(found) - set(self.KNOWN))}; "
            f"gone: {sorted(set(self.KNOWN) - set(found))}"
        )

    def test_the_scanner_sees_the_shapes_it_claims_to(self, tmp_path) -> None:
        """Gate for the scanner itself: each evasion shape it names is flagged."""
        (tmp_path / "alias.py").write_text("from django.template import Template as T\nT('x')\n")
        (tmp_path / "assign.py").write_text(
            "from django.template import Template\nT = Template\nT('x')\n"
        )
        (tmp_path / "chain.py").write_text(
            "e = Engine()\ne.from_string('x')\nEngine().from_string('y')\n"
        )
        (tmp_path / "dyn.py").write_text(
            "import importlib\nm = importlib.import_module('django.template')\ngetattr(m, 'Template')('x')\n"
        )
        (tmp_path / "plain.py").write_text("x = 1\n")
        sub = tmp_path / "pkg"
        sub.mkdir()
        (sub / "deep.py").write_text("Template('x')\n")
        got = _template_source_sites(tmp_path)
        assert set(got) == {"alias.py", "assign.py", "chain.py", "dyn.py", "pkg/deep.py"}


# ---------------------------------------------------------------------------
# Shape checks
# ---------------------------------------------------------------------------


class TestShapeChecks:
    @pytest.mark.parametrize("engine", ["native", "django"])
    @pytest.mark.parametrize("tag", ["djust_pwa_head", "djust_pwa_manifest"])
    @pytest.mark.parametrize("bad", ['"><b>x</b>', "red;}</style>", "a\nb", "{{ x }}", "x" * 80])
    def test_a_theme_color_that_is_not_a_colour_renders_the_default(
        self, engine: str, tag: str, bad: str
    ) -> None:
        out = (
            _native(tag, "theme_color", bad, False)
            if engine == "native"
            else _django(tag, "theme_color", bad)
        )
        assert '<meta name="theme-color" content="#007bff">' in out, (
            "a non-colour theme_color must fall back to the default, not render"
        )

    @pytest.mark.parametrize("engine", ["native", "django"])
    @pytest.mark.parametrize("bad", ['"><b>x</b>', "red;}</style>", "a\nb", "{{ x }}", "x" * 80])
    def test_a_background_color_that_is_not_a_colour_renders_the_default(
        self, engine: str, bad: str
    ) -> None:
        tag = "djust_pwa_manifest"
        out = (
            _native(tag, "background_color", bad, False)
            if engine == "native"
            else _django(tag, "background_color", bad)
        )
        assert "&quot;background_color&quot;: &quot;#ffffff&quot;" in out, (
            "a non-colour background_color must fall back to the default, not render"
        )

    @pytest.mark.parametrize("engine", ["native", "django"])
    @pytest.mark.parametrize("good", ["#101010", "navy", "rgb(1, 2, 3)"])
    def test_a_real_background_colour_is_kept(self, engine: str, good: str) -> None:
        tag = "djust_pwa_manifest"
        out = (
            _native(tag, "background_color", good, False)
            if engine == "native"
            else _django(tag, "background_color", good)
        )
        assert "&quot;background_color&quot;: &quot;%s&quot;" % good in out

    @pytest.mark.parametrize("engine", ["native", "django"])
    @pytest.mark.parametrize(
        "good", ["#007bff", "#fff", "red", "rgb(0, 128, 255)", "hsl(210 50% 40% / 0.5)"]
    )
    def test_a_real_colour_is_kept(self, engine: str, good: str) -> None:
        tag = "djust_pwa_head"
        out = (
            _native(tag, "theme_color", good, False)
            if engine == "native"
            else _django(tag, "theme_color", good)
        )
        assert 'name="theme-color" content="%s"' % good in out

    @pytest.mark.parametrize("engine", ["native", "django"])
    @pytest.mark.parametrize(
        "bad",
        [
            "javascript:alert(1)",
            "  JaVaScRiPt:alert(1)",
            "java\tscript:alert(1)",
            "data:text/javascript,alert(1)",
            "vbscript:x",
            "blob:https://x/y",
            "/sw.js\nINJECT",
        ],
    )
    def test_a_sw_url_that_is_not_a_service_worker_url_renders_the_default(
        self, engine: str, bad: str
    ) -> None:
        out = (
            _native("djust_sw_register", "sw_url", bad, False)
            if engine == "native"
            else _django("djust_sw_register", "sw_url", bad)
        )
        assert "register(" in out
        assert 'register("/static/sw.js"' in out, f"the default was not used: {out[:300]!r}"

    @pytest.mark.parametrize("engine", ["native", "django"])
    @pytest.mark.parametrize("good", ["/sw.js", "/static/sw.js", "https://example.com/sw.js"])
    def test_a_real_sw_url_is_kept(self, engine: str, good: str) -> None:
        out = (
            _native("djust_sw_register", "sw_url", good, False)
            if engine == "native"
            else _django("djust_sw_register", "sw_url", good)
        )
        assert 'register("%s"' % good in out


# ---------------------------------------------------------------------------
# Ordinary values
# ---------------------------------------------------------------------------


class TestOrdinaryValuesAreUnchanged:
    def test_pwa_head_with_ordinary_literals_keeps_its_bytes(self) -> None:
        out = _rust.render_template('{% djust_pwa_head name="My App" theme_color="#123456" %}', {})
        assert '<meta name="theme-color" content="#123456">' in out
        assert '<meta name="apple-mobile-web-app-title" content="My App">' in out
        assert '<meta name="application-name" content="My App">' in out
        assert "&lt;meta" not in out

    def test_manifest_with_ordinary_literals_keeps_its_bytes(self) -> None:
        out = _rust.render_template(
            '{% djust_pwa_manifest name="My App" theme_color="#123456" %}', {}
        )
        assert out.startswith('<meta name="theme-color" content="#123456">\n<link rel="manifest"')
        assert "&quot;name&quot;: &quot;My App&quot;" in out

    def test_a_variable_with_an_ordinary_value_renders_like_a_literal(self) -> None:
        lit = _rust.render_template('{% djust_pwa_head name="My App" %}', {})
        var = _rust.render_template("{% djust_pwa_head name=n %}", {"n": "My App"})
        assert lit == var

    def test_the_handlers_still_return_marked_html(self) -> None:
        for tag in _DJANGO_TAGS:
            out = get_registered_handlers()[tag].render([], {})
            assert hasattr(out, "__html__"), f"{tag} lost its mark"
            assert "render failed" not in out
