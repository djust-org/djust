"""The Django admin under the recommended ``TEMPLATES`` shape (#2872).

With ``DjustTemplateBackend`` first (``APP_DIRS: True``) and
``DjangoTemplates`` as the fallback — the exact shape ``djust new --with-db``
generates and djust.C016's hint recommends — the admin changelist and change
pages used to 500 with ``AttributeError: 'DjustTemplateBackend' object has no
attribute 'select_template'``. The chain: the admin's inclusion tags
(``InclusionAdminNode``, ``django/contrib/admin/templatetags/base.py:40``)
call ``context.template.engine.select_template([names])``; inside a djust
render that engine is ``template_libraries._StubEngine``, which hands the
call to the active backend; the backend implemented ``get_template`` but not
``select_template``. ``/admin/login/`` renders fine, so a login-page smoke
test never saw the break — the tests below drive the changelist and the
change page and assert 200 plus rendered content.

The child also pins the semantics of ``DjustTemplateBackend.select_template``
itself: priority order across ``DIRS``, first EXISTING name wins, a
``TemplateDoesNotExist`` listing every name when none exists, and the
empty-list case — the contract Django's ``Engine.select_template`` gives the
three consumers that call it (``InclusionAdminNode``, an ``inclusion_tag``
registered with a list of names, ``{% include [...] %}``).

``settings.configure`` mutates process globals, so the project lives in a
child process (the ``test_django_template_suite_2517`` convention); the
repo's ``python/`` goes FIRST on ``PYTHONPATH`` so a worktree run imports the
checkout under test, not an installed djust (#2533).
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

pytest.importorskip("django")

ROOT = pathlib.Path(__file__).resolve().parents[2]
PYTHON_DIR = ROOT / "python"

CHILD = r"""
import json
import pathlib

import django
from django.conf import settings

# The exact TEMPLATES shape `djust new --with-db` generates
# (scaffolding/templates.py: TEMPLATES + ADMIN_TEMPLATE_BACKEND, #2872): the
# djust backend FIRST with APP_DIRS and the four standard context processors,
# DjangoTemplates as the fallback with APP_DIRS=False + app_directories
# loaders (the admin's admin.E403 check requires that entry to exist).
settings.configure(
    SECRET_KEY="x",
    DEBUG=False,
    ALLOWED_HOSTS=["*"],
    ROOT_URLCONF=__name__,
    INSTALLED_APPS=[
        "django.contrib.admin",
        "django.contrib.auth",
        "django.contrib.contenttypes",
        "django.contrib.sessions",
        "django.contrib.messages",
        "djust",
    ],
    MIDDLEWARE=[
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.middleware.common.CommonMiddleware",
        "django.middleware.csrf.CsrfViewMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "django.contrib.messages.middleware.MessageMiddleware",
    ],
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    TEMPLATES=[
        {
            "BACKEND": "djust.template_backend.DjustTemplateBackend",
            "DIRS": [],
            "APP_DIRS": True,
            "OPTIONS": {
                "context_processors": [
                    "django.template.context_processors.debug",
                    "django.template.context_processors.request",
                    "django.contrib.auth.context_processors.auth",
                    "django.contrib.messages.context_processors.messages",
                ]
            },
        },
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [],
            "APP_DIRS": False,
            "OPTIONS": {
                "context_processors": [
                    "django.template.context_processors.debug",
                    "django.template.context_processors.request",
                    "django.contrib.auth.context_processors.auth",
                    "django.contrib.messages.context_processors.messages",
                ],
                "loaders": [
                    "django.template.loaders.app_directories.Loader",
                ],
            },
        },
    ],
)

django.setup()

from django.contrib import admin
from django.core.management import call_command
from django.urls import path

urlpatterns = [path("admin/", admin.site.urls)]

call_command("migrate", verbosity=0)

results = {}

# --- A. The issue: admin pages under the recommended shape ----------------

from django.contrib.auth.models import User
from django.test import Client

User.objects.create_superuser("admin", "admin@example.com", "pw")
client = Client(raise_request_exception=False)

# Control: the login page renders (asserted while logged OUT — an
# authenticated GET of /admin/login/ is a 302 redirect, not a page).
results["login"] = client.get("/admin/login/").status_code

