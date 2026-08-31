"""Tests for the ``djust replay`` CLI (B7 iter C, #1561).

Covers the three modes from the issue — browser, ``--inspect``, ``--diff`` —
plus the two things a CLI that opens URLs has to get right: it must not become
a way to talk someone into opening an attacker-chosen URI, and it must refuse a
malformed blob with a message rather than a traceback.

Every case drives the real ``main()`` argv path where it can, so the argparse
wiring (the subparser, the mutually-exclusive mode group, the dispatch-table
entry) is exercised rather than assumed — a direct ``cmd_replay(Namespace(...))``
call would pass even if the subparser were never registered.
"""

from __future__ import annotations

import json
import sys

import pytest
from django.test import override_settings

from djust.bug_capture import BugCapture
from djust.cli import REPLAY_DEFAULT_BASE_URL, _extract_blob, _replay_url, main


def _capture() -> BugCapture:
    return BugCapture(
        state_before={"count": 0, "step": "claimant", "gone": True},
        state_after={"count": 1, "step": "vehicle"},
        vdom_patches=[{"op": "text", "path": [0, 1], "text": "1"}],
        event_name="next_step",
    )


@pytest.fixture
def blob():
    with override_settings(DEBUG=True):
        return _capture().encode()


def _run(argv, monkeypatch):
    """Run ``main()`` with *argv* and return its exit code."""
    monkeypatch.setattr(sys, "argv", ["djust", *argv])
    with pytest.raises(SystemExit) as exc:
        main()
    return exc.value.code


# ---------------------------------------------------------------------------
# Blob extraction — the input surface
# ---------------------------------------------------------------------------


class TestExtractBlob:
    def test_bare_blob_passes_through(self, blob):
        assert _extract_blob(blob) == blob

    def test_surrounding_whitespace_is_stripped(self, blob):
        assert _extract_blob("  %s\n" % blob) == blob

    def test_full_replay_url_is_accepted(self, blob):
        url = "http://localhost:8000/__djust__/replay/" + blob
        assert _extract_blob(url) == blob

    def test_trailing_slash_is_tolerated(self, blob):
        assert _extract_blob("http://h/__djust__/replay/%s/" % blob) == blob

    def test_query_and_fragment_are_dropped(self, blob):
        assert _extract_blob("http://h/replay/%s?x=1#top" % blob) == blob

    def test_store_reference_form_is_accepted(self):
        # Shape only — resolving it needs a configured store (that path is
        # covered by the store's own suite).
        assert _extract_blob("djbug1.store.abcdefghijklmnopqrstuv") == (
            "djbug1.store.abcdefghijklmnopqrstuv"
        )

    @pytest.mark.parametrize(
        "hostile",
        [
            "https://phishing.example/pay",
            "http://127.0.0.1:8000/admin/logout/",
            "file:///etc/passwd",
            "javascript:alert(1)",
            "not-a-blob",
            "djbug2.abcdef",
            "",
        ],
    )
    def test_anything_that_is_not_a_blob_is_refused(self, hostile):
        """A blob arrives by paste, so this is the "open what I sent you" guard."""
        with pytest.raises(ValueError, match="not a bug-capture blob"):
            _extract_blob(hostile)


# ---------------------------------------------------------------------------
# URL construction
# ---------------------------------------------------------------------------


class TestReplayUrl:
    def test_default_host_and_literal_route(self, blob):
        url = _replay_url(blob, REPLAY_DEFAULT_BASE_URL)
        assert url == REPLAY_DEFAULT_BASE_URL + "/__djust__/replay/" + blob
        # The blob survives verbatim: every character of the base64url
        # alphabet (plus `.`) is unreserved, so quoting must not mangle it.
        assert blob in url

    def test_base_url_trailing_slash_does_not_double(self, blob):
        assert "//__djust__" not in _replay_url(blob, "http://h:9000/")

    @pytest.mark.parametrize(
        "scheme_url",
        [
            "javascript:alert(1)",
            "file:///tmp",
            "data:text/html,<script>alert(1)</script>",
            "ftp://h/x",
            "",
        ],
    )
    def test_non_http_base_url_is_refused(self, blob, scheme_url):
        """The result is handed to ``webbrowser.open``, so bound the scheme."""
        with pytest.raises(ValueError, match="must be http"):
            _replay_url(blob, scheme_url)

    def test_uses_the_urlconf_when_the_route_is_mounted(self, blob, settings):
        """A project that mounts ``djust.urls`` under a prefix gets its prefix."""
        settings.DEBUG = True
        from django.urls import path, include, clear_url_caches
        import types

        module = types.ModuleType("_replay_cli_urlconf")
        module.urlpatterns = [path("tools/", include("djust.urls"))]
        sys.modules["_replay_cli_urlconf"] = module
        try:
            settings.ROOT_URLCONF = "_replay_cli_urlconf"
            clear_url_caches()
            url = _replay_url(blob, "http://h:9000")
        finally:
            del sys.modules["_replay_cli_urlconf"]
            clear_url_caches()
        assert "/tools/__djust__/replay/" in url


# ---------------------------------------------------------------------------
# The three modes, through the real argv path
# ---------------------------------------------------------------------------


