"""The URL-scheme sweep's shortcuts change nothing it measures (#3665).

``python/tests/test_url_scheme_sweep_3665.py`` drives every parameter of every tag, about
230,000 cells, through both engines. It outgrew its 900 s subprocess timeout in CI
(``python-tests (py3.12, shard 2/4)`` on main, 02:56 to 03:12): PR #3765 added about 23% more
cells and the run had been borderline before it. Where the time went, measured on the tree
before this change (``--corpus test``, 228,522 cells, 12 cores under load 10):

* 63% was the Rust engine, and nearly all of THAT was Python serialising the context: the
  engine walks every dict of the context it is given on every render, and each call was given
  the whole context (``data_table``: 127 keys per dict, 3.8 ms a render against 0.66 ms once
  the context holds only the names the call's template uses);
* 27% was reading outputs (every attribute of every element, entity-decoded), twice per cell
  (``findings`` and ``carries`` each parsed the markup), for outputs that are mostly the same
  page repeated for every payload a parameter does not reach;
* ``carries`` was measured for every cell, and only a tag with a native handler (the engine
  disagreement check) and the ``https`` value (the ``reached`` set) read it.

``run_sweep`` now gives each call only the context names its template uses, reads each distinct
output once per tag, and measures ``carries`` where something reads it. Those are shortcuts, and
a shortcut that drops a measurement would make the sweep pass for the wrong reason, so this
module asserts they are not that:

* ``TestTheContextAGivenCallGets`` and ``TestWhereCarriesIsMeasured`` pin the two small
  functions on their edges (a word, not a substring; a name the template does not use);
* ``TestTheShortcutsChangeNothing`` runs a sample of tags with and without
  ``--exhaustive`` (the whole context, every output re-read, ``carries`` everywhere) and
  compares everything the sweep reports, cell by cell. The same comparison was made once, by
  hand, over the whole ``--corpus test`` sweep (228,522 tag cells and 142,863 component cells):
  the dump of every cell was byte-identical before and after.

The budget that keeps the sweep from growing back into the timeout is in
``test_url_scheme_sweep_3665.py::TestTheSweep::test_the_sweep_stays_inside_its_cell_budget``.
It counts cells, never seconds (``docs/patterns/wall-clock-assertion.md``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.lib.template_library_parity import url_sweep
from tests.git_env import isolated_git_env

pytestmark = pytest.mark.pr_tier

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "template-library-url-sweep.py"

#: Tags chosen for what they read, not for being fast: a native tag with a plain URL parameter,
#: a ``**attrs`` tag, a block tag whose two engines disagree on purpose (the only cell that
#: reads ``carries`` to find a difference), and two that take a ``form`` (a bound form that is
#: not in the context the shortcut builds from the template's names).
SAMPLE = (
    "djust_components:avatar",
    "djust_components:nav_item",
    "djust_components:dj_form",
    "theme_components:theme_input",
    "theme_pages:theme_login_page",
)


class TestTheContextAGivenCallGets:
    NAMES = ("p_scalar", "p_list_dict", "n", "none", "lst_str", "bound_form")

    def test_a_name_counts_as_a_word_not_as_a_substring(self):
        got = url_sweep.referenced_names(self.NAMES, "{% t a=p_scalar b=none %}")
        assert got == ("p_scalar", "none")  # ``n`` is inside ``none`` and is not used

    def test_a_name_followed_by_a_lookup_or_a_filter_is_used(self):
        src = "{% t a=lst_str.0 b=n|add:1 %}"
        assert url_sweep.referenced_names(self.NAMES, src) == ("n", "lst_str")

    def test_a_name_the_template_does_not_mention_is_left_out(self):
        assert url_sweep.referenced_names(self.NAMES, "{% t a=1 %}") == ()


class TestWhereCarriesIsMeasured:
    @pytest.mark.parametrize(
        ("native", "label", "measured"),
        [
            (True, "js", True),  # the engine-disagreement check reads it
            (True, "https", True),
            (False, "https", True),  # the ``reached`` set reads it
            (False, "js", False),  # nothing reads it
            (False, "relative", False),
        ],
    )
    def test_only_a_native_tag_or_the_https_value_reads_it(self, native, label, measured):
        assert url_sweep.needs_carries(native, label) is measured

    def test_an_unmeasured_cell_says_so_rather_than_saying_no(self):
        """``None`` is "not measured", ``False`` is "measured, did not survive"."""
        cell = url_sweep.Cell("t", "p", "scalar", "x", "js")
        assert cell.python_carries is None and cell.rust_carries is None


def _run(out: Path, *extra: str) -> dict:
    env = isolated_git_env()
    env.pop("DJANGO_SETTINGS_MODULE", None)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT / "python"), env.get("PYTHONPATH")]))
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--corpus", "test", "--json", str(out), *extra],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode in (0, 1), proc.stderr[-2000:]
    return json.loads(out.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def both(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict, dict]:
    out = tmp_path_factory.mktemp("urlsweepcapacity")
    only = ",".join(SAMPLE)
    return _run(out / "fast.json", "--only", only), _run(
        out / "full.json", "--only", only, "--exhaustive"
    )


class TestTheShortcutsChangeNothing:
    def test_the_sample_was_driven_and_rendered(self, both):
        """Presence before equality: two empty sweeps are equal."""
        fast, _ = both
        assert set(fast["driven"]) == set(SAMPLE), sorted(set(SAMPLE) ^ set(fast["driven"]))
        assert fast["cells"] > 5000 and fast["rendered_both"] > fast["cells"] // 2
        assert fast["reached"], "no URL reached an attribute: the sweep read the wrong input"
        assert fast["disagreements"], "nav_item's deliberate engine difference was not seen"

    def test_the_exhaustive_run_really_takes_no_shortcut(self, both):
        """Else the comparison below compares a run with itself and cannot fail (#1859)."""
        fast, full = both
        assert full["carries_measured"] == full["cells"]
        assert fast["carries_measured"] < fast["cells"] // 2
        assert fast["context_names"] < full["context_names"] // 2

    def test_every_cell_is_measured_the_same(self, both):
        fast, full = both
        assert fast["cells"] == full["cells"]
        assert fast["digest"] == full["digest"]

    def test_every_report_is_the_same(self, both):
        fast, full = both
        for key in (
            "holes",
            "disagreements",
            "reached",
            "rendered_both",
            "no_params",
            "unrendered",
        ):
            assert fast[key] == full[key], key
