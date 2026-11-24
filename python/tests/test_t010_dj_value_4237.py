"""T010 matches ``dj-value-*`` navigation params and whole names only (#4237)."""

import pytest

from djust.checks import check_templates


def _t010(tmp_path, settings, html):
    tpl_dir = tmp_path / "templates"
    tpl_dir.mkdir()
    (tpl_dir / "page.html").write_text(html)
    settings.TEMPLATES = [
        {
            "DIRS": [str(tpl_dir)],
            "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
        }
    ]
    return [e for e in check_templates(None) if e.id == "djust.T010"]


@pytest.mark.parametrize(
    "attr",
    ["dj-value-view", "dj-value-tab", "dj-value-page", "dj-value-section", "dj-value-tab:int"],
)
def test_dj_value_navigation_params_warn(tmp_path, settings, attr):
    found = _t010(tmp_path, settings, '<button dj-click="show" %s="x">Go</button>' % attr)
    assert len(found) == 1
    assert attr.split(":")[0] in found[0].msg


@pytest.mark.parametrize(
    "html",
    [
        '<button dj-patch="?tab=a" dj-value-tab="a">Go</button>',
        '<button dj-click="show" dj-value-viewer="a">Go</button>',
        '<div dj-click="show" data-viewport="a">Go</div>',
        '<div dj-click="show" data-views="a">Go</div>',
    ],
)
def test_no_warning(tmp_path, settings, html):
    assert _t010(tmp_path, settings, html) == []
