"""``djust init`` completes the project's ``TEMPLATES`` (#2884).

A project with a ``DjangoTemplates`` entry gains the documented shape:
``DjustTemplateBackend`` first, the project's own entries after it as the
fallback. A TEMPLATES the project customised is never rewritten: the djust
entry mirrors it, the user's entries stay byte-for-byte, and any shape init
cannot read with certainty is reported as ATTENTION with the snippet to add.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from djust.scaffolding import init_project as init
from djust.scaffolding import templates as T
from djust.tests.test_djust_init import make_project

FIXTURES = Path(__file__).parent / "fixtures"
DJANGO_BACKEND = "django.template.backends.django.DjangoTemplates"
DJUST_BACKEND = "djust.template_backend.DjustTemplateBackend"


def _write_settings(root, text):
    path = root / "mysite" / "settings.py"
    path.write_text(text)
    return path


def _project(tmp_path, templates_source, *, extra=""):
    """A project whose settings are ``templates_source`` plus ``extra``."""
    make_project(tmp_path)
    return _write_settings(
        tmp_path,
        'from pathlib import Path\nBASE_DIR = Path("/base")\n'
        'INSTALLED_APPS = ["django.contrib.auth"]\n%s\n%s' % (templates_source, extra),
    )


def _evaluate(path):
    """The settings module's namespace after running it, as Django would."""
    namespace = {"__file__": str(path)}
    text = path.read_bytes().decode("utf-8-sig")
    exec(compile(text, str(path), "exec"), namespace)  # noqa: S102 — test fixture
    return namespace


def _templates_step(result):
    return next(step for step in result.steps if step.name == "TEMPLATES")


def _run_init(tmp_path, templates=True, **kwargs):
    """``djust init --templates`` unless a test is about the default."""
    return init.init_project(tmp_path, install=False, force=True, templates=templates, **kwargs)


# --- stock startproject output -------------------------------------------------


@pytest.mark.parametrize("version", ["4_2", "5_2"])
def test_startproject_settings_gain_the_documented_shape(tmp_path, version):
    make_project(tmp_path)
    original = (FIXTURES / ("startproject_settings_django_%s.txt" % version)).read_text()
    path = _write_settings(tmp_path, original)
    stock = _evaluate(path)["TEMPLATES"]

    result = _run_init(tmp_path)

    assert _templates_step(result).status == init.DONE
    assert result.exit_code == 0
    text = path.read_text()
    assert text.startswith(original.rstrip("\n"))  # nothing of the user's file moved
    templates = _evaluate(path)["TEMPLATES"]
    assert [t["BACKEND"] for t in templates] == [DJUST_BACKEND, DJANGO_BACKEND]
    assert templates[1] == stock[0]  # the project's own entry is untouched
    djust_entry = templates[0]
    assert djust_entry["APP_DIRS"] is True
    assert djust_entry["DIRS"] == stock[0]["DIRS"]
    assert (
        djust_entry["OPTIONS"]["context_processors"] == (stock[0]["OPTIONS"]["context_processors"])
    )
    # What the admin needs is in the entry that now renders it (djust.C016).
    for processor in (
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ):
        assert processor in djust_entry["OPTIONS"]["context_processors"]


# --- customised TEMPLATES are mirrored, never rewritten --------------------------

CUSTOM_DIRS = """\
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates", BASE_DIR / "shared"],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    },
]
"""

CUSTOM_PROCESSORS = """\
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "shop.context_processors.cart",
            ],
        },
    },
]
"""

LOADERS_BUILTINS_LIBRARIES = """\
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "OPTIONS": {
            "loaders": [
                ("django.template.loaders.cached.Loader", [
                    "django.template.loaders.filesystem.Loader",
                    "django.template.loaders.app_directories.Loader",
                ]),
            ],
            "builtins": ["django.templatetags.static"],
            "libraries": {"shop_tags": "shop.templatetags.shop_tags"},
            "string_if_invalid": "?",
            "file_charset": "utf-8",
        },
    },
]
"""

TUPLE_TEMPLATES = """\
TEMPLATES = (
    {"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": ["t"]},
)
"""

