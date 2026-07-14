"""Integration tests for the per-item loop render cache (#1967).

Exercises the cache end-to-end through the PyO3 ``RustLiveView.render_with_diff``
path — the real production render path — proving:

* the flag defaults OFF and is byte-identical to the pre-#1967 path,
* with the cache ON the rendered HTML is byte-identical to OFF across initial
  render, reorder, content-change, append, and remove,
* a pure reorder is all cache HITS (the O(changed) win), persisted across
  ``render_with_diff`` calls,
* a content-change of one item costs exactly one miss,
* position-dependent loop bodies ({% if %}/{% cycle %}/forloop) are NOT cached
  yet still render correct positions (the guard is load-bearing),
* the Python config flag ``loop_render_cache_enabled`` defaults False.

Native Rust unit + correctness tests (cached==uncached battery, the two
gate-offs) live in
``crates/djust_templates/tests/test_loop_render_cache_1967.rs``.
"""

from __future__ import annotations

import pytest

from djust._rust import RustLiveView

PLAIN_SRC = "<ul>{% for x in xs %}<li>{{ x.name }}</li>{% endfor %}</ul>"
IF_SRC = "<ul>{% for x in xs %}<li>{% if x.name %}{{ x.name }}{% endif %}</li>{% endfor %}</ul>"
CYCLE_SRC = (
    "<ul>{% for x in xs %}<li class=\"{% cycle 'odd' 'even' %}\">{{ x.name }}</li>{% endfor %}</ul>"
)


def _items(rows):
    return [{"id": i, "name": n} for (i, n) in rows]


INITIAL = _items([("1", "alpha"), ("2", "bravo"), ("3", "charlie")])
REORDERED = _items([("3", "charlie"), ("1", "alpha"), ("2", "bravo")])
CHANGED = _items([("3", "charlie"), ("1", "ALPHA-CHANGED"), ("2", "bravo")])
APPENDED = _items([("3", "charlie"), ("1", "ALPHA-CHANGED"), ("2", "bravo"), ("4", "delta")])
REMOVED = _items([("3", "charlie"), ("2", "bravo"), ("4", "delta")])

SEQUENCE = [INITIAL, REORDERED, CHANGED, APPENDED, REMOVED]


def _render_sequence(src, enabled):
    """Run the standard op sequence; return list of (html, hits, misses)."""
    lv = RustLiveView(src)
    lv.set_loop_render_cache_enabled(enabled)
    out = []
    for i, state in enumerate(SEQUENCE):
        if i > 0:
            lv.set_changed_keys(["xs"])
        lv.update_state({"xs": state})
        html, _patches, _ver = lv.render_with_diff()
        out.append((html, lv.loop_render_cache_hits(), lv.loop_render_cache_misses()))
    return out


class TestLoopRenderCacheDefaults:
    def test_flag_defaults_off(self):
        lv = RustLiveView(PLAIN_SRC)
        assert lv.loop_render_cache_enabled() is False

    def test_config_default_is_true(self):
        """Default flipped ON in #2062 after soaking flag-OFF since v1.1.0rc5
        (byte-identity proven; #2067 cross-loop keyspace fix landed first).
        The flag remains an explicit opt-OUT."""
        from djust.config import get_config

        assert get_config().get("loop_render_cache_enabled", "MISSING") is True

    def test_enable_disable_round_trip(self):
        lv = RustLiveView(PLAIN_SRC)
        lv.set_loop_render_cache_enabled(True)
        assert lv.loop_render_cache_enabled() is True
        lv.set_loop_render_cache_enabled(False)
        assert lv.loop_render_cache_enabled() is False


class TestOutputIdentity:
    """Cache-ENABLED output must be byte-identical to cache-DISABLED."""

    @pytest.mark.parametrize("src", [PLAIN_SRC, IF_SRC, CYCLE_SRC])
    def test_enabled_identical_to_disabled(self, src):
        on = _render_sequence(src, enabled=True)
        off = _render_sequence(src, enabled=False)
        for step, (s_on, s_off) in enumerate(zip(on, off)):
            assert s_on[0] == s_off[0], f"step {step}: cache-enabled HTML diverged from disabled"


