"""#3298: the ``[HotReload]`` banner must not land on stdout.

With ``DEBUG=True`` and ``watchdog`` installed, ``DjustConfig.ready()`` calls
``enable_hot_reload()`` on every management command. It printed its banner to
stdout, so ``manage.py djust_audit --dump-permissions > permissions.yaml``
began with ``[HotReload] Hot reload enabled for directories: ...`` and the file
was not valid YAML. A command's stdout is its output; diagnostics go to stderr.

The test runs a real management command in a subprocess with DEBUG on, as
``runserver`` and every other command would, and parses what it wrote.
"""

import os
import subprocess
import sys
import textwrap

import pytest

pytest.importorskip("watchdog")
yaml = pytest.importorskip("yaml")

SETTINGS = """\
DEBUG = True
SECRET_KEY = "x"
BASE_DIR = {base!r}
INSTALLED_APPS = ["django.contrib.contenttypes", "django.contrib.auth", "channels", "djust"]
ROOT_URLCONF = "urls3298"
ASGI_APPLICATION = "urls3298.application"
CHANNEL_LAYERS = {{"default": {{"BACKEND": "channels.layers.InMemoryChannelLayer"}}}}
USE_TZ = True
{extra}
"""

RUNNER = textwrap.dedent(
    """\
    import sys
    from django.core.management import execute_from_command_line
    execute_from_command_line(["manage.py", *sys.argv[1:]])
    """
)


def _run(tmp_path, *args, extra=""):
    import djust

    (tmp_path / "s3298.py").write_text(SETTINGS.format(base=str(tmp_path), extra=extra))
    (tmp_path / "urls3298.py").write_text("urlpatterns = []\n")
    pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(djust.__file__)))
    env = dict(os.environ)
    env.pop("PYTEST_CURRENT_TEST", None)  # ready() skips the watcher under pytest
    env["DJANGO_SETTINGS_MODULE"] = "s3298"
    env["DJUST_NO_UPDATE_CHECK"] = "1"
    env["PYTHONPATH"] = os.pathsep.join([str(tmp_path), pkg_root, env.get("PYTHONPATH", "")])
    return subprocess.run(
        [sys.executable, "-c", RUNNER, *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=120,
    )


def test_redirected_command_output_has_no_hot_reload_banner(tmp_path):
    proc = _run(tmp_path, "djust_audit", "--dump-permissions")
    assert proc.returncode == 0, proc.stderr
    assert "[HotReload]" not in proc.stdout
    # What the issue's redirect produced: a file that must parse.
    assert isinstance(yaml.safe_load(proc.stdout), dict)


def test_banner_is_still_shown_on_stderr(tmp_path):
    proc = _run(tmp_path, "djust_audit", "--dump-permissions")
    assert "[HotReload] Hot reload enabled for directories:" in proc.stderr


def test_no_banner_when_auto_enable_is_off(tmp_path):
    extra = 'LIVEVIEW_CONFIG = {"hot_reload_auto_enable": False}'
    proc = _run(tmp_path, "djust_audit", "--dump-permissions", extra=extra)
    assert "[HotReload]" not in proc.stdout + proc.stderr