TWO_ENGINES = """\
TEMPLATES = [
    {"BACKEND": "django.template.backends.jinja2.Jinja2", "DIRS": ["jinja"]},
    {"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": ["t"]},
]
"""


@pytest.mark.parametrize(
    "source",
    [
        CUSTOM_DIRS,
        CUSTOM_PROCESSORS,
        LOADERS_BUILTINS_LIBRARIES,
        TUPLE_TEMPLATES,
        TWO_ENGINES,
    ],
)
def test_custom_templates_keep_their_entries_and_get_a_mirroring_djust_entry(tmp_path, source):
    path = _project(tmp_path, source)
    before = _evaluate(path)["TEMPLATES"]
    old_text = path.read_text()

    result = _run_init(tmp_path)

    assert _templates_step(result).status == init.DONE
    assert path.read_text().startswith(old_text.rstrip("\n"))
    after = _evaluate(path)["TEMPLATES"]
    assert list(after[1:]) == list(before)  # every user entry, in order, equal
    djust_entry = after[0]
    assert djust_entry["BACKEND"] == DJUST_BACKEND
    assert djust_entry["APP_DIRS"] is True
    mirrored = next(t for t in before if t["BACKEND"] == DJANGO_BACKEND)
    assert djust_entry["DIRS"] == list(mirrored.get("DIRS", []))
    options = mirrored.get("OPTIONS", {})
    for key in ("context_processors", "builtins", "libraries", "string_if_invalid"):
        assert djust_entry["OPTIONS"].get(key) == options.get(key)
    # Keys DjustTemplateBackend does not implement stay on the Django entry
    # (it logs a warning for each one it is given).
    assert "loaders" not in djust_entry["OPTIONS"]
    assert "file_charset" not in djust_entry["OPTIONS"]


def test_formatting_and_comments_inside_templates_are_preserved(tmp_path):
    source = (
        "TEMPLATES = [  # the project's engines\n"
        "    {\n"
        '        "BACKEND": "django.template.backends.django.DjangoTemplates",  # keep\n'
        '        "DIRS": [],\n'
        "        # context processors, alphabetical\n"
        '        "OPTIONS": {"context_processors": ["a.b", "c.d"]},\n'
        "    },\n"
        "]\n"
    )
    path = _project(tmp_path, source)
    old_text = path.read_text()

    _run_init(tmp_path)

    new_text = path.read_text()
    assert new_text.startswith(old_text.rstrip("\n"))
    assert source in new_text


def test_the_first_django_entry_is_the_one_mirrored(tmp_path):
    source = (
        "TEMPLATES = [\n"
        '    {"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": ["one"], "NAME": "one"},\n'
        '    {"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": ["two"], "NAME": "two"},\n'
        "]\n"
    )
    path = _project(tmp_path, source)
    _run_init(tmp_path)
    templates = _evaluate(path)["TEMPLATES"]
    assert [t.get("DIRS") for t in templates] == [["one"], ["one"], ["two"]]


def test_a_later_mutation_of_templates_is_seen_by_the_block(tmp_path):
    """The block runs at the end of the file, so an in-place edit above it
    (not an assignment) is part of what the djust entry mirrors."""
    path = _project(
        tmp_path,
        CUSTOM_DIRS,
        extra='TEMPLATES[0]["DIRS"] = [BASE_DIR / "late"]\n',
    )
    result = _run_init(tmp_path)
    assert _templates_step(result).status == init.DONE
    templates = _evaluate(path)["TEMPLATES"]
    assert templates[0]["DIRS"] == [Path("/base/late")]
    assert templates[1]["DIRS"] == [Path("/base/late")]


# --- already configured: nothing to do -------------------------------------------

DJUST_FIRST = """\
TEMPLATES = [
    {
        "BACKEND": "djust.template_backend.DjustTemplateBackend",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["a.b"]},
    },
    {"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": []},
]
"""

DJUST_ONLY = """\
TEMPLATES = [
    {"BACKEND": "djust.template_backend.DjustTemplateBackend", "DIRS": [], "APP_DIRS": True},
]
"""

