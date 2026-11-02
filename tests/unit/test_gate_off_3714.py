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

import contextlib
import hashlib
import importlib.util
import json
import os
import py_compile
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


@pytest.fixture(scope="module", autouse=True)
def _gate_off_state_dir(tmp_path_factory):
    """Locks and journals live in a state dir; never let a test touch the real one."""
    patch = pytest.MonkeyPatch()
    patch.setenv("GATE_OFF_STATE_DIR", str(tmp_path_factory.mktemp("gate-off-state")))
    yield Path(os.environ["GATE_OFF_STATE_DIR"])
    patch.undo()


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
            "import-hang: the module spins at import",
            "VALUE = 7\n",
            "VALUE = 7\n\nwhile True:\n    pass\n",
            deadline=3,
        ),
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


def test_a_mutation_that_hangs_at_import_is_invalid_like_one_that_raises_at_import(shared_run):
    """Rule (d): an import-time hang is not a catch; a loop that runs inside a test is."""
    r = shared_run["results"]["import-hang"]
    assert (r.status, r.kind) == (go.INVALID, "hung-at-import")
    assert shared_run["results"]["hang"].kind == "hung"


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


# --------------------------------------------------------------------------- handlers that must not swallow


def test_killing_a_group_that_is_already_gone_is_not_an_error():
    proc = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    proc.wait()
    go._kill_group(proc.pid)  # ProcessLookupError is the normal case after a clean exit


def test_a_group_that_cannot_be_killed_is_a_failure_not_a_shrug(monkeypatch):
    def refuse(pgid, sig):
        raise PermissionError("not ours")

    monkeypatch.setattr(go.os, "killpg", refuse)
    with pytest.raises(PermissionError):
        go._kill_group(12345)


def test_a_stale_pyc_that_cannot_be_deleted_propagates(tmp_path, monkeypatch):
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "calc.cpython-0.pyc").write_bytes(b"stale")

    def refuse(self, missing_ok=False):
        raise PermissionError("read-only")

    monkeypatch.setattr(go.Path, "unlink", refuse)
    with pytest.raises(PermissionError):
        go._clear_pycache(tmp_path / "calc.py")


def test_a_failed_atomic_write_re_raises_the_original_error_and_leaves_no_temp_file(
    tmp_path, monkeypatch
):
    target = tmp_path / "f.py"
    target.write_text("original")

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(go.os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        go._write_atomic(target, b"new", 0o644)
    assert target.read_text() == "original"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.py"]


def _failing_restore(real_write):
    def failing_restore(path, data, mode):
        if data == CALC.encode():  # only the restore writes the original bytes
            raise OSError("cannot write")
        real_write(path, data, mode)

    return failing_restore


def test_a_restore_that_raises_is_reported_as_a_restore_error(tmp_path, monkeypatch):
    root = _fixture(tmp_path)
    real_write = go._write_atomic
    monkeypatch.setattr(go, "_write_atomic", _failing_restore(real_write))
    with pytest.raises(go.RestoreError, match="could not be restored"):
        go.run_mutations(
            [_m("caught: add subtracts", "return a + b", "return a - b")],
            root=root,
            command=COMMAND,
            control=False,
        )
    monkeypatch.setattr(go, "_write_atomic", real_write)
    go._restore_all_failsafe()  # put the file back for the rest of the session
    assert (root / "calc.py").read_text() == CALC
    assert not go._ACTIVE


def test_cli_exits_three_when_a_restore_raises(tmp_path, monkeypatch, capsys):
    root = _fixture(tmp_path)
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {
                "root": str(root),
                "command": COMMAND,
                "mutations": [
                    {"label": "m", "file": "calc.py", "old": "return a + b", "new": "return a - b"}
                ],
            }
        )
    )
    real_write = go._write_atomic
    monkeypatch.setattr(go, "_write_atomic", _failing_restore(real_write))
    assert go.main([str(spec), "--no-control"]) == 3
    assert "RESTORE FAILED" in capsys.readouterr().err
    monkeypatch.setattr(go, "_write_atomic", real_write)
    go._restore_all_failsafe()
    assert (root / "calc.py").read_text() == CALC


