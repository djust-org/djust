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
* the body still rendered on a cache HIT when this file was written; the
  lazy-body block-handler protocol that fixes it landed afterwards, and
  :class:`TestTheBodyDoesNotRenderOnAHit` below pins the new behaviour where
  ``TestTheBodyStillRendersOnAHit`` pinned the gap.
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


@pytest.fixture
def body_probe() -> Any:
    """A tag that records every time it renders, for use INSIDE a cache body.

    The observable side effect has to be a tag rather than a ``__str__`` on a
    context value: the handler is handed a snapshot of the context on every
    render, so a ``__str__`` probe counts the snapshot too and cannot tell "the
    body ran" from "the tag was reached at all" (#2658).
    """
    from djust._rust import register_tag_handler, unregister_tag_handler

    calls: List[int] = []

    class Probe:
        RESOLVE_ARG_POSITIONS = frozenset()

        def render(self, args: List[str], context: Dict[str, Any]) -> str:
            calls.append(1)
            return "X"

    register_tag_handler("body_probe_2658", Probe())
    try:
        yield calls
    finally:
        unregister_tag_handler("body_probe_2658")


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


class TestReassertKeepsCacheABlockTag:
    """``reassert()`` must re-register ``cache`` the way it was registered.

    ``djust.test_isolation`` calls ``reassert()`` before each test, and it
    dispatched on ``isinstance`` of the two generic library handler classes —
    so ``CacheTagHandler``, which is neither, fell to the INLINE branch:
    ``cache`` was re-registered as an inline tag and its block handler
    unregistered, and ``{% cache %}…{% endcache %}`` stopped parsing as a block
    for the rest of the worker. Silent while the handler still had a ``render``
    the inline registry accepted; a ``TypeError`` out of ``reassert`` once it
    became a two-phase ``LAZY_BODY`` handler (#2658, #1646).
    """

    def test_a_bridged_cache_survives_a_reassert(self, backend: Any) -> None:
        from djust import template_libraries
        from djust._rust import has_block_tag_handler, has_tag_handler

        src = '{% load cache %}{% cache 300 "reassert2658" %}ok{% endcache %}'
        assert str(backend.from_string(src).render({})) == "ok"
        assert "cache" in template_libraries.owned_tags()

        template_libraries.reassert()

        assert has_block_tag_handler("cache"), "reassert dropped the block handler"
        assert not has_tag_handler("cache"), "reassert re-registered cache as an inline tag"
        caches["default"].clear()
        assert str(backend.from_string(src + " ").render({})) == "ok "


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