MANUAL_INSERT = """\
TEMPLATES = [
    {"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": []},
]
TEMPLATES.insert(0, {"BACKEND": "djust.template_backend.DjustTemplateBackend", "DIRS": []})
"""


@pytest.mark.parametrize("source", [DJUST_FIRST, DJUST_ONLY, MANUAL_INSERT])
def test_a_project_that_already_uses_the_djust_backend_is_left_alone(tmp_path, source):
    path = _project(tmp_path, source)
    before = _evaluate(path)["TEMPLATES"]

    result = _run_init(tmp_path)

    step = _templates_step(result)
    assert step.status == init.UNCHANGED
    assert T.TEMPLATES_MARKER not in path.read_text()
    assert _evaluate(path)["TEMPLATES"] == before


def test_djust_after_django_is_reported_not_reordered(tmp_path):
    source = (
        "TEMPLATES = [\n"
        '    {"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": []},\n'
        '    {"BACKEND": "djust.template_backend.DjustTemplateBackend", "DIRS": []},\n'
        "]\n"
    )
    path = _project(tmp_path, source)
    before = _evaluate(path)["TEMPLATES"]

    result = _run_init(tmp_path)

    assert _templates_step(result).status == init.ATTENTION
    assert result.exit_code == 2
    assert any("djust.C016" in note for note in result.notes)
    assert T.TEMPLATES_MARKER not in path.read_text()
    assert _evaluate(path)["TEMPLATES"] == before


# --- shapes init does not guess ---------------------------------------------------


@pytest.mark.parametrize(
    "source, reason",
    [
        ("TEMPLATES = build_templates()\n", "not a list literal"),
        ("from .base import *\n", "not assigned"),
        ("TEMPLATES = []\n", "no DjangoTemplates"),
        (
            'TEMPLATES = [{"BACKEND": "django.template.backends.jinja2.Jinja2", "DIRS": []}]\n',
            "no DjangoTemplates",
        ),
        (CUSTOM_DIRS + "TEMPLATES += [{}]\n", "changed after"),
        (CUSTOM_DIRS + 'TEMPLATES.append({"BACKEND": "x"})\n', "changed after"),
        (CUSTOM_DIRS + "TEMPLATES = TEMPLATES[:1]\n", "more than once"),
        ("if True:\n    TEMPLATES = []\n", "not assigned"),
        (
            'TEMPLATES = [dict(BACKEND="django.template.backends.django.DjangoTemplates")]\n',
            "plain dict literal",
        ),
        (
            "TEMPLATES = [{**BASE, \"BACKEND\": 'django.template.backends.django.DjangoTemplates'}]\n",
            "plain dict literal",
        ),
        ('TEMPLATES = [{"DIRS": []}]\n', "no literal BACKEND"),
    ],
)
def test_an_unrecognised_templates_is_reported_with_the_snippet(tmp_path, source, reason):
    path = _project(tmp_path, source)
    old_text = path.read_text()

    result = _run_init(tmp_path)

    step = _templates_step(result)
    assert step.status == init.ATTENTION
    assert reason in step.detail
    assert result.exit_code == 2
    assert T.TEMPLATES_MARKER not in path.read_text()
    # The rest of the setup still happens: only TEMPLATES is left to the user.
    assert path.read_text().startswith(old_text.rstrip("\n"))
    assert init.SETTINGS_MARKER in path.read_text()
    note = next(n for n in result.notes if "TEMPLATES was not changed" in n)
    assert T.TEMPLATES_ENTRY_SNIPPET in note


def test_a_settings_file_that_does_not_parse_is_reported(tmp_path):
    plan = init.classify_templates("TEMPLATES = [\n")
    assert plan.kind == "unrecognised"


# --- idempotence, dry run, upgrade from an earlier init -----------------------------


def test_rerunning_init_changes_nothing(tmp_path):
    path = _project(tmp_path, CUSTOM_PROCESSORS)
    _run_init(tmp_path)
    first = path.read_text()
    assert first.count(T.TEMPLATES_MARKER) == 1

    result = _run_init(tmp_path)

    assert path.read_text() == first
    assert _templates_step(result).status == init.UNCHANGED
    assert result.exit_code == 0
    assert result.changes == [c for c in result.changes if c.path != path]