class TestCacheBehavior:
    """Hit/miss accounting proves the O(changed) render win."""

    def test_reorder_is_all_hits(self):
        on = _render_sequence(PLAIN_SRC, enabled=True)
        # step 0 = initial: 3 misses, 0 hits
        assert on[0][1] == 0 and on[0][2] == 3
        # step 1 = reorder: 3 hits, 0 misses (no item re-rendered)
        assert on[1][1] == 3, "a pure reorder must reuse every cached fragment"
        assert on[1][2] == 0, "a pure reorder must not re-render any item"

    def test_content_change_is_one_miss(self):
        on = _render_sequence(PLAIN_SRC, enabled=True)
        # step 2 = content-change of one item: 1 miss, 2 hits
        assert on[2][2] == 1, "only the changed item re-renders"
        assert on[2][1] == 2, "the two unchanged items are reused"

    def test_append_misses_only_new_item(self):
        on = _render_sequence(PLAIN_SRC, enabled=True)
        # step 3 = append delta to the 3 already-cached items: 1 miss, 3 hits
        assert on[3][2] == 1, "only the appended item misses"
        assert on[3][1] == 3

    def test_position_dependent_body_is_not_cached(self):
        # The {% if %} body is position-dependent (dj-if marker carries the
        # loop index) → caching disabled → 0 hits AND 0 misses every render.
        on = _render_sequence(IF_SRC, enabled=True)
        for step, (_html, hits, misses) in enumerate(on):
            assert hits == 0, f"step {step}: position-dependent body must not hit"
            assert misses == 0, f"step {step}: position-dependent body must not cache"

    def test_disabled_cache_is_inert(self):
        off = _render_sequence(PLAIN_SRC, enabled=False)
        for _html, hits, misses in off:
            assert hits == 0 and misses == 0


# A loop body that reads an OUTER-context var (#1967 review 🔴). Outer context is
# constant within a render but not across renders, and the cache is persistent
# across renders — so without the dep-subset gate a reorder after an outer-var
# change serves stale fragments. Such bodies must be NON-cacheable.
PREFIX_SRC = "<ul>{% for x in xs %}<li>{{ prefix }}-{{ x.name }}</li>{% endfor %}</ul>"


class TestOuterContextNonCacheable:
    """A body reading outer context is non-cacheable; cache ON must equal OFF
    even when the outer var changes across renders + the list reorders."""

    def _render(self, src, enabled, prefix_seq, item_seq):
        lv = RustLiveView(src)
        lv.set_loop_render_cache_enabled(enabled)
        out = []
        for i, (prefix, items) in enumerate(zip(prefix_seq, item_seq)):
            if i > 0:
                lv.set_changed_keys(["xs", "prefix"])
            lv.update_state({"xs": items, "prefix": prefix})
            html, _patches, _ver = lv.render_with_diff()
            out.append((html, lv.loop_render_cache_hits(), lv.loop_render_cache_misses()))
        return out

    def test_outer_context_change_enabled_equals_disabled(self):
        # prefix flips A -> B while the list reorders (same items).
        prefix_seq = ["A", "B"]
        item_seq = [INITIAL, REORDERED]
        on = self._render(PREFIX_SRC, True, prefix_seq, item_seq)
        off = self._render(PREFIX_SRC, False, prefix_seq, item_seq)
        for step, (s_on, s_off) in enumerate(zip(on, off)):
            assert s_on[0] == s_off[0], (
                f"step {step}: outer-context body served STALE cached fragment "
                f"(cache served old prefix). on={s_on[0]!r} off={s_off[0]!r}"
            )
        # Render-2 must reflect the NEW prefix B for every item, never stale A.
        html2 = on[1][0]
        assert "B-" in html2 and "A-" not in html2, (
            f"render-2 must use new prefix B for all items; got {html2!r}"
        )

    def test_outer_context_body_never_cached(self):
        # The outer-context body must never hit/miss the cache (it's disabled
        # for that body) — even on the reorder where an item-only body is all
        # hits.
        prefix_seq = ["A", "A"]
        item_seq = [INITIAL, REORDERED]
        on = self._render(PREFIX_SRC, True, prefix_seq, item_seq)
        for step, (_html, hits, misses) in enumerate(on):
            assert hits == 0, f"step {step}: outer-context body must not hit the cache"
            assert misses == 0, f"step {step}: outer-context body must not touch the cache"


