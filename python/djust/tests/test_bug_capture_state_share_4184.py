"""The debug panel's State-tab Share button encodes a capture the Python side reads (#4184).

Share no longer goes through the server: the panel builds the ``djbug1.`` blob in the
browser from the history its State tab already keeps. These tests run the real
built ``debug-panel.js`` methods under node and decode the result with
``BugCapture.decode`` (the decoder the replay viewer and ``djust replay`` use), and pin
the scrub list the server hands the panel.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from django.test import override_settings

from djust.bug_capture import BugCapture

PANEL = Path(__file__).resolve().parents[1] / "static" / "djust" / "debug-panel.js"

RUNNER = r"""
const fs = require('fs');
globalThis.window = globalThis;
window.DEBUG_MODE = true;
new Function(fs.readFileSync(process.argv[2], 'utf8')).call(globalThis);
const input = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
window.DJUST_DEBUG_INFO = { config: { bugCaptureScrub: input.scrub } };
const P = window.DjustDebugPanel.prototype;
const self = { stateHistory: input.history };
process.stdout.write(P.encodeBugCapture.call(self, P.buildBugCapture.call(self, input.index)));
"""


def _encode_in_browser_code(tmp_path, history, *, index=0, scrub=()):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    runner = tmp_path / "share.js"
    runner.write_text(RUNNER)
    data = tmp_path / "input.json"
    data.write_text(json.dumps({"history": history, "index": index, "scrub": list(scrub)}))
    result = subprocess.run(
        [node, str(runner), str(PANEL), str(data)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


HISTORY = [
    {
        "eventName": "next_step",
        "state": {
            "step": {"name": "step", "type": "str", "value": '"vehicle"'},
            "password": {"name": "password", "type": "str", "value": '"hunter2"'},
            "note": {"name": "note", "type": "str", "value": '"café ✓"'},
        },
        "patches": [{"type": "SetText", "path": [0, 2], "text": "Vehicle"}],
    },
    {
        "eventName": None,
        "state": {"step": {"name": "step", "type": "str", "value": '"claimant"'}},
        "patches": None,
    },
]


def test_the_browser_blob_round_trips_through_the_python_decoder(tmp_path):
    blob = _encode_in_browser_code(tmp_path, HISTORY, scrub=["password"])
    capture = BugCapture.decode(blob)
    assert capture.event_name == "next_step"
    assert capture.state_before == {"step": '"claimant"'}
    assert capture.state_after == {"step": '"vehicle"', "note": '"café ✓"'}
    # The event's real patches, not a re-render after the fact.
    assert capture.vdom_patches == [{"type": "SetText", "path": [0, 2], "text": "Vehicle"}]
    assert capture.scrubbed_fields == ["password"]
    assert "hunter2" not in json.dumps(capture.to_dict())


@override_settings(DEBUG=True)
def test_the_browser_blob_matches_what_python_encodes_for_the_same_capture(tmp_path):
    ascii_history = [dict(HISTORY[0], state={"step": HISTORY[0]["state"]["step"]}), HISTORY[1]]
    blob = _encode_in_browser_code(tmp_path, ascii_history)
    expected = BugCapture(
        state_before={"step": '"claimant"'},
        state_after={"step": '"vehicle"'},
        vdom_patches=[{"type": "SetText", "path": [0, 2], "text": "Vehicle"}],
        event_name="next_step",
    ).encode()
    assert blob == expected


def test_the_server_hands_the_panel_the_scrub_list():
    from djust import LiveView
    from djust.decorators import state

    class Shared(LiveView):
        exposure_policy = "explicit"
        template = "<div dj-root></div>"
        visible = state(1, client=True)

    view = object.__new__(Shared)
    with override_settings(LIVEVIEW_CONFIG={"bug_capture_default_scrub": ["password", "ssn"]}):
        cfg = view.get_debug_info()["config"]
    assert cfg["bugCaptureScrub"] == ["password", "ssn"]


def test_a_malformed_scrub_list_is_ignored_not_split_into_characters(caplog):
    from djust.mixins.post_processing import bug_capture_scrub

    with override_settings(LIVEVIEW_CONFIG={"bug_capture_default_scrub": "password"}):
        assert bug_capture_scrub() == []
    assert "must be a list of field names" in caplog.text