def test_the_block_is_idempotent_when_executed_twice():
    namespace = {"TEMPLATES": [{"BACKEND": DJANGO_BACKEND, "DIRS": ["t"]}]}
    exec(T.TEMPLATES_BLOCK, namespace)  # noqa: S102 — trusted template
    once = list(namespace["TEMPLATES"])
    exec(T.TEMPLATES_BLOCK, namespace)  # noqa: S102 — trusted template
    assert namespace["TEMPLATES"] == once


def test_a_project_initialised_before_this_feature_gets_only_the_templates_block(tmp_path):
    path = _project(tmp_path, CUSTOM_DIRS)
    old = path.read_text()
    path.write_text(old.rstrip("\n") + "\n\n\n" + init.render_settings_block("mysite.asgi"))
    (tmp_path / "mysite" / "asgi.py").write_text(init.render_asgi(init.detect_project(tmp_path)))
    earlier = path.read_text()

    result = _run_init(tmp_path)

    steps = {step.name: step.status for step in result.steps}
    assert steps["mysite/settings.py"] == init.UNCHANGED
    assert steps["TEMPLATES"] == init.DONE
    text = path.read_text()
    assert text.startswith(earlier.rstrip("\n"))
    assert text.count(init.SETTINGS_MARKER) == 1
    assert text.count(T.TEMPLATES_MARKER) == 1


def test_dry_run_shows_exactly_what_a_real_run_writes(tmp_path):
    path = _project(tmp_path, CUSTOM_PROCESSORS)
    old = path.read_text()

    dry = init.init_project(tmp_path, install=False, dry_run=True, templates=True)

    assert path.read_text() == old  # nothing written
    assert _templates_step(dry).status == init.PLANNED
    settings_changes = [c for c in dry.changes if c.path == path]
    assert len(settings_changes) == 1  # both blocks in the one file change
    assert "DjustTemplateBackend" in init.format_result(dry, tmp_path)

    _run_init(tmp_path)
    assert path.read_text() == settings_changes[0].new


def test_dry_run_reports_an_unrecognised_templates_the_same_way(tmp_path):
    _project(tmp_path, "TEMPLATES = build_templates()\n")
    dry = init.init_project(tmp_path, install=False, dry_run=True, templates=True)
    assert _templates_step(dry).status == init.ATTENTION
    assert any("TEMPLATES was not changed" in n for n in dry.notes)
    assert dry.exit_code == 0  # a dry run never fails


def test_crlf_settings_keep_their_line_endings(tmp_path):
    path = _project(tmp_path, CUSTOM_DIRS)
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))

    _run_init(tmp_path)

    raw = path.read_bytes()
    assert b"\r\n" in raw
    assert raw.count(b"\n") == raw.count(b"\r\n")


# --- the two generators agree -------------------------------------------------------


def test_djust_new_and_djust_init_write_the_same_djust_entry():
    """`djust new` writes the djust entry first, ``APP_DIRS`` on, with the four
    standard processors; the snippet ``djust init`` prints is that entry plus the
    ``NAME`` alias (which ``djust new`` leaves to Django's default)."""
    new_settings = T.SETTINGS_PY % {
        "app_name": "app",
        "secret_key": "x",
        "admin_app": "",
        "theming_app": "",
        "theming_context_processor": "",
        "admin_template_backend": T.ADMIN_TEMPLATE_BACKEND,
        "theming_settings": "",
        "extra_settings": "",
    }
    namespace = {"os": os, "BASE_DIR": Path("/base")}
    # Only the TEMPLATES literal is evaluated: the rest needs a real project.
    start = new_settings.index("TEMPLATES = [")
    end = new_settings.index("ASGI_APPLICATION")
    exec(new_settings[start:end], namespace)  # noqa: S102 — trusted template
    generated = namespace["TEMPLATES"][0]

    snippet_ns = {"TEMPLATES": [], "BASE_DIR": Path("/base")}
    exec(T.TEMPLATES_ENTRY_SNIPPET, snippet_ns)  # noqa: S102 — trusted template
    documented = dict(snippet_ns["TEMPLATES"][0])

    assert documented.pop("NAME") == "djust"
    assert generated["OPTIONS"]["context_processors"]  # the comparison is not vacuous
    assert generated == documented
    assert [t["BACKEND"] for t in namespace["TEMPLATES"]] == [DJUST_BACKEND, DJANGO_BACKEND]


