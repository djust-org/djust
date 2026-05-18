"""Tests for scripts/check-lockfile-versions.py — #1498.

Mirrors tests/test_check_adr_status.py: temp-dir fixtures driven through
the script via subprocess with the `--root` override. Each `*_fails`
test is tautology-guarded (Action #1200 / #254) — it asserts BOTH the
exit code AND a specific substring in the message, so it cannot pass if
the script merely exits 1 for an unrelated reason.

Fixture shape per temp dir:
    pyproject.toml  — PEP 440 djust version
    Cargo.toml      — [workspace.package] version (Cargo form)
    uv.lock         — one editable `djust` [[package]] self-entry
    Cargo.lock      — N `djust*` workspace-crate [[package]] entries
"""

import pathlib
import subprocess
import sys
import tempfile
import textwrap

_SELF = pathlib.Path(__file__).resolve()
_LINTER = _SELF.parents[1] / "scripts" / "check-lockfile-versions.py"


def _write(directory, name, content):
    p = directory / name
    p.write_text(textwrap.dedent(content).lstrip("\n"))
    return p


def _pyproject(version):
    return f"""
        [project]
        name = "djust"
        version = "{version}"
        """


def _cargo_toml(version):
    return f"""
        [workspace]
        members = ["crates/djust_core"]

        [workspace.package]
        version = "{version}"
        edition = "2021"
        """


def _uv_lock(djust_version):
    """A minimal uv.lock with the editable djust self-entry."""
    return f"""
        version = 1

        [[package]]
        name = "channels"
        version = "4.0.0"
        source = {{ registry = "https://pypi.org/simple" }}

        [[package]]
        name = "djust"
        version = "{djust_version}"
        source = {{ editable = "." }}
        """


def _cargo_lock(crate_versions):
    """Build a Cargo.lock from {crate_name: version} pairs, plus one
    unrelated third-party crate that must be ignored."""
    blocks = [
        textwrap.dedent(
            """
            [[package]]
            name = "serde"
            version = "1.0.200"
            source = "registry+https://github.com/rust-lang/crates.io-index"
            """
        ).strip()
    ]
    for name, version in crate_versions.items():
        blocks.append(
            textwrap.dedent(
                f"""
                [[package]]
                name = "{name}"
                version = "{version}"
                """
            ).strip()
        )
    return "version = 3\n\n" + "\n\n".join(blocks) + "\n"


def _build_fixture(directory, *, py_version, cargo_version, uv_djust, cargo_crates):
    _write(directory, "pyproject.toml", _pyproject(py_version))
    _write(directory, "Cargo.toml", _cargo_toml(cargo_version))
    _write(directory, "uv.lock", _uv_lock(uv_djust))
    directory.joinpath("Cargo.lock").write_text(_cargo_lock(cargo_crates))


def _run(root):
    """Run the audit via subprocess against a fixture root."""
    result = subprocess.run(
        [sys.executable, str(_LINTER), "--root", str(root)],
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout


class TestCheckLockfileVersions:
    """Core checks for the lockfile self-entry version audit."""

    def test_in_sync_passes(self):
        """All lockfile self-entries match their manifests → exit 0."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            _build_fixture(
                d,
                py_version="1.0.0rc1",
                cargo_version="1.0.0-rc.1",
                uv_djust="1.0.0rc1",
                cargo_crates={
                    "djust": "1.0.0-rc.1",
                    "djust_core": "1.0.0-rc.1",
                    "djust_vdom": "1.0.0-rc.1",
                },
            )
            code, out = _run(d)
            assert code == 0, f"expected exit 0, got {code}: {out}"
            assert "OK" in out

    def test_stale_uv_lock_self_entry_fails(self):
        """Stale uv.lock djust self-entry (#1487 shape) → exit 1.

        pyproject 1.0.0rc1 but uv.lock djust still 0.9.7.
        """
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            _build_fixture(
                d,
                py_version="1.0.0rc1",
                cargo_version="1.0.0-rc.1",
                uv_djust="0.9.7",
                cargo_crates={
                    "djust": "1.0.0-rc.1",
                    "djust_core": "1.0.0-rc.1",
                },
            )
            code, out = _run(d)
            assert code == 1, f"expected exit 1, got {code}: {out}"
            assert "uv.lock" in out
            assert "0.9.7" in out
            assert "1.0.0rc1" in out

    def test_stale_cargo_lock_crate_fails(self):
        """One Cargo.lock crate behind Cargo.toml → exit 1."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            _build_fixture(
                d,
                py_version="1.0.0rc1",
                cargo_version="1.0.0-rc.1",
                uv_djust="1.0.0rc1",
                cargo_crates={
                    "djust": "1.0.0-rc.1",
                    "djust_core": "0.9.7",  # stale
                    "djust_vdom": "1.0.0-rc.1",
                },
            )
            code, out = _run(d)
            assert code == 1, f"expected exit 1, got {code}: {out}"
            assert "Cargo.lock" in out
            assert "djust_core" in out

    def test_missing_lockfile_usage_error(self):
        """A missing lockfile → exit 2 usage error."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            _write(d, "pyproject.toml", _pyproject("1.0.0rc1"))
            _write(d, "Cargo.toml", _cargo_toml("1.0.0-rc.1"))
            _write(d, "uv.lock", _uv_lock("1.0.0rc1"))
            # No Cargo.lock written.
            code, out = _run(d)
            assert code == 2, f"expected exit 2, got {code}: {out}"
            assert "Cargo.lock" in out
            assert "not found" in out.lower()

    def test_dynamic_crate_discovery(self):
        """A new djust* crate not in any hard-coded list is still checked.

        Fixture has `djust_newthing` stale; the script must discover and
        flag it (exit 1) — proving crate names are scanned, not hard-coded.
        """
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            _build_fixture(
                d,
                py_version="1.0.0rc1",
                cargo_version="1.0.0-rc.1",
                uv_djust="1.0.0rc1",
                cargo_crates={
                    "djust": "1.0.0-rc.1",
                    "djust_core": "1.0.0-rc.1",
                    "djust_newthing": "0.9.7",  # new crate, stale
                },
            )
            code, out = _run(d)
            assert code == 1, f"expected exit 1, got {code}: {out}"
            assert "djust_newthing" in out

    def test_real_repo_passes(self):
        """Dogfood gate (Action #1060): the real repo lockfiles pass.

        Runs against the repo root after the uv.lock self-entry fix
        lands in this PR.
        """
        result = subprocess.run(
            [sys.executable, str(_LINTER)],
            capture_output=True,
            text=True,
            cwd=str(_SELF.parents[1]),
        )
        assert result.returncode == 0, (
            f"real repo lockfiles must be in sync: {result.stdout}{result.stderr}"
        )
