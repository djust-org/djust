"""``scripts/gate_off.py``: the shared gate-off harness (#3714).

The harness is tested the way CLAUDE.md says a gate-off harness must behave: against a
tiny REAL fixture package (a module, a conftest fixture, a test file) in ``tmp_path``,
run through real ``python -m pytest`` subprocesses. Nothing here asserts on elapsed
time: the hang and the runaway are bounded by a deadline / RSS ceiling handed to the
harness, and the tests assert on the *reported status*.

``scripts/gate-off-selfcheck.json`` turns the harness on itself (each rule dropped in
turn must turn a test here red); ``test_every_selfcheck_mutation_text_still_applies``
pins that the list cannot rot.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "gate_off.py"
SELFCHECK = ROOT / "scripts" / "gate-off-selfcheck.json"


def _load():
    spec = importlib.util.spec_from_file_location("gate_off_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["gate_off_under_test"] = module
    spec.loader.exec_module(module)
    return module


go = _load()

CALC = '''\
"""Fixture module for the gate-off harness tests."""

import time

VALUE = 7


def add(a, b):
    return a + b


def untested(x):
    return x * 2


def make():
    return {"v": VALUE}


def spin(n):
    i = 0
    while i < n:
        i += 1
    return i


def grow(n):
    chunks = []
    for _ in range(n):
        chunks.append(b"x" * 1_000_000)
        time.sleep(0.002)
    return len(chunks)
'''

CONFTEST = """\
import pytest


@pytest.fixture
def base():
    import calc

    return calc.make()
"""

TESTS = """\
import calc


def test_add():
    assert calc.add(2, 3) == 5


def test_make(base):
    assert base == {"v": 7}


def test_spin():
    assert calc.spin(3) == 3


def test_grow():
    assert calc.grow(3) == 3