def test_an_unavailable_ps_refuses_to_run_unbounded(monkeypatch):
    def missing(*a, **k):
        raise FileNotFoundError("ps")

    monkeypatch.setattr(go.subprocess, "run", missing)
    with pytest.raises(RuntimeError, match="cannot sample RSS"):
        go._group_rss_mb(1)


# --------------------------------------------------------------------------- lock, journal, symlink


HANG = {"label": "hang", "file": "calc.py", "old": "        i += 1\n", "new": "        i += 0\n"}
CATCH = {"label": "caught", "file": "calc.py", "old": "return a + b", "new": "return a - b"}


# The SECOND run gets a short deadline: if a harness mutation lets it run on A's mutant it
# must end by itself (HUNG) instead of leaving an endless child behind.
def _spec_file(tmp_path, root, name, mutations, **extra):
    spec = tmp_path / name
    spec.write_text(
        json.dumps(
            {
                "root": str(root),
                "command": COMMAND,
                "deadline": 600,
                "mutations": mutations,
                **extra,
            }
        )
    )
    return spec


def _start(spec):
    return subprocess.Popen(
        [sys.executable, str(SCRIPT), str(spec), "--no-control"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_for_mutant(root, proc):
    for _ in range(1200):
        if b"i += 0" in (root / "calc.py").read_bytes() or proc.poll() is not None:
            break
        time.sleep(0.05)
    assert b"i += 0" in (root / "calc.py").read_bytes(), "the mutation never reached the disk"


def _children(pid):
    out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout
    return [int(p) for p in out.split()]


def _wait_for_children(proc):
    for _ in range(1200):
        if _children(proc.pid) or proc.poll() is not None:
            break
        time.sleep(0.05)
    return _children(proc.pid)


def _kill_groups(pids):
    for pid in pids:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pid, signal.SIGKILL)


def _journal_of(root):
    return go._journal_path((root / "calc.py").resolve())


def test_a_second_run_on_a_file_that_is_mid_mutation_refuses_and_touches_nothing(
    tmp_path, _gate_off_state_dir
):
    root = _fixture(tmp_path)
    first = _start(_spec_file(tmp_path, root, "a.json", [HANG]))
    try:
        _wait_for_mutant(root, first)
        mutant = (root / "calc.py").read_bytes()
        second = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                str(_spec_file(tmp_path, root, "b.json", [CATCH], deadline=20)),
                "--no-control",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert second.returncode == 4
        assert "locked by another gate_off run" in second.stderr
        assert "CAUGHT" not in second.stdout and "SURVIVED" not in second.stdout
        assert (root / "calc.py").read_bytes() == mutant, "the refused run touched the file"
    finally:
        first.send_signal(signal.SIGTERM)
        first.communicate(timeout=60)
    assert (root / "calc.py").read_text() == CALC
    assert not _journal_of(root).exists()


def _kill_first_run_mid_mutation(tmp_path, root):
    """SIGKILL a harness while it holds a mutant: nothing of ITS cleanup can run."""
    first = _start(_spec_file(tmp_path, root, "a.json", [HANG]))
    _wait_for_mutant(root, first)
    orphans = _wait_for_children(first)
    first.kill()
    first.communicate(timeout=60)
    _kill_groups(orphans)
    assert b"i += 0" in (root / "calc.py").read_bytes(), "SIGKILL should leave the mutant behind"


def test_a_run_after_a_sigkilled_run_restores_the_original_before_it_snapshots(
    tmp_path, _gate_off_state_dir
):
    root = _fixture(tmp_path)
    _kill_first_run_mid_mutation(tmp_path, root)
    assert _journal_of(root).exists()
    second = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(_spec_file(tmp_path, root, "b.json", [CATCH], deadline=20)),
            "--no-control",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert second.returncode == 0, second.stdout + second.stderr
    assert "recovered" in second.stderr and "stale journal" in second.stderr
    assert "CAUGHT" in second.stdout
    assert (root / "calc.py").read_text() == CALC
    assert not _journal_of(root).exists()


def test_a_stale_journal_for_a_file_edited_since_is_refused_not_overwritten(
    tmp_path, _gate_off_state_dir
):
    root = _fixture(tmp_path)
    _kill_first_run_mid_mutation(tmp_path, root)
    edited = CALC + "\n# edited by hand after the killed run\n"
    (root / "calc.py").write_text(edited)
    second = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(_spec_file(tmp_path, root, "b.json", [CATCH], deadline=20)),
            "--no-control",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert second.returncode == 4
    assert "matches neither the original" in second.stderr
    assert (root / "calc.py").read_text() == edited
    _journal_of(root).unlink()


def test_a_run_whose_original_already_matches_the_journal_just_drops_it(
    tmp_path, _gate_off_state_dir
):
    root = _fixture(tmp_path)
    saved = go._snapshot(root / "calc.py")
    go._journal_write((root / "calc.py").resolve(), saved, b"mutant")
    go._recover((root / "calc.py").resolve())
    assert not _journal_of(root).exists()
    assert (root / "calc.py").read_text() == CALC


def test_lock_dir_refuses_a_directory_that_is_already_held(tmp_path):
    guard = go._lock_dir(tmp_path.resolve())
    try:
        with pytest.raises(go.TargetLocked, match="locked by another gate_off run"):
            go._lock_dir(tmp_path.resolve())
    finally:
        go._release(guard)
    go._release(go._lock_dir(tmp_path.resolve()))  # free again once released


def test_an_unreadable_journal_is_refused(tmp_path, _gate_off_state_dir):
    target = tmp_path / "f.py"
    target.write_text("x")
    target = target.resolve()
    go._journal_path(target).write_text("{not json")
    with pytest.raises(go.StaleJournal, match="cannot be read"):
        go._recover(target)
    go._journal_path(target).unlink()


# -- the lock does not depend on the environment, and it covers EVERY target ---------------

ONE, TWO = "one/fx/calc.py", "two/fx/calc.py"  # _fixture() puts the files in <dir>/fx
SLEEP = ["{python}", "-c", "import time; time.sleep(600)"]
QUICK = ["{python}", "-c", "pass"]


def _two_roots(tmp_path):
    """Two fixture trees whose target files share a basename (``calc.py``) but not a directory."""
    one, two = _fixture(tmp_path / "one"), _fixture(tmp_path / "two")
    (two / "calc.py").write_text(CALC + "\n# the second tree differs\n")
    return one, two


def _sleeper_spec(tmp_path, name, files, command=SLEEP, **extra):
    """A run that mutates ``files`` then sits in ``command`` (runner=exit: no pytest needed)."""
    spec = tmp_path / name
    spec.write_text(
        json.dumps(
            {
                "root": str(tmp_path),
                "runner": "exit",
                "command": command,
                "deadline": 600,
                "mutations": [
                    {"label": f"m-{f}", "file": f, "old": "return a + b", "new": "return a - b"}
                    for f in files
                ],
                **extra,
            }
        )
    )
    return spec


def _wait_until_changed(path, original, proc):
    for _ in range(1200):
        if path.read_bytes() != original or proc.poll() is not None:
            break
        time.sleep(0.05)
    assert path.read_bytes() != original, "the mutation never reached the disk"


def _stop(proc):
    proc.send_signal(signal.SIGTERM)
    proc.communicate(timeout=60)


def _run_cli(spec, env=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(spec), "--no-control"],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )


def test_the_lock_holds_even_when_the_second_run_uses_another_state_dir_and_home(
    tmp_path, _gate_off_state_dir
):
    """The original corruption needed overlapping runs that did not share a lock directory."""
    one, _ = _two_roots(tmp_path)
    first = _start(_sleeper_spec(tmp_path, "a.json", [ONE]))
    try:
        _wait_until_changed(one / "calc.py", CALC.encode(), first)
        mutant = (one / "calc.py").read_bytes()
        other = tmp_path / "elsewhere"
        env = {
            **os.environ,
            "GATE_OFF_STATE_DIR": str(other / "state"),
            "HOME": str(other),
            "XDG_CACHE_HOME": str(other / "cache"),
        }
        second = _run_cli(_sleeper_spec(tmp_path, "b.json", [ONE], QUICK), env)
        assert second.returncode == 4, second.stdout + second.stderr
        assert "locked by another gate_off run" in second.stderr
        assert (one / "calc.py").read_bytes() == mutant, "the refused run touched the file"
    finally:
        _stop(first)
    assert (one / "calc.py").read_text() == CALC


def test_the_default_state_dir_does_not_depend_on_home_or_xdg(tmp_path, monkeypatch):
    monkeypatch.delenv("GATE_OFF_STATE_DIR", raising=False)
    before = go._default_state_dir()
    monkeypatch.setenv("HOME", str(tmp_path / "other-home"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "other-cache"))
    assert go._default_state_dir() == before
    assert before.parts[-2:] == (".cache", "gate-off")
    assert "other-home" not in str(before) and "other-cache" not in str(before)