def test_the_block_and_the_snippet_agree_on_the_name():
    namespace = {"TEMPLATES": [{"BACKEND": DJANGO_BACKEND}]}
    exec(T.TEMPLATES_BLOCK, namespace)  # noqa: S102 — trusted template
    assert namespace["TEMPLATES"][0]["NAME"] == "djust"
    assert '"NAME": "djust"' in T.TEMPLATES_ENTRY_SNIPPET


def test_the_installation_guide_shows_the_snippet_init_prints():
    guide = Path(__file__).resolve().parents[3] / "docs/website/getting-started/installation.md"
    text = guide.read_text()
    assert T.TEMPLATES_ENTRY_SNIPPET.startswith("TEMPLATES.insert(0, {")  # not vacuous
    assert T.TEMPLATES_ENTRY_SNIPPET in text
    assert "--templates" in text


def test_the_guide_does_not_claim_a_django_fallback_for_templates_djust_finds():
    guide = Path(__file__).resolve().parents[3] / "docs/website/getting-started/installation.md"
    text = guide.read_text()
    assert "still render" not in text  # the claim was false: djust renders what it finds
    assert "TEMPLATES[0]" in text  # the override hazard is documented
    # The Django entry is found by BACKEND: its index depends on what comes first.
    assert "(`TEMPLATES[1]`)" not in text
    assert "by its `BACKEND`" in text
    assert "`loaders` and `file_charset`" in text


# --- a real startproject: check, admin, a plain Django view and a LiveView ------------

PAGES_VIEWS = """\
from django.views.generic import TemplateView
from djust import LiveView
from djust.decorators import event_handler


class Counter(LiveView):
    template_name = "counter.html"
    login_required = False

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1


class Plain(TemplateView):
    template_name = "plain.html"
"""

PAGES_URLS = """\
from django.contrib import admin
from django.urls import path

from pages import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("plain/", views.Plain.as_view()),
    path("counter/", views.Counter.as_view()),
]
"""

PROBE = """
import os, django
os.environ["DJANGO_SETTINGS_MODULE"] = "mysite.settings"
django.setup()
from django.conf import settings
settings.ALLOWED_HOSTS = ["*"]
from django.core.management import call_command
call_command("migrate", verbosity=0)
from django.contrib.auth.models import User
from django.template import engines
from django.template.loader import get_template
from django.test import Client

print("engines", [type(e).__name__ for e in engines.all()])
client = Client()
login = client.get("/admin/login/")
print("login", login.status_code, b"Django administration" in login.content)
plain = client.get("/plain/")
print("plain", plain.status_code, b'id="plain"' in plain.content, b"anon" in plain.content)
live = client.get("/counter/")
print("live", live.status_code, b"count 0" in live.content, b"dj-root" in live.content)
user = User.objects.create_superuser("admin", "admin@example.com", "pw")
client.force_login(user)
for url in ("/admin/", "/admin/auth/user/", "/admin/auth/user/%d/change/" % user.pk):
    print("admin", client.get(url).status_code)
print("loader", type(get_template("plain.html")).__module__)
"""


def _check(root, env):
    return subprocess.run(
        [sys.executable, "manage.py", "check"], cwd=root, env=env, capture_output=True, text=True
    )


