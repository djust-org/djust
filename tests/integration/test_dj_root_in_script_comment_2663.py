"""Regression for #2663 — a tag-like string inside a ``<script>`` must not
make the whole document the dj-root.

Symptom (djust.org ``/examples/``): the base template wraps ``{% block
content %}`` in ``<div dj-root>``; the child's ``{% block extra_scripts %}``
holds a JavaScript comment that *mentions* ``<div dj-root>``. The served page
carried its entire shell twice — two ``<nav>``s, two ``<footer>``s, a nested
``<!DOCTYPE html>`` inside ``<main>`` — with no warning.

Root cause, traced symptom-up through the REAL initial-GET path
(``LiveView.as_view()`` → ``get`` → ``get_template`` → ``render_full_template``):

1. ``get_template()`` picks the VDOM source with ``_DJ_ROOT_RE.search(child)``.
   The comment's ``<div dj-root>`` is a perfect regex match, so the CHILD
   (which has no real root — that lives in the base) is chosen.
2. ``_extract_liveview_root_with_wrapper(child)`` finds that phantom tag,
   ``_find_closing_div_pos`` never balances, ``(None, None)`` → the WHOLE
   child source (``{% extends %}`` and all) becomes the liveview template.
3. ``render_full_template`` renders that as the root and splices a complete
   second document into the shell's real ``<div dj-root>``.

The fix masks ``<script>``/``<style>`` bodies and HTML comments (raw text,
not markup) at every dj-root locating sink and in the depth walk
(``mixins/template.py::_mask_raw_text``), with the Rust scanner
``find_dj_root_content_range`` fixed the same way (#1646 twin).
"""

from __future__ import annotations

import os
import re
import shutil
from importlib import import_module

import pytest
from django.apps import apps
from django.conf import settings
from django.template import loader
from django.test import override_settings

from djust.live_view import LiveView
from djust.utils import clear_template_dirs_cache

_BASE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Shell 2663</title>
    {% block head_scripts %}{% endblock %}
</head>
<body>
    <nav class="navbar">nav-marker</nav>
    <!-- Main Content — dj-root wraps content for LiveView navigation -->
    <main>
        <div dj-root>
            {% block content %}{% endblock %}
        </div>
    </main>
    <footer class="site-footer">footer-marker</footer>
    {% block extra_scripts %}{% endblock %}
</body>
</html>
"""

# The djust.org shape: the comment lives in the child's extra_scripts block,
# AFTER the root in document order, and the child itself has NO real root.
_INDEX_AFTER = """\
{% extends "demo2663/base.html" %}

{% block content %}
<p class="page-marker">Items: {{ total_count }}</p>
{% endblock %}

{% block extra_scripts %}
<script>
    // Tab switching + code-copy. This block MUST stay OUTSIDE the dj-root.
    // base.html wraps the page content block in <div dj-root>, and on the
    // WS-mount morph an inline script inside it would never execute.
    const mobileMenuButton = document.getElementById('mobile-menu-button');
</script>
{% endblock %}
"""

# The same comment BEFORE the root (a <head> script): exercises the locating
# scan rather than the balancing walk.
_INDEX_BEFORE = """\
{% extends "demo2663/base.html" %}

{% block head_scripts %}
<script>
    // this comment mentions <div dj-root> and that is enough
</script>
{% endblock %}

{% block content %}
<p class="page-marker">Items: {{ total_count }}</p>
{% endblock %}
"""


class ScriptCommentAfterRootView(LiveView):
    template_name = "demo2663/index_after.html"

    def mount(self, request, **kwargs):
        self.total_count = 2


class ScriptCommentBeforeRootView(LiveView):
    template_name = "demo2663/index_before.html"

    def mount(self, request, **kwargs):
        self.total_count = 2


_DJUST_BACKEND = "djust.template.backend.DjustTemplateBackend"


@pytest.fixture
def _templates_2663():
    app_config = apps.get_app_config("demo_app")
    dest_dir = os.path.join(app_config.path, "templates", "demo2663")
    os.makedirs(dest_dir, exist_ok=True)
    for name, body in (
        ("base.html", _BASE_HTML),
        ("index_after.html", _INDEX_AFTER),
        ("index_before.html", _INDEX_BEFORE),
    ):
        with open(os.path.join(dest_dir, name), "w") as f:
            f.write(body)

    override = override_settings(
        TEMPLATES=[
            {
                "BACKEND": _DJUST_BACKEND,
                "DIRS": [],
                "APP_DIRS": True,
                "OPTIONS": {
                    "context_processors": ["django.template.context_processors.request"],
                },
            }
        ]
    )
    override.enable()
    loader.engines._engines = {}
    clear_template_dirs_cache()
    try:
        yield dest_dir
    finally:
        shutil.rmtree(dest_dir, ignore_errors=True)
        override.disable()
        loader.engines._engines = {}
        clear_template_dirs_cache()


def _attach_session(request):
    engine = import_module(settings.SESSION_ENGINE)
    request.session = engine.SessionStore()
    request.session.save()


def _get(rf, view_cls):
    request = rf.get("/")
    _attach_session(request)
    response = view_cls.as_view()(request)
    assert response.status_code == 200
    return response.content.decode("utf-8")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "view_cls",
    [ScriptCommentAfterRootView, ScriptCommentBeforeRootView],
    ids=["comment-after-root", "comment-before-root"],
)
def test_script_comment_does_not_double_the_shell(rf, _templates_2663, view_cls):
    """The served document has exactly ONE shell (#2663): one doctype, one
    nav, one footer, one root, and the page content spliced into it."""
    body = _get(rf, view_cls)

    assert body.lower().count("<!doctype html>") == 1, body
    assert body.count("<nav") == 1, "shell rendered twice — two <nav>s (#2663)"
    assert body.count("<footer") == 1, "shell rendered twice — two <footer>s (#2663)"
    # The comment's own "<div dj-root>" text is served verbatim inside the
    # <script>, so count real roots OUTSIDE script bodies.
    outside_scripts = re.sub(r"<script\b.*?</script>", "", body, flags=re.DOTALL | re.IGNORECASE)
    assert outside_scripts.count("<div dj-root") == 1, body
    assert body.count("page-marker") == 1
    assert "Items: 2" in body
    # The script (and its comment) is still served verbatim, once.
    assert body.count("<script>") >= 1
    assert body.count("mentions <div dj-root>") <= 1


@pytest.mark.django_db
def test_extraction_picks_the_real_root_not_the_comment(rf, _templates_2663):
    """The get_template() pick that produced the doubled page: the VDOM
    template must be the real root, never the child source with its
    ``{% extends %}`` (#2663)."""
    view = ScriptCommentAfterRootView()
    view.mount(rf.get("/"))
    vdom_template = view.get_template()

    assert "{% extends" not in vdom_template, (
        "get_template() returned the whole child source — the phantom "
        "<div dj-root> inside the script comment was taken as the root (#2663)"
    )
    assert vdom_template.startswith("<div dj-root"), vdom_template[:80]
    assert "<script" not in vdom_template
