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


def _run(tmp_path, settings, name, body, **others):
    """T002/T012 reported on ``name``. ``others`` are more templates in the
    same directory; ``__`` in a keyword stands for ``/`` and ``.``."""
    tpl_dir = tmp_path / "templates"
    tpl_dir.mkdir(exist_ok=True)
    (tpl_dir / name).parent.mkdir(parents=True, exist_ok=True)
    (tpl_dir / name).write_text(body)
    for other, text in others.items():
        path = tpl_dir / other.replace("__html", ".html").replace("__", "/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    settings.TEMPLATES = [
        {
            "DIRS": [str(tpl_dir)],
            "BACKEND": "django.template.backends.django.DjangoTemplates",
        }
    ]
    from djust.checks import check_templates

    import os

    mine = os.path.relpath(tpl_dir / name)
    return [
        e
        for e in check_templates(None)
        if e.id in ("djust.T002", "djust.T012") and e.msg.split(" -- ")[0] == mine
    ]


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
    """A tripwire, not a proof: the scan sees ``get/hasAttribute('dj-x')`` and a
    bare ``[dj-x]`` selector, plus the two ``_template_bindings`` tables (which
    their own test ties to the client). A name built in a template literal or a
    ``startsWith('dj-')`` loop is invisible to it."""
    from djust._template_bindings import DIRECTIVES, NON_EVENT_ATTRIBUTES
    from djust.checks.templates import _T012_EXEMPT_ATTRIBUTES, _t012_trigger_attributes

    _T012_TRIGGER_ATTRIBUTES = _t012_trigger_attributes()

    known = _client_attributes() | set(DIRECTIVES) | set(NON_EVENT_ATTRIBUTES)
    assert not _T012_TRIGGER_ATTRIBUTES & _T012_EXEMPT_ATTRIBUTES
    unclassified = sorted(known - _T012_TRIGGER_ATTRIBUTES - _T012_EXEMPT_ATTRIBUTES)
    assert unclassified == [], (
        "classify these client attributes for T012 (trigger or exempt) in "
        "djust/checks/templates.py: %s" % unclassified
    )
    # Every server-event directive is a trigger; none may be exempted.
    assert set(DIRECTIVES) <= _T012_TRIGGER_ATTRIBUTES
    # Both directions: an entry the client no longer reads is stale.
    stale = sorted((_T012_TRIGGER_ATTRIBUTES | _T012_EXEMPT_ATTRIBUTES) - known)
    assert stale == [], "remove these from the T012 tables: %s" % stale


def test_every_trigger_attribute_is_matched_by_the_regex():
    from djust.checks.templates import _dj_event_directives_re, _t012_trigger_attributes

    for name in sorted(_t012_trigger_attributes()):
        assert _dj_event_directives_re().search('<div %s="x">' % name), name


# -- Review of #3238: no warnings on working templates (M1, M2, M3, L2) -------------


@pytest.mark.parametrize(
    "markup",
    [
        '<canvas dj-hook="Chart"></canvas>',  # hooks mount page-wide (14-init.js)
        '<ul dj-update="append"></ul>',
        '<ul dj-stream-mode="append"></ul>',
    ],
)
def test_t012_silent_for_attributes_that_work_without_a_connection(tmp_path, settings, markup):
    assert _run(tmp_path, settings, "p.html", "<div>%s</div>" % markup) == []


def test_t012_follows_extends_to_a_root_in_the_base(tmp_path, settings):
    found = _run(
        tmp_path,
        settings,
        "child.html",
        '{% extends "base.html" %}{% block content %}<ul dj-viewport-bottom="more"></ul>'
        "{% endblock %}",
        base__html="<html><body><div dj-root>{% block content %}{% endblock %}</div></body></html>",
    )
    assert found == []


def test_extends_child_under_a_rootless_base_gets_t012(tmp_path, settings):
    found = _run(
        tmp_path,
        settings,
        "child.html",
        '{% extends "base.html" %}{% block content %}<ul dj-viewport-bottom="more"></ul>'
        "{% endblock %}",
        base__html="<html><body>{% block content %}{% endblock %}</body></html>",
    )
    assert _ids(found) == ["djust.T012"]


def test_base_directives_do_not_warn_on_every_child(tmp_path, settings):
    # The base's own rootless directive is the base's warning, not each child's.
    found = _run(
        tmp_path,
        settings,
        "child.html",
        '{% extends "base.html" %}{% block content %}<p>hi</p>{% endblock %}',
        base__html='<nav><b dj-click="x">x</b></nav>{% block content %}{% endblock %}',
    )
    assert found == []


