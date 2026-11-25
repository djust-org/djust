"""The browser turn benchmark stays runnable (#4314).

``scripts/bench_client_turns.py`` needs Chromium and is not run in CI. What can
rot without a browser is checked here: the hook it inserts into the shipped
client, the triggers it adds to the server bench's pages, and the committed
baseline (every cell present, sample sizes recorded). Nothing about durations
is asserted (#2156).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
SCRIPT = os.path.join(ROOT, "scripts", "bench_client_turns.py")
BASELINE = os.path.join(ROOT, "docs", "performance", "client-turns-baseline.json")
DOC = os.path.join(ROOT, "docs", "performance", "turn-transports-baseline.md")


def _load_script():
    """Import the script; the paths it adds to ``sys.path`` are put back."""
    saved = list(sys.path)
    saved_modules = set(sys.modules)
    try:
        spec = importlib.util.spec_from_file_location("bench_client_turns_4314", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = saved
        for name in set(sys.modules) - saved_modules:
            if name == "bench_turn_transports":
                del sys.modules[name]
    return module


def test_the_response_handler_hook_still_applies():
    module = _load_script()
    wrapped = module._client_core()
    assert "async function __djbHandleServerResponse(" in wrapped
    assert wrapped.count("async function handleServerResponse(") == 1


def test_every_trigger_is_in_the_server_bench_pages():
    module = _load_script()
    source = open(
        os.path.join(ROOT, "scripts", "bench_turn_transports.py"), encoding="utf-8"
    ).read()
    assert set(module.TRIGGERS) == set(module.SHAPES)
    for shape, (old, _new) in module.TRIGGERS.items():
        assert old in source, shape


def test_the_committed_baseline_has_every_cell():
    module = _load_script()
    with open(BASELINE, encoding="utf-8") as fh:
        result = json.load(fh)
    expected = {
        (shape, cell)
        for shape in module.SHAPES
        for cell in module.CELLS
        if not (cell != "ws" and (shape, "http") in module.server_bench.NOT_APPLICABLE)
    }
    assert {(c["shape"], c["cell"]) for c in result["cells"]} == expected
    assert result["turns"] > 0 and result["machine"]["browser"]
    for cell in result["cells"]:
        assert cell["turns"] == result["turns"]
        assert cell["answer"] == ("page" if cell["cell"] == "http-nosave" else "patches")
    doc = open(DOC, encoding="utf-8").read()
    assert "make benchmark-client-turns" in doc
    assert "client-turns-baseline.json" in doc
