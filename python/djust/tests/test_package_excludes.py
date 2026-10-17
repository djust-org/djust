"""The wheel and sdist leave out djust's own tests and the client's source
modules. CI builds both and lists them (``scripts/check-package-contents.py``);
this pins the excludes and the checker's rules without a build.
"""

from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _checker():
    spec = importlib.util.spec_from_file_location(
        "check_package_contents", REPO / "scripts" / "check-package-contents.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_maturin_excludes_tests_and_client_sources():
    exclude = tomllib.loads((REPO / "pyproject.toml").read_text())["tool"]["maturin"]["exclude"]
    assert "**/djust/tests/**" in exclude
    assert "**/djust/static/djust/src/**" in exclude


def test_checker_flags_tests_and_sources(tmp_path):
    import zipfile

    wheel = tmp_path / "djust-0-cp312-cp312-linux_x86_64.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        for name in (
            "djust/static/djust/client.js",
            "djust/static/djust/client.min.js",
            "djust/tests/test_x.py",
            "djust/static/djust/src/00-namespace.js",
        ):
            zf.writestr(name, "")
    errors = _checker().check(str(wheel))
    assert len(errors) == 2
    assert any("djust/tests/test_x.py" in e for e in errors)
    assert any("src/00-namespace.js" in e for e in errors)


def test_checker_requires_the_bundles(tmp_path):
    import tarfile

    sdist = tmp_path / "djust-0.tar.gz"
    with tarfile.open(sdist, "w:gz") as tf:
        info = tarfile.TarInfo("djust-0/python/djust/__init__.py")
        tf.addfile(info)
    errors = _checker().check(str(sdist))
    assert any("missing djust/static/djust/client.js" in e for e in errors)


def test_component_hooks_are_read_from_the_shipped_bundle():
    """``component_client``'s ``hook_shipped`` scans client.js, which ships,
    not the source modules, which do not."""
    source = (REPO / "python/djust/theming/gallery/component_registry.py").read_text()
    body = source.split("def _client_hook_sources", 1)[1].split("\ndef ", 1)[0]
    assert '"client.js"' in body
    assert '"src"' not in body
