"""`djust init` tells the user to route djust's SSE transport (#4242).

``djust new`` routes ``djust.sse.sse_urlpatterns`` under ``djust/`` (#4216), but
``djust init`` (djust added to an existing project) left the URLconf alone and
said nothing, so a client that fell back to SSE got a 404 at
``/djust/sse/<id>/``. ``djust init`` does not edit urls.py; it now names the
route in its output when the project's URLconf does not mention it, and stays
quiet when it already does. Neither case changes the exit code.
"""

from pathlib import Path

import pytest

from djust.scaffolding import init_project as init
from djust.tests.test_djust_init import make_project

STOCK_URLS = """\
from django.contrib import admin
from django.urls import path

urlpatterns = [
    path("admin/", admin.site.urls),
]
"""

ROUTED_URLS = """\
from django.contrib import admin
from django.urls import include, path

from djust.sse import sse_urlpatterns

urlpatterns = [
    path("admin/", admin.site.urls),
    path("djust/", include(sse_urlpatterns)),
]
"""

SSE_ROUTE = 'path("djust/", include(sse_urlpatterns))'


@pytest.fixture(autouse=True)
def _no_inherited_git_env(monkeypatch):
    from tests.git_env import GIT_EXECUTION_VARS

    for var in GIT_EXECUTION_VARS:
        monkeypatch.delenv(var, raising=False)


def _project(root: Path, urls: str) -> Path:
    make_project(root)
    (root / "mysite" / "urls.py").write_text(urls)
    return root


@pytest.mark.parametrize("dry_run", [False, True])
def test_unrouted_project_is_told_to_add_the_sse_route(tmp_path, dry_run):
    _project(tmp_path, STOCK_URLS)
    result = init.init_project(tmp_path, install=False, force=True, dry_run=dry_run)
    out = init.format_result(result, tmp_path)
    assert "from djust.sse import sse_urlpatterns" in out
    assert SSE_ROUTE in out
    assert "mysite/urls.py" in out
    assert result.exit_code == 0
    # init names the route; it does not edit the URLconf.
    assert (tmp_path / "mysite" / "urls.py").read_text() == STOCK_URLS


def test_routed_project_is_not_told_again(tmp_path):
    _project(tmp_path, ROUTED_URLS)
    result = init.init_project(tmp_path, install=False, force=True)
    out = init.format_result(result, tmp_path)
    assert "from djust.sse import sse_urlpatterns" not in out
    assert result.exit_code == 0


def test_root_urlconf_elsewhere_is_the_one_checked(tmp_path):
    # ROOT_URLCONF names config.urls: that file routes SSE, mysite/urls.py does not.
    _project(tmp_path, STOCK_URLS)
    settings = tmp_path / "mysite" / "settings.py"
    settings.write_text(settings.read_text() + '\nROOT_URLCONF = "config.urls"\n')
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "urls.py").write_text(ROUTED_URLS)
    result = init.init_project(tmp_path, install=False, force=True)
    out = init.format_result(result, tmp_path)
    assert "from djust.sse import sse_urlpatterns" not in out
    assert "config/urls.py" in out
    assert result.exit_code == 0


def test_root_urlconf_package_checks_its_init(tmp_path):
    # ROOT_URLCONF names a package: mysite/urls/__init__.py is the file checked.
    _project(tmp_path, STOCK_URLS)
    settings = tmp_path / "mysite" / "settings.py"
    settings.write_text(settings.read_text() + '\nROOT_URLCONF = "mysite.urls"\n')
    (tmp_path / "mysite" / "urls.py").unlink()
    (tmp_path / "mysite" / "urls").mkdir()
    (tmp_path / "mysite" / "urls" / "__init__.py").write_text(ROUTED_URLS)
    result = init.init_project(tmp_path, install=False, force=True)
    out = init.format_result(result, tmp_path)
    assert "from djust.sse import sse_urlpatterns" not in out
    assert "does not route" not in out
    assert result.exit_code == 0