class TestTheBodyDoesNotRenderOnAHit:
    """#2658 item 1, now fixed: a HIT does not pay for the body.

    Was ``TestTheBodyStillRendersOnAHit``, which pinned the gap as a gap and
    failed with an instruction the moment a lazy body landed. The lazy-body
    block-handler protocol (``LAZY_BODY`` +
    ``before_body``/``after_body``) landed, so the same scenarios are pinned
    here as CORRECT behaviour instead.

    The side effect has to be a TAG, not a ``__str__`` on a context value: the
    context is snapshotted for the handler on every render, so a ``__str__``
    probe counts the snapshot as well as the body and cannot tell the two
    apart. That is what the old test measured, which is why it went on passing
    for a render or two after the body had in fact stopped running.
    """

    def test_the_body_side_effect_happens_once_across_two_renders(
        self, backend: Any, body_probe: List[int]
    ) -> None:
        src = '{% load cache %}{% cache 300 "hitprobe" %}[{% body_probe_2658 %}]{% endcache %}'
        template = backend.from_string(src)

        assert str(template.render({})) == "[X]"
        assert len(body_probe) == 1, "the body must render on a MISS"

        assert str(template.render({})) == "[X]", "the hit must still emit the fragment"
        assert len(body_probe) == 1, (
            f"the body ran {len(body_probe)} times; a cache HIT must not render it (#2658)"
        )

    def test_a_miss_stores_the_bytes_the_body_produced(self, backend: Any) -> None:
        """The stored fragment is byte-identical to the body's own output."""
        from django.core.cache.utils import make_template_fragment_key

        src = '{% load cache %}{% cache 300 "storebytes" %}<b>{{ p }}</b>{% endcache %}'
        out = str(backend.from_string(src).render({"p": "<script>&x"}))

        # The fragment name is the RAW token, quotes included — Django's
        # `do_cache` passes `tokens[2]` straight through ("fragment_name can't
        # be a variable"), which is why the keys read `template.cache."f"`.
        stored = caches["default"].get(make_template_fragment_key('"storebytes"', []))
        assert stored == out == "<b>&lt;script&gt;&amp;x</b>", (out, stored)

    def test_the_hit_output_is_not_escaped_a_second_time(
        self, backend: Any, django_engine: Any
    ) -> None:
        """A stored fragment is markup, and stays markup on the way back out."""
        src = '{% load cache %}{% cache 300 "escapetwice" %}<b>{{ p }}</b>{% endcache %}'
        ctx = {"p": "<script>&x"}

        caches["default"].clear()
        expected = django_engine.from_string(src).render(DjangoContext(ctx))

        caches["default"].clear()
        template = backend.from_string(src)
        assert str(template.render(ctx)) == expected
        assert str(template.render(ctx)) == expected, "the HIT re-escaped the fragment"

    def test_a_fragment_stored_as_a_plain_str_is_still_inserted_raw(self, backend: Any) -> None:
        """The ``mark_safe`` on the hit path, exercised where it is load-bearing.

        The hit returns what the CACHE BACKEND handed back. Locmem round-trips
        the body's ``SafeString`` through pickle, so the previous test would
        pass with or without the re-marking; a backend that stores bytes —
        Redis, memcached — hands back a plain ``str``, and the fragment would
        then be escaped a second time on every hit. Priming the cache with a
        plain ``str`` is that backend's behaviour without needing one.
        """
        from django.core.cache.utils import make_template_fragment_key

        src = '{% load cache %}{% cache 300 "plainstr" %}unused{% endcache %}'
        caches["default"].set(make_template_fragment_key('"plainstr"', []), "<b>stored</b>")

        assert str(backend.from_string(src).render({})) == "<b>stored</b>"

    def test_nested_cache_blocks(self, backend: Any, body_probe: List[int]) -> None:
        """An inner ``{% cache %}`` inside an outer one: both lazy, both keyed."""
        src = (
            '{% load cache %}{% cache 300 "outer2658" %}A'
            '{% cache 300 "inner2658" %}{% body_probe_2658 %}{% endcache %}'
            "B{% endcache %}"
        )
        template = backend.from_string(src)

        assert str(template.render({})) == "AXB"
        assert len(body_probe) == 1
        assert str(template.render({})) == "AXB"
        assert len(body_probe) == 1, "the outer hit re-rendered the nested body"

    def test_cache_block_inside_a_for_loop(self, backend: Any, body_probe: List[int]) -> None:
        """One key per iteration; the second pass is three hits, zero bodies."""
        src = (
            "{% load cache %}{% for i in v %}"
            '{% cache 300 "loop2658" i %}{% body_probe_2658 %}{% endcache %}'
            "{% endfor %}"
        )
        template = backend.from_string(src)

        assert str(template.render({"v": [1, 2, 3]})) == "XXX"
        assert len(body_probe) == 3, "one body per distinct vary operand"
        assert str(template.render({"v": [1, 2, 3]})) == "XXX"
        assert len(body_probe) == 3, "the second pass must be three hits"

    def test_a_body_that_raises_propagates_and_stores_nothing(self, backend: Any) -> None:
        """A failing body is not a fragment. Nothing is stored, and the next
        render is still a MISS rather than a hit on a half-written entry."""
        from djust._rust import register_tag_handler, unregister_tag_handler

        class Boom:
            RESOLVE_ARG_POSITIONS = frozenset()

            def render(self, args: List[str], context: Dict[str, Any]) -> str:
                raise RuntimeError("boom from the body")

        register_tag_handler("boom_probe_2658", Boom())
        try:
            src = '{% load cache %}{% cache 300 "boom2658" %}{% boom_probe_2658 %}{% endcache %}'
            template = backend.from_string(src)
            with pytest.raises(Exception) as caught:
                str(template.render({}))
            assert "boom from the body" in str(caught.value)
            assert _keys() == [], f"a failed body was stored anyway: {_keys()}"
        finally:
            unregister_tag_handler("boom_probe_2658")

    def test_item_4_now_matches_django_exactly(self, backend: Any, django_engine: Any) -> None:
        """#2658 item 4, resolved by item 1 exactly as the issue predicted.

        The issue reported ``{{ k }}`` rendering EMPTY after a
        ``{% cycle … as k %}`` inside a ``{% cache %}`` body and called it an
        ``as``-binding that never escaped the body's sub-scope::

            django -> '<1>1|<1>1|<1>1|'
            djust  -> '<1>|<1>|<1>|'

        There was no scope bug by the time it was measured — the binding
        escaped fine, and djust rendered ``'<1>1|<1>2|<1>3|'`` because it
        re-rendered the body on iterations 2 and 3 and the ``{% cycle %}``
        advanced. With the body lazy, those iterations are hits, the cycle does
        not advance, and the two engines agree.
        """
        src = (
            "{% load cache %}{% for i in v %}"
            '{% cache 300 f4 %}<{% cycle "1" "2" "3" as k %}>{% endcache %}'
            "{{ k }}|{% endfor %}"
        )
        ctx = {"v": [1, 2, 3]}

        caches["default"].clear()
        expected = django_engine.from_string(src).render(DjangoContext(ctx))
        assert expected == "<1>1|<1>1|<1>1|"

        caches["default"].clear()
        assert str(backend.from_string(src).render(ctx)) == expected