"""

# ``-p no:django``: the parent's pytest-django exports DJANGO_SETTINGS_MODULE into the children
COMMAND = [
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "-p",
    "no:cacheprovider",
    "-p",
    "no:django",
    "tests/test_calc.py",
]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path) -> Path:
    root = tmp_path / "fx"
    (root / "tests").mkdir(parents=True)
    (root / "calc.py").write_text(CALC)
    (root / "tests" / "conftest.py").write_text(CONFTEST)
    (root / "tests" / "test_calc.py").write_text(TESTS)
    return root


def _m(label, old, new, **kw):
    return go.Mutation(label=label, file="calc.py", old=old, new=new, **kw)


ADD = "def add(a, b):\n    return a + b\n"


def _mutations():
    return [
        _m("caught: add subtracts", "return a + b", "return a - b"),
        _m("survived: untested() is never asserted", "return x * 2", "return x * 3"),
        _m("noop: replacement equals the original", "return x * 2", "return x * 2"),
        _m("not found: the text is not in the file", "this text is nowhere", "x"),
        _m("ambiguous: the text occurs many times", "return", "return None  #"),
        _m("import-break: raises at import", ADD, ADD + '\n\nraise RuntimeError("import-time")\n'),
        _m("syntax: does not compile", "return a + b", "return a +"),
        _m(
            "setup error: the fixture's factory raises",
            'def make():\n    return {"v": VALUE}',
            'def make():\n    raise RuntimeError("fixture broke")',
        ),
        _m("hang: spin never advances", "        i += 1\n", "        i += 0\n", deadline=3),
        _m(
            "runaway: grow never stops allocating",
            "for _ in range(n):",
            "for _ in iter(int, 1):",
            rss_mb=500,
            deadline=120,
        ),
        _m(
            "smoke: the build check fails on the mutant",
            "return a + b",
            "return a - b",
            smoke=[sys.executable, "-c", "import calc; assert calc.add(1, 1) == 2"],
        ),
    ]


@pytest.fixture(scope="module")
def shared_run(tmp_path_factory):
    """One real run of every mutation shape; the tests below read its results."""
    root = _fixture(tmp_path_factory.mktemp("gateoff"))
    before = _sha(root / "calc.py")
    cache = root / "__pycache__"
    cache.mkdir()
    (cache / "calc.cpython-0.pyc").write_bytes(b"stale")
    (cache / "calc.cpython-0.opt-1.pyc").write_bytes(b"stale")
    (cache / "other.cpython-0.pyc").write_bytes(b"keep")
    results, baseline = go.run_mutations(
        _mutations(), root=root, command=COMMAND, deadline=120, rss_mb=2048
    )
    return {
        "root": root,
        "before": before,
        "results": {r.label.split(":")[0]: r for r in results},
        "ordered": results,
        "baseline": baseline,
    }


# --------------------------------------------------------------------------- the six shapes


def test_baseline_is_green_and_every_mutation_reports(shared_run):
    assert shared_run["baseline"] is None
    assert [r.label for r in shared_run["ordered"]] == [m.label for m in _mutations()]


def test_a_mutation_the_tests_notice_is_caught_with_counts(shared_run):
    r = shared_run["results"]["caught"]
    assert (r.status, r.kind) == (go.CAUGHT, "failed")
    assert r.failed == 1


def test_a_mutation_the_tests_miss_survives(shared_run):
    r = shared_run["results"]["survived"]
    assert (r.status, r.kind) == (go.SURVIVED, "survived")


def test_a_no_op_mutation_is_invalid_not_survived(shared_run):
    r = shared_run["results"]["noop"]
    assert (r.status, r.kind) == (go.INVALID, "no-op")


def test_text_that_is_not_found_is_invalid(shared_run):
    r = shared_run["results"]["not found"]
    assert (r.status, r.kind) == (go.INVALID, "text-not-found")
    assert "found 0 time(s)" in r.detail


def test_text_found_the_wrong_number_of_times_is_invalid(shared_run):
    r = shared_run["results"]["ambiguous"]
    assert (r.status, r.kind) == (go.INVALID, "text-not-found")
    assert "expected 1" in r.detail


def test_a_mutation_that_breaks_the_import_is_invalid_not_caught(shared_run):
    r = shared_run["results"]["import-break"]
    assert (r.status, r.kind) == (go.INVALID, "collection")


def test_a_mutation_that_does_not_compile_is_invalid_before_any_run(shared_run):
    r = shared_run["results"]["syntax"]
    assert (r.status, r.kind) == (go.INVALID, "syntax")


def test_a_test_time_error_is_counted_as_caught(shared_run):
    """pytest prints ``1 error`` for a fixture failure, not ``1 failed``."""
    r = shared_run["results"]["setup error"]
    assert (r.status, r.kind) == (go.CAUGHT, "failed")
    assert (r.failed, r.errors) == (0, 1)


def test_an_endless_loop_is_reported_hung_and_counts_as_caught(shared_run):
    r = shared_run["results"]["hang"]
    assert (r.status, r.kind) == (go.CAUGHT, "hung")
    assert r.peak_rss_mb is not None and "HUNG" in r.detail


def test_unbounded_allocation_is_reported_as_runaway_memory_with_the_measurement(shared_run):
    r = shared_run["results"]["runaway"]
    assert (r.status, r.kind) == (go.CAUGHT, "runaway-memory")
    assert r.peak_rss_mb is not None and r.peak_rss_mb > 500
    assert "RUNAWAY-MEMORY" in r.detail and "MB" in r.detail


def test_a_failing_smoke_command_makes_the_mutation_invalid(shared_run):
    r = shared_run["results"]["smoke"]
    assert (r.status, r.kind) == (go.INVALID, "smoke")


# --------------------------------------------------------------------------- restore


def test_every_file_is_restored_byte_for_byte_after_a_full_run(shared_run):
    root = shared_run["root"]
    assert _sha(root / "calc.py") == shared_run["before"]
    assert (root / "calc.py").read_text() == CALC
    assert not go._ACTIVE, "a file was left registered as mutated"


def test_no_temp_files_are_left_behind(shared_run):
    assert sorted(p.name for p in shared_run["root"].iterdir() if p.is_file()) == ["calc.py"]


def test_stale_bytecode_of_the_touched_file_is_cleared_and_others_are_kept(shared_run):
    names = {p.name for p in (shared_run["root"] / "__pycache__").iterdir()}
    assert "calc.cpython-0.pyc" not in names
    assert "calc.cpython-0.opt-1.pyc" not in names
    assert "other.cpython-0.pyc" in names


def test_children_run_without_writing_bytecode():
    assert go._child_env()["PYTHONDONTWRITEBYTECODE"] == "1"


def test_restore_does_not_preserve_the_original_mtime(tmp_path):
    """``copy2`` would: cargo and ``.pyc`` validation then treat the mutant as current."""
    root = _fixture(tmp_path)
    long_ago = 1_000_000_000
    os.utime(root / "calc.py", (long_ago, long_ago))
    go.run_mutations(
        [_m("caught: add subtracts", "return a + b", "return a - b")],
        root=root,
        command=COMMAND,
        control=False,
    )
    assert (root / "calc.py").read_text() == CALC
    assert (root / "calc.py").stat().st_mtime > long_ago + 1000


def test_restore_keeps_the_file_mode(tmp_path):
    root = _fixture(tmp_path)
    os.chmod(root / "calc.py", 0o755)
    go.run_mutations(
        [_m("caught: add subtracts", "return a + b", "return a - b")],
        root=root,
        command=COMMAND,
        control=False,
    )
    assert (root / "calc.py").stat().st_mode & 0o777 == 0o755


def test_a_restore_that_is_not_byte_identical_raises(tmp_path, monkeypatch):
    """The hash check: a restore that writes the wrong bytes must not pass silently."""
    root = _fixture(tmp_path)
    real_write = go._write_atomic

    def lying_write(path, data, mode):
        # mutation writes pass through; the restore (the original bytes) is corrupted
        real_write(path, data + b"\n# drift\n" if data == CALC.encode() else data, mode)

    monkeypatch.setattr(go, "_write_atomic", lying_write)
    with pytest.raises(go.RestoreError):
        go.run_mutations(
            [_m("caught: add subtracts", "return a + b", "return a - b")],
            root=root,
            command=COMMAND,
            control=False,
        )
    go._ACTIVE.clear()


def test_the_file_is_restored_when_the_run_crashes_midway(tmp_path, monkeypatch):
    root = _fixture(tmp_path)
    seen = {}

    def crash(command, cwd, deadline, rss_mb):
        seen["mutant_on_disk"] = (root / "calc.py").read_text() != CALC
        raise RuntimeError("harness crashed mid-run")

    monkeypatch.setattr(go, "_run_bounded", crash)
    with pytest.raises(RuntimeError, match="crashed mid-run"):
        go.run_mutations(
            [_m("caught: add subtracts", "return a + b", "return a - b")],
            root=root,
            command=COMMAND,
            control=False,
        )
    assert seen["mutant_on_disk"] is True, "the crash was not injected while mutated"
    assert (root / "calc.py").read_text() == CALC
    assert not go._ACTIVE


@pytest.mark.parametrize(
    ("sig", "label"), [(signal.SIGINT, "SIGINT"), (signal.SIGTERM, "SIGTERM")], ids=lambda v: str(v)
)
def test_the_file_is_restored_when_the_harness_is_signalled(tmp_path, sig, label):
    """A real harness process, a mutation that hangs the tests, a real signal."""
    root = _fixture(tmp_path)
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {
                "root": str(root),
                "command": COMMAND,
                "deadline": 600,
                "mutations": [
                    {
                        "label": "hang",
                        "file": "calc.py",
                        "old": "        i += 1\n",
                        "new": "        i += 0\n",
                    }
                ],
            }
        )
    )
    proc = subprocess.Popen(
        [sys.executable, str(SCRIPT), str(spec), "--no-control"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        # wait on observable state (the mutant is on disk), not on a fixed delay
        for _ in range(1200):
            if b"i += 0" in (root / "calc.py").read_bytes() or proc.poll() is not None:
                break
            time.sleep(0.05)
        assert b"i += 0" in (root / "calc.py").read_bytes(), "the mutation never reached the disk"
        proc.send_signal(sig)
        proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert proc.returncode != 0
    assert (root / "calc.py").read_text() == CALC, f"{label} left the mutant behind"
    assert sorted(p.name for p in root.iterdir() if p.is_file()) == ["calc.py"]


# --------------------------------------------------------------------------- baseline and CLI


def test_a_red_baseline_refuses_to_judge_anything(tmp_path):
    root = _fixture(tmp_path)
    (root / "tests" / "test_calc.py").write_text(TESTS + "\n\ndef test_red():\n    assert False\n")
    results, baseline = go.run_mutations(
        [_m("caught: add subtracts", "return a + b", "return a - b")],
        root=root,
        command=COMMAND,
    )
    assert results == []
    assert baseline is not None and baseline.kind == "baseline"
    assert (root / "calc.py").read_text() == CALC, "nothing may be mutated against a red baseline"


def _spec(tmp_path, root, mutations, **extra):
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {
                "root": str(root),
                "command": COMMAND,
                "mutations": [
                    {"label": m.label, "file": m.file, "old": m.old, "new": m.new}
                    for m in mutations
                ],
                **extra,
            }
        )
    )
    return spec


def test_cli_exit_status_zero_when_everything_is_caught(tmp_path, capsys):
    root = _fixture(tmp_path)
    spec = _spec(tmp_path, root, [_m("caught: add subtracts", "return a + b", "return a - b")])
    assert go.main([str(spec)]) == 0
    out = capsys.readouterr().out
    assert "CAUGHT" in out and "summary: 1 CAUGHT, 0 SURVIVED, 0 INVALID" in out


def test_cli_exit_status_one_and_the_question_when_a_mutation_survives(tmp_path, capsys):
    root = _fixture(tmp_path)
    spec = _spec(
        tmp_path,
        root,
        [
            _m("caught: add subtracts", "return a + b", "return a - b"),
            _m("survived: untested", "return x * 2", "return x * 3"),
        ],
    )
    assert go.main([str(spec), "--no-control"]) == 1
    out = capsys.readouterr().out
    assert "SURVIVED  survived: untested" in out
    assert "is a question, not a pass" in out


def test_cli_only_filters_by_label(tmp_path, capsys):
    root = _fixture(tmp_path)
    spec = _spec(
        tmp_path,
        root,
        [
            _m("caught: add subtracts", "return a + b", "return a - b"),
            _m("survived: untested", "return x * 2", "return x * 3"),
        ],
    )
    assert go.main([str(spec), "--no-control", "--only", "add subtracts"]) == 0
    assert "survived: untested" not in capsys.readouterr().out


def test_cli_exit_status_two_on_a_red_baseline(tmp_path, capsys):
    root = _fixture(tmp_path)
    (root / "tests" / "test_calc.py").write_text("def test_red():\n    assert False\n")
    spec = _spec(tmp_path, root, [_m("caught: add subtracts", "return a + b", "return a - b")])
    assert go.main([str(spec)]) == 2
    assert "BASELINE" in capsys.readouterr().err


def test_cli_exit_status_two_on_a_bad_spec(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert go.main([str(bad)]) == 2


def test_python_token_in_a_command_is_this_interpreter():
    assert go._as_command(["{python}", "-m", "pytest"]) == [sys.executable, "-m", "pytest"]
    assert go._as_command("{python} -c 'pass'") == [sys.executable, "-c", "pass"]


def test_cli_reads_a_toml_spec(tmp_path):
    root = _fixture(tmp_path)
    toml = tmp_path / "spec.toml"
    toml.write_text(
        f'root = "{root}"\n'
        f"command = {json.dumps(COMMAND)}\n"
        "[[mutations]]\n"
        'label = "caught: add subtracts"\nfile = "calc.py"\n'
        'old = "return a + b"\nnew = "return a - b"\n'
    )
    assert go.main([str(toml), "--no-control"]) == 0


# --------------------------------------------------------------------------- classify (pure)

FAILED = "FAILED tests/t.py::test_a - assert 1 == 2\n=== 1 failed, 3 passed in 0.12s ===\n"
SETUP_ERROR = "ERROR tests/t.py::test_b - RuntimeError\n=== 3 passed, 1 error in 0.12s ===\n"
COLLECTION = (
    "ERROR collecting tests/t.py\n!!! Interrupted: 1 error during collection !!!\n"
    "=== 1 error in 0.05s ===\n"
)


@pytest.mark.parametrize(
    ("rc", "output", "status", "kind"),
    [
        (1, FAILED, go.CAUGHT, "failed"),
        (1, SETUP_ERROR, go.CAUGHT, "failed"),
        (1, "1 failed, 2 errors in 0.1s\n", go.CAUGHT, "failed"),
        (2, COLLECTION, go.INVALID, "collection"),
        (1, "ERROR collecting x\n=== 1 failed, 1 error in 0.1s ===\n", go.INVALID, "collection"),
        (4, "ImportError while loading conftest '/x/conftest.py'.\n", go.INVALID, "collection"),
        (0, "=== 4 passed in 0.1s ===\n", go.SURVIVED, "survived"),
        (0, "=== 2 skipped in 0.1s ===\n", go.INVALID, "nothing-ran"),
        (5, "=== no tests ran in 0.01s ===\n", go.INVALID, "bad-exit"),
        (1, "Traceback: nothing pytest-shaped\n", go.INVALID, "no-summary"),
        (2, "=== 1 passed in 0.1s ===\n", go.INVALID, "bad-exit"),
    ],
)
def test_classify_pytest_outputs(rc, output, status, kind):
    result = go.classify(rc, output)
    assert (result.status, result.kind) == (status, kind)


def test_classify_counts_failures_and_errors_separately():
    r = go.classify(1, "=== 2 failed, 3 errors, 1 passed in 1s ===\n")
    assert (r.failed, r.errors) == (2, 3)
    assert r.detail == "2 failed, 3 errors"


def test_classify_names_the_first_failing_test():
    r = go.classify(1, FAILED)
    assert r.detail == "1 failed; first: tests/t.py::test_a"


def test_classify_exit_runner_trusts_the_exit_status_only():
    assert go.classify(0, "", runner="exit").status == go.SURVIVED
    assert go.classify(101, "", runner="exit").status == go.CAUGHT


# --------------------------------------------------------------------------- the self-check list


def test_every_selfcheck_mutation_text_still_applies():
    """The harness is gate-off'd with itself; this keeps that list from rotting."""
    spec = json.loads(SELFCHECK.read_text())
    source = SCRIPT.read_text()
    assert spec["mutations"], "the self-check list is empty"
    for entry in spec["mutations"]:
        found = source.count(entry["old"])
        assert found == entry.get("expect", 1), f"{entry['label']!r}: found {found} time(s)"
        assert entry["file"] == "scripts/gate_off.py"
        assert entry["old"] != entry["new"]
