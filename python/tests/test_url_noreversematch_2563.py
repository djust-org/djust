"""`{% url %}` raises `NoReverseMatch` like Django; `as var` stores `''` (#2563).

Django's `URLNode.render`::

    url = ""
    try:
        url = reverse(view_name, args=args, kwargs=kwargs, current_app=current_app)
    except NoReverseMatch:
        if self.asvar is None:
            raise
    if self.asvar:
        context[self.asvar] = url
        return ""
    return url

Historically the backend had a regex URL pre-pass alongside the native
CustomTag channel. Issue #2616 removed that pre-pass: every entry point now
uses UrlTagHandler at the node's actual render position. These tests retain
coverage of literal and variable names, assignment, and exception identity.

Every parity case is measured against LIVE Django in-process on both the
plain backend and the LiveView entry.

Refs #2563, #2557 (the parse-time cells), #2547, #2508, #2506, #2037, #1646.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest

pytest.importorskip("django")

from django.core.exceptions import PermissionDenied  # noqa: E402
from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402
from django.template import TemplateSyntaxError  # noqa: E402
from django.test import RequestFactory, override_settings  # noqa: E402
from django.urls import NoReverseMatch, include, path  # noqa: E402

from djust import _rust  # noqa: E402
from djust.template_tags import AsVarName  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# URLconf — `index` and `client/<int:id>/`, as in Django's own test_url.py,
# plus a namespace so the `ns:name` shape is on the table.
# ---------------------------------------------------------------------------
def _view(request, *args, **kwargs):
    return None


_ns_patterns = [path("", _view, name="index")]

urlpatterns = [
    path("", _view, name="index"),
    path("client/<int:id>/", _view, name="client"),
    path("ns/", include((_ns_patterns, "ns"), namespace="ns")),
]


# ---------------------------------------------------------------------------
# The three render paths, and one outcome shape for all of them
# ---------------------------------------------------------------------------
def django_render(source: str, context: dict) -> str:
    return DjangoTemplate(source).render(DjangoContext(dict(context)))


def backend_render(source: str, context: dict) -> str:
    """The plain backend, with URL nodes dispatched during rendering."""
    from djust.template_backend import DjustTemplateBackend

    backend = DjustTemplateBackend(
        params={"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
    )
    return str(backend.from_string(source).render(context=dict(context), request=None))


def liveview_render(source: str, context: dict) -> str:
    """The LiveView entry uses the same URL handler."""
    view = _rust.RustLiveView(source, [])
    view.set_raw_py_values(dict(context))
    return view.render()


DJUST_PATHS = [
    pytest.param(backend_render, id="DjustTemplateBackend"),
    pytest.param(liveview_render, id="RustLiveView"),
]


@pytest.fixture(autouse=True)
def _this_urlconf():
    with override_settings(ROOT_URLCONF=__name__):
        yield


def outcome(render: Callable[[str, dict], str], source: str, context: dict) -> tuple:
    """`("ok", output)` or `("raise", type, message)` — comparable across engines."""
    try:
        return ("ok", render(source, context))
    except Exception as exc:  # noqa: BLE001 — the type IS the measurement
        return ("raise", type(exc), str(exc))


# ---------------------------------------------------------------------------
# Django-parity differential
# ---------------------------------------------------------------------------
PARITY_CASES = [
    pytest.param("{% url 'nope' %}", {}, id="quoted-missing-raises"),
    pytest.param("{% url 'nope' as v %}[{{ v }}]", {}, id="quoted-missing-asvar-stores-empty"),
    pytest.param("{% url 'index' %}", {}, id="quoted-valid"),
    pytest.param("{% url 'index' as v %}[{{ v }}]", {}, id="quoted-valid-asvar"),
    pytest.param("{% url named %}", {"named": "nope"}, id="variable-missing-raises"),
    pytest.param("{% url named %}", {"named": "index"}, id="variable-valid"),
    pytest.param("{% url named as v %}[{{ v }}]", {"named": "nope"}, id="variable-missing-asvar"),
    pytest.param("{% url named as v %}[{{ v }}]", {"named": "index"}, id="variable-valid-asvar"),
    pytest.param("{% url 'client' %}", {}, id="missing-args-message-lists-patterns"),
    pytest.param("{% url 'client' id=7 %}", {}, id="kwargs-literal"),
    pytest.param("{% url 'client' 7 %}", {}, id="args-literal"),
    pytest.param("{% url 'client' id=cid %}", {"cid": 7}, id="kwargs-variable"),
    pytest.param("{% url 'client' cid %}", {"cid": 7}, id="args-variable"),
    pytest.param("{% url 'client' id='x' %}", {}, id="kwargs-wrong-type-raises"),
    pytest.param(
        "{% for c in cs %}{% url 'nope' c.id %}{% endfor %}",
        {"cs": [{"id": 3}]},
        id="loop-variable-missing-raises",
    ),
    pytest.param(
        "{% for c in cs %}{% url 'client' c.id %}{% endfor %}",
        {"cs": [{"id": 3}]},
        id="loop-variable-valid",
    ),
    pytest.param(
        "{% for c in cs %}{% url 'nope' c.id as v %}[{{ v }}]{% endfor %}",
        {"cs": [{"id": 3}]},
        id="loop-variable-missing-asvar",
    ),
    pytest.param("{% url 'ns:index' %}", {}, id="namespaced-valid"),
    pytest.param("{% url 'ns:nope' %}", {}, id="namespaced-missing-raises"),
    pytest.param("{% url 'ns:nope' as v %}[{{ v }}]", {}, id="namespaced-missing-asvar"),
    # The `as`-tail decision belongs to the RAW tokens, and only to them
    # (#2563 review). Both rows below have an ORDINARY argument that merely
    # RESOLVES to the string `as`, so Django reverses with it and raises;
    # while the handler re-ran `args[-2] == "as"` on its resolved arguments
    # it read them as `as var` forms and rendered `''` instead.
    pytest.param(
        "{% url named 'as' v %}",
        {"named": "index", "v": "x"},
        id="quoted-as-literal-is-an-argument",
    ),
    pytest.param(
        "{% url named sep v %}",
        {"named": "index", "sep": "as", "v": "x"},
        id="variable-valued-as-is-an-argument",
    ),
    # Django's `TemplateSyntaxError` must reach the caller with its type, not
    # flattened into `Exception("Error rendering template: …")` (#2563
    # review). The handler-level test cannot see this — it never crosses the
    # render boundary — so the row lives here, on both entries.
    pytest.param("{% url %}", {}, id="no-arguments-is-template-syntax-error"),
]


class TestDjangoParity:
    """Same source, same context, same outcome as Django — output or
    exception type AND message — on both djust entry points."""

    @pytest.mark.parametrize("render", DJUST_PATHS)
    @pytest.mark.parametrize("source,context", PARITY_CASES)
    def test_same_outcome_as_django(self, render, source, context):
        expected = outcome(django_render, source, context)
        actual = outcome(render, source, context)
        assert actual == expected, f"{source!r} with {context!r}"

    def test_the_table_covers_both_directions(self):
        """The differential must contain raise cases AND `''` cases, or a
        fail-soft regression on one side would pass every row."""
        kinds = {outcome(django_render, p.values[0], p.values[1])[0] for p in PARITY_CASES}
        assert kinds == {"ok", "raise"}
        raising = [
            p.id
            for p in PARITY_CASES
            if outcome(django_render, p.values[0], p.values[1])[0] == "raise"
        ]
        assert len(raising) >= 6, raising
        for p in PARITY_CASES:
            if "asvar" in p.id:
                assert outcome(django_render, p.values[0], p.values[1])[0] == "ok", p.id


# ---------------------------------------------------------------------------
# The #2506-style pin: a NoReverseMatch is NEVER swallowed into ''
# ---------------------------------------------------------------------------
class TestNeverSwallowed:
    @pytest.mark.parametrize("render", DJUST_PATHS)
    def test_reverse_failure_reaches_the_caller_as_the_same_instance(self, render, monkeypatch):
        """Count the `reverse` calls and hand back ONE exception instance:
        the caller must see that instance — not `''`, not a wrapper."""
        import django.urls

        raised = NoReverseMatch("planted")
        calls = []

        def counting_reverse(*args, **kwargs):
            calls.append((args, kwargs))
            raise raised

        monkeypatch.setattr(django.urls, "reverse", counting_reverse)
        with pytest.raises(NoReverseMatch) as excinfo:
            render('<a href="{% url named %}">x</a>', {"named": "anything"})
        assert excinfo.value is raised
        assert len(calls) == 1, calls

    @pytest.mark.parametrize("render", DJUST_PATHS)
    def test_reverse_failure_under_asvar_binds_empty_not_the_wrapper(self, render, monkeypatch):
        import django.urls

        def failing_reverse(*args, **kwargs):
            raise NoReverseMatch("planted")

        monkeypatch.setattr(django.urls, "reverse", failing_reverse)
        assert render("[{% url named as v %}{{ v }}]", {"named": "anything"}) == "[]"

    def test_liveview_http_get_does_not_serve_a_blank_href(self):
        """The migration-risk path (§3.4 of the plan): a LiveView whose
        template reverses a missing name must NOT answer 200 with `href=""`."""
        from djust import LiveView

        class BrokenLinkView(LiveView):
            template = "<div dj-root><a href=\"{% url 'nope' %}\">x</a></div>"

        request = RequestFactory().get("/")
        request.session = MagicMock()  # item assignment for the view's session save
        request.session.session_key = "test-session-2563"
        with pytest.raises(NoReverseMatch):
            BrokenLinkView.as_view()(request)


# ---------------------------------------------------------------------------
# The exception type crosses the sidecar WHOLE on all three call sites
# ---------------------------------------------------------------------------
class _Raises:
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    def render(self, *args: Any) -> str:
        raise self.exc


class CustomFailure(Exception):
    """A project's own exception type — not on any allowlist."""


