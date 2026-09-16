"""`djust init` adds djust to an existing Django project."""

from pathlib import Path

import pytest

from djust.scaffolding import init_project as init

STOCK_ASGI = '''"""
ASGI config for mysite project.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mysite.settings')

application = get_asgi_application()
'''


def make_project(root: Path, name: str = "mysite", asgi: str = STOCK_ASGI) -> Path:
    (root / name).mkdir(parents=True)
    (root / "manage.py").write_text(
        "import os\nos.environ.setdefault('DJANGO_SETTINGS_MODULE', '%s.settings')\n" % name
    )
    (root / name / "__init__.py").write_text("")
    (root / name / "settings.py").write_text(
        'INSTALLED_APPS = ["django.contrib.auth"]\nTEMPLATES = []\n'
    )
    (root / name / "asgi.py").write_text(asgi)
    return root


def test_detects_settings_from_manage_py(tmp_path):
    project = init.detect_project(make_project(tmp_path))
    assert project.settings_module == "mysite.settings"
    assert project.settings_path == tmp_path / "mysite" / "settings.py"
    assert project.asgi_path == tmp_path / "mysite" / "asgi.py"
    assert project.asgi_module == "mysite.asgi"


def test_settings_flag_overrides_manage_py(tmp_path):
    make_project(tmp_path)
    (tmp_path / "mysite" / "local.py").write_text("")
    project = init.detect_project(tmp_path, settings_module="mysite.local")
    assert project.settings_path.name == "local.py"


def test_missing_manage_py_is_refused(tmp_path):
    with pytest.raises(init.InitError, match="manage.py"):
        init.detect_project(tmp_path)


def test_settings_package_is_refused_with_snippet(tmp_path):
    make_project(tmp_path)
    (tmp_path / "mysite" / "settings.py").unlink()
    (tmp_path / "mysite" / "settings").mkdir()
    (tmp_path / "mysite" / "settings" / "__init__.py").write_text("")
    with pytest.raises(init.InitError) as exc:
        init.detect_project(tmp_path)
    assert "package" in str(exc.value)
    assert init.SETTINGS_MARKER in str(exc.value)


def test_settings_block_appended_once(tmp_path):
    project = init.detect_project(make_project(tmp_path))
    change, step = init.plan_settings(project)
    assert step.status == init.DONE
    assert change.new.startswith(change.old)
    assert change.new.count(init.SETTINGS_MARKER) == 1
    project.settings_path.write_text(change.new)
    change, step = init.plan_settings(project)
    assert change is None
    assert step.status == init.UNCHANGED


def run_block(**settings):
    namespace = dict(settings)
    exec(init.render_settings_block("mysite.asgi"), namespace)  # noqa: S102 — trusted template
    return namespace


@pytest.mark.parametrize("container", [list, tuple])
def test_settings_block_adds_apps_and_backend(container):
    django_backend = {"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": ["t"]}
    ns = run_block(
        INSTALLED_APPS=container(["django.contrib.auth", "channels"]),
        TEMPLATES=container([django_backend]),
    )
    assert ns["INSTALLED_APPS"] == ["django.contrib.auth", "channels", "djust"]
    assert ns["TEMPLATES"][0]["BACKEND"] == "djust.template_backend.DjustTemplateBackend"
    assert ns["TEMPLATES"][0]["DIRS"] == ["t"]
    assert ns["TEMPLATES"][1] == django_backend
    assert ns["ASGI_APPLICATION"] == "mysite.asgi.application"
    assert ns["CHANNEL_LAYERS"]["default"]["BACKEND"] == "channels.layers.InMemoryChannelLayer"


def test_settings_block_respects_existing_configuration():
    djust_backend = {"BACKEND": "djust.template_backend.DjustTemplateBackend"}
    redis = {"default": {"BACKEND": "channels_redis.core.RedisChannelLayer"}}
    ns = run_block(INSTALLED_APPS=["djust"], TEMPLATES=[djust_backend], CHANNEL_LAYERS=redis)
    assert ns["INSTALLED_APPS"] == ["djust", "channels"]
    assert ns["TEMPLATES"] == [djust_backend]
    assert ns["CHANNEL_LAYERS"] is redis


@pytest.mark.parametrize(
    "source",
    [STOCK_ASGI, STOCK_ASGI.replace("'", '"'), STOCK_ASGI.split('"""\n', 2)[2]],
)
def test_stock_asgi_is_replaced(tmp_path, source):
    project = init.detect_project(make_project(tmp_path, asgi=source))
    change, step, snippet = init.plan_asgi(project)
    assert step.status == init.DONE
    assert "LiveViewConsumer" in change.new
    assert '"DJANGO_SETTINGS_MODULE", "mysite.settings"' in change.new
    assert snippet is None


def test_customized_asgi_is_left_alone(tmp_path):
    custom = STOCK_ASGI + "\napplication = Wrapper(application)\n"
    project = init.detect_project(make_project(tmp_path, asgi=custom))
    change, step, snippet = init.plan_asgi(project)
    assert change is None
    assert step.status == init.ATTENTION
    assert "LiveViewConsumer" in snippet


def test_configured_asgi_is_unchanged(tmp_path):
    project = init.detect_project(
        make_project(tmp_path, asgi="from djust.websocket import LiveViewConsumer\n")
    )
    change, step, snippet = init.plan_asgi(project)
    assert (change, step.status, snippet) == (None, init.UNCHANGED, None)
