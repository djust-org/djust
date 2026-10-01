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


def _enable(monkeypatch, tmp_path):
    import djust
    from djust.dev_server import hot_reload_server

    monkeypatch.setattr(hot_reload_server, "is_running", lambda: False)
    monkeypatch.setattr(hot_reload_server, "start", lambda **kwargs: None)
    from django.test import override_settings

    return djust, override_settings(DEBUG=True, BASE_DIR=tmp_path)


def _banner_count(captured):
    return captured.err.count("[HotReload] Hot reload enabled for directories")


@pytest.mark.parametrize(
    "setup",
    [
        "nothing_listens",
        "djust_info_to_non_console_handler",
        "root_console_handler_at_warning",
        "root_console_handler_at_info",
    ],
)
def test_banner_is_on_stderr_exactly_once_whatever_the_logging_config(
    monkeypatch, tmp_path, capsys, caplog, setup
):
    """#3312(5): the banner must not depend on the logging configuration.

    The old ``isEnabledFor(INFO) and hasHandlers()`` guard routed it through
    ``logger.info`` whenever ANY handler existed (``apps.py`` always installs the
    observability one), so a ``djust`` logger at INFO with no console handler, or a
    root console handler at WARNING, printed nothing at all. It is written to
    stderr directly, once, and never also through logging.
    """
    import logging
    import sys

    djust, ctx = _enable(monkeypatch, tmp_path)
    root = logging.getLogger()
    djust_logger = logging.getLogger("djust")
    monkeypatch_level = djust_logger.level
    monkeypatch_root_level = root.level
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(djust_logger, "handlers", [])
    if setup == "djust_info_to_non_console_handler":
        monkeypatch.setattr(djust_logger, "level", djust_logger.level)  # restored on teardown
        djust_logger.setLevel(logging.INFO)
        djust_logger.addHandler(logging.NullHandler())
    elif setup == "root_console_handler_at_warning":
        monkeypatch.setattr(root, "level", root.level)  # restored on teardown
        root.setLevel(logging.INFO)  # logger passes INFO; the console HANDLER drops it
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(logging.WARNING)
        root.addHandler(handler)
    elif setup == "root_console_handler_at_info":
        monkeypatch.setattr(djust_logger, "level", djust_logger.level)  # restored on teardown
        djust_logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(logging.INFO)
        root.addHandler(handler)
    try:
        with ctx:
            djust.enable_hot_reload()
    finally:
        djust_logger.setLevel(monkeypatch_level)
        root.setLevel(monkeypatch_root_level)
        for h in list(root.handlers) + list(djust_logger.handlers):
            if not isinstance(h, logging.NullHandler):
                root.removeHandler(h)
                djust_logger.removeHandler(h)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert _banner_count(captured) == 1