def _register(kind: str, name: str, handler: object) -> None:
    if kind == "tag":
        _rust.register_tag_handler(name, handler)
    elif kind == "block":
        _rust.register_block_tag_handler(name, f"end{name}", handler)
    elif kind == "assign":
        _rust.register_assign_tag_handler(name, handler)
    else:  # pragma: no cover
        raise AssertionError(kind)


def _source(kind: str, name: str) -> str:
    if kind == "block":
        return f"{{% {name} %}}body{{% end{name} %}}"
    return f"{{% {name} %}}"


SIDECAR_SITES = [
    pytest.param("tag", id="call_handler_with_py_sidecar"),
    pytest.param("block", id="call_block_handler_with_py_sidecar"),
    pytest.param("assign", id="call_assign_handler_with_py_sidecar"),
]


class TestExceptionCrossesWhole:
    """#2508 closed this for the attribute walk and #2547 for the bindings
    channel; the three sidecar sites were still flattening to a string —
    `PermissionDenied` from a handler was a 500, not a 403 (#1646)."""

    @pytest.mark.parametrize("kind", SIDECAR_SITES)
    def test_raw_render_template_sees_the_original_instance(self, kind):
        planted = PermissionDenied("planted")
        _register(kind, f"boom_{kind}", _Raises(planted))
        with pytest.raises(PermissionDenied) as excinfo:
            _rust.render_template(_source(kind, f"boom_{kind}"), {})
        assert excinfo.value is planted
        assert excinfo.type is PermissionDenied

    @pytest.mark.parametrize("kind", SIDECAR_SITES)
    def test_a_custom_exception_type_is_not_a_wrapper(self, kind):
        planted = CustomFailure("custom")
        _register(kind, f"custom_{kind}", _Raises(planted))
        with pytest.raises(CustomFailure) as excinfo:
            _rust.render_template(_source(kind, f"custom_{kind}"), {})
        assert excinfo.value is planted
        assert not isinstance(excinfo.value, RuntimeError)

    @pytest.mark.parametrize("kind", SIDECAR_SITES)
    def test_backend_render_dispatches_on_the_type(self, kind):
        """Through `DjustTemplate.render`, a `PermissionDenied` must stay a
        `PermissionDenied` so Django answers 403, not 500 (#2508)."""
        planted = PermissionDenied("planted")
        _register(kind, f"deny_{kind}", _Raises(planted))
        with pytest.raises(PermissionDenied) as excinfo:
            backend_render(_source(kind, f"deny_{kind}"), {})
        assert excinfo.value is planted

    @pytest.mark.parametrize("kind", SIDECAR_SITES)
    def test_liveview_render_dispatches_on_the_type(self, kind):
        planted = PermissionDenied("planted")
        _register(kind, f"lv_{kind}", _Raises(planted))
        with pytest.raises(PermissionDenied) as excinfo:
            liveview_render(_source(kind, f"lv_{kind}"), {})
        assert excinfo.value is planted

    def test_engine_errors_keep_the_attribution_prefix(self):
        """A REGISTRY failure — not the handler's exception — is still an
        engine error with the tag named, so the hint machinery keeps working."""

        class NotAString:
            def render(self, args, context):
                return 42

        _rust.register_tag_handler("notastring", NotAString())
        with pytest.raises(RuntimeError, match=r"Custom tag 'notastring' error: .*must return"):
            _rust.render_template("{% notastring %}", {})