# ---------------------------------------------------------------------------
# #1970 — parsed-subtree cache: end-to-end byte-identity (cache ON == OFF) +
# the parse-count probe + the gate-off. The parse cache reuses the SAME
# loop_render_cache_enabled flag and the SAME content-hash key as the render
# cache; on a reorder of unchanged-content keyed items it skips html5ever-parse
# for the unchanged items too (only changed/new items re-parse). Output MUST
# stay byte-identical to the cache-OFF path across every template/op.
# ---------------------------------------------------------------------------

KEYED_SRC = (
    '<ul>{% for x in xs %}<li dj-key="{{ x.id }}"><span>{{ x.name }}</span></li>{% endfor %}</ul>'
)
DIV_KEYED_SRC = '<div>{% for x in xs %}<div dj-key="{{ x.id }}"><span>{{ x.name }}</span></div>{% endfor %}</div>'
TABLE_KEYED_SRC = (
    "<table><tbody>{% for x in xs %}"
    '<tr dj-key="{{ x.id }}"><td>{{ x.name }}</td></tr>{% endfor %}</tbody></table>'
)
SELECT_KEYED_SRC = (
    "<select>{% for x in xs %}"
    '<option dj-key="{{ x.id }}" value="{{ x.id }}">{{ x.name }}</option>{% endfor %}</select>'
)
MULTIROOT_SRC = (
    "<div>{% for x in xs %}<dt>{{ x.id }}</dt>"
    '<dd dj-key="{{ x.id }}">{{ x.name }}</dd>{% endfor %}</div>'
)

# A sequence exercising initial / reorder / content-change / append / remove,
# all by dj-key so the keyed reconcile + parse-cache splice are the real path.
_K_INITIAL = _items([("1", "alpha"), ("2", "bravo"), ("3", "charlie"), ("4", "delta")])
_K_REORDER = _items([("4", "delta"), ("1", "alpha"), ("3", "charlie"), ("2", "bravo")])
_K_CHANGE = _items([("4", "delta"), ("1", "ALPHA2"), ("3", "charlie"), ("2", "bravo")])
_K_APPEND = _items(
    [("4", "delta"), ("1", "ALPHA2"), ("3", "charlie"), ("2", "bravo"), ("5", "echo")]
)
_K_REMOVE = _items([("3", "charlie"), ("1", "ALPHA2"), ("5", "echo")])
_K_REORDER2 = _items([("5", "echo"), ("3", "charlie"), ("1", "ALPHA2")])
KEYED_SEQUENCE = [_K_INITIAL, _K_REORDER, _K_CHANGE, _K_APPEND, _K_REMOVE, _K_REORDER2]


def _render_keyed_sequence(src, enabled, states=KEYED_SEQUENCE, binary=False):
    """Run a dj-key op sequence; return list of (html, patches, version)."""
    lv = RustLiveView(src)
    lv.set_loop_render_cache_enabled(enabled)
    out = []
    for i, state in enumerate(states):
        if i > 0:
            lv.set_changed_keys(["xs"])
        lv.update_state({"xs": state})
        if binary:
            html, patches, ver = lv.render_binary_diff()
            out.append((html, bytes(patches) if patches is not None else b"", ver))
        else:
            html, patches, ver = lv.render_with_diff()
            out.append((html, patches or "[]", ver))
    return out


