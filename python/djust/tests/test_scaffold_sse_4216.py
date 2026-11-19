"""A project made with ``djust new`` serves the SSE transport (#4216).

The scaffold routed the page (WebSocket and the HTTP fallback) but not djust's
SSE views, so a client that fell back to SSE got a 404 at ``/djust/sse/<id>/``.
2.0 serves a view the same way over WebSocket, SSE and the HTTP fallback, so a
fresh project routes ``djust.sse.sse_urlpatterns`` under ``djust/``, where the
client looks by default.

The URLconf has two writers: ``URLS_PY`` (every project) and the
``--from-schema`` writer, which replaces the URLconf wholesale. Each case
generates a real project in a temporary directory, runs its ``manage.py check``
and then, in the project's own process, resolves the SSE URLs and mounts the
index view over SSE with ``djust.testing``'s live page, then sends one event
over that session and reads the re-render it answers with.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from djust.scaffolding import generator

APP = "ssechild"

SCHEMA = {"models": [{"name": "Post", "fields": [{"name": "title", "type": "string"}]}]}

#: Per variant: an event its scaffolded view handles, its parameters, and text
#: that only the re-render after that event contains.
EVENTS = {
    "default": ("add_item", {"name": "Added over SSE 4216"}, "Added over SSE 4216"),
    "bare": ("ping", {}, "1 round trip "),
    "schema": ("create", {"title": "Posted over SSE 4216"}, "Posted over SSE 4216"),
}

PROBE = """
import asyncio, json, os, django
os.environ["DJANGO_SETTINGS_MODULE"] = "%(app)s.settings"
django.setup()
from django.test.utils import setup_test_environment
setup_test_environment()
from django.core.management import call_command
call_command("migrate", run_syncdb=True, verbosity=0)

from django.urls import Resolver404, resolve
from djust.sse import DjustSSEMessageView, DjustSSEStreamView

out = {}
for key, path, view in (
    ("stream", "/djust/sse/probe/", DjustSSEStreamView),
    ("message", "/djust/sse/probe/message/", DjustSSEMessageView),
):
    try:
        out[key] = getattr(resolve(path).func, "view_class", None) is view
    except Resolver404:
        out[key] = False

from djust.testing import close_live_pages, open_live_page

page = open_live_page("/")


async def mount_over_sse():
    sse = await page.connect_sse()
    try:
        out["mount_type"] = sse.mount.get("type")
        out["mount_has_html"] = bool(sse.mount.get("html"))
        # One event round trip over the same session: POST to message/, answer on the stream.
        answer = await sse.send_event(%(event)r, %(params)r)
        out["event_type"] = answer.get("type")
        out["event_answer"] = json.dumps(answer)
    finally:
        await sse.close()


try:
    asyncio.run(mount_over_sse())
except AssertionError as exc:
    out["mount_error"] = str(exc)[:500]
finally:
    close_live_pages()
print("PROBE_JSON", json.dumps(out))
"""


def _env(project):
    env = dict(os.environ, DJANGO_SETTINGS_MODULE="%s.settings" % APP)
    env["PYTHONPATH"] = os.pathsep.join([str(project), env.get("PYTHONPATH", "")])
    return env


def _run(project, *args):
    return subprocess.run(
        [sys.executable, *args],
        cwd=project,
        env=_env(project),
        capture_output=True,
        text=True,
        timeout=180,
    )


def _generate(tmp_path, variant):
    if variant == "schema":
        schema_path = tmp_path / "schema.json"
        schema_path.write_text(json.dumps(SCHEMA))
        project = generator.generate_project(
            APP, target_dir=str(tmp_path), auto_setup=False, from_schema=str(schema_path)
        )
        made = _run(project, "manage.py", "makemigrations", APP)
        assert made.returncode == 0, made.stdout + made.stderr
        return project
    return generator.generate_project(
        APP, target_dir=str(tmp_path), auto_setup=False, bare=(variant == "bare")
    )


@pytest.mark.parametrize("variant", ["default", "bare", "schema"])
def test_a_scaffolded_project_serves_sse(tmp_path, variant):
    project = _generate(tmp_path, variant)

    check = _run(project, "manage.py", "check")
    assert check.returncode == 0, check.stdout + check.stderr

    event, params, marker = EVENTS[variant]
    probe = _run(project, "-c", PROBE % {"app": APP, "event": event, "params": params})
    lines = [line for line in probe.stdout.splitlines() if line.startswith("PROBE_JSON")]
    assert lines, "the probe printed no result:\n%s\n%s" % (probe.stdout, probe.stderr)
    out = json.loads(lines[-1].split(" ", 1)[1])

    assert out["stream"] is True, out
    assert out["message"] is True, out
    assert "mount_error" not in out, out["mount_error"]
    assert out["mount_type"] == "mount", out
    assert out["mount_has_html"] is True, out
    assert out["event_type"] in ("patch", "html_update"), out
    assert marker in out["event_answer"], out
    assert "sse_urlpatterns" in (project / APP / "urls.py").read_text()
