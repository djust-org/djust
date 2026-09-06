"""``{% cache %}`` operand resolution must be Django's, byte for byte (#2658).

#2658 asked for message-text parity and called it minor. Running the two
engines side by side showed the message was the visible end of a real
divergence: ``CacheTagHandler`` resolved every operand with
``ignore_failures=True`` and then applied a miss policy of its own — ``None``
for the vary operands, an "unknown variable" error for the expiry. Its comment
said Django resolves the vary operands with ``ignore_failures``.
``django/templatetags/cache.py`` says otherwise::

    vary_on = [var.resolve(context) for var in self.vary_on]

so an unresolvable vary operand is ``string_if_invalid`` (``''``) in Django and
was ``None`` here — and ``make_template_fragment_key`` hashes the vary list, so
the two engines stored the SAME fragment under DIFFERENT keys. A message nit
and a cache-key bug, one root.

Two of the issue's four items were already true when this was written, which is
why every claim below is measured against a live Django ``Engine`` rather than
asserted from the issue text:

* the arity errors (``test_cache11`` / ``test_cache12``) already raise at PARSE
  time, with Django's exact text — ``CacheTagHandler.validate_at_parse`` calls
  Django's own ``do_cache``;
* the body still renders on a cache HIT, which needs a lazy-body block-handler
  protocol and is deliberately NOT attempted here (see the issue).
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pytest

pytest.importorskip("django")

from django.core.cache import caches  # noqa: E402
from django.template import Context as DjangoContext  # noqa: E402
from django.template import Engine  # noqa: E402

from djust.template.backend import DjustTemplateBackend  # noqa: E402

_LIB = {"cache": "django.templatetags.cache"}


@pytest.fixture(autouse=True)
def _clear() -> Any:
    caches["default"].clear()
    yield
    caches["default"].clear()


@pytest.fixture
def backend() -> Any:
    return DjustTemplateBackend(
        {
            "NAME": "cache2658",
            "DIRS": [],
            "APP_DIRS": False,
            "OPTIONS": {"libraries": _LIB},
        }
    )


@pytest.fixture
def django_engine() -> Any:
    return Engine(libraries=_LIB)


def _django(engine: Any, src: str, ctx: Dict[str, Any]) -> Tuple[str, str]:
    try:
        template = engine.from_string(src)
    except Exception as exc:  # noqa: BLE001
        return "parse", f"{type(exc).__name__}: {exc}"
    try:
        return "ok", template.render(DjangoContext(ctx))
    except Exception as exc:  # noqa: BLE001
        return "render", f"{type(exc).__name__}: {exc}"


def _djust(backend: Any, src: str, ctx: Dict[str, Any]) -> Tuple[str, str]:
    try:
        template = backend.from_string(src)
    except Exception as exc:  # noqa: BLE001
        return "parse", f"{type(exc).__name__}: {exc}"
    try:
        return "ok", str(template.render(ctx))
    except Exception as exc:  # noqa: BLE001
        return "render", f"{type(exc).__name__}: {exc}"


def _keys() -> List[str]:
    """The fragment keys currently in the cache, cleared before each engine."""
    return sorted(caches["default"]._cache.keys())


#: Every shape the issue names, plus the neighbours a curated table misses.
#: ``(label, template body after ``{% load cache %}``, context)``.
CASES: List[Tuple[str, str, Dict[str, Any]]] = [
    # --- the two scoreboard cells (arity, parse-time) ----------------------
    ("cache11 no operands", "{% cache %}{% endcache %}", {}),
    ("cache12 one operand", "{% cache 1 %}{% endcache %}", {}),
    # --- the expiry --------------------------------------------------------
    ("literal expiry", '{% cache 60 "f" %}b{% endcache %}', {}),
    ("literal None is forever", '{% cache None "f" %}b{% endcache %}', {}),
    ("variable expiry", '{% cache t "f" %}b{% endcache %}', {"t": 60}),
    ("variable expiry is None", '{% cache t "f" %}b{% endcache %}', {"t": None}),
    ("filter expiry", '{% cache 2|add:1 "f" %}b{% endcache %}', {}),
    ("unresolvable expiry", '{% cache nope "f" %}b{% endcache %}', {}),
    ("non-integer expiry", '{% cache t "f" %}b{% endcache %}', {"t": "x"}),
    ("expiry resolves to a list", '{% cache t "f" %}b{% endcache %}', {"t": [1]}),
    ("dotted expiry", '{% cache d.t "f" %}b{% endcache %}', {"d": {"t": 5}}),
    ("dotted expiry missing leaf", '{% cache d.t "f" %}b{% endcache %}', {"d": {}}),
    # --- the vary operands (the cache-key axis) ----------------------------
    ("one vary", '{% cache 60 "f" v %}b{% endcache %}', {"v": "a"}),
    ("vary is None", '{% cache 60 "f" v %}b{% endcache %}', {"v": None}),
    ("vary unresolvable", '{% cache 60 "f" nope %}b{% endcache %}', {}),
    ("vary int", '{% cache 60 "f" v %}b{% endcache %}', {"v": 7}),
    ("two varies one missing", '{% cache 60 "f" v nope %}b{% endcache %}', {"v": 1}),
    ("two varies both missing", '{% cache 60 "f" a b %}b{% endcache %}', {}),
    ("vary with a filter", '{% cache 60 "f" v|upper %}b{% endcache %}', {"v": "a"}),
    ("vary dotted missing", '{% cache 60 "f" d.x %}b{% endcache %}', {"d": {}}),
    ("vary literal string", '{% cache 60 "f" "lit" %}b{% endcache %}', {}),
    # --- `using=` ----------------------------------------------------------
    ("using default", '{% cache 60 "f" using="default" %}b{% endcache %}', {}),
    ("using unknown alias", '{% cache 60 "f" using="nosuch" %}b{% endcache %}', {}),
    ("using with two operands only", '{% cache 10 using="default" %}b{% endcache %}', {}),
    ("using with a vary", '{% cache 60 "f" v using="default" %}b{% endcache %}', {"v": 1}),
]


class TestOutcomeMatchesDjango:
    @pytest.mark.parametrize("label,body,ctx", CASES, ids=[c[0] for c in CASES])
    def test_stage_and_message_match(
        self, backend: Any, django_engine: Any, label: str, body: str, ctx: Dict[str, Any]
    ) -> None:
        src = "{% load cache %}" + body
        caches["default"].clear()
        expected = _django(django_engine, src, ctx)
        caches["default"].clear()
        actual = _djust(backend, src, ctx)
        assert actual == expected, (
            f"{label}: djust {actual!r} != django {expected!r}\n"
            "Django's message text is the standard (#2581); the resolution that "
            "produces it is Django's own FilterExpression (#2658)."
        )


class TestCacheKeyMatchesDjango:
    """The key, not just the output — a divergent key is a silent cache miss.

    Each engine renders into a CLEARED cache and the resulting key is compared,
    so this measures ``make_template_fragment_key(fragment, vary_on)`` and thus
    the resolution of every vary operand. The same-name-into-one-cache shape
    would instead make the second render a HIT on the first engine's value and
    compare a string to itself.
    """

    @pytest.mark.parametrize(
        "label,body,ctx",
        [c for c in CASES if "nosuch" not in c[1] and "cache11" not in c[0]],
        ids=[c[0] for c in CASES if "nosuch" not in c[1] and "cache11" not in c[0]],
    )
    def test_key_matches(
        self, backend: Any, django_engine: Any, label: str, body: str, ctx: Dict[str, Any]
    ) -> None:
        src = "{% load cache %}" + body
        caches["default"].clear()
        if _django(django_engine, src, ctx)[0] != "ok":
            pytest.skip("Django does not reach the store for this case")
        expected = _keys()
        caches["default"].clear()
        assert _djust(backend, src, ctx)[0] == "ok"
        actual = _keys()
        assert actual == expected, (
            f"{label}: djust stored under {actual} where Django stores under "
            f"{expected} — the two engines cache the same fragment under "
            "different names (#2658)."
        )

    def test_an_unresolvable_vary_operand_is_the_case_that_diverged(
        self, backend: Any, django_engine: Any
    ) -> None:
        """The specific pre-fix divergence, spelled out.

        ``None`` and ``''`` are different vary values, so they hash to
        different keys. Pre-fix djust produced the ``None`` key; Django
        produces the ``''`` one. Asserted here against Django's key AND
        against the key the bug produced, so a revert cannot look green.
        """
        from django.core.cache.utils import make_template_fragment_key

        src = '{% load cache %}{% cache 60 "f" nope %}b{% endcache %}'
        # The fragment name is the RAW token, quote characters and all —
        # Django's own comment is "fragment_name can't be a variable", so
        # neither engine strips them.
        django_key = make_template_fragment_key('"f"', [""])
        buggy_key = make_template_fragment_key('"f"', [None])
        assert django_key != buggy_key, "the two policies must be distinguishable"

        caches["default"].clear()
        assert _django(django_engine, src, {})[0] == "ok"
        assert any(django_key in k for k in _keys()), _keys()

        caches["default"].clear()
        assert _djust(backend, src, {})[0] == "ok"
        assert any(django_key in k for k in _keys()), (
            f"djust did not use Django's key; stored {_keys()} (#2658)"
        )
        assert not any(buggy_key in k for k in _keys()), (
            "djust still resolves a missing vary operand to None (#2658)"
        )


class TestArityIsAlreadyRefusedAtParseTime:
    """#2658 item 2 was fixed before this PR; pinned so it cannot regress.

    ``CacheTagHandler.validate_at_parse`` hands the tokens to Django's own
    ``do_cache``, so the message is Django's by construction — including its
    doubled quotes, which are Django's and not a djust artefact.
    """

    @pytest.mark.parametrize("body", ["{% cache %}{% endcache %}", "{% cache 1 %}{% endcache %}"])
    def test_refused_by_from_string_not_by_render(self, backend: Any, body: str) -> None:
        from django.template import TemplateSyntaxError

        with pytest.raises((TemplateSyntaxError, Exception)) as caught:
            backend.from_string("{% load cache %}" + body)
        assert "requires at least 2 arguments" in str(caught.value)


class TestTheBodyStillRendersOnAHit:
    """#2658 item 1 is NOT fixed here — pinned as a known gap, not as correct.

    The block-handler protocol hands ``render()`` a body that is already a
    string, so the handler cannot decide *whether* to render. Making the tag
    actually save the work needs a lazy-body protocol; this test exists so that
    when one lands, it fails and is updated deliberately rather than the gap
    being rediscovered.
    """

    def test_a_hit_returns_the_cached_output_but_still_paid_for_the_body(
        self, backend: Any
    ) -> None:
        renders: List[int] = []

        class Probe:
            def __str__(self) -> str:
                renders.append(1)
                return "body"

        src = '{% load cache %}{% cache 300 "hitprobe" %}{{ probe }}{% endcache %}'
        template = backend.from_string(src)

        assert str(template.render({"probe": Probe()})) == "body"
        after_miss = len(renders)
        assert after_miss > 0, "the body must render on a MISS"

        assert str(template.render({"probe": Probe()})) == "body"
        assert len(renders) > after_miss, (
            "the body no longer renders on a cache hit — #2658 item 1 is fixed; "
            "delete this test and assert the new behaviour instead."
        )

    def test_item_4_is_now_only_a_consequence_of_item_1(
        self, backend: Any, django_engine: Any
    ) -> None:
        """#2658 item 4 no longer has the symptom the issue reported.

        The issue showed ``{{ k }}`` rendering EMPTY after a
        ``{% cycle … as k %}`` inside a ``{% cache %}`` body — an ``as``-binding
        that never escaped the body's sub-scope::

            django -> '<1>1|<1>1|<1>1|'
            djust  -> '<1>|<1>|<1>|'

        Measured now, the binding escapes fine; what differs is only that
        Django's iterations 2 and 3 are cache HITS, so its body — and its
        ``{% cycle %}`` — never runs again and ``k`` keeps the value bound on
        iteration 1. djust re-renders the body (item 1), so the cycle advances.

        So item 4 is not a separate scope bug to fix; it resolves exactly when
        item 1 does. Pinned rather than described, because the issue's
        description is now wrong and a reader would otherwise go looking for a
        scope bug that is not there.
        """
        src = (
            "{% load cache %}{% for i in v %}"
            '{% cache 300 f4 %}<{% cycle "1" "2" "3" as k %}>{% endcache %}'
            "{{ k }}|{% endfor %}"
        )
        ctx = {"v": [1, 2, 3]}

        caches["default"].clear()
        assert django_engine.from_string(src).render(DjangoContext(ctx)) == "<1>1|<1>1|<1>1|"

        caches["default"].clear()
        out = str(backend.from_string(src).render(ctx))
        assert "|" in out and out.split("|")[0].endswith("1"), out
        assert out == "<1>1|<1>2|<1>3|", (
            f"got {out!r}. Empty `{{{{ k }}}}` segments would be the ORIGINAL item-4 "
            "report (the binding not escaping the body); '<1>1|<1>1|<1>1|' would "
            "mean item 1 is fixed and this test should be replaced by an equality "
            "assertion against Django."
        )