client.force_login(User.objects.get(username="admin"))

# Control: the admin index always rendered, which is why an /admin/ smoke
# test passed while the rest of the admin was broken.
results["index"] = client.get("/admin/").status_code

changelist = client.get("/admin/auth/user/")
results["changelist"] = changelist.status_code
if changelist.status_code == 200:
    results["changelist_lists_user"] = "admin@example.com" in changelist.content.decode()

change = client.get("/admin/auth/user/1/change/")
results["change"] = change.status_code

# --- B. select_template semantics on the backend ---------------------------

import tempfile

from djust.template_backend import DjustTemplateBackend

d1 = tempfile.mkdtemp()
d2 = tempfile.mkdtemp()
pathlib.Path(d1, "shared.html").write_text("D1-SHARED")
pathlib.Path(d2, "shared.html").write_text("D2-SHARED")
pathlib.Path(d2, "only_second.html").write_text("D2-ONLY")

backend = DjustTemplateBackend(
    params={"NAME": "djust", "DIRS": [d1, d2], "APP_DIRS": False, "OPTIONS": {}}
)


def probe(key, fn):
    try:
        results[key] = fn()
    except Exception as exc:  # noqa: BLE001 — the probe records, the test asserts
        results[key] = f"EXC {type(exc).__name__}: {exc}"


probe("select_is_djust_template", lambda: type(backend.select_template(["shared.html"])).__name__)
probe("select_first_dir_wins", lambda: backend.select_template(["shared.html"]).render(context=None))
probe(
    "select_first_existing_name_wins",
    lambda: backend.select_template(["nope1.html", "only_second.html"]).render(context=None),
)

from django.template import TemplateDoesNotExist


def all_missing():
    try:
        backend.select_template(["nope1.html", "nope2.html"])
        return "no exception"
    except TemplateDoesNotExist as exc:
        return "TemplateDoesNotExist: " + str(exc)


def empty_list():
    try:
        backend.select_template([])
        return "no exception"
    except TemplateDoesNotExist as exc:
        return "TemplateDoesNotExist: " + str(exc)


probe("select_all_missing", all_missing)
probe("select_empty", empty_list)

print("RESULTS " + json.dumps(results))
"""


def run_child() -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(PYTHON_DIR), env.get("PYTHONPATH", "")) if p
    )
    return subprocess.run(
        [sys.executable, "-c", CHILD],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=env,
        timeout=180,
        check=False,
    )


def child_results(proc: subprocess.CompletedProcess) -> dict:
    for line in proc.stdout.splitlines():
        if line.startswith("RESULTS "):
            return json.loads(line[len("RESULTS ") :])
    raise AssertionError(
        f"no RESULTS line; rc={proc.returncode}\nstdout:\n{proc.stdout[-2000:]}\n"
        f"stderr:\n{proc.stderr[-3000:]}"
    )


class TestAdminChangelistUnderRecommendedShape:
    """The issue's reproducer: admin pages 500 while /admin/ renders."""

    def test_child_completes_and_admin_pages_render(self):
        proc = run_child()
        results = child_results(proc)  # asserts the child ran to completion

        # The known-good control pages from the issue.
        assert results["index"] == 200, f"index: {results}"
        assert results["login"] == 200, f"login: {results}"

        # The broken pages.
        assert results["changelist"] == 200, (
            f"changelist returned {results.get('changelist')!r} — the full results were {results}"
        )
        assert results.get("changelist_lists_user") is True, f"changelist content: {results}"
        assert results["change"] == 200, f"change page: {results}"


class TestSelectTemplateSemantics:
    """The backend method's contract, as Django's Engine defines it."""

    def test_backend_select_template_semantics(self):
        proc = run_child()
        results = child_results(proc)

        assert results["select_is_djust_template"] == "DjustTemplate", f"{results}"
        assert results["select_first_dir_wins"] == "D1-SHARED", f"{results}"
        assert results["select_first_existing_name_wins"] == "D2-ONLY", f"{results}"
        assert results["select_all_missing"].startswith(
            "TemplateDoesNotExist: nope1.html, nope2.html"
        ), f"{results}"
        assert results["select_empty"].startswith(
            "TemplateDoesNotExist: No template names provided"
        ), f"{results}"