@pytest.mark.parametrize("version", ["4_2", "5_2"])
def test_a_startproject_renders_under_the_templates_flag(tmp_path, version):
    """`djust init --templates` end to end: the admin, a plain Django template view
    and a LiveView answer 200 with djust's engine first, `manage.py check` has
    nothing to say about templates, and T019 still sees a broken binding. This
    asserts status and a few substrings only, NOT that the admin renders exactly
    as under Django's engine (it does not: see the installation guide)."""
    subprocess.run(
        [sys.executable, "-m", "django", "startproject", "mysite", str(tmp_path)],
        check=True,
        capture_output=True,
    )
    settings = (FIXTURES / ("startproject_settings_django_%s.txt" % version)).read_text()
    settings = settings.replace(
        '"django.contrib.staticfiles",', '"django.contrib.staticfiles",\n    "pages",'
    )
    (tmp_path / "mysite" / "settings.py").write_text(settings)
    (tmp_path / "mysite" / "urls.py").write_text(PAGES_URLS)
    pages = tmp_path / "pages"
    (pages / "templates").mkdir(parents=True)
    (pages / "__init__.py").write_text("")
    (pages / "views.py").write_text(PAGES_VIEWS)
    (pages / "templates" / "plain.html").write_text(
        '{% load static %}<p id="plain">{% if user.is_authenticated %}in{% else %}anon{% endif %}</p>\n'
    )
    (pages / "templates" / "counter.html").write_text(
        '<div dj-root><p>count {{ count }}</p><button dj-click="inc">+</button></div>\n'
    )

    result = _run_init(tmp_path)
    assert result.exit_code == 0, result.steps

    env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
    env["PYTHONPATH"] = os.pathsep.join([str(tmp_path), *sys.path])
    checked = _check(tmp_path, env)
    output = checked.stdout + checked.stderr
    assert checked.returncode == 0, output
    for template_issue in ("djust.C016", "djust.T019", "djust.T020", "djust.T021", "admin.E40"):
        assert template_issue not in output, output

    probe = subprocess.run(
        [sys.executable, "-c", PROBE], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    lines = [line for line in probe.stdout.splitlines() if not line.startswith("[")]
    assert lines == [
        "engines ['DjustTemplateBackend', 'DjangoTemplates']",
        "login 200 True",
        "plain 200 True True",
        "live 200 True True",
        "admin 200",
        "admin 200",
        "admin 200",
        "loader djust.template.rendering",
    ], probe.stdout + probe.stderr

    # T019 still sees a broken binding when djust's engine is first.
    (pages / "templates" / "counter.html").write_text(
        '<div dj-root><p>count {{ count }}</p><button dj-click="nope">+</button></div>\n'
    )
    broken = _check(tmp_path, env)
    assert "djust.T019" in broken.stdout + broken.stderr


# --- the default: report, never edit -----------------------------------------------


def test_the_default_run_reports_the_snippet_and_edits_nothing(tmp_path):
    path = _project(tmp_path, CUSTOM_PROCESSORS)
    before = _evaluate(path)["TEMPLATES"]

    result = _run_init(tmp_path, templates=False)

    step = _templates_step(result)
    assert step.status == init.SKIPPED
    assert "--templates" in step.detail
    assert result.exit_code == 0
    assert T.TEMPLATES_MARKER not in path.read_text()
    assert _evaluate(path)["TEMPLATES"] == before
    note = next(n for n in result.notes if "TEMPLATES was not changed" in n)
    assert T.TEMPLATES_ENTRY_SNIPPET in note
    assert "--templates" in note
    # The rest of init still happens.
    assert init.SETTINGS_MARKER in path.read_text()


@pytest.mark.parametrize(
    "source",
    [
        "TEMPLATES = build_templates()\n",
        "from .base import *\n",
        "TEMPLATES = []\n",
        'TEMPLATES = [{"BACKEND": "django.template.backends.jinja2.Jinja2", "DIRS": []}]\n',
        CUSTOM_DIRS + "TEMPLATES += [{}]\n",
        "TEMPLATES = [\n",
    ],
)
def test_an_unreadable_templates_is_informational_without_the_flag(tmp_path, source):
    """No permanent exit 2 for a project that does not want djust-first."""
    _project(tmp_path, source)
    result = _run_init(tmp_path, templates=False)
    assert _templates_step(result).status == init.SKIPPED
    assert result.exit_code == 0


def test_djust_after_django_is_informational_without_the_flag(tmp_path):
    _project(
        tmp_path,
        "TEMPLATES = [\n"
        '    {"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": []},\n'
        '    {"BACKEND": "djust.template_backend.DjustTemplateBackend", "DIRS": []},\n'
        "]\n",
    )
    result = _run_init(tmp_path, templates=False)
    assert _templates_step(result).status == init.SKIPPED
    assert result.exit_code == 0
    assert any("djust.C016" in note for note in result.notes)


@pytest.mark.parametrize("templates", [False, True])
def test_an_existing_djust_entry_is_unchanged_with_or_without_the_flag(tmp_path, templates):
    path = _project(tmp_path, DJUST_FIRST)
    result = _run_init(tmp_path, templates=templates)
    assert _templates_step(result).status == init.UNCHANGED
    assert T.TEMPLATES_MARKER not in path.read_text()


def test_the_flag_then_a_plain_rerun_changes_nothing(tmp_path):
    path = _project(tmp_path, CUSTOM_PROCESSORS)
    _run_init(tmp_path, templates=True)
    first = path.read_text()

    result = _run_init(tmp_path, templates=False)

    assert path.read_text() == first
    assert _templates_step(result).status == init.UNCHANGED
    assert result.exit_code == 0


def test_a_removed_block_is_not_re_added_without_the_flag(tmp_path):
    """The marker is the only state: remove the block and a plain rerun only
    reports, it does not bring the block back."""
    path = _project(tmp_path, CUSTOM_PROCESSORS)
    _run_init(tmp_path, templates=True)
    text = path.read_text()
    removed = text[: text.index(T.TEMPLATES_MARKER)].rstrip("\n") + "\n"
    path.write_text(removed)

    result = _run_init(tmp_path, templates=False)

    assert path.read_text() == removed
    assert _templates_step(result).status == init.SKIPPED
    assert result.exit_code == 0


def test_the_flag_on_an_unreadable_templates_is_attention(tmp_path):
    _project(tmp_path, "TEMPLATES = build_templates()\n")
    result = _run_init(tmp_path, templates=True)
    assert _templates_step(result).status == init.ATTENTION
    assert result.exit_code == 2


def test_a_settings_file_with_a_byte_order_mark_is_read(tmp_path):
    path = _project(tmp_path, CUSTOM_DIRS)
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())

    default = _run_init(tmp_path, templates=False)
    assert "parse" not in _templates_step(default).detail
    assert "--templates" in _templates_step(default).detail

    result = _run_init(tmp_path, templates=True)
    assert _templates_step(result).status == init.DONE
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")  # kept
    assert _evaluate(path)["TEMPLATES"][0]["BACKEND"] == DJUST_BACKEND


