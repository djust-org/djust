"""#3225: T002 and T012 must agree about a template that never connects.

A LiveView template with neither ``dj-root`` nor ``dj-view`` renders as a
static page: djust stamps no ``dj-view`` and the client mounts only
``[dj-view]``. Before the fix:

- T002 said "This is OK — dj-root is auto-inferred from dj-view" for any
  template with ``dj-click`` and friends, even without ``dj-view``.
- T012 only knew ten event names, so a template driven by
  ``dj-viewport-bottom``, ``dj-model``, ``dj-hook``, ``dj-poll`` or
  ``dj-upload`` got no warning.
- The ``dj-view`` test was a bare substring, so ``dj-viewport-bottom`` itself
  counted as a ``dj-view`` attribute and silenced T012.

T012's trigger set is now derived from the client's directive table
(``djust._template_bindings``), and a drift test below scans the client
source so a new client attribute must be classified for T012.
"""

import re
from pathlib import Path

import pytest

import djust


def _run(tmp_path, settings, name, body):
    tpl_dir = tmp_path / "templates"
    tpl_dir.mkdir(exist_ok=True)
    (tpl_dir / name).write_text(body)
    settings.TEMPLATES = [
        {
            "DIRS": [str(tpl_dir)],
            "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
        }
    ]
    from djust.checks import check_templates

    return [e for e in check_templates(None) if e.id in ("djust.T002", "djust.T012")]


def _ids(messages):
    return sorted(m.id for m in messages)


# -- The issue's two cases -------------------------------------------------------


def test_viewport_bottom_only_gets_t012_and_no_t002(tmp_path, settings):
    found = _run(
        tmp_path,
        settings,
        "feed.html",
        '<section><ul dj-viewport-bottom="load_more"><li>x</li></ul></section>',
    )
    assert _ids(found) == ["djust.T012"]
    assert not any("OK" in m.msg for m in found)


def test_dj_view_without_root_gets_only_t002(tmp_path, settings):
    found = _run(
        tmp_path,
        settings,
        "view.html",
        '<div dj-view="myapp.views.MyView"><button dj-click="go">Go</button></div>',
    )
    assert _ids(found) == ["djust.T002"]
    t002 = found[0]
    assert "dj-view" in t002.msg
    assert "<div dj-root>" in t002.hint
    assert 'dj-view="myapp' not in t002.hint


def test_directives_without_view_or_root_never_say_ok(tmp_path, settings):
    found = _run(tmp_path, settings, "p.html", '<div><button dj-click="go">Go</button></div>')
    assert _ids(found) == ["djust.T012"]


# -- T012 covers the directives the old list missed -------------------------------


@pytest.mark.parametrize(
    "markup",
    [
        '<ul dj-viewport-top="older"></ul>',
        '<input dj-model="query">',
        '<input dj-model.lazy="query">',
        '<canvas dj-hook="Chart"></canvas>',
        '<div dj-poll="refresh"></div>',
        '<input type="file" dj-upload="avatar">',
        '<input dj-keydown.enter="save">',
        '<div dj-mounted="ready"></div>',
        '<div dj-window-scroll="scrolled"></div>',
        '<textarea dj-paste="pasted"></textarea>',
    ],
)
def test_t012_fires_for_every_live_directive(tmp_path, settings, markup):
    found = _run(tmp_path, settings, "p.html", "<div>%s</div>" % markup)
    assert _ids(found) == ["djust.T012"], markup


@pytest.mark.parametrize(
    "markup",
    [
        '<div dj-root><ul dj-viewport-bottom="more"></ul></div>',
        '<div dj-view="a.B"><input dj-model="q"></div>',
    ],
)
def test_t012_silent_with_a_root(tmp_path, settings, markup):
    found = _run(tmp_path, settings, "p.html", markup)
    assert "djust.T012" not in _ids(found)


def test_t012_keeps_component_and_partial_opt_outs(tmp_path, settings):
    assert (
        _run(tmp_path, settings, "c.html", '<div dj-component="x"><input dj-model="q"></div>') == []
    )
    assert (
        _run(tmp_path, settings, "p2.html", '{# djust:partial #}\n<ul dj-viewport-bottom="m"></ul>')
        == []
    )


def test_t012_skips_live_component_templates(tmp_path, settings):
    # A LiveComponent's own template (tutorial-live-component.md) carries
    # data-component-id on its root and renders inside the parent's dj-root.
    found = _run(
        tmp_path,
        settings,
        "star_rating.html",
        '<div dj-mouseleave="clear" data-component-id="{{ component_id }}">'
        '<button dj-click="rate">x</button></div>',
    )
    assert found == []


def test_non_live_attributes_alone_do_not_trigger_t012(tmp_path, settings):
    # Layout-level attributes that legitimately sit outside a LiveView root.
    found = _run(
        tmp_path,
        settings,
        "base.html",
        '<nav><a href="/x" dj-navigate>X</a></nav><div dj-offline-hide>online</div>',
    )
    assert found == []


def test_dj_view_transitions_is_not_a_dj_view(tmp_path, settings):
    found = _run(
        tmp_path, settings, "p.html", '<main dj-view-transitions><b dj-click="x">x</b></main>'
    )
    assert _ids(found) == ["djust.T012"]


# -- Drift: every attribute the client reads is classified for T012 ------------------


def _client_attributes():
    src = Path(djust.__file__).parent / "static" / "djust" / "src"
    source = "\n".join(p.read_text(encoding="utf-8") for p in sorted(src.glob("*.js")))
    read = set(re.findall(r"""(?:get|has)Attribute\(\s*['"](dj-[a-z-]+)['"]""", source))
    read |= set(re.findall(r"\[(dj-[a-z-]+)\]", source))
    return read


def test_every_client_attribute_is_classified_for_t012():
    from djust._template_bindings import DIRECTIVES, NON_EVENT_ATTRIBUTES
    from djust.checks.templates import _T012_EXEMPT_ATTRIBUTES, _T012_TRIGGER_ATTRIBUTES

    known = _client_attributes() | set(DIRECTIVES) | set(NON_EVENT_ATTRIBUTES)
    assert not _T012_TRIGGER_ATTRIBUTES & _T012_EXEMPT_ATTRIBUTES
    unclassified = sorted(known - _T012_TRIGGER_ATTRIBUTES - _T012_EXEMPT_ATTRIBUTES)
    assert unclassified == [], (
        "classify these client attributes for T012 (trigger or exempt) in "
        "djust/checks/templates.py: %s" % unclassified
    )
    # Every server-event directive is a trigger; none may be exempted.
    assert set(DIRECTIVES) <= _T012_TRIGGER_ATTRIBUTES


def test_every_trigger_attribute_is_matched_by_the_regex():
    from djust.checks.templates import _DJ_EVENT_DIRECTIVES_RE, _T012_TRIGGER_ATTRIBUTES

    for name in sorted(_T012_TRIGGER_ATTRIBUTES):
        assert _DJ_EVENT_DIRECTIVES_RE.search('<div %s="x">' % name), name