def test_two_files_with_the_same_basename_in_different_directories_do_not_share_a_lock(tmp_path):
    one, two = _two_roots(tmp_path)
    first = _start(_sleeper_spec(tmp_path, "a.json", [ONE]))
    try:
        _wait_until_changed(one / "calc.py", CALC.encode(), first)
        second = _run_cli(_sleeper_spec(tmp_path, "b.json", [TWO], QUICK))
        assert second.returncode == 1, second.stdout + second.stderr  # ran: SURVIVED (exit 0)
        assert "locked" not in second.stderr
        assert "SURVIVED" in second.stdout
    finally:
        _stop(first)
    assert (one / "calc.py").read_text() == CALC
    assert (two / "calc.py").read_text() == CALC + "\n# the second tree differs\n"


def test_a_journal_is_keyed_on_the_full_path_not_the_basename(tmp_path, _gate_off_state_dir):
    """A journal left on one/calc.py must not be applied to, or block, two/calc.py."""
    one, two = _two_roots(tmp_path)
    first = _start(_sleeper_spec(tmp_path, "a.json", [ONE]))
    _wait_until_changed(one / "calc.py", CALC.encode(), first)
    orphans = _wait_for_children(first)
    first.kill()
    first.communicate(timeout=60)
    _kill_groups(orphans)
    journal_one = go._journal_path((one / "calc.py").resolve())
    journal_two = go._journal_path((two / "calc.py").resolve())
    assert journal_one.exists() and journal_one != journal_two
    second = _run_cli(_sleeper_spec(tmp_path, "b.json", [TWO], QUICK))
    assert second.returncode == 1, second.stdout + second.stderr  # ran, not refused
    assert journal_one.exists(), "the other tree's journal was consumed"
    assert (two / "calc.py").read_text() == CALC + "\n# the second tree differs\n"
    third = _run_cli(_sleeper_spec(tmp_path, "c.json", [ONE], QUICK))
    assert "recovered" in third.stderr
    assert (one / "calc.py").read_text() == CALC
    assert not journal_one.exists()


def test_every_target_of_a_multi_file_spec_is_locked_not_just_the_first(tmp_path):
    """The lint's own spec mutates three files: a run touching the 2nd or 3rd must be refused."""
    one, two = _two_roots(tmp_path)
    first = _start(_sleeper_spec(tmp_path, "a.json", [ONE, TWO]))
    try:
        _wait_until_changed(one / "calc.py", CALC.encode(), first)
        second = _run_cli(_sleeper_spec(tmp_path, "b.json", [TWO], QUICK))
        assert second.returncode == 4, second.stdout + second.stderr
        assert "locked by another gate_off run" in second.stderr
    finally:
        _stop(first)
    assert (two / "calc.py").read_text() == CALC + "\n# the second tree differs\n"


def test_a_symlinked_target_is_mutated_through_the_link_and_the_link_survives(tmp_path):
    root = _fixture(tmp_path)
    link = root / "calc_link.py"
    link.symlink_to("calc.py")
    results, _ = go.run_mutations(
        [go.Mutation("caught: via the link", "calc_link.py", "return a + b", "return a - b")],
        root=root,
        command=COMMAND,
        control=False,
    )
    assert results[0].status == go.CAUGHT
    assert link.is_symlink() and os.readlink(link) == "calc.py"
    assert (root / "calc.py").read_text() == CALC


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_an_unwritable_directory_is_invalid_and_not_a_restore_alarm(tmp_path):
    root = _fixture(tmp_path)
    os.chmod(root, 0o555)
    try:
        results, _ = go.run_mutations(
            [_m("caught: add subtracts", "return a + b", "return a - b")],
            root=root,
            command=COMMAND,
            control=False,
        )
    finally:
        os.chmod(root, 0o755)
    assert (results[0].status, results[0].kind) == (go.INVALID, "unwritable")
    assert (root / "calc.py").read_text() == CALC
    assert not go._ACTIVE


