"""A PWA tag's render-failure diagnostic stays an invisible comment (#2434).

The four PWA handlers share one failure exit — ``_render_django_tag``'s
``except`` arm — which returns::

    <!-- djust: djust_pwa_head render failed (check server logs) -->

as a plain ``str``. Since #2379 the Rust bridge ESCAPES a handler's return
unless it carries ``__html__`` (Django's ``SimpleNode.render`` rule), so that
comment reached the page as the visible text ``&lt;!-- djust: … --&gt;`` — a
server-side failure shouted at the end user, on the one path whose entire job
is to be readable in view-source and invisible on the page.

Why the #2379 audit could not see it
------------------------------------
``test_custom_tag_return_escape_2379.py`` calls every handler with
``render([], {})``, and under this repo's own settings all four PWA tags
render SUCCESSFULLY there — ``Template.render`` returns a ``SafeString``, so
the enumeration only ever reached the success exit. The failure exit needs an
argument that makes the generated Django source unparseable, which is why the
same enumeration now carries a quote-bearing kwarg vector
(``_ARG_VECTORS``); before this fix that vector turns the enumeration red with
these four handlers as offenders, which is the mechanical net rather than this
file's targeted rows.

Reached from a real template, not by monkeypatching
---------------------------------------------------
``_build_django_tag`` assembles ``key="value"`` by concatenation, so a kwarg
value carrying a double quote produces ``name="he said "hi""`` — a
``TemplateSyntaxError``. That is a genuine end-to-end trigger under the repo's
own settings: no ``settings.configure(INSTALLED_APPS=[])``, no patched
``Template``, no forced exception. (The library-not-loadable case the issue
describes — ``djust`` absent from ``INSTALLED_APPS`` — is the same exit,
reached by a route a configured test process cannot take.)

What was decided, and why marked rather than emptied
-----------------------------------------------------
The alternative was to drop the comment and leave ``logger.exception`` as the
only record. It was rejected on two grounds, both checked here:

* the failure is an **absence** — no manifest link, no service-worker
  registration — and an absence is unattributable from the browser. The
  comment is what names the tag that went missing for a front-end developer
  who has no server-log access;
* ``{% call %}``'s sibling diagnostic in
  ``djust/components/function_component.py`` is ALREADY marked, by #2379's own
  single-exit ``safe_html``. Emptying this one would make two diagnostics of
  the same kind disagree —
  :class:`TestTheSiblingDiagnosticAgrees` pins that they do not.

The marker covers a constant shape plus ``escape(tag_name)``, per CLAUDE.md's
``mark_safe`` rule; :class:`TestTheMarkerCannotBeWidenedByItsArgument` is what
makes that escape load-bearing rather than decorative.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

pytest.importorskip("django")

import django  # noqa: E402
from django.conf import settings  # noqa: E402

if not settings.configured:  # pragma: no cover — import-time bootstrap
    settings.configure(
        DEBUG=False,
        USE_I18N=False,
        USE_TZ=False,
        TIME_ZONE="UTC",
        INSTALLED_APPS=[],
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        ],
    )
    django.setup()

from djust import _rust  # noqa: E402
from djust.template_tags import get_registered_handlers  # noqa: E402
from djust.template_tags import pwa  # noqa: E402,F401  (registers the four handlers)

#: The four handlers sharing ``_render_django_tag``'s failure exit.
PWA_TAGS = (
    "djust_pwa_head",
    "djust_pwa_manifest",
    "djust_sw_register",
    "djust_offline_indicator",
)

#: A kwarg value whose double quote breaks ``_build_django_tag``'s naive
#: ``key="value"`` assembly, so the generated Django source raises.
BREAKS_THE_GENERATED_SOURCE = 'he said "hi"'


def _diagnostic(tag_name: str) -> str:
    return f"<!-- djust: {tag_name} render failed (check server logs) -->"


# ---------------------------------------------------------------------------
# The regression
# ---------------------------------------------------------------------------


class TestTheFailureDiagnosticReachesThePageAsAComment:
    """The bug, from a real template through the real engine."""

    @pytest.mark.parametrize("tag", PWA_TAGS)
    def test_the_rendered_page_carries_a_comment_and_not_escaped_text(self, tag: str) -> None:
        out = _rust.render_template("{%% %s name=n %%}" % tag, {"n": BREAKS_THE_GENERATED_SOURCE})
        assert out == _diagnostic(tag), (
            "the failure exit was not reached — this row proves nothing about "
            f"the marker unless the diagnostic is what rendered: {out!r}"
        )
        assert "&lt;!--" not in out, (
            "the diagnostic rendered as VISIBLE ESCAPED TEXT, which is #2434"
        )

    @pytest.mark.parametrize("tag", PWA_TAGS)
    def test_the_handler_marks_the_diagnostic_it_returns(self, tag: str) -> None:
        out = get_registered_handlers()[tag].render([f"name={BREAKS_THE_GENERATED_SOURCE}"], {})
        assert out == _diagnostic(tag), f"the failure exit was not reached: {out!r}"
        assert hasattr(out, "__html__"), (
            "an unmarked `str` carrying markup is escaped by the bridge (#2379)"
        )

    def test_the_success_path_still_renders_live_markup(self) -> None:
        """The half #2379 got right, so the fix cannot be a blanket widening."""
        out = _rust.render_template('{% djust_pwa_manifest name="App" %}', {})
        assert "<meta" in out and "&lt;meta" not in out
        marked = get_registered_handlers()["djust_pwa_manifest"].render(["name=App"], {})
        assert hasattr(marked, "__html__")
        assert "render failed" not in marked, "this is the SUCCESS row"