# ---------------------------------------------------------------------------
# The handler's own contract
# ---------------------------------------------------------------------------
class TestUrlTagHandlerContract:
    def test_no_name_is_djangos_syntax_error(self):
        from djust.template_tags.url import UrlTagHandler

        with pytest.raises(TemplateSyntaxError) as excinfo:
            UrlTagHandler().render([], {})
        assert str(excinfo.value) == "'url' takes at least one argument, a URL pattern name."

    def test_returns_bindings_shape(self):
        from djust.template_tags.url import UrlTagHandler

        handler = UrlTagHandler()
        assert handler.RETURNS_BINDINGS is True
        assert handler.ACCEPTS_AS_VAR is True
        assert handler.render(["index"], {}) == ("/", {})
        assert handler.render(["index", "as", AsVarName("v")], {}) == ("", {"v": "/"})
        assert handler.render(["nope", "as", AsVarName("v")], {}) == ("", {"v": ""})
        with pytest.raises(NoReverseMatch):
            handler.render(["nope"], {})

    def test_accepts_as_var_requires_returns_bindings(self):
        """The Rust registry refuses the half-declaration: an `as var` tail
        can only be bound through the bindings return."""

        class Half:
            ACCEPTS_AS_VAR = True

            def render(self, args, context):
                return ""

        with pytest.raises(TypeError, match="ACCEPTS_AS_VAR but not RETURNS_BINDINGS"):
            _rust.register_tag_handler("half_as_var", Half())

    def test_as_var_tail_reaches_an_opted_in_handler_as_tokens(self):
        """The mechanism behind (1): without `ACCEPTS_AS_VAR` the tail is two
        resolved (empty) variables; with it, the two literal tokens."""
        seen: dict = {}

        class Plain:
            def render(self, args, context):
                seen["plain"] = list(args)
                return ""

        class OptedIn:
            RETURNS_BINDINGS = True
            ACCEPTS_AS_VAR = True

            def render(self, args, context):
                seen["opted"] = list(args)
                return "", {}

        _rust.register_tag_handler("plain_tail", Plain())
        _rust.register_tag_handler("opted_tail", OptedIn())
        _rust.render_template("{% plain_tail named as v %}", {"named": "x"})
        _rust.render_template("{% opted_tail named as v %}", {"named": "x"})
        assert seen["plain"] == ["x", "", ""]
        assert seen["opted"] == ["x", "as", "v"]

    def test_filter_expressions_in_args_still_resolve_on_p2(self):
        """`ACCEPTS_AS_VAR` must not have cost the engine's resolver: Django's
        `url13`/`url14` use `arg|join:"-"` and pass through P2 today."""
        assert liveview_render("{% url 'client' cid|add:'1' %}", {"cid": 6}) == "/client/7/"


