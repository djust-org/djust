"""The 1.x compatibility shims are gone (#4207)."""

from pathlib import Path

import pytest
from django.test import RequestFactory

from djust._removed_settings import REMOVED_NAMES

THEMING = Path(__file__).resolve().parents[1] / "theming"
_THEMING_KEYS = sorted(k for k in REMOVED_NAMES if k[0].startswith("djust.theming."))


class TestServerShims:
    def test_consumer_has_no_handle_event_url_change_or_mount(self):
        from djust.websocket import LiveViewConsumer

        for name in ("handle_event", "handle_url_change", "handle_mount"):
            assert not hasattr(LiveViewConsumer, name), name

    def test_push_event_mixin_has_no_private_flush_alias(self):
        from djust.mixins.push_events import PushEventMixin

        assert not hasattr(PushEventMixin, "_flush_pending_push_events")
        assert hasattr(PushEventMixin, "flush_push_events")

    def test_startproject_is_refused_naming_new(self, capsys, monkeypatch):
        from djust import cli

        monkeypatch.setattr("sys.argv", ["djust", "startproject", "demo"])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 2
        assert "djust new" in capsys.readouterr().err


class TestThemingReExports:
    def test_the_table_covers_every_removed_name(self):
        names = {n for m, n in _THEMING_KEYS}
        assert {
            "ColorScale",
            "ThemeTokens",
            "DEFAULT_CONFIG",
            "BLUE_THEME",
            "PATTERN_GLASS",
        } <= names

    @pytest.mark.parametrize("module,name", _THEMING_KEYS)
    def test_import_is_refused_naming_djust_theming(self, module, name):
        with pytest.raises(ImportError) as exc:
            exec("from %s import %s" % (module, name), {})
        assert "djust.theming" in str(exc.value)
        assert "removed in djust 2.0" in str(exc.value)

    def test_the_replacement_import_works(self):
        from djust.theming import ColorScale, ThemeTokens  # noqa: F401
        from djust.theming._config import DEFAULT_CONFIG  # noqa: F401

    def test_an_unknown_name_is_still_an_attribute_error(self):
        import djust.theming.presets as presets

        with pytest.raises(AttributeError):
            presets.NO_SUCH_NAME  # noqa: B018


class TestBaseCssAndThemeJs:
    def test_base_css_is_gone_and_scaffold_stays(self):
        css = THEMING / "static/djust_theming/css"
        assert not (css / "base.css").exists()
        assert (css / "scaffold.css").exists()

    def test_theme_js_has_no_client_command_listener_or_legacy_cookie_delete(self):
        js = (THEMING / "static/djust_theming/js/theme.js").read_text()
        assert "client-command" not in js
        assert "_deleteLegacyCookie" not in js


@pytest.mark.django_db
def test_namespaced_site_ignores_an_unprefixed_pack_cookie():
    from djust.theming.manager import ThemeManager

    req = RequestFactory().get("/")
    req.COOKIES["djust_theme_pack"] = "aurora"
    req.session = {}
    mgr = ThemeManager(request=req)
    mgr.config = dict(mgr.config, cookie_namespace="ns", enable_client_override=True)
    assert mgr.get_state().pack != "aurora"
    req.COOKIES["ns_djust_theme_pack"] = "aurora"
    assert mgr.get_state().pack == "aurora"
