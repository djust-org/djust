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
    python/djust/djust.cdx.json — SBOM whose root component is djust (#3184)
"""

import json
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


def _write_sbom(directory, version, name="djust"):
    """The shape `djust.assets.sbom.to_cyclonedx` writes, trimmed to what the
    audit reads."""
    path = directory / "python" / "djust" / "djust.cdx.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "metadata": {
            "component": {
                "bom-ref": "root:%s" % name,
                "name": name,
                "type": "application",
                "version": version,
            }
        },
        "components": [],
    }
    path.write_text(json.dumps(document, indent=2))
    return path


def _build_fixture(
    directory, *, py_version, cargo_version, uv_djust, cargo_crates, sbom_version=None
):
    _write(directory, "pyproject.toml", _pyproject(py_version))
    _write(directory, "Cargo.toml", _cargo_toml(cargo_version))
    _write(directory, "uv.lock", _uv_lock(uv_djust))
    directory.joinpath("Cargo.lock").write_text(_cargo_lock(cargo_crates))
    _write_sbom(directory, py_version if sbom_version is None else sbom_version)


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


_IN_SYNC = dict(
    py_version="1.3.0rc4",
    cargo_version="1.3.0-rc.4",
    uv_djust="1.3.0rc4",
    cargo_crates={"djust_core": "1.3.0-rc.4"},
)


class TestSbomVersion:
    """#3184: the shipped SBOM must name the version being released."""

    def test_stale_sbom_version_fails(self, tmp_path):
        """The 1.3.0rc4 cut: manifests bumped, SBOM still on rc3."""
        _build_fixture(tmp_path, **_IN_SYNC, sbom_version="1.3.0rc3")
        code, out = _run(tmp_path)
        assert code == 1, f"expected exit 1, got {code}: {out}"
        assert "djust.cdx.json" in out
        assert "1.3.0rc3" in out and "1.3.0rc4" in out

    def test_sbom_in_sync_passes(self, tmp_path):
        _build_fixture(tmp_path, **_IN_SYNC)
        code, out = _run(tmp_path)
        assert code == 0, f"expected exit 0, got {code}: {out}"

    def test_missing_sbom_is_a_usage_error(self, tmp_path):
        _build_fixture(tmp_path, **_IN_SYNC)
        (tmp_path / "python" / "djust" / "djust.cdx.json").unlink()
        code, out = _run(tmp_path)
        assert code == 2, f"expected exit 2, got {code}: {out}"
        assert "djust.cdx.json" in out and "not found" in out.lower()

    def test_sbom_whose_root_is_not_djust_is_a_usage_error(self, tmp_path):
        _build_fixture(tmp_path, **_IN_SYNC)
        _write_sbom(tmp_path, "1.3.0rc4", name="someapp")
        code, out = _run(tmp_path)
        assert code == 2, f"expected exit 2, got {code}: {out}"
        assert "someapp" in out

    def test_make_version_regenerates_the_sbom(self):
        """`make version` is what keeps the SBOM in sync; pin that its
        recipe runs the distribution SBOM writer (#3184)."""
        makefile = (_SELF.parents[1] / "Makefile").read_text()
        recipe = makefile.split("\nversion:", 1)[1].split("\n.PHONY:", 1)[0]
        assert "-m djust.assets.sbom --distribution" in recipe