# ---------------------------------------------------------------------------
# Structural pins
# ---------------------------------------------------------------------------
class TestStructuralPins:
    def test_url_handler_has_no_fail_soft_arm(self):
        src = (REPO / "python/djust/template_tags/url.py").read_text()
        assert "except Exception" not in src
        assert re.search(r"except NoReverseMatch as e:", src) is None
        assert "logger.warning" not in src

    def test_no_sidecar_site_stringifies_the_exception(self):
        """All three `call_*_with_py_sidecar` functions route the handler's
        exception through ONE mapping (#1646); the pre-#2563 `format!` arms
        are gone."""
        src = (REPO / "crates/djust_templates/src/registry.rs").read_text()
        for arm in (
            "Handler '{}' raised exception",
            "Block handler '{}' raised exception",
            "Assign handler '{}' raised exception",
        ):
            assert arm not in src, arm
        assert src.count(".map_err(handler_exception)") == 3
        assert "DjangoRustError::PythonException(err)" in src

    def test_renderer_routes_all_sidecar_sites_through_one_mapping(self):
        src = (REPO / "crates/djust_templates/src/renderer.rs").read_text()
        # 2 assign sites + custom + block = 4 call sites, one definition.
        assert src.count("handler_call_error(") == 5
        assert "DjangoRustError::PythonException(_) => err" in src

    def test_noreversematch_is_user_raised(self):
        from djust.template.rendering import _is_user_raised

        assert _is_user_raised(NoReverseMatch("x"))
        assert not _is_user_raised(RuntimeError("engine"))

    def test_template_syntax_error_is_user_raised(self):
        """A handler's own `TemplateSyntaxError` is user-raised BY
        CONSTRUCTION — the Rust engine never builds one — so
        `DjustTemplate.render` must not flatten it (#2563 review, #2605)."""
        from djust.template.rendering import _is_user_raised

        assert _is_user_raised(TemplateSyntaxError("'url' takes at least one argument"))
        assert not _is_user_raised(RuntimeError("engine"))

    def test_exactly_one_site_decides_whether_there_is_an_as_tail(self):
        """Django asks `bits[-2] == "as"` ONCE, of the raw tokens. So does
        djust — in `renderer.rs::resolve_custom_tag_args` — and everyone else
        READS that decision off `AsVarName` (#1646).

        The handler's copy of the test was the #2563-review bug: by the time
        it ran, the other positions were resolved, so an ordinary argument
        that merely resolved to `as` became a false `as var` and swallowed the
        `NoReverseMatch`. The two parity rows
        `quoted-as-literal-is-an-argument` /
        `variable-valued-as-is-an-argument` are the behavioural half of this
        pin; this is the structural half.
        """
        renderer = (REPO / "crates/djust_templates/src/renderer.rs").read_text()
        # The one decision, on the raw token stream.
        assert renderer.count('args[args.len() - 2] == "as"') == 1
        assert "TagArg::as_var_name(" in renderer

        # No handler that declared the policy re-derives it. Checked over the
        # LIVE registry rather than a hand-written file list, so a future
        # `ACCEPTS_AS_VAR` handler is covered the moment it registers — and
        # over the AST, so the prose above may quote the banned comparison
        # without tripping its own pin.
        import ast
        import inspect

        from djust.template_tags import get_registered_handlers

        declarers = [
            handler
            for handler in get_registered_handlers().values()
            if getattr(handler, "ACCEPTS_AS_VAR", False)
        ]
        assert declarers, "no handler declares ACCEPTS_AS_VAR — the pin would be vacuous"
        for handler in declarers:
            source_file = inspect.getsourcefile(type(handler))
            assert source_file is not None
            tree = ast.parse(Path(source_file).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Compare) and any(
                    isinstance(c, ast.Constant) and c.value == "as" for c in node.comparators
                ):
                    raise AssertionError(
                        f"{source_file} compares against the literal 'as' at line "
                        f"{node.lineno} — the engine already decided; read AsVarName"
                    )

        # And the handler consumes the marker.
        url_src = (REPO / "python/djust/template_tags/url.py").read_text()
        assert "isinstance(args[-1], AsVarName)" in url_src

    def test_as_var_name_is_a_plain_str_subclass(self):
        """The marker must not change what a handler that ignores it sees."""
        name = AsVarName("v")
        assert isinstance(name, str)
        assert name == "v"
        assert type(name) is not str
        assert name.upper() == "V"