class TestTheSiblingDiagnosticAgrees:
    """``{% call %}``'s missing-name comment, the precedent this follows.

    Marked at ``FunctionComponentTagHandler.render``'s single exit by #2379.
    Asserted at RUNTIME rather than read off that docstring: it is the whole
    consistency argument for marking rather than emptying, so it has to be
    true and not merely claimed.
    """

    def test_the_call_tags_missing_name_comment_renders_as_a_comment(self) -> None:
        from djust.components.rust_handlers import register_with_rust_engine

        register_with_rust_engine()
        out = _rust.render_template("{% call %}{% endcall %}", {})
        assert out == "<!-- djust: {% call %} missing component name -->"
        assert "&lt;!--" not in out


# ---------------------------------------------------------------------------
# The marker's own bound
# ---------------------------------------------------------------------------


class TestTheMarkerCannotBeWidenedByItsArgument:
    """``escape(tag_name)``, made load-bearing.

    ``mark_safe`` on a ``%``-interpolated string is the shape CLAUDE.md bans,
    and "every caller passes a literal" is an argument about today's four call
    sites rather than about the function. Escaping the value makes the bound
    local: whatever a caller passes, the marked string is one comment.

    Gate the ``escape`` off and this class goes red while every other row in
    the file stays green — the two mechanisms (mark, escape) are separately
    reachable (#2129).
    """

    def test_a_tag_name_carrying_the_comment_terminator_cannot_break_out(self) -> None:
        hostile = "--><script>alert(1)</script><!--"
        out = pwa._render_django_tag(hostile, {"name": "x"})
        assert hasattr(out, "__html__"), "still the marked exit"
        assert "<script>" not in out, (
            "the tag name closed the comment and injected an element — the "
            "marker was applied to an interpolated value that could carry markup"
        )
        assert out.count("-->") == 1, f"more than one comment terminator: {out!r}"


# ---------------------------------------------------------------------------
# The caller set
# ---------------------------------------------------------------------------


class TestEveryPwaHandlerRoutesThroughTheOneExit:
    """The SET of callers, not a floor (v1.1.1-2 retro).

    The diagnostic is correct in one place only because all four handlers
    delegate to ``_render_django_tag``. A fifth PWA tag that hand-rolls its own
    ``try/except`` would re-introduce #2434 in a place no marker covers, and
    would fail here rather than on a page.
    """

    @staticmethod
    def _module_ast() -> ast.Module:
        return ast.parse(pathlib.Path(pwa.__file__).read_text(encoding="utf-8"))

    def test_the_delegating_render_methods_are_exactly_the_registered_tags(self) -> None:
        tree = self._module_ast()
        delegating: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            registered = [
                d.args[0].value
                for d in node.decorator_list
                if isinstance(d, ast.Call)
                and isinstance(d.func, ast.Name)
                and d.func.id == "register"
                and d.args
                and isinstance(d.args[0], ast.Constant)
            ]
            if not registered:
                continue
            calls = {
                sub.func.id
                for sub in ast.walk(node)
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
            }
            if "_render_django_tag" in calls:
                delegating.update(registered)
        assert delegating == set(PWA_TAGS), (
            "a PWA tag handler does not route through `_render_django_tag`, so "
            "its failure path is not covered by the marked exit"
        )

    def test_the_failure_exit_is_a_single_marked_return(self) -> None:
        tree = self._module_ast()
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_render_django_tag"
        )
        handlers = [n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)]
        assert len(handlers) == 1, f"{len(handlers)} except arms — which one is the exit?"
        returns = [n for n in ast.walk(handlers[0]) if isinstance(n, ast.Return)]
        assert len(returns) == 1, f"{len(returns)} returns in the failure arm"
        call = returns[0].value
        assert isinstance(call, ast.Call) and isinstance(call.func, ast.Name), (
            f"the failure exit is no longer a marking call: {ast.dump(call)}"
        )
        assert call.func.id == "safe_html", (
            f"the failure exit returns through `{call.func.id}`, not `safe_html`"
        )
