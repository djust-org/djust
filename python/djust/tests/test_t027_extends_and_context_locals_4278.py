"""djust.T027 follows ``{% extends %}`` chains, includes and ``return ctx`` overrides (#4278).

T027 used to skip (silently) every view whose template has ``{% extends %}`` and every
view whose ``get_context_data()`` returns a local (``ctx = super().get_context_data();
ctx["x"] = ...; return ctx``, the most common shape). It now reads a ``template_name``
view's template as the runtime does (extends resolved, includes inlined) and follows the
context local in the forms the key reader understands; what it still cannot follow is
counted in one Info message.
"""

from __future__ import annotations

from typing import Any

import pytest

from djust import LiveView
from djust.utils import clear_template_dirs_cache

pytestmark = pytest.mark.django_db


@pytest.fixture
def templates(tmp_path, settings):
    (tmp_path / "t4278").mkdir()
    files = {
        "base.html": "<title>{{ site_name }}</title>{% block body %}{% endblock %}",
        "child.html": '{% extends "t4278/base.html" %}{% block body %}<div dj-root>'
        "{{ count }}</div>{% endblock %}",
        "inc_page.html": '<div dj-root>{% include "t4278/_part.html" %}</div>',
        "_part.html": "<i>{{ from_include }}</i>",
        "flat.html": "<div dj-root>{{ count }}{{ extra }}{{ shown }}</div>",
        "user_base.html": "<nav>{{ user }}{{ site_widget }}</nav>{% block body %}{% endblock %}",
        "user_child.html": '{% extends "t4278/user_base.html" %}{% block body %}<div dj-root>'
        "</div>{% endblock %}",
        "plain_extends.html": '{% extends "t4278/base.html" %}{% block body %}'
        "<div dj-root></div>{% endblock %}",
    }
    for name, text in files.items():
        (tmp_path / "t4278" / name).write_text(text)
    dirs = [dict(t) for t in settings.TEMPLATES]
    dirs[0] = {**dirs[0], "DIRS": [str(tmp_path), *dirs[0].get("DIRS", [])]}
    settings.TEMPLATES = dirs
    clear_template_dirs_cache()
    yield tmp_path
    clear_template_dirs_cache()


def _run(monkeypatch, *views):
    from djust.checks import templates as module

    monkeypatch.setattr(module, "_walk_subclasses", lambda base: list(views))
    monkeypatch.setattr(module, "_routed_liveview_classes", lambda: [])
    return module.check_unsupplied_template_names(None)


def _named(messages, view):
    label = "%s.%s" % (view.__module__, view.__qualname__)
    return [
        m.msg.split("reads '")[1].split("'")[0] for m in messages if m.msg.startswith(label + " ")
    ]


def _info(messages):
    return [m for m in messages if m.msg.startswith("T027: skipped ")]


class FileExtends(LiveView):
    exposure_policy = "explicit"
    template_name = "t4278/child.html"

    def mount(self, request, **kwargs):
        self.count = 1


class BaseOnly(LiveView):
    exposure_policy = "explicit"
    template_name = "t4278/plain_extends.html"
    site_name = "x"


class IncludeOnly(LiveView):
    exposure_policy = "explicit"
    template_name = "t4278/inc_page.html"
    from_include = "x"


class HoldsUserInLayout(LiveView):
    exposure_policy = "explicit"
    template_name = "t4278/user_child.html"

    def mount(self, request, **kwargs):
        self.user = request.user
        self.site_widget = "w"


def project_processor_4278(request):
    return {"site_widget": "w"}


class UpdateBase(LiveView):
    exposure_policy = "explicit"

    def get_context_data(self, **kwargs: Any) -> dict:
        ctx = super().get_context_data(**kwargs)
        ctx.update(count=self.count)
        return ctx


class UpdateChild(UpdateBase):
    template_name = "t4278/flat.html"
    shown = "s"

    def mount(self, request, **kwargs):
        self.count = 1


class SubscriptStore(LiveView):
    exposure_policy = "explicit"
    template_name = "t4278/flat.html"
    shown = "s"

    def mount(self, request, **kwargs):
        self.count = 1

    def get_context_data(self, **kwargs: Any) -> dict:
        ctx = super().get_context_data(**kwargs)
        ctx["count"] = self.count
        ctx.setdefault("shown", self.shown)
        ctx.update({"more": 3})
        return ctx


class Unfollowable(LiveView):
    exposure_policy = "explicit"
    template_name = "t4278/flat.html"

    def mount(self, request, **kwargs):
        self.count = 1

    def _fill(self, ctx):
        ctx["extra"] = 1

    def get_context_data(self, **kwargs: Any) -> dict:
        ctx = super().get_context_data(**kwargs)
        self._fill(ctx)
        return ctx


class InlineExtends(LiveView):
    exposure_policy = "explicit"
    template = '{% extends "t4278/base.html" %}{% block body %}<div dj-root>{{ count }}</div>{% endblock %}'

    def mount(self, request, **kwargs):
        self.count = 1


