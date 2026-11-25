"""Differential tests for the multi-span text-region fast path (#4325).

``render_with_diff`` used to skip the parse only when the whole-HTML byte
diff was ONE text span. ``try_multi_text_region_fast_path`` also takes a
diff over several text nodes (two cells of one re-rendered row, a heading
and a footer). The oracle is a second ``RustLiveView`` fed the same state
whose fragment cache and text index are cleared before every render, so it
always runs the full html5ever parse and VDOM diff against a tree it parsed
itself. Both views must produce the same HTML and the same patches, turn
after turn, which also proves the fast path left its VDOM and byte index
consistent for the renders that follow.
"""

from __future__ import annotations

import json
import random
import re

import pytest

from djust._rust import RustLiveView

TEMPLATE = (
    "<div dj-root><h1>{{ title }}</h1>"
    '<table>{% for r in rows %}<tr class="{{ r.cls }}">'
    "<td>{{ r.a }}</td><td>\n  {{ r.b }} units\n</td>"
    "{% if r.flag %}<td><b>on</b></td>{% endif %}</tr>{% endfor %}</table>"
    "<pre>{{ pre }}</pre><textarea>{{ note }}</textarea>"
    "<p>{{ foot }}</p></div>"
)

FAST_PATH_TEXT_REGION = 2.0

# Values chosen to hit the validation edges: entities, multibyte text that
# shares leading bytes, blank text, leading newlines, markup-looking text.
VALUES = [
    "x",
    "longer value",
    "",
    " ",
    "\n",
    "\nlead",
    "a & b",
    "<b>not markup</b>",
    "1 > 0",
    "quote \" '",
    "↑",
    "↓",
    "été",
    "tab\tsep",
    "&amp; literal",
    "cr\r\nlf",
]


def _rows(n: int) -> list[dict]:
    return [{"a": "a%d" % i, "b": "b%d" % i, "cls": "c", "flag": False} for i in range(n)]


def _state(rows: list[dict], **extra: str) -> dict:
    base = {"title": "Title", "rows": rows, "pre": "pre", "note": "note", "foot": "foot"}
    base.update(extra)
    return base


_ID = re.compile(r'(dj-id="|"d":"|"child_d":"|"dj-id":"|"djust_id":")([^"]*)"')


def _canonical(prev_html: str, html: str, patches: str) -> tuple[str, str]:
    """Rename dj-ids by first appearance in the old, then the new HTML.

    The two views share the thread's id counter and the oracle's parses
    consume ids, so freshly inserted elements get different (equally
    valid) ids in each; the shape and every reference must still agree.
    """
    names: dict[str, str] = {}
    for source in (prev_html, html):
        for match in _ID.finditer(source):
            names.setdefault(match.group(2), "id%d" % len(names))

    def rename(text: str) -> str:
        return _ID.sub(
            lambda m: '%s%s"' % (m.group(1), names.get(m.group(2), "?" + m.group(2))), text
        )

    return rename(html), rename(patches)


class _Pair:
    """The fast-path view and its full-parse oracle, driven in lockstep."""

    def __init__(self, state: dict) -> None:
        self.fast = RustLiveView(TEMPLATE, None)
        self.oracle = RustLiveView(TEMPLATE, None)
        self.fast_paths: list[float] = []
        self.patch_counts: list[int] = []
        self.html: dict[int, str] = {}
        for view in (self.fast, self.oracle):
            view.update_state(state)
            self.html[id(view)] = view.render_with_diff()[0]
        # One more full render so the fast view holds a text index.
        self.step({}, [])

    def step(self, updates: dict, keys: list[str]) -> None:
        self.fast.update_state(updates)
        self.fast.set_changed_keys(keys)
        self.oracle.update_state(updates)
        self.oracle.clear_fragment_cache()
        fast_html, fast_patches, _ = self.fast.render_with_diff()
        oracle_html, oracle_patches, _ = self.oracle.render_with_diff()
        fast_raw, oracle_raw = fast_html, oracle_html
        assert self.oracle.get_render_timing()["fast_path"] == 0.0
        fast_html, fast_patches = _canonical(
            self.html[id(self.fast)], fast_html, fast_patches or "[]"
        )
        oracle_html, oracle_patches = _canonical(
            self.html[id(self.oracle)], oracle_html, oracle_patches or "[]"
        )
        self.html[id(self.fast)] = fast_raw
        self.html[id(self.oracle)] = oracle_raw
        assert fast_html == oracle_html, updates
        fast_list = json.loads(fast_patches)
        oracle_list = json.loads(oracle_patches)
        assert fast_list == oracle_list, (updates, fast_list, oracle_list)
        self.fast_paths.append(self.fast.get_render_timing()["fast_path"])
        self.patch_counts.append(len(fast_list))

    def multi_hits(self) -> int:
        return sum(
            1
            for path, count in zip(self.fast_paths, self.patch_counts)
            if path == FAST_PATH_TEXT_REGION and count > 1
        )


def test_two_cells_of_one_row_take_the_fast_path() -> None:
    rows = _rows(6)
    pair = _Pair(_state(rows))
    rows = [dict(r) for r in rows]
    rows[2]["a"] = "A2"
    rows[2]["b"] = "a much longer B2"
    pair.step({"rows": rows}, ["rows"])
    assert pair.fast_paths[-1] == FAST_PATH_TEXT_REGION
    assert pair.patch_counts[-1] == 2