def test_a_command_that_cannot_start_is_invalid_and_the_file_is_restored(tmp_path):
    root = _fixture(tmp_path)
    results, _ = go.run_mutations(
        [_m("caught: add subtracts", "return a + b", "return a - b")],
        root=root,
        command=["no-such-binary-for-gate-off"],
        control=False,
    )
    assert (results[0].status, results[0].kind) == (go.INVALID, "command-not-found")
    assert (root / "calc.py").read_text() == CALC


def test_a_command_that_cannot_start_stops_at_the_baseline(tmp_path):
    root = _fixture(tmp_path)
    results, baseline = go.run_mutations(
        [_m("caught: add subtracts", "return a + b", "return a - b")],
        root=root,
        command=["no-such-binary-for-gate-off"],
    )
    assert results == [] and baseline.kind == "baseline"
    assert "could not start" in baseline.detail


def test_a_crlf_file_with_an_lf_mutation_text_says_why(tmp_path):
    root = _fixture(tmp_path)
    (root / "calc.py").write_bytes(CALC.replace("\n", "\r\n").encode())
    results, _ = go.run_mutations(
        [_m("crlf", "def add(a, b):\n    return a + b", "def add(a, b):\n    return a - b")],
        root=root,
        command=COMMAND,
        control=False,
    )
    assert results[0].kind == "text-not-found" and "CRLF" in results[0].detail


def test_a_stale_unchecked_hash_pyc_cannot_certify_a_mutation_that_never_ran(tmp_path):
    """The PRE-run clear: bytecode of the ORIGINAL that Python trusts without looking at the source."""
    root = _fixture(tmp_path)
    pyc = importlib.util.cache_from_source(str(root / "calc.py"))
    Path(pyc).parent.mkdir(exist_ok=True)
    py_compile.compile(
        str(root / "calc.py"),
        cfile=pyc,
        doraise=True,
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
    )
    results, _ = go.run_mutations(
        [_m("caught: add subtracts", "return a + b", "return a - b")],
        root=root,
        command=COMMAND,
        control=False,
    )
    assert results[0].status == go.CAUGHT, "a stale .pyc made the mutation look equivalent"


def test_the_atexit_failsafe_restores_a_file_left_mutated_at_interpreter_exit(tmp_path):
    target = tmp_path / "f.py"
    target.write_text("original\n")
    code = f"""
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("g", {str(SCRIPT)!r})
g = importlib.util.module_from_spec(spec)
sys.modules["g"] = g
spec.loader.exec_module(g)
p = Path({str(target.resolve())!r})
saved = g._snapshot(p)
g._ACTIVE[p] = saved
g._journal_write(p, saved, b"mutant\\n")
p.write_text("mutant\\n")
"""
    subprocess.run([sys.executable, "-c", code], check=True, timeout=60)
    assert target.read_text() == "original\n"


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_the_whole_process_group_is_killed_even_after_the_leader_exited(tmp_path):
    """A grandchild left behind by a clean exit must not outlive the run (no 200 s stall to notice)."""
    pidfile = tmp_path / "child.pid"
    code = (
        "import subprocess, sys; "
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); "
        f"open({str(pidfile)!r}, 'w').write(str(c.pid))"
    )
    go._run_bounded([sys.executable, "-c", code], tmp_path, 60, 4096)
    pid = int(pidfile.read_text())
    try:
        gone = False
        for _ in range(200):
            if not _alive(pid):
                gone = True
                break
            time.sleep(0.05)
        assert gone, "the grandchild survived the run"
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


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


@pytest.mark.parametrize("rc", [126, 127])
def test_classify_exit_runner_does_not_call_a_missing_binary_a_catch(rc):
    r = go.classify(rc, "", runner="exit")
    assert (r.status, r.kind) == (go.INVALID, "not-executable")


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
