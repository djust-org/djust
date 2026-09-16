"""Every ``djust new`` flag combination must produce a working project (#2876).

Two nets:

1. A fast in-process guard: context VALUES substituted into the ``%``-format
   scaffold templates must not carry ``%%``/``{%%`` escaping. Values are
   inserted verbatim — escaping belongs only in the format templates — and a
   value that carries it ships broken output (the shared root cause behind
   the ``--with-streaming`` SyntaxError, the ``--with-auth`` nav fragment and
   the ``--with-presence``/``--with-streaming`` template extras in #2876).
2. The end-to-end net: for every boolean flag combination (2^5 grid) and the
   ``--from-schema`` shapes, generate a project with ``auto_setup=False``,
   run ``manage.py check``, then render ``/`` through the Django test client
   (the RENDER_PROBE pattern from ``test_scaffold_setup_environment.py``).
   This is what would have caught all four #2876 failures.
"""

import itertools
import json
import os
import subprocess
import sys

import pytest

from djust.scaffolding import generator

FLAG_AXES = ("bare", "with_auth", "with_db", "with_presence", "with_streaming")

BOOLEAN_COMBOS = [
    dict(zip(FLAG_AXES, values)) for values in itertools.product((False, True), repeat=5)
]

SCHEMA_STRING = {"models": [{"name": "Post", "fields": [{"name": "title", "type": "string"}]}]}
# No text-like fields: the #2876 `def create(self, , **kwargs):` trigger.
SCHEMA_INT_ONLY = {"models": [{"name": "Counter", "fields": [{"name": "n", "type": "integer"}]}]}

SCHEMA_CASES = [
    pytest.param({"__schema__": SCHEMA_STRING}, id="schema-string"),
    pytest.param({"__schema__": SCHEMA_INT_ONLY}, id="schema-int-only"),
    pytest.param({"__schema__": SCHEMA_STRING, "with_auth": True}, id="schema-string+auth"),
]


def _combo_id(flags):
    parts = [name for name in FLAG_AXES if flags.get(name)]
    return "+".join(parts) if parts else "default"


# --- net 1: escaping-context guard (fast, in-process) ------------------------


@pytest.mark.parametrize("flags", BOOLEAN_COMBOS, ids=_combo_id)
def test_context_values_carry_no_percent_escaping(flags):
    ctx = generator._build_context("child", **flags)
    for key, value in ctx.items():
        if isinstance(value, str):
            assert "%%" not in value, (
                "ctx[%r] carries '%%' escaping but is substituted, not %%-formatted "
                "— it would ship verbatim into the generated file (#2876)" % key
            )


# --- net 2: every flag combination checks clean and renders ------------------

PROBE = """
import json, os, django
os.environ["DJANGO_SETTINGS_MODULE"] = "child.settings"
django.setup()
from django.conf import settings
settings.ALLOWED_HOSTS = ["*"]
from django.core.management import call_command
call_command("migrate", run_syncdb=True, verbosity=0)

markers = {}
from django.test import Client
client = Client()
resp = client.get("/")
html = resp.content.decode()
markers["status"] = resp.status_code
markers["doctype"] = "<!DOCTYPE html>" in html
markers["djroot"] = "dj-root" in html
markers["stream_container"] = 'dj-stream="feed"' in html
markers["login_link"] = 'href="/login/"' in html

# Authenticated nav branch: the logout route resolves and the username renders.
try:
    from django.contrib.auth.models import User
    User.objects.create_user("sally", password="pw")
    client.force_login(User.objects.get(username="sally"))
    auth_html = client.get("/").content.decode()
    markers["authenticated_nav"] = (
        'href="/logout/"' in auth_html and ">sally<" in auth_html
    )
except Exception as exc:  # noqa: BLE001 — reported as a failed marker, not a crash
    markers["authenticated_nav"] = "error: %s" % exc

# Presence render path with a real backend record. The scaffolded view uses
# PresenceMixin's default key: "<module>.<ClassName>".
try:
    from djust.presence import PresenceManager
    PresenceManager.join_presence("child.views.ChildView", "u1", {"name": "Tester"})
    pres_html = client.get("/").content.decode()
    markers["presence_rendered"] = (
        "Tester" in pres_html and "1 user online" in pres_html
    )
except Exception as exc:  # noqa: BLE001
    markers["presence_rendered"] = "error: %s" % exc

print("PROBE_JSON", json.dumps(markers))
"""


def _generate(tmp_path, flags):
    schema = flags.pop("__schema__", None)
    if schema is not None:
        schema_path = tmp_path / "schema.json"
        schema_path.write_text(json.dumps(schema))
        return generator.generate_project(
            "child",
            target_dir=str(tmp_path),
            auto_setup=False,
            from_schema=str(schema_path),
            **flags,
        )
    return generator.generate_project("child", target_dir=str(tmp_path), auto_setup=False, **flags)


def _check(project):
    env = dict(os.environ, DJANGO_SETTINGS_MODULE="child.settings")
    env["PYTHONPATH"] = os.pathsep.join([str(project), env.get("PYTHONPATH", "")])
    return subprocess.run(
        [sys.executable, "manage.py", "check"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _probe(project):
    env = dict(os.environ, DJANGO_SETTINGS_MODULE="child.settings")
    env["PYTHONPATH"] = os.pathsep.join([str(project), env.get("PYTHONPATH", "")])
    return subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _markers(probe):
    for line in probe.stdout.splitlines():
        if line.startswith("PROBE_JSON"):
            return json.loads(line.split(" ", 1)[1])
    pytest.fail("probe produced no PROBE_JSON line:\n%s\n%s" % (probe.stdout, probe.stderr))


def _assert_combo(tmp_path, flags):
    schema = flags.get("__schema__")
    project = _generate(tmp_path, flags)
    if flags.get("with_db") or schema is not None:
        env = dict(os.environ, DJANGO_SETTINGS_MODULE="child.settings")
        env["PYTHONPATH"] = os.pathsep.join([str(project), env.get("PYTHONPATH", "")])
        subprocess.run(
            [sys.executable, "manage.py", "makemigrations", "child"],
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )

    check = _check(project)
    assert check.returncode == 0, check.stdout + check.stderr

    markers = _markers(_probe(project))
    assert markers["status"] == 200, markers
    assert markers["djroot"], markers
    # Demo (non-bare) projects ship the themed base template; a page without
    # it (no DOCTYPE) means the base failed to render — the #2876 --with-auth
    # failure mode rendered 200 as a bare fragment.
    if not flags.get("bare"):
        assert markers["doctype"], markers
    demo = not flags.get("bare")
    if flags.get("with_auth") and demo:
        assert markers["login_link"], json.dumps(markers, indent=1)
        assert markers["authenticated_nav"] is True, markers
    if flags.get("with_presence") and demo:
        assert markers["presence_rendered"] is True, markers
    if flags.get("with_streaming") and demo:
        assert markers["stream_container"], markers


@pytest.mark.parametrize("flags", BOOLEAN_COMBOS, ids=_combo_id)
def test_flag_combo_checks_and_renders(tmp_path, flags):
    _assert_combo(tmp_path, dict(flags))


@pytest.mark.parametrize("flags", SCHEMA_CASES)
def test_from_schema_checks_and_renders(tmp_path, flags):
    _assert_combo(tmp_path, dict(flags))
