"""Missing components extras produce actionable startup and import errors."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("missing", ["markdown", "nh3"])
@pytest.mark.parametrize("entrypoint", ["ready", "import"])
def test_missing_components_dependency_names_install_extra(missing, entrypoint):
    root = Path(__file__).resolve().parents[3]
    code = """
import sys
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
settings.configure(SECRET_KEY="test", INSTALLED_APPS=[])
sys.modules[sys.argv[1]] = None
try:
    if sys.argv[2] == "ready":
        import django
        from djust.components.apps import DjustComponentsConfig
        from djust import components
        DjustComponentsConfig("djust.components", components).ready()
    else:
        from djust.components.components import Markdown
except ImproperlyConfigured as exc:
    assert sys.argv[1] in str(exc), str(exc)
    assert 'pip install "djust[components]"' in str(exc), str(exc)
    assert isinstance(exc.__cause__, ModuleNotFoundError)
else:
    raise AssertionError("missing dependency did not fail")
"""
    result = subprocess.run(
        [sys.executable, "-c", code, missing, entrypoint],
        env={**os.environ, "PYTHONPATH": os.pathsep.join([str(root / "python"), str(root)])},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
