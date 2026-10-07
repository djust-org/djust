# Python 3.15 and abi3 wheels: what blocked 3.15, what was built, what abi3 costs (2026-10-05)

Evidence for [#3255](https://github.com/djust-org/djust/issues/3255). It decides nothing. The owner's decision of 2026-10-05 is: resolve the 3.15 dependency and build blockers and produce install-tested wheels; evaluate abi3 with build and benchmark evidence; keep the existing release-wheel strategy and the Python floor (3.11) until a separate decision. `.github/workflows/release.yml` and `publish.yml` are untouched.

Everything here was measured on one machine: macOS arm64 (Apple silicon), rustc 1.98.0, maturin 1.12.6, PyO3 0.29.2, CPython 3.11.14, 3.12.13, 3.13.15, 3.14.7, 3.14.0t, 3.15.0rc2 and 3.15.0rc3 (CI uses rc3). The last full-suite and wheel runs below are rc3 on a tree rebased onto origin/main `5ca3a8d1d`; the benchmark and abi3 install runs were on the earlier base (the Rust sources differ by the later merges, not by anything abi3 touches) and on rc2 for 3.15. Linux and Windows were not built, so nothing below says anything about manylinux or MSVC wheels.

## Why the four py3.15 CI shards failed

The push-to-main run `37267750809` (CPython 3.15.0rc3, ubuntu x86_64) failed all four `py3.15` shards in `uv sync --frozen --extra dev`, before any test ran. 14 locked packages had no `cp315` wheel, so uv built them from source, and it stops at the first build error. Shard 1 stopped on `rpds-py` (its `pyo3-ffi 0.27.2`), shards 2 to 4 on `watchfiles` (`pyo3-ffi 0.26.0`): "the configured Python version (3.15) is newer than PyO3's maximum supported version (3.14)". djust's own extension was never reached.

| Package (locked) | Needed by | `cp315`/abi3 wheel on PyPI | Action here |
|---|---|---|---|
| `rpds-py` 0.30.0 | `jsonschema`, `referencing` (via `mcp`, dev) | 2026.9.1 | locked to 2026.9.1 |
| `watchfiles` 1.1.1 | `uvicorn[standard]` (dev) | 1.3.0 (abi3) | locked to 1.3.0 |
| `pydantic-core` 2.46.1 | `pydantic` (via `mcp`, dev) | only 2.48.0 and later, which only pydantic 2.14 pins (beta) | `pydantic>=2.14.0b2,<3` for 3.15 only |
| `orjson` 3.11.8 | `performance` extra, dev | 3.12.0 | locked to 3.12.0 |
| `msgpack` 1.2.1 | **runtime dependency** | 1.2.3 | locked to 1.2.3 |
| `cffi` 2.0.0 | `cryptography`, `autobahn` | 2.1.1 | locked to 2.1.1 |
| `librt` 0.8.1 | `mypy` (dev) | 0.16.0 | locked to 0.16.0 |
| `ujson` 5.13.0 | `autobahn` | 6.0.0 | locked to 6.0.0 |
| `uvloop` 0.22.1 | `uvicorn[standard]` (dev) | 0.23.0 | locked to 0.23.0 |
| `autobahn` 26.7.1 | `daphne` (runtime) | none at any version | builds from source (6 s) |
| `httptools` 0.7.1 | `uvicorn[standard]` (dev) | none at any version (latest 0.8.0) | builds from source (9 s) |
| `pyyaml` 6.0.3 | `uvicorn[standard]` (dev) | none at any version | builds from source (8 s) |
| `zope-interface` 8.2 | `twisted` (runtime) | none at any version (latest 8.6) | builds from source (3 s) |
| `zstandard` 0.25.0 | `compression` and `performance` extras, dev | none at any version | builds from source (58 s on a loaded machine) |
| `watchdog` 6.0.0 | dev | Linux has `py3-none-manylinux` wheels; macOS has none | builds from source on macOS only (3 s) |

The source-build times are single builds on the shared machine from a cold uv cache; the plain-C ones need only a C compiler. The Rust packages cannot be built from source on 3.15 with their old PyO3, which is why they had to move.

PyO3 itself is not a blocker: `pyo3 0.29.2` (`pyo3-ffi` maximum CPython 3.15) built djust's extension on 3.15.0rc2 and rc3 without a change. 0.29.3 (2026-09-30) fixes a crash when a detached thread is terminated during interpreter finalization and a crash when the GC traverses a `#[pyclass]` type object during initialization; neither is needed for 3.15, and `Cargo.lock` is unchanged here.

### Test failures once the environment installs

With the dependencies in place the full suite (`tests/`, `python/tests/`, `python/djust/tests/`, 41775 collected items, run as eight pytest-split groups with the CI's other options) had five failures on 3.15, all in tests that pin interpreter data:

| Test | Why |
|---|---|
| `test_truncate_slugify_parity_2262::TestTitleExhaustive::test_every_codepoint` | Unicode 17.0 made 54 codepoints cased; `truncate.rs`'s tables stop at 15.1. Pinned the way the 16.0 additions already are. |
| `test_truncate_nfc_slugify_fold_2319::...test_the_combining_class_total_for_this_interpreter` | No `17.0.0` row. 968 codepoints; no already-assigned codepoint changed its combining class, NFC form or NFKD form since 16.0. |
| `test_py_repr_isprintable_table_2292::...matches_the_doc_table` | No `17.0.0` row in the `py_repr_string` doc table (a one-line change in `crates/djust_core/src/lib.rs`). 159613 printable; 4803 more than 16.0, none stopped being printable. |
| `test_stringformat_grammar_2358::test_django_raises_and_djust_renders_empty` (2 cases) | `'%*d' % 2**70`: 3.15 raises `TypeError` (not enough arguments) where 3.14 raised `OverflowError`. Django's filter catches `TypeError`, so Django now renders `""`, the same as djust. |

None needed a product change. `asyncio.iscoroutinefunction` is also deprecated since 3.14 and removed in 3.16; three modules called it and now use `asgiref.sync.iscoroutinefunction`.

Also checked on 3.15: `ruff`, `mypy python/djust` (1376 files), the VDOM fixture freshness check, `check-adr-status`, `check-doc-snippets`, the template-backend lists and the lockfile version check all pass.

### Not fixed, noted for later

- `frozendict` (PEP 814) is not a `dict` subclass. In the Rust bridge, `fast_json_dumps` and `serialize_context` turn it into the string `"frozendict({...})"`, as they already do for `MappingProxyType`, `UserDict` and `ChainMap`. Template lookups through it work (`{{ d.a }}` renders). View state that holds one would not survive serialization.
- PEP 686 (UTF-8 by default): seven `open()` calls in `python/djust` pass no `encoding=` (`cli.py`, `mixins/jit.py`, `checks/configuration.py`, `deploy_cli.py`). On 3.15 they read and write UTF-8; before it they used the locale encoding. No break.
- The `Programming Language :: Python :: 3.15` classifier is not added, and `release.yml` has no 3.15 row (see the open questions in the pull request).

## Install-tested wheels

Built as `release.yml` builds them (`maturin build --release -i <interpreter>`, no extra features), then installed into a fresh venv with the dependency versions from `uv.lock` and no editable install and no repo on `sys.path`. Each wheel was checked with a smoke script (`import djust, djust._rust`, a Rust template render, a VDOM diff, `RustLiveView` state/render/diff, msgpack round trip, `fast_json_dumps`, and a `LiveView` mounted through `djust.testing.LiveViewTestClient`), then with 695 tests from the installed `djust.tests` package (`rust`, `vdom`, `diff`, `render` and `template_engine` file names, run against a copy of the demo project's settings).

| Wheel | Interpreter | Size | Smoke | Installed-package tests |
|---|---|---|---|---|
| `cp311-cp311-macosx_11_0_arm64` | 3.11.14 | 11.37 MB | pass | 694 passed, 1 skipped |
| `cp312-cp312` | 3.12.13 | 11.35 MB | pass | 694 passed, 1 skipped |
| `cp313-cp313` | 3.13.15 | 11.36 MB | pass | 694 passed, 1 skipped |
| `cp314-cp314` | 3.14.7 | 11.36 MB | pass | 694 passed, 1 skipped |
| `cp314-cp314t` | 3.14.0t | 11.34 MB | pass, GIL stays off | 694 passed, 1 skipped |
| `cp315-cp315` | 3.15.0rc3 | 11.36 MB | pass | 694 passed, 1 skipped |

One test in that set (`test_render_env_per_view_2741::test_every_backend_entry_that_applies_auto_call_applies_the_env`) reads `crates/djust_live/src/lib.rs` relative to the package and cannot run against an installed wheel; it was deselected. The 3.14t venv took its dependencies unpinned because a locked package has no `cp314t` wheel and failed to build.

## abi3

`pyo3/abi3-py311` (the floor is 3.11, not the 3.10 the issue assumed) compiled `crates/` on the first attempt, with no source change. The PyO3 limitations listed in the issue's evaluation (buffer protocol, `datetime` field accessors, type slots) are not used by the crates, or have limited-API fallbacks: nothing in the code base is excluded by abi3.

| | version-specific (`cp312`) | `cp311-abi3` |
|---|---|---|
| Wheel | 11,353,942 bytes | 11,365,100 bytes |
| `_rust` extension in it | 9,142,640 bytes | 9,126,352 bytes |
| SBOM files (`djust.cdx.json`, `dist-info/sboms/`) | present | present, identical size |
| Installs and passes the smoke script and the 695 installed-package tests on | its own interpreter | 3.11.14, 3.12.13, 3.13.15, 3.14.7 and 3.15.0rc3 (rc2 earlier) |
| Installs on free-threaded 3.14t | yes (`cp314t` wheel) | **no**: uv refuses it ("the wheel was built for the stable ABI (`abi3`), which requires a GIL-enabled interpreter") |

One abi3 wheel replaces the GIL-enabled `cp311` to `cp314` wheels of a platform, and a 3.15 or later interpreter needs no new wheel. It does not replace `cp314t`. Wheels per release for the matrix as it is today (21 wheels): 4 abi3 plus the 4 `cp314t`, if the free-threaded wheels stay. The size per wheel does not change, so the saving is the wheel count, not the size.

### Hot paths, abi3 against version-specific

Both extensions are built from the same tree in the same way (release profile, LTO, the only difference being `pyo3/abi3-py311`), installed into separate venvs, and run alternately (3 to 5 rounds each, 15 timing rounds per case per round) by `bench.py`, which times the `djust._rust` calls directly. The ratio is abi3 CPU time divided by version-specific CPU time (this thread's CPU time, which the machine's load disturbs less than wall time). Each cell is `median / best`; above 1.00 is slower under abi3. **The machine was shared and loaded the whole time**: 1-minute load average 308 to 483 for the 3.12 runs, 233 to 266 for 3.13, 180 to 223 for 3.14, 184 to 307 for 3.15, so the absolute times are not reliable and differences under about 5% are inside the noise.

| Case (per call) | 3.12 | 3.13 | 3.14 | 3.15 rc2 |
|---|---|---|---|---|
| `render_template`, 20-row context | 1.05 / 0.98 | 1.05 / 1.03 | 1.04 / 1.02 | 0.94 / 1.00 |
| `render_template`, 500 rows | 0.95 / 1.00 | 1.03 / 1.02 | 1.03 / 1.02 | 0.97 / 1.01 |
| `diff_html`, 500-row table, 2 cells changed | 1.05 / 1.00 | 0.99 / 1.01 | 0.98 / 0.98 | 0.91 / 0.99 |
| `RustLiveView` `update_state` + `render_with_diff`, 500 rows | 0.95 / 0.99 | 1.04 / 1.07 | 1.05 / 1.03 | 0.98 / 1.00 |
| the same, 20 rows | 1.13 / 0.96 | 1.02 / 1.04 | 0.99 / 1.00 | 1.13 / 1.01 |
| `fast_json_dumps`, 500 dicts | 1.16 / 1.09 | 1.13 / 1.19 | 1.16 / 1.17 | 1.11 / 1.16 |
| `serialize_context`, 500 dicts | 1.14 / 1.13 | 1.18 / 1.20 | 1.19 / 1.21 | 1.23 / 1.18 |
| `update_state`, 500 dicts | 1.14 / 1.12 | 1.17 / 1.18 | 1.18 / 1.19 | 1.14 / 1.22 |
| `serialize_msgpack` + `deserialize_msgpack` | 1.14 / 1.00 | 1.04 / 1.02 | 1.03 / 1.00 | 1.00 / 0.98 |

Absolute figures on 3.13, the least loaded set (microseconds per call, median CPU time; the version-specific wheel then abi3): `render_template` small 94.6 then 99.1; 500 rows 11,749 then 12,137; `diff_html` 5,094 then 5,039; `fast_json_dumps` 1,893 then 2,131; `serialize_context` 1,159 then 1,365; `update_state` 1,967 then 2,307.

What it shows: work that stays inside Rust (rendering, diffing, msgpack) is within noise to about 5% slower. Work that walks Python containers from Rust (`fast_json_dumps`, `serialize_context`, `update_state`) is a consistent 12 to 22% slower under abi3 on every interpreter. That is the expected cost: the limited API has no unchecked list, tuple and dict accessors, so each element goes through a function call. In a real update the Python-to-Rust conversion of a large view state is the part that grows, so an app with big state pays more than an app with small state. This benchmark times the extension calls only, not a request, so it says nothing about the effect on end-to-end latency.

### Other things abi3 touches

- Development tooling expects the version-specific file name: `scripts/doctor.sh` (stale-extension check) looks for `python/djust/_rust.cpython-*.so`, and `scripts/pre-push-pytest.sh` copies `_rust.cpython-<tag>-darwin.so`. An abi3 `maturin develop` writes `_rust.abi3.so`. Python tries `.cpython-312-darwin.so` before `.abi3.so`, so a stale version-specific file shadows an abi3 build.
- `tests/`: `python/djust/tests/test_free_threaded_contract_3074.py::test_the_release_workflow_builds_and_verifies_cp314t_wheels` reads `release.yml`; changing the workflow needs the `release-workflow-reviewed` label (`check-release-workflow-deps`) and an update to that test.
- `maturin build --features pyo3/abi3-py311` replaces the `features` list in `pyproject.toml` instead of extending it, so `pyo3/extension-module` has to be repeated on the command line (or the setting moved into `pyproject.toml`). Without it the macOS build still worked.
- `publish.yml` builds a second set of wheels for the same releases (manylinux on Linux). A change to one workflow and not the other would publish different wheel sets.

### abi3t

PyO3 0.29.2 has `abi3t-py315` (the free-threaded stable ABI, PEP 803), which needs CPython 3.15 or later, so 3.14t stays version-specific in any case. Tried on 3.15.0rc3t with `--features pyo3/extension-module,pyo3/abi3t-py315`: the crates compile, the module exports `PyModExport__rust` (PEP 793) instead of `PyInit__rust`, it imports, the GIL stays off, and a template render and a `RustLiveView` render/diff work. maturin 1.12.6 does not know abi3t yet: it warns "Couldn't find the symbol `PyInit__rust`" and names the wheel `cp315-cp315t`, an ordinary version-specific tag, so a real abi3t wheel cannot be produced with it today. Not benchmarked.

### Source distribution on 3.15

`maturin sdist` produces an 8.3 MB sdist (it carries `Cargo.lock`). `pip install` of it into a fresh 3.15.0rc3 venv, with unpinned dependencies, built the extension and imported: what a 3.15 user gets today, since there is no `cp315` wheel on PyPI, and it needs a Rust toolchain (the `autobahn` and `zope-interface` dependencies also built from source there; they need a C compiler).

## What would be needed to flip the 3.15 cell to a gate

The cell is allow-failure through a job-level `continue-on-error` in `.github/workflows/test.yml`. Not flipped here: a 3.15 cell that gates merges would depend on a pre-release interpreter and, for the dev extra, on a pydantic beta, and the green run above is macOS arm64, not the Linux runner. Proposed order: land this, watch one main push run, then remove `continue-on-error: ${{ matrix.python-version == '3.15' }}` once 3.15.0 is final and the cell is green on the runner.