def test_t027_follows_extends_chain(templates, monkeypatch):
    messages = _run(monkeypatch, FileExtends)
    assert _named(messages, FileExtends) == ["count"]
    (message,) = [m for m in messages if m.msg.startswith("%s." % FileExtends.__module__)]
    # The read is in the child, so its line is known.
    assert message.line_number == 1 and "at line 1" in message.msg


def test_t027_reports_a_name_read_only_in_the_base_template(templates, monkeypatch):
    messages = _run(monkeypatch, BaseOnly)
    assert _named(messages, BaseOnly) == ["site_name"]
    (message,) = [m for m in messages if "reads 'site_name'" in m.msg]
    assert message.line_number is None
    assert "in a template it extends or includes (t4278/plain_extends.html)" in message.msg


def test_t027_reports_a_name_read_only_in_an_include(templates, monkeypatch):
    messages = _run(monkeypatch, IncludeOnly)
    assert _named(messages, IncludeOnly) == ["from_include"]


def test_t027_follows_context_update_in_a_base_class(templates, monkeypatch):
    messages = _run(monkeypatch, UpdateChild)
    # ``count`` is supplied by the base's ``ctx.update(...)``; ``shown`` is held and is not.
    assert _named(messages, UpdateChild) == ["shown"]
    assert _info(messages) == []


def test_t027_follows_subscript_store_on_returned_context(templates, monkeypatch):
    messages = _run(monkeypatch, SubscriptStore)
    assert _named(messages, SubscriptStore) == []
    assert _info(messages) == []


def test_t027_still_skips_an_unfollowable_override(templates, monkeypatch):
    messages = _run(monkeypatch, Unfollowable)
    assert _named(messages, Unfollowable) == []
    (info,) = _info(messages)
    assert info.id == "djust.T027"
    assert info.msg.startswith("T027: skipped 1 view(s) whose get_context_data()")
    assert "0 view(s) whose template" in info.msg
    assert "DJUST_UNSUPPLIED_NAMES" in info.hint


def test_t027_skips_inline_extends_and_counts_it(templates, monkeypatch):
    messages = _run(monkeypatch, InlineExtends)
    assert _named(messages, InlineExtends) == []
    (info,) = _info(messages)
    assert "skipped 0 view(s) whose get_context_data()" in info.msg
    assert "1 view(s) whose template" in info.msg


def test_t018_still_skips_return_context():
    from djust.checks.templates import _get_context_data_is_too_dynamic

    assert _get_context_data_is_too_dynamic(SubscriptStore) is True
    assert _get_context_data_is_too_dynamic(UpdateChild) is True
    assert _get_context_data_is_too_dynamic(FileExtends) is False


class TenantView(LiveView):
    """A mixin's provider contract renders ``tenant``: a followed override must not report it."""

    exposure_policy = "explicit"
    template = "<div dj-root>{{ tenant }}</div>"

    def get_context_data(self, **kwargs: Any) -> dict:
        ctx = super().get_context_data(**kwargs)
        ctx["x"] = 1
        return ctx


def test_t027_counts_names_a_provider_contract_renders(templates, monkeypatch):
    from djust._exposure import ProviderContract

    class WithProvider(TenantView):
        tenant = "held"
        _djust_context_providers = (ProviderContract("t.p", rendered=frozenset({"tenant"})),)

    assert _named(_run(monkeypatch, WithProvider), WithProvider) == []

    class WithoutProvider(TenantView):
        tenant = "held"

    assert _named(_run(monkeypatch, WithoutProvider), WithoutProvider) == ["tenant"]


def _processors(settings, *paths):
    templates = [dict(t) for t in settings.TEMPLATES]
    options = dict(templates[0].get("OPTIONS", {}))
    options["context_processors"] = list(paths)
    templates[0] = {**templates[0], "OPTIONS": options}
    settings.TEMPLATES = templates


def test_t027_counts_names_a_context_processor_supplies(templates, settings, monkeypatch):
    """B1: a layout reading ``{{ user }}`` / a project processor's key is not "renders empty"."""
    _processors(
        settings,
        "django.contrib.auth.context_processors.auth",
        "djust.tests.test_t027_extends_and_context_locals_4278.project_processor_4278",
    )
    assert _named(_run(monkeypatch, HoldsUserInLayout), HoldsUserInLayout) == []


def test_t027_still_reports_those_names_without_the_processors(templates, settings, monkeypatch):
    _processors(settings)
    assert sorted(_named(_run(monkeypatch, HoldsUserInLayout), HoldsUserInLayout)) == [
        "site_widget",
        "user",
    ]


def test_t027_info_names_the_skipped_views(templates, monkeypatch):
    (info,) = _info(_run(monkeypatch, Unfollowable))
    assert "Unfollowable" in info.msg
