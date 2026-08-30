"""An unknown filter NAME is refused while the template compiles (#2419).

The defect
----------
Django looks a filter name up in ``FilterExpression.__init__`` — at COMPILE
time::

    filter_func = parser.find_filter(filter_name)   # TemplateSyntaxError: Invalid filter: 'x'

djust looked it up in ``filters::apply_filter_full_safe``, on the VALUE, which
only ever happens if the node renders. So a name nothing implements compiled
here and refused on Django whenever a branch was dead or an operand was
short-circuited::

    {% if 0 %}{{ p|nosuchfilter }}{% endif %}   django  <<TemplateSyntaxError: Invalid filter>>
                                                djust   ''

    {% if 0 and p|nosuchfilter %}Y{% endif %}   django  <<TemplateSyntaxError: Invalid filter>>
                                                djust   ''

The blocker, and why it does not hold
-------------------------------------
#2411 measured this class and left it alone for a stated reason: djust's filter
registry is filled from PYTHON at runtime, so a parse-time refusal could refuse
a project's own ``@register.filter`` if the template were parsed before the
filter was registered. That is the right question, and the answer is measured
in ``TestTheRegistryIsPopulatedBeforeAnythingCanBeParsed``:

* ``DjustConfig.ready()`` warms the bridge, so the registry is complete at the
  END of ``django.setup()`` — strictly before any request, and djust never
  parses a template outside a render call (``Template::new`` is reached from
  ``render``/``render_with_diff``/``render_template*`` and nowhere else), so
  there is no window in which a user template is parsed with an empty registry.
* Django's ``Engine.template_libraries`` is filled from ``INSTALLED_APPS`` at
  engine construction, WITHOUT ``{% load %}`` — so the one bootstrap sweep sees
  every filter Django itself could ever see. djust's registry is therefore a
  superset of Django's per-template view of the names, and refusing only names
  in NEITHER the built-in table NOR the registry can never refuse a template
  Django compiles.
* A refusal is never CACHED: ``TEMPLATE_CACHE``/``PARSED_TEMPLATE_CACHE`` are
  written only after a successful parse, so a filter registered later is picked
  up on the next render rather than poisoning the process.

One site, both shapes
---------------------
#2411's condition for moving this at all was that ``{{ … }}`` and the tag
operands move TOGETHER, since doing one would be new parallel-path drift
(#1646). One edit does both: ``{{ … }}`` reaches ``parse_filter_specs`` through
``parse_token`` and every tag operand reaches it through
``validate_tag_operand``, so the lookup went into ``parse_filter_specs`` and
nowhere else. ``TestOneSiteClosesBothShapes`` pins that it is one site.

Every expectation here is LIVE Django, never a transcription.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

import pytest
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate

from djust import _rust

CTX: dict[str, object] = {"p": "a b c", "q": 2}

_CRATES = pathlib.Path(__file__).resolve().parents[2] / "crates" / "djust_templates" / "src"
PARSER_RS = _CRATES / "parser.rs"
FILTERS_RS = _CRATES / "filters.rs"
ARITY_RS = _CRATES / "filter_arity.rs"

#: Every shape #2419 is about: a position where djust's render-time walk could
#: fail to reach the filter. The ``{{ }}`` half and the TAG half are both here
#: on purpose — one without the other is the drift #2411 refused to introduce.
UNREACHED_SHAPES = [
    # `{{ … }}`, which `parse_token` compiles
    "{% if 0 %}{{ p|nosuchfilter }}{% endif %}",
    "{% if 1 %}A{% else %}{{ p|nosuchfilter }}{% endif %}",
    "{% for x in empty %}{{ x|nosuchfilter }}{% endfor %}",
    "{% with v=1 %}{% if 0 %}{{ p|nosuchfilter }}{% endif %}{% endwith %}",
    "{% block b %}{% if 0 %}{{ p|nosuchfilter }}{% endif %}{% endblock %}",
    # TAG operands, which `validate_tag_operand` compiles
    "{% if 0 and p|nosuchfilter %}Y{% endif %}",
    "{% if 1 or p|nosuchfilter %}Y{% endif %}",
    "{% if 0 %}{% if p|nosuchfilter %}Y{% endif %}{% endif %}",
    "{% if 0 %}{% for x in p|nosuchfilter %}Y{% endfor %}{% endif %}",
    "{% if 0 %}{% with v=p|nosuchfilter %}Y{% endwith %}{% endif %}",
    "{% if 0 %}A{% elif p|nosuchfilter %}B{% endif %}",
    "{% if p|date:missingvar|nosuchfilter %}Y{% endif %}",
]

#: The shapes Django does NOT compile the contents of, so it never sees the
#: name at all. They are the control: a parse-time check that refused these
#: would be MORE strict than Django, which is a divergence in the other
#: direction.
NOT_COMPILED_SHAPES = [
    "{% comment %}{{ p|nosuchfilter }}{% endcomment %}",
    "{% verbatim %}{{ p|nosuchfilter }}{% endverbatim %}",
]


def django_refuses(source: str) -> bool:
    try:
        DjangoTemplate(source).render(DjangoContext(dict(CTX, empty=[])))
    except Exception:  # noqa: BLE001 — any refusal is a refusal
        return True
    return False


def djust_refuses(source: str) -> tuple[bool, str]:
    try:
        return False, _rust.render_template(source, dict(CTX, empty=[]))
    except Exception as exc:  # noqa: BLE001
        return True, str(exc)
    except BaseException as exc:  # noqa: BLE001 — a panic is not a refusal
        return True, f"PANIC {exc}"


# ---------------------------------------------------------------------------
# The defect, on every shape that hid it
# ---------------------------------------------------------------------------


class TestAnUnknownNameRefusesWhereverItAppears:
    @pytest.mark.parametrize("source", UNREACHED_SHAPES)
    def test_django_refuses_and_so_does_djust(self, source: str) -> None:
        assert django_refuses(source), f"premise: Django compiles {source!r}"
        refused, out = djust_refuses(source)
        assert refused, f"djust rendered {out!r} for a template Django refuses"
        assert "Unknown filter" in out, out

    @pytest.mark.parametrize("source", NOT_COMPILED_SHAPES)
    def test_a_body_django_never_compiles_is_not_refused_here_either(self, source: str) -> None:
        """The control. ``{% comment %}`` and ``{% verbatim %}`` bodies are not
        compiled by Django, so a name inside one is not a name at all — and a
        check that refused them would be stricter than Django rather than
        equal to it."""
        assert not django_refuses(source), f"premise: Django refuses {source!r}"
        refused, out = djust_refuses(source)
        assert not refused, f"djust refused {source!r}: {out}"

    def test_the_reached_shape_was_already_refused_and_still_is(self) -> None:
        """The half that never diverged, kept so a fix that moved the refusal
        cannot quietly delete it."""
        refused, out = djust_refuses("{{ p|nosuchfilter }}")
        assert refused and "Unknown filter" in out, out


# ---------------------------------------------------------------------------
# The blocker the issue raised, measured
# ---------------------------------------------------------------------------


class TestARegisteredCustomFilterStillCompiles:
    """The regression this change had to avoid, in both registration shapes.

    The raw ``_rust.register_custom_filter`` is what the framework's own bridge
    calls; ``bootstrap_django_filters`` is the bridge itself, walking Django's
    ``template_libraries``. Both are exercised because only the second proves
    the PRODUCTION path — a project writes ``@register.filter``, never the
    PyO3 entry point.
    """

    NAME = "cf_2419_probe"

    @staticmethod
    def _register(name: str) -> None:
        _rust.register_custom_filter(name, lambda value, arg=None: f"[{value}:{arg}]", False, False)

    def test_a_registered_filter_compiles_in_a_position_that_never_renders(self) -> None:
        self._register(self.NAME)
        try:
            for source in (
                "{%% if 0 %%}{{ p|%s }}{%% endif %%}" % self.NAME,
                "{%% if 0 and p|%s %%}Y{%% endif %%}" % self.NAME,
                "{%% if 0 %%}{%% for x in p|%s %%}Y{%% endfor %%}{%% endif %%}" % self.NAME,
            ):
                refused, out = djust_refuses(source)
                assert not refused, f"a REGISTERED custom filter was refused: {out}"
        finally:
            _rust.unregister_custom_filter(self.NAME)

    def test_and_it_still_renders_where_it_is_reached(self) -> None:
        self._register(self.NAME)
        try:
            assert _rust.render_template("{{ p|%s }}" % self.NAME, {"p": "a"}) == "[a:None]"
        finally:
            _rust.unregister_custom_filter(self.NAME)

    def test_the_django_library_bridge_registers_in_time_for_the_parser(self) -> None:
        """The production shape: a ``@register.filter`` in a Django
        ``Library``, forwarded by ``bootstrap_django_filters`` — the same call
        ``DjustConfig.ready()`` makes at startup."""
        from django import template as django_template

        from djust.template_filters import bootstrap_django_filters

        library = django_template.Library()

        @library.filter(name="cf_2419_library")
        def _shout(value):  # pragma: no cover - trivial
            return f"{value}!"

        engine = DjangoTemplate("").engine
        engine.template_libraries["cf_2419_lib"] = library
        try:
            bootstrap_django_filters()
            assert _rust.has_custom_filter("cf_2419_library"), "the bridge did not forward it"
            refused, out = djust_refuses("{% if 0 %}{{ p|cf_2419_library }}{% endif %}")
            assert not refused, f"a bridged @register.filter was refused: {out}"
            assert _rust.render_template("{{ p|cf_2419_library }}", {"p": "a"}) == "a!"
        finally:
            engine.template_libraries.pop("cf_2419_lib", None)
            _rust.unregister_custom_filter("cf_2419_library")

    def test_an_unregistered_name_is_refused_which_is_what_makes_the_above_mean_anything(
        self,
    ) -> None:
        """The gate-off sibling (#1468/#1859). Without it,
        ``test_a_registered_filter_compiles…`` would pass just as well against
        an engine that never checks anything."""
        refused, out = djust_refuses("{% if 0 %}{{ p|cf_2419_probe_NEVER_REGISTERED }}{% endif %}")
        assert refused and "Unknown filter" in out, out


class TestTheRegistryIsPopulatedBeforeAnythingCanBeParsed:
    """Why the parse-time lookup is safe, as properties rather than as prose.

    Neither of these is about #2419's own code — they are the premises the
    change rests on, so they belong in a test that goes red if either stops
    holding.
    """

    def test_djangos_libraries_are_collected_without_a_load_tag(self) -> None:
        """The bridge sweeps ``engine.template_libraries``. If that map were
        filled by ``{% load %}`` rather than by ``INSTALLED_APPS``, the sweep
        would see only what had already been loaded and the registry would be
        a SUBSET of Django's names rather than a superset."""
        engine = DjangoTemplate("").engine
        assert "i18n" in engine.template_libraries, sorted(engine.template_libraries)
        # …and Django itself refuses an unloaded library's filter, which is the
        # other half: djust's registry being a superset means this check can
        # only ever refuse names Django refuses too.
        assert django_refuses("{{ p|language_bidi }}"), "premise: Django needs {% load %}"

    def test_the_bridge_has_run_by_the_end_of_django_setup(self) -> None:
        """``DjustConfig.ready()`` warms it, so by the time any template can be
        parsed the registry already holds the project's filters.

        Measured in a SUBPROCESS, for two reasons: the claim is about the state
        at the end of ``django.setup()`` and nothing in-process can observe
        that moment any more, and the registry is a process global that another
        test file clears at teardown
        (``tests/unit/test_rust_custom_filters_1121.py``), so an in-process
        assertion would be reading whatever ran before it.
        """
        script = (
            "import django;django.setup();"
            "from djust import _rust;"
            "print(int(_rust.has_custom_filter('field_value')))"
        )
        env = dict(os.environ, DJANGO_SETTINGS_MODULE="demo_project.settings")
        env.pop("PYTEST_CURRENT_TEST", None)
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(pathlib.Path(__file__).resolve().parents[2] / "examples" / "demo_project"),
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip().endswith("1"), (proc.stdout, proc.stderr)

    def test_the_backend_render_path_arms_the_bridge_itself(self) -> None:
        """The third top-level render entry (#2223's shape, one entry over).

        ``DjustConfig.ready()``'s startup warm is opt-OUT-able
        (``filter_bridge_warm``), and the LiveView path re-arms the bridge in
        ``_initialize_rust_view`` while this one did not — so a project that
        turned the warm off had no bridge on the plain-Django-view path at all.
        Now that an unknown name refuses at PARSE time, "the registry is
        populated before the parse" has to hold for every entry rather than
        for two of three.
        """
        from unittest import mock

        from djust.template.backend import DjustTemplateBackend

        backend = DjustTemplateBackend(
            {
                "NAME": "djust_2419",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )
        with mock.patch("djust.mixins.rust_bridge._ensure_custom_filters_bridged") as armed:
            backend.from_string("{{ p|upper }}").render({"p": "a"})
        assert armed.called, "the backend render path never armed the filter bridge"

    def test_a_refusal_is_not_cached_so_a_later_registration_heals_it(self) -> None:
        """``TEMPLATE_CACHE`` is written only after a SUCCESSFUL parse. A
        template refused now must compile once the filter arrives, or a
        transient empty registry would poison the process for its lifetime."""
        source = "{% if 0 %}{{ p|cf_2419_late }}{% endif %}"
        refused, _ = djust_refuses(source)
        assert refused, "premise: not registered yet"
        _rust.register_custom_filter("cf_2419_late", lambda value, arg=None: value, False, False)
        try:
            refused, out = djust_refuses(source)
            assert not refused, f"the refusal was cached: {out}"
        finally:
            _rust.unregister_custom_filter("cf_2419_late")


# ---------------------------------------------------------------------------
# The oracle, and the two ways it could drift
# ---------------------------------------------------------------------------


def _builtin_match_arm_names() -> set[str]:
    """The names ``apply_builtin_filter``'s top-level match dispatches on, read
    out of ``filters.rs``."""
    src = FILTERS_RS.read_text()
    body = src.split("fn apply_builtin_filter(", 1)[1]
    names: set[str] = set()
    for line in body.splitlines():
        # Top-level arms sit at eight spaces; a nested match's arms are deeper.
        match = re.match(r'^ {8}("(?:[a-z_0-9]+)"(?:\s*\|\s*"[a-z_0-9]+")*)\s*=>', line)
        if match:
            names |= set(re.findall(r'"([a-z_0-9]+)"', match.group(1)))
    return names


def _arity_table() -> dict[str, int]:
    """``name -> minimum template arguments``, read out of the ARITY table."""
    rows = re.findall(r'^    \("([a-z_0-9]+)", (\d+), \d+, \d+\)', ARITY_RS.read_text(), re.M)
    return {name: int(minimum) for name, minimum in rows}


def _arity_table_names() -> set[str]:
    return set(_arity_table())


class TestTheOracleIsTheDispatchTable:
    """``filters::is_known_filter`` asks the ARITY table for the built-in half
    rather than carrying a name list of its own, so that the parse-time check
    and the render-time dispatch cannot answer differently.

    Both drift directions are silent in production and loud here:

    * an arm added WITHOUT an arity row — the parser refuses a filter the
      engine implements;
    * an arity row added WITHOUT an arm — the parser accepts a name that then
      fails at render, which is the pre-#2419 behaviour restored for one name.
    """

    def test_the_two_name_sets_are_equal(self) -> None:
        arms, arity = _builtin_match_arm_names(), _arity_table_names()
        assert arms, "the match-arm extraction found nothing — the regex is stale"
        assert arms == arity, {
            "in the dispatch but not the arity table": sorted(arms - arity),
            "in the arity table but not the dispatch": sorted(arity - arms),
        }

    def test_the_oracle_reads_the_arity_table_and_the_registry_and_nothing_else(self) -> None:
        """A second name list is what this whole class exists to prevent, so
        the body is pinned rather than only its output (#1646)."""
        body = FILTERS_RS.read_text().split("pub fn is_known_filter(", 1)[1].split("\n}\n", 1)[0]
        assert "filter_arity::builtin_arity(" in body, body
        assert "is_registered_custom_filter(" in body, body

    @pytest.mark.parametrize("name,minimum", sorted(_arity_table().items()))
    def test_every_built_in_still_compiles_in_an_unrendered_position(
        self, name: str, minimum: int
    ) -> None:
        """The consequence, checked per name rather than in aggregate: refusing
        a real built-in would break every template using it.

        The argument is supplied when the table says the filter requires one,
        because otherwise the ARITY check (#2400) refuses first and this would
        be measuring that instead. Its VALUE is irrelevant — the node never
        renders, and only the parse is under test.
        """
        spec = f'{name}:"1"' if minimum else name
        refused, out = djust_refuses("{%% if 0 %%}{{ p|%s }}{%% endif %%}" % spec)
        assert not refused, f"built-in {name!r} was refused at parse time: {out}"


# ---------------------------------------------------------------------------
# One site, both shapes — the condition #2411 attached to moving this
# ---------------------------------------------------------------------------


class TestOneSiteClosesBothShapes:
    def test_the_lookup_lives_in_parse_filter_specs(self) -> None:
        body = PARSER_RS.read_text().split("fn parse_filter_specs(", 1)[1].split("\n}\n", 1)[0]
        assert "is_known_filter(" in body, body

    def test_and_nowhere_else_in_the_parser(self) -> None:
        """A second call site would mean the rule had been copied rather than
        shared — the drift #2411 declined to introduce (#1646)."""
        assert PARSER_RS.read_text().count("is_known_filter(") == 1

    def test_both_entry_points_reach_it(self) -> None:
        """``{{ … }}`` through ``parse_token``, tag operands through
        ``validate_tag_operand``. Pinned as SOURCE because the behavioural
        halves above cannot tell "both call the shared function" from "each
        has its own copy"."""
        src = PARSER_RS.read_text()
        operand = src.split("pub(crate) fn validate_tag_operand", 1)[1].split("\n}\n", 1)[0]
        assert "parse_filter_specs(" in operand, operand
        assert src.count("parse_filter_specs(&parts") >= 1, "parse_token's call went missing"


# ---------------------------------------------------------------------------
# Django's order among the refusals, measured rather than assumed
# ---------------------------------------------------------------------------


class TestDjangosOrderAmongTheRefusals:
    """``FilterExpression.__init__`` runs argument-``Variable`` →
    ``find_filter`` → ``args_check``. Two consequences are worth pinning, and
    one non-consequence is worth recording so nobody "fixes" it."""

    def test_the_underscore_rule_still_wins_over_the_name_lookup(self) -> None:
        """Django builds the argument's ``Variable`` BEFORE ``find_filter``,
        and djust's ``validate_variable_name`` call sits above the lookup for
        that reason. Same message on both engines."""
        assert django_refuses("{{ p|nosuchfilter:_y }}")
        refused, out = djust_refuses("{{ p|nosuchfilter:_y }}")
        assert refused and "may not begin with underscores" in out, out

    def test_the_arity_check_and_the_lookup_can_never_both_apply(self) -> None:
        """Which is why their relative order is not a behavioural choice: the
        arity table describes exactly the built-ins, and the lookup fires only
        for names it does not describe. A reordering would change nothing,
        and a mechanism that changes nothing is decorative (#2233)."""
        refused, out = djust_refuses('{{ p|nosuchfilter:"x" }}')
        assert refused and "Unknown filter" in out, out
        refused, out = djust_refuses('{{ p|upper:"x" }}')
        assert refused and "requires 1 arguments" in out, out

    def test_the_lexer_bound_runs_first_here_and_second_on_django(self) -> None:
        """The one ordering djust does NOT reproduce, recorded rather than
        hidden. ``split_filter_spec`` is what produces the name at all, so it
        cannot run after the lookup. Both engines refuse the template; only
        the wording differs, which is the property this change is about."""
        source = '{{ p|nosuchfilter:"a":"b" }}'
        with pytest.raises(Exception, match="Invalid filter"):
            DjangoTemplate(source)
        refused, out = djust_refuses(source)
        assert refused, out
        assert "Could not parse the remainder" in out, out
