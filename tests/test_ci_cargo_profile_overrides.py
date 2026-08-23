"""Test jobs relax LTO; the benchmark job must not.

`[profile.release]` carries `lto = true` + `codegen-units = 1`. Those are right
for a shipped wheel — compiled once, users pay nothing — and expensive for a
binary CI throws away. Measured on this workspace:

    profile                       compile   run    total
    release (lto, cgu=1)             62s     14s     76s
    debug                            31s    186s    217s
    release, no LTO, cgu=16          26s     10s     36s

`opt-level = 3` is what makes these tests fast; they are compute-heavy, so a
debug build runs them 13x slower and is a net loss despite compiling quicker.
LTO and `codegen-units = 1` buy almost nothing at run time and cost 2.4x at
compile, so the test jobs drop them via `CARGO_PROFILE_*` env vars — which
leaves Cargo.toml, and therefore the published wheel, untouched.

Two things have to stay true, and neither is enforced by a comment:

1. The `benchmarks` job must NOT get the override. It enforces latency
   thresholds, so it has to measure the same binary users receive. Inheriting a
   no-LTO build would move every threshold silently — the suite would still
   pass, just against a different program, which is the worst shape a
   benchmark regression can take.

2. `[profile.release]` in Cargo.toml must keep `lto = true`. If someone
   "simplifies" by moving the override into the manifest, the wheel loses LTO
   and the win becomes a shipped-performance regression.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/test.yml"
CARGO = ROOT / "Cargo.toml"

OVERRIDE_KEYS = ("CARGO_PROFILE_RELEASE_LTO", "CARGO_PROFILE_RELEASE_CODEGEN_UNITS")

# Only COMPILE-DOMINATED jobs benefit. `rust-tests` spends 234s compiling
# against 73s running, so cheapening the compile wins outright: 407s -> 187s.
COMPILE_BOUND_JOBS = ("rust-tests",)

# Jobs that build Rust but are TEST-dominated. Measured on both, and the
# override is a loss: `python-tests` builds for 118s and then tests for 176s,
# and its tests run THROUGH the extension — so a no-LTO build made the build
# 29s cheaper and the tests 27s dearer, taking the job 347s -> 355s.
# `python-free-threaded` showed no signal either way (86s -> 85s).
TEST_BOUND_JOBS = ("python-tests", "python-free-threaded")


def _jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]


def _job_env(name: str) -> dict:
    jobs = _jobs()
    assert name in jobs, (
        f"job {name!r} no longer exists in test.yml — this guard is pointing at "
        f"a job that was renamed or removed and must be updated rather than "
        f"left silently passing. Jobs present: {sorted(jobs)}"
    )
    return {str(k): str(v) for k, v in (jobs[name].get("env") or {}).items()}


@pytest.mark.parametrize("job", COMPILE_BOUND_JOBS)
@pytest.mark.parametrize("key", OVERRIDE_KEYS)
def test_compile_bound_jobs_relax_the_release_profile(job: str, key: str) -> None:
    assert key in _job_env(job), (
        f"{job} lost {key}. That restores full LTO + codegen-units=1 for a "
        f"binary CI discards. Measured: this job was 407s with it and 187s "
        f"without — 234s of it was compiling against 73s running."
    )


@pytest.mark.parametrize("job", TEST_BOUND_JOBS)
def test_test_bound_jobs_do_not_get_the_override(job: str) -> None:
    """The override is not a global good — on these jobs it made things worse.

    It is tempting to apply it everywhere that builds Rust. It was, and the
    measurement said no: `python-tests` went 347s -> 355s, because its tests
    run through the extension and a no-LTO build slows every one of them by
    more than the cheaper build saves.
    """
    env = _job_env(job)
    leaked = sorted(k for k in OVERRIDE_KEYS if k in env)
    assert not leaked, (
        f"{job} gained {leaked}. This job is TEST-dominated, not "
        f"compile-dominated: its tests execute Rust, so relaxing the profile "
        f"costs more in test time than it saves in build time. Measured "
        f"347s -> 355s. Only compile-dominated jobs benefit."
    )


def test_the_benchmark_job_keeps_the_shipped_profile() -> None:
    """The benchmark job must measure what users actually get."""
    env = _job_env("benchmarks")
    leaked = sorted(k for k in OVERRIDE_KEYS if k in env)
    assert not leaked, (
        f"the benchmarks job inherited {leaked}. It enforces latency thresholds, "
        f"so it must build the same profile users receive — LTO included. With "
        f"the override it measures a different program and every threshold "
        f"shifts silently, which the suite cannot detect because it still passes."
    )


def test_no_workflow_level_env_leaks_the_override_to_every_job() -> None:
    """A top-level `env:` would reach benchmarks too, defeating the test above."""
    top = yaml.safe_load(WORKFLOW.read_text()).get("env") or {}
    leaked = sorted(k for k in OVERRIDE_KEYS if k in {str(x) for x in top})
    assert not leaked, (
        f"{leaked} set at workflow level, which applies to EVERY job including "
        f"benchmarks. Set it per-job on the test jobs instead."
    )


def test_cargo_toml_still_ships_lto() -> None:
    """The override is per-invocation; the manifest must stay as-is.

    Moving it into Cargo.toml would be simpler to read and would ship a wheel
    without LTO — turning a CI speedup into a user-facing performance
    regression, in a way no test in this repo would otherwise catch.
    """
    body = CARGO.read_text()
    m = re.search(r"^\[profile\.release\]$(.*?)(?=^\[|\Z)", body, re.M | re.S)
    assert m, "no [profile.release] section in Cargo.toml"
    section = m.group(1)
    assert re.search(r"^\s*lto\s*=\s*true", section, re.M), (
        "[profile.release] no longer sets `lto = true`. The published wheel is "
        "built from this profile; the CI speedup is supposed to come from "
        "per-job CARGO_PROFILE_* overrides, not from changing what ships."
    )
    assert re.search(r"^\s*codegen-units\s*=\s*1", section, re.M), (
        "[profile.release] no longer sets `codegen-units = 1` — same reasoning."
    )