class TestInspectMode:
    def test_prints_one_json_document_with_every_field(self, blob, monkeypatch, capsys):
        assert _run(["replay", "--inspect", blob], monkeypatch) == 0
        payload = json.loads(capsys.readouterr().out)
        original = _capture()
        assert payload["state_before"] == original.state_before
        assert payload["state_after"] == original.state_after
        assert payload["vdom_patches"] == original.vdom_patches
        assert payload["event_name"] == "next_step"
        assert payload["scrubbed_fields"] == []

    def test_output_is_parseable_by_a_jq_style_consumer(self, blob, monkeypatch, capsys):
        """One document, not a concatenation — the point of the mode."""
        assert _run(["replay", "--inspect", blob], monkeypatch) == 0
        out = capsys.readouterr().out
        assert out.count("\n") > 1
        assert isinstance(json.loads(out), dict)

    def test_inspect_never_opens_a_browser(self, blob, monkeypatch):
        import webbrowser

        opened = []
        monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
        assert _run(["replay", "--inspect", blob], monkeypatch) == 0
        assert opened == []


class TestDiffMode:
    def test_prints_a_unified_diff_of_the_two_states(self, blob, monkeypatch, capsys):
        assert _run(["replay", "--diff", blob], monkeypatch) == 0
        out = capsys.readouterr().out
        assert out.startswith("--- state_before")
        assert "+++ state_after" in out
        # `count` changed, `gone` was removed, `step` changed.
        assert '-  "count": 0' in out
        assert '+  "count": 1' in out
        assert '-  "gone": true' in out

    def test_identical_states_say_so_on_stderr_not_stdout(self, monkeypatch, capsys):
        """`djust replay --diff … > patch` must still write an EMPTY patch."""
        with override_settings(DEBUG=True):
            same = BugCapture(state_before={"a": 1}, state_after={"a": 1}, vdom_patches=[]).encode()
        assert _run(["replay", "--diff", same], monkeypatch) == 0
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "identical" in captured.err

    def test_diff_never_opens_a_browser(self, blob, monkeypatch):
        import webbrowser

        opened = []
        monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
        assert _run(["replay", "--diff", blob], monkeypatch) == 0
        assert opened == []


class TestBrowserMode:
    def test_opens_the_replay_url(self, blob, monkeypatch, capsys):
        import webbrowser

        opened = []
        monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
        assert _run(["replay", blob], monkeypatch) == 0
        assert len(opened) == 1
        assert opened[0].startswith(REPLAY_DEFAULT_BASE_URL)
        assert "__djust__/replay/" in opened[0]
        assert opened[0] in capsys.readouterr().out

    def test_base_url_flag_wins(self, blob, monkeypatch):
        import webbrowser

        opened = []
        monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
        monkeypatch.setenv("DJUST_REPLAY_BASE_URL", "http://from-env:1234")
        assert _run(["replay", "--base-url", "http://from-flag:9999", blob], monkeypatch) == 0
        assert opened[0].startswith("http://from-flag:9999")

    def test_env_var_is_used_when_the_flag_is_absent(self, blob, monkeypatch):
        import webbrowser

        opened = []
        monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
        monkeypatch.setenv("DJUST_REPLAY_BASE_URL", "http://from-env:1234")
        assert _run(["replay", blob], monkeypatch) == 0
        assert opened[0].startswith("http://from-env:1234")

    def test_a_url_paste_round_trips_to_the_same_blob(self, blob, monkeypatch):
        """Paste the URL a teammate sent; get the same page back."""
        import webbrowser

        opened = []
        monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
        pasted = "http://their-box:8000/__djust__/replay/" + blob
        assert _run(["replay", "--base-url", "http://my-box:8000", pasted], monkeypatch) == 0
        assert opened[0] == "http://my-box:8000/__djust__/replay/" + blob

    def test_a_browser_that_will_not_open_is_a_nonzero_exit(self, blob, monkeypatch, capsys):
        import webbrowser

        monkeypatch.setattr(webbrowser, "open", lambda url: False)
        assert _run(["replay", blob], monkeypatch) == 1
        assert "Paste the URL" in capsys.readouterr().out


class TestErrorPaths:
    def test_a_non_blob_argument_is_a_message_not_a_traceback(self, monkeypatch, capsys):
        assert _run(["replay", "https://phishing.example/"], monkeypatch) == 1
        assert "not a bug-capture blob" in capsys.readouterr().out

    def test_a_non_blob_argument_never_opens_a_browser(self, monkeypatch):
        import webbrowser

        opened = []
        monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
        assert _run(["replay", "https://phishing.example/"], monkeypatch) == 1
        assert opened == []

    def test_a_corrupt_blob_is_a_message_not_a_traceback(self, monkeypatch, capsys):
        assert _run(["replay", "djbug1.!!!not-base64!!!"], monkeypatch) == 1
        assert "Error:" in capsys.readouterr().out

    def test_a_non_http_base_url_is_refused_before_opening(self, blob, monkeypatch, capsys):
        import webbrowser

        opened = []
        monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
        assert _run(["replay", "--base-url", "javascript:alert(1)", blob], monkeypatch) == 1
        assert opened == []
        assert "must be http" in capsys.readouterr().out

    def test_inspect_and_diff_are_mutually_exclusive(self, blob, monkeypatch):
        assert _run(["replay", "--inspect", "--diff", blob], monkeypatch) == 2


class TestSubparserWiring:
    def test_replay_is_listed_in_the_top_level_help(self, monkeypatch, capsys):
        assert _run([], monkeypatch) == 0
        assert "replay" in capsys.readouterr().out

    def test_replay_requires_a_blob(self, monkeypatch):
        assert _run(["replay"], monkeypatch) == 2
