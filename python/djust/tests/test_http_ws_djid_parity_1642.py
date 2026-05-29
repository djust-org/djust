"""Test-infra: HTTP-GET vs WebSocket-mount dj-id parity harness (#1642).

From the #1641 investigation: every VDOM-if reproducer used
``LiveViewTestClient.render_with_patches()`` for both baseline and transition,
so the real initial-page path (HTTP ``GET`` renders the browser DOM; the WS
mount establishes a separate baseline) was never exercised. If those two paths
assigned divergent ``dj-id`` baselines, the first WS event's patches would miss
``d``-resolution and fall back to path traversal — the ``getNodeByPath → null``
failure shape #1636/#1641 describe.

`LiveViewTestClient.assert_http_ws_djid_parity()` makes that hypothesis testable
framework-side (without a downstream consumer's private repo) and locks the
#1370 "marker IDs match between initial HTTP DOM and subsequent WS diffs"
invariant against regression.

These cases all currently PASS — i.e. parity holds across the shapes derivable
from #1641 — which is itself the documented finding: #1641's failure is not a
framework-side HTTP/WS dj-id divergence for these shapes. The harness exists so
the next investigator can drop a suspect view in and get a definitive answer.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from djust import LiveView
from djust.decorators import event_handler
from djust.testing import LiveViewTestClient


class _SimpleView(LiveView):
    template = '<div dj-root><div class="c"><span>{{ n }}</span></div></div>'

    def mount(self, request, **kwargs):
        self.n = 0


class _IfAddView(LiveView):
    """The #1641 shape: a false->true {% if %} add with no {% else %}, plus a
    sibling form control."""

    template = (
        '<div dj-root><div class="c">'
        '<div class="card">Settings</div>'
        '<input name="q" value="{{ q }}">'
        '{% if show %}<div class="status">{{ status }}</div>{% endif %}'
        "</div></div>"
    )

    def mount(self, request, **kwargs):
        self.q = ""
        self.show = False
        self.status = "idle"

    @event_handler()
    def go(self, **kwargs):
        self.show = True
        self.status = "fetching"


class _NestedIfElifView(LiveView):
    template = (
        '<div dj-root><div class="c"><section><div class="b">'
        "<p>v: <strong>{{ q }}</strong></p>"
        "{% if a %}<span>a</span>{% elif b %}<span>b</span>{% endif %}"
        "</div></section></div></div>"
    )

    def mount(self, request, **kwargs):
        self.q = ""
        self.a = False
        self.b = False


@pytest.mark.django_db
@pytest.mark.parametrize("view_cls", [_SimpleView, _IfAddView, _NestedIfElifView])
def test_http_ws_djid_parity_holds(view_cls):
    """The HTTP-GET and WS-mount render paths assign an identical dj-id baseline
    for each representative view shape (#1642)."""
    client = LiveViewTestClient(view_cls)
    ids = client.assert_http_ws_djid_parity()
    # Sanity: a non-trivial view actually produced a baseline to compare.
    assert isinstance(ids, list)


@pytest.mark.django_db
def test_parity_harness_extracts_djroot_djids():
    """Unit-pin the extraction helper: it returns the ordered dj-id sequence
    from the dj-root subtree only."""
    html = (
        '<head><meta dj-id="ignored-shell"></head>'
        '<div dj-root dj-id="0"><span dj-id="1">x</span><b dj-id="2">y</b></div>'
    )
    assert LiveViewTestClient._djroot_djids(html) == ["0", "1", "2"]


@pytest.mark.django_db
def test_parity_harness_reports_divergence_empirical_canary():
    """Empirical canary (#1654, Action #1468 spirit): the harness passes for
    every real view shape today, so its catch-power rests on the
    path-differential argument. Prove it actually REPORTS a divergence by
    forcing one — patch the dj-id extraction so the HTTP and WS baselines differ
    — and assert ``assert_http_ws_djid_parity`` raises with a useful message.

    Without this, a future refactor that silently neutered the comparison (e.g.
    always returning the same list, or comparing an instance against itself)
    would leave the harness green-but-toothless and this whole test file would
    still pass.
    """
    client = LiveViewTestClient(_SimpleView)

    # First call (HTTP baseline) → ["0","1"]; second call (WS baseline) → ["0","2"].
    diverging = [["0", "1"], ["0", "2"]]
    with patch.object(LiveViewTestClient, "_djroot_djids", side_effect=diverging):
        with pytest.raises(AssertionError) as exc:
            client.assert_http_ws_djid_parity()

    msg = str(exc.value)
    # The diagnostic must name both baselines so a real divergence is debuggable.
    assert "divergence" in msg.lower()
    assert "['0', '1']" in msg and "['0', '2']" in msg


@pytest.mark.django_db
def test_parity_harness_passes_when_baselines_match_canary_control():
    """Control for the canary: identical patched baselines must NOT raise — so
    the canary's failure above is attributable to the divergence, not the patch
    machinery itself."""
    client = LiveViewTestClient(_SimpleView)
    with patch.object(LiveViewTestClient, "_djroot_djids", side_effect=[["0", "1"], ["0", "1"]]):
        ids = client.assert_http_ws_djid_parity()
    assert ids == ["0", "1"]