class TestParseCacheByteIdentity1970:
    """cache-ENABLED render_with_diff/render_binary_diff output (html + patches +
    version) MUST be byte-identical to cache-DISABLED across every template/op —
    the load-bearing correctness requirement of a hot-path parse cache."""

    @pytest.mark.parametrize(
        "src",
        [
            KEYED_SRC,
            DIV_KEYED_SRC,
            TABLE_KEYED_SRC,  # foster-UNSAFE container → falls back, must still match
            SELECT_KEYED_SRC,  # foster-UNSAFE container → falls back, must still match
            MULTIROOT_SRC,  # 2 roots/item → falls back, must still match
            PLAIN_SRC,  # unkeyed positional
        ],
    )
    @pytest.mark.parametrize("binary", [False, True])
    def test_enabled_identical_to_disabled(self, src, binary):
        on = _render_keyed_sequence(src, True, binary=binary)
        off = _render_keyed_sequence(src, False, binary=binary)
        for step, (s_on, s_off) in enumerate(zip(on, off)):
            assert s_on[0] == s_off[0], f"step {step}: HTML diverged (src={src!r})"
            assert s_on[1] == s_off[1], f"step {step}: patches diverged (src={src!r})"
            assert s_on[2] == s_off[2], f"step {step}: version diverged (src={src!r})"

    def test_dj_key_reorder_round_trip_preserves_ids(self):
        """THE key correctness case: a dj-key reorder of unchanged items must
        produce the SAME post-diff dj-ids/dj-keys as the cache-OFF path."""
        on = _render_keyed_sequence(KEYED_SRC, True)
        off = _render_keyed_sequence(KEYED_SRC, False)
        # Step 1 is the pure reorder.
        assert on[1][0] == off[1][0], "reorder HTML (incl dj-ids/dj-keys) must match cache-off"
        assert on[1][1] == off[1][1], "reorder patches must match cache-off exactly"


class TestParseCountProbe1970:
    """The acceptance criterion: a reorder of N unchanged item-only-body keyed
    items skips parse for the unchanged items (~0 item re-parses); a
    content-change of K items re-parses ~K."""

    def _run(self, states):
        lv = RustLiveView(KEYED_SRC)
        lv.set_loop_render_cache_enabled(True)
        probe = []
        for i, st in enumerate(states):
            if i > 0:
                lv.set_changed_keys(["xs"])
            lv.update_state({"xs": st})
            lv.render_with_diff()
            probe.append((lv.loop_parse_cache_hits(), lv.loop_parse_cache_misses()))
        return probe

    def test_pure_reorder_is_all_parse_hits(self):
        # initial (5 misses, populate) -> reorder (5 hits, 0 misses).
        five = _items([("1", "a"), ("2", "b"), ("3", "c"), ("4", "d"), ("5", "e")])
        reordered = _items([("5", "e"), ("1", "a"), ("3", "c"), ("2", "b"), ("4", "d")])
        probe = self._run([five, reordered])
        assert probe[0] == (0, 5), f"initial: all parse misses (populate); got {probe[0]}"
        assert probe[1] == (5, 0), (
            f"reorder of 5 unchanged items: ALL parse hits, ZERO re-parses; got {probe[1]}"
        )

    def test_append_reparses_only_the_new_item(self):
        four = _items([("1", "a"), ("2", "b"), ("3", "c"), ("4", "d")])
        reorder = _items([("4", "d"), ("1", "a"), ("3", "c"), ("2", "b")])
        append = _items([("4", "d"), ("1", "a"), ("3", "c"), ("2", "b"), ("5", "NEW")])
        probe = self._run([four, reorder, append])
        # The reorder is all hits; the append re-parses only the new item.
        assert probe[1] == (4, 0), f"reorder: 4 hits 0 misses; got {probe[1]}"
        assert probe[2][1] >= 1, f"append: at least the new item re-parses; got {probe[2]}"
        assert probe[2][0] >= 4, f"append: the 4 unchanged items hit; got {probe[2]}"

    def test_gate_off_no_parse_activity_when_disabled(self):
        """GATE-OFF (#1468): with the flag OFF the parse cache never activates —
        proving the flag gates the whole feature (and the probe is honest)."""
        lv = RustLiveView(KEYED_SRC)
        lv.set_loop_render_cache_enabled(False)
        five = _items([("1", "a"), ("2", "b"), ("3", "c"), ("4", "d"), ("5", "e")])
        reordered = _items([("5", "e"), ("1", "a"), ("3", "c"), ("2", "b"), ("4", "d")])
        lv.update_state({"xs": five})
        lv.render_with_diff()
        lv.set_changed_keys(["xs"])
        lv.update_state({"xs": reordered})
        lv.render_with_diff()
        assert lv.loop_parse_cache_hits() == 0
        assert lv.loop_parse_cache_misses() == 0