@pytest.mark.parametrize(
    "body",
    [
        '{% extends "missing.html" %}{% block c %}<ul dj-viewport-bottom="m"></ul>{% endblock %}',
        '{% extends "missing.html" %}{% block c %}<div dj-view="a.B"></div>{% endblock %}',
        '{% extends parent_name %}{% block c %}<ul dj-viewport-bottom="m"></ul>{% endblock %}',
    ],
)
def test_t002_and_t012_both_skip_an_unresolvable_parent(tmp_path, settings, body):
    assert _run(tmp_path, settings, "child.html", body) == []


def test_t002_follows_extends_to_a_root_in_the_base(tmp_path, settings):
    found = _run(
        tmp_path,
        settings,
        "child.html",
        '{% extends "base.html" %}{% block c %}<div dj-view="a.B"></div>{% endblock %}',
        base__html="<div dj-root>{% block c %}{% endblock %}</div>",
    )
    assert found == []
    found = _run(
        tmp_path,
        settings,
        "child2.html",
        '{% extends "base2.html" %}{% block c %}<div dj-view="a.B"></div>{% endblock %}',
        base2__html="<main>{% block c %}{% endblock %}</main>",
    )
    assert _ids(found) == ["djust.T002"]


def test_included_partial_is_checked_inside_its_includer(tmp_path, settings):
    partial = '<textarea dj-model="body"></textarea>'
    ok = '<div dj-root>{% include "parts/editor.html" %}</div>'
    assert _run(tmp_path, settings, "parts/editor.html", partial, page__html=ok) == []
    # An includer without a root is warned, with the partial inlined.
    bad = '<div>{% include "parts2/editor.html" %}</div>'
    found = _run(tmp_path, settings, "page2.html", bad, parts2__editor__html=partial)
    assert _ids(found) == ["djust.T012"]


def test_prose_in_code_is_not_a_directive(tmp_path, settings):
    found = _run(
        tmp_path,
        settings,
        "doc.html",
        '<p>Put <code>dj-click="save"</code> on the button, or '
        '&lt;ul dj-viewport-bottom="more"&gt;.</p>'
        '<script>el.setAttribute("dj-model", "q"); const s = \'dj-poll="x"\';</script>',
    )
    assert found == []


@pytest.mark.parametrize(
    "body",
    [
        '<div class="dj-components"><b dj-click="x">x</b></div>',
        '<div><b dj-click="x">x</b></div>{# data-component-id="{{ id }}" #}',
        '<div><b dj-click="x">x</b><code>data-component-id="1"</code></div>',
    ],
)
def test_component_opt_out_needs_a_real_attribute(tmp_path, settings, body):
    # A class name or prose mentioning the marker is not the marker.
    assert _ids(_run(tmp_path, settings, "c.html", body)) == ["djust.T012"]


def test_no_engine_still_reads_real_attributes(tmp_path, settings, monkeypatch):
    """Without a Django engine the template is read as tokens: same answers."""
    import djust._template_bindings as tb

    monkeypatch.setattr(tb, "django_engine", lambda: None)
    assert _ids(_run(tmp_path, settings, "a.html", '<ul dj-viewport-bottom="m"></ul>')) == [
        "djust.T012"
    ]
    assert _run(tmp_path, settings, "b.html", '<code>dj-click="x"</code>') == []
    assert (
        _run(tmp_path, settings, "c.html", '{% extends "base.html" %}<b dj-click="x">x</b>') == []
    )


class _EmbeddedChild:
    """Stand-in for a LiveView a ``{% live_render %}`` embeds."""

    template_name = "sticky/child.html"


def test_live_render_child_template_is_not_warned(tmp_path, settings):
    """A ``{% live_render %}`` child renders inside the tag's dj-view wrapper."""
    child = '<div class="widget"><button dj-click="toggle">Play</button></div>'
    parent = (
        '<div dj-root>{% load live_tags %}{% live_render "'
        + __name__
        + '._EmbeddedChild" sticky=True %}</div>'
    )
    assert _run(tmp_path, settings, "sticky/child.html", child, page__html=parent) == []
    # Unembedded, the same template is warned.
    assert _ids(_run(tmp_path, settings, "sticky/other.html", child)) == ["djust.T012"]
