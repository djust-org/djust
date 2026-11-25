"""The transport turn benchmark keeps running (#3657).

``scripts/bench_turn_transports.py`` is the baseline later performance work
(#3998, #3999, #4001) measures against, so it must not rot between runs. This
runs it end to end at a tiny size (seconds) and checks the shape of what it
reports. It asserts nothing about durations (#2156).
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
SCRIPT = os.path.join(ROOT, "scripts", "bench_turn_transports.py")


def _load_script():
    spec = importlib.util.spec_from_file_location("bench_turn_transports_3657", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_benchmark_script_runs_every_transport(tmp_path):
    out = tmp_path / "result.json"
    env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
    proc = subprocess.run(
        [
            sys.executable,
            SCRIPT,
            "--quick",
            "--shapes",
            "form,push",
            "--json",
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
        cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "simulated RTT 0 ms" in proc.stdout
    assert "simulated RTT 20 ms" in proc.stdout
    assert "(no push/http row:" in proc.stdout

    result = json.loads(out.read_text())
    cells = result["cells"]
    # RTT 0 runs both save modes on every applicable transport; RTT 20 only "save".
    expected = {"form/%s/%s/0" % (t, m) for t in ("ws", "sse", "http") for m in ("save", "nosave")}
    expected |= {"push/%s/%s/0" % (t, m) for t in ("ws", "sse") for m in ("save", "nosave")}
    expected |= {"form/%s/save/20" % t for t in ("ws", "sse", "http")}
    expected |= {"push/%s/save/20" % t for t in ("ws", "sse")}
    assert set(cells) == expected
    for key, cell in cells.items():
        assert cell["turns"] >= 1, key
        assert cell["p50_ms"] > 0 and cell["p95_ms"] >= cell["p50_ms"], key
        assert cell["bytes_down"] > 0, key
        assert sum(cell["answers"].values()) == cell["turns"], key
        if cell["rtt_ms"]:
            # The simulated link holds each direction for RTT/2.
            assert cell["p50_ms"] >= cell["rtt_ms"], key
    # A socket or stream answers with patches; the page-POST fallback without
    # a save mounts again and answers with the whole page.
    assert set(cells["form/ws/save/0"]["answers"]) == {"patch"}
    assert set(cells["form/http/nosave/0"]["answers"]) == {"html"}
    assert set(result["memory_kb"]) == {
        "form/%s/%s" % (t, m) for t in ("ws", "sse", "http") for m in ("save", "nosave")
    } | {"push/%s/%s" % (t, m) for t in ("ws", "sse") for m in ("save", "nosave")}
    assert result["machine"]["python"] and result["settings"]["rows"] == 20

    # --compare against itself: every cell found, every ratio 1.00.
    table = _load_script().format_compare(result, result)
    rows = [line for line in table.splitlines() if line.startswith(("form/", "push/"))]
    assert len(rows) == len(cells)
    assert all(line.split()[3] == "1.00" and line.split()[6] == "1.00" for line in rows)


def test_baseline_file_matches_the_script():
    """The committed baseline is a run of this script: a later ``--compare``
    against it finds every cell the full run produces."""
    module = _load_script()
    path = os.path.join(ROOT, "docs", "performance", "turn-transports-baseline.json")
    with open(path) as handle:
        baseline = json.load(handle)
    cells = baseline["cells"]
    for shape in module.SHAPES:
        for transport in module.TRANSPORTS:
            if (shape, transport) in module.NOT_APPLICABLE:
                continue
            for rtt in (0, 20, 100, 250):
                assert "%s/%s/save/%g" % (shape, transport, rtt) in cells
            assert "%s/%s/nosave/0" % (shape, transport) in cells
    assert baseline["settings"]["rows"] == 1000