class TestParseCacheSentinelCollision1970:
    """SECURITY regression (#1970, adversarial-review 🔴): a loop item rendering
    a LITERAL `<dj-pc ...>` element via `|safe`/`mark_safe`, alongside a sibling
    that emitted a real (nonce-tagged) placeholder, must NOT corrupt output — the
    per-render nonce makes the sentinel unforgeable, and the literal user
    `<dj-pc>` is preserved. Byte-identity cache ON == OFF."""

    SAFE_SRC = (
        '<ul>{% for x in xs %}<li dj-key="{{ x.id }}">{{ x.name|safe }}</li>{% endfor %}</ul>'
    )

    def _run(self, enabled, states, binary=False):
        lv = RustLiveView(self.SAFE_SRC)
        lv.set_loop_render_cache_enabled(enabled)
        out = []
        for i, st in enumerate(states):
            if i > 0:
                lv.set_changed_keys(["xs"])
            lv.update_state({"xs": st})
            if binary:
                html, patches, ver = lv.render_binary_diff()
                out.append((html, bytes(patches) if patches is not None else b"", ver))
            else:
                html, patches, ver = lv.render_with_diff()
                out.append((html, patches or "[]", ver))
        return out

    @pytest.mark.parametrize("binary", [False, True])
    def test_literal_dj_pc_via_safe_is_byte_identical(self, binary):
        # render-1: two plain items (populates the parse cache). render-2: item 2
        # changes to a LITERAL <dj-pc h="ffff"></dj-pc>X while item 1 (unchanged)
        # emits a real placeholder.
        states = [
            [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}],
            [{"id": 1, "name": "a"}, {"id": 2, "name": '<dj-pc h="ffff"></dj-pc>X'}],
        ]
        on = self._run(True, states, binary=binary)
        off = self._run(False, states, binary=binary)
        for step, (s_on, s_off) in enumerate(zip(on, off)):
            assert s_on[0] == s_off[0], f"step {step}: HTML diverged (sentinel collision)"
            assert s_on[1] == s_off[1], f"step {step}: patches diverged (sentinel collision)"
            assert s_on[2] == s_off[2], f"step {step}: version diverged"
        # The user's literal <dj-pc> must survive in the rendered HTML.
        assert "ffff" in on[1][0], "user's literal <dj-pc h=ffff> must be preserved"

    def test_crafted_hash_cannot_hijack_a_cached_subtree(self):
        # An attacker who knows a content-hash crafts <dj-pc h="<hash>"> in
        # |safe content to try to splice a DIFFERENT cached item's subtree into
        # that position. The nonce defeats it: the literal tag never matches the
        # nonce-tagged sentinel, so the crafted content renders verbatim.
        states = [
            [{"id": 1, "name": "secret-row"}, {"id": 2, "name": "b"}],
            [
                {"id": 1, "name": "secret-row"},
                {"id": 2, "name": '<dj-pc h="0"></dj-pc>'},  # try to hijack hash 0
            ],
        ]
        on = self._run(True, states)
        off = self._run(False, states)
        assert on[1][0] == off[1][0], "crafted-hash content must render verbatim (no hijack)"