def test_cells_in_distant_rows_and_a_following_render_stay_consistent() -> None:
    rows = _rows(12)
    pair = _Pair(_state(rows))
    for turn in range(4):
        rows = [dict(r) for r in rows]
        rows[turn]["a"] = "first-%d" % turn
        rows[11 - turn]["b"] = "last-" + "x" * turn
        pair.step({"rows": rows}, ["rows"])
        assert pair.fast_paths[-1] == FAST_PATH_TEXT_REGION
    # A structural change after several in-place renders: the oracle's
    # patches only match if the fast view's VDOM stayed exact.
    rows = [dict(r) for r in rows]
    rows[5]["flag"] = True
    pair.step({"rows": rows}, ["rows"])
    assert pair.fast_paths[-1] == 0.0


@pytest.mark.parametrize(
    "a,b",
    [
        ("<b>x</b>", "y"),  # escaped markup: no raw '<' reaches the run
        ("1 > 0", "y"),  # escaped '>'
        ("", "y"),  # blank cell: run leaves the index
        ("x", " "),  # becomes blank
        ("\nlead", "y"),  # leading newline appears
    ],
)
def test_edge_values_match_the_oracle(a: str, b: str) -> None:
    rows = _rows(4)
    pair = _Pair(_state(rows))
    rows = [dict(r) for r in rows]
    rows[1]["a"] = a
    rows[1]["b"] = b
    pair.step({"rows": rows}, ["rows"])
    rows = [dict(r) for r in rows]
    rows[1]["a"] = "back"
    rows[2]["b"] = "again"
    pair.step({"rows": rows}, ["rows"])


def test_more_regions_than_the_cap_take_the_full_parse() -> None:
    rows = _rows(12)
    pair = _Pair(_state(rows))
    rows = [dict(r, a="changed-%d" % i) for i, r in enumerate(rows)]
    pair.step({"rows": rows}, ["rows"])
    assert pair.fast_paths[-1] == 0.0


def test_text_and_attribute_change_together_take_the_full_parse() -> None:
    rows = _rows(4)
    pair = _Pair(_state(rows))
    rows = [dict(r) for r in rows]
    rows[0]["a"] = "new"
    rows[3]["cls"] = "hot"
    pair.step({"rows": rows}, ["rows"])
    assert pair.fast_paths[-1] == 0.0


def test_pre_and_textarea_text_match_the_oracle() -> None:
    pair = _Pair(_state(_rows(2)))
    for pre, note in [("p2", "n2"), ("p3\nmid", "n3\n"), ("p5 &", "n5 <")]:
        pair.step({"pre": pre, "note": note, "title": pre + note}, ["pre", "note", "title"])
        pair.step({"pre": pre + "!", "foot": note + "!"}, ["pre", "foot"])
    assert FAST_PATH_TEXT_REGION in pair.fast_paths or 1.0 in pair.fast_paths


@pytest.mark.parametrize("key", ["pre", "note"])
@pytest.mark.parametrize("keys_extra", [[], ["title"]])
@pytest.mark.parametrize(
    "before,after",
    [("p2", "\np3"), ("\np2", "p3"), ("\np2", "\np3"), ("p2", "\r\np3"), ("p2", "a\rb")],
)
def test_leading_newline_in_pre_and_textarea_matches_the_full_parse(
    key: str, keys_extra: list, before: str, after: str
) -> None:
    # The parser drops a newline that opens <pre>/<textarea> text and turns
    # CR into LF. The fragment path (alone, or with the title changing too)
    # and the text-region paths used to send the raw "\n..." as SetText.
    pair = _Pair(_state(_rows(2), **{key: before}))
    updates = {key: after}
    for extra in keys_extra:
        updates[extra] = "changed"
    pair.step(updates, [key] + keys_extra)
    pair.step({key: after + "!"}, [key])


@pytest.mark.parametrize("seed", range(40))
def test_random_turns_match_the_full_parse(seed: int) -> None:
    rng = random.Random(seed)
    rows = _rows(10)
    pair = _Pair(_state(rows))
    for _ in range(25):
        rows = [dict(r) for r in rows]
        updates: dict = {}
        keys: list[str] = []
        for _ in range(rng.randint(1, 3)):
            choice = rng.random()
            if choice < 0.6:
                row = rows[rng.randrange(len(rows))]
                row[rng.choice("ab")] = rng.choice(VALUES)
                updates["rows"] = rows
                keys.append("rows")
            elif choice < 0.7:
                rows[rng.randrange(len(rows))]["flag"] ^= True
                updates["rows"] = rows
                keys.append("rows")
            elif choice < 0.75 and len(rows) > 2:
                rows.pop(rng.randrange(len(rows)))
                updates["rows"] = rows
                keys.append("rows")
            else:
                key = rng.choice(["title", "foot", "pre", "note"])
                updates[key] = rng.choice(VALUES)
                keys.append(key)
        pair.step(updates, sorted(set(keys)))
    # The sequence must actually exercise the new path, not only fall back.
    assert pair.multi_hits() > 0
