"""The free-threaded (cp314t) contract (#3074).

djust publishes wheels for free-threaded CPython 3.14t. That only makes sense
while four things hold, and each is pinned here:

* the Rust extension declares ``#[pymodule(gil_used = false)]``, so importing
  it does not make CPython turn the GIL back on;
* ``orjson`` (no free-threaded build) stays out of djust's core dependencies,
  and djust imports without it;
* the release workflow builds a ``3.14t`` wheel on every platform family and
  verifies the GIL stays off after importing it;
* on a free-threaded interpreter, the GIL really is off while djust runs.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _free_threaded_build() -> bool:
    return bool(sysconfig.get_config_var("Py_GIL_DISABLED"))


def test_the_extension_declares_that_it_does_not_need_the_gil():
    lib = (ROOT / "crates" / "djust_live" / "src" / "lib.rs").read_text()
    assert "#[pymodule(gil_used = false)]" in lib


def test_orjson_is_not_a_core_dependency():
    text = (ROOT / "pyproject.toml").read_text()
    deps = text.split("dependencies = [", 1)[1].split("]", 1)[0]
    names = re.findall(r'^\s*"([A-Za-z0-9_.\-]+)', deps, re.M)
    assert names, "could not read [project].dependencies"
    assert "orjson" not in {n.lower() for n in names}


def test_djust_imports_and_serialises_without_orjson():
    """A free-threaded install has no orjson; djust must still work."""
    code = (
        "import importlib.machinery as m, sys\n"
        "class Hide(m.PathFinder):\n"
        "    @classmethod\n"
        "    def find_spec(cls, name, path=None, target=None):\n"
        "        if name == 'orjson' or name.startswith('orjson.'):\n"
        "            return None\n"
        "        return m.PathFinder.find_spec(name, path, target)\n"
        "sys.meta_path[:] = [Hide if f is m.PathFinder else f for f in sys.meta_path]\n"
        "import django\n"
        "from django.conf import settings\n"
        "settings.configure(INSTALLED_APPS=['django.contrib.contenttypes'])\n"
        "django.setup()\n"
        "import djust, djust.serialization, djust.push\n"
        "from djust.serialization import HAS_ORJSON, fast_json_loads\n"
        "assert HAS_ORJSON is False\n"
        "assert fast_json_loads('{\"a\": [1, 2]}') == {'a': [1, 2]}\n"
        "print('ok')\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env={
            "PYTHONPATH": str(ROOT / "python"),
            "PATH": "",
            # Windows needs SYSTEMROOT to start Python at all.
            **({"SYSTEMROOT": os.environ["SYSTEMROOT"]} if "SYSTEMROOT" in os.environ else {}),
        },
        timeout=120,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().endswith("ok")


def test_the_release_workflow_builds_and_verifies_cp314t_wheels():
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    oses = re.findall(
        r"-\s*os:\s*['\"]?([\w.-]+)['\"]?\s*\n\s*python-version:\s*['\"]?3\.14t['\"]?", wf
    )
    assert {"ubuntu-latest", "windows-latest"} <= set(oses)
    assert any(o.startswith("macos") for o in oses)
    assert "if: matrix.python-version == '3.14t'" in wf
    assert "sys._is_gil_enabled()" in wf


@pytest.mark.skipif(not _free_threaded_build(), reason="needs a free-threaded (3.13t/3.14t) build")
def test_the_gil_is_off_while_djust_runs():
    # Under PYTHON_GIL=0 (the CI subset step) this cannot fail; the real
    # gate is CI's import check without that override. It still catches a
    # free-threaded run that forgot the override and got the GIL back.
    import djust._rust  # noqa: F401
    import djust.websocket  # noqa: F401

    assert not sys._is_gil_enabled()