def test_the_cli_passes_the_flag_through(tmp_path, monkeypatch):
    import argparse

    from djust import cli

    seen = {}

    def fake(root, **kwargs):
        seen.update(kwargs)
        return init.InitResult(steps=[init.Step("x", init.DONE, "")])

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(init, "init_project", fake)
    base = dict(settings=None, dry_run=False, no_install=True, force=False)
    cli.cmd_init(argparse.Namespace(templates=True, **base))
    assert seen["templates"] is True
    cli.cmd_init(argparse.Namespace(templates=False, **base))
    assert seen["templates"] is False


def test_the_parser_defaults_templates_off(tmp_path, monkeypatch, capsys):
    from djust import cli

    make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["djust", "init", "--dry-run", "--no-install"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "skipped" in out and "--templates" in out
    monkeypatch.setattr(sys, "argv", ["djust", "init", "--dry-run", "--no-install", "--templates"])
    with pytest.raises(SystemExit):
        cli.main()
    assert "would change  add DjustTemplateBackend first" in capsys.readouterr().out


def test_a_namespace_without_the_templates_attribute_still_works(tmp_path, monkeypatch):
    """Callers built before ``--templates`` pass a Namespace that lacks it."""
    import argparse

    from djust import cli

    make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(settings=None, dry_run=True, no_install=True, force=False)
    assert not hasattr(args, "templates")
    assert cli.cmd_init(args) == 0


def test_the_default_note_points_at_the_installation_guide(tmp_path):
    _project(tmp_path, CUSTOM_DIRS)
    result = _run_init(tmp_path, templates=False)
    note = next(n for n in result.notes if "TEMPLATES was not changed" in n)
    assert init.INSTALLATION_URL in note
