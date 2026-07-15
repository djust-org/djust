/**
 * Bug-capture Share button tests (B7 iter B, #1562, JSDOM).
 *
 * Exercises the debug panel's "Share bug" button end-to-end at the
 * client layer:
 *   1. renderTimeTravelTab() shows/hides the button correctly.
 *   2. Clicking the button sends a `bug_capture_share` WS frame
 *      (both direct-method AND real delegated-click DOM dispatch,
 *      per CLAUDE.md's "Delegated-listener integration tests" rule —
 *      #1196).
 *   3. A `djust:bug-capture-share-result` CustomEvent (dispatched by
 *      03-websocket.js when the server replies) copies the blob to the
 *      clipboard via navigator.clipboard.writeText and updates the
 *      panel's status indicator.
 *   4. Clipboard-unavailable fallback path.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { createPanel } from './helpers/debug-panel-harness.js';

// happy-dom (the configured vitest environment, see vitest.config.js)
// defines `navigator.clipboard` as an own, getter-only accessor property
// on the Navigator instance (configurable, no setter) — a direct
// `navigator.clipboard = {...}` assignment throws
// "Cannot set property clipboard ... which has only a getter" in
// strict-mode ES modules. `Object.defineProperty` bypasses the missing
// setter by redefining the property outright (allowed because it's
// configurable). Mirrors the pattern debug-panel-harness.js itself would
// need if happy-dom's built-in `navigator.clipboard` weren't already
// truthy (which is why the harness's own `if (!window.navigator.clipboard)`
// guard never fires under happy-dom).
function mockClipboard(writeTextImpl) {
    Object.defineProperty(window.navigator, 'clipboard', {
        value: { writeText: writeTextImpl || vi.fn().mockResolvedValue(undefined) },
        configurable: true,
        writable: true,
    });
    return window.navigator.clipboard;
}

function removeClipboard() {
    Object.defineProperty(window.navigator, 'clipboard', {
        value: undefined,
        configurable: true,
        writable: true,
    });
}

describe('Bug-capture Share button — rendering', () => {
    let panel;

    beforeEach(() => {
        panel = createPanel();
    });

    afterEach(() => {
        delete window.DjustDebugPanel;
        delete window.djustDebugPanel;
        delete window.djust;
    });

    it('is present in the header once history is captured', () => {
        panel.timeTravelHistory = [
            { event_name: 'increment', params: {}, ts: 0, state_before: {}, state_after: {} },
        ];
        const html = panel.renderTimeTravelTab();
        expect(html).toContain('tt-share-btn');
        expect(html).toContain('data-tt-share');
        expect(html).toContain('Share bug');
    });

    it('is absent from the empty state (nothing to share yet)', () => {
        panel.timeTravelHistory = [];
        const html = panel.renderTimeTravelTab();
        expect(html).not.toContain('tt-share-btn');
    });

    it('shows a "Copied!" status after a successful share', () => {
        panel.timeTravelHistory = [
            { event_name: 'increment', params: {}, ts: 0, state_before: {}, state_after: {} },
        ];
        panel._bugCaptureShareStatus = 'copied';
        const html = panel.renderTimeTravelTab();
        expect(html).toContain('tt-share-copied');
        expect(html).toContain('Copied!');
    });

    it('shows a fallback status when clipboard was unavailable', () => {
        panel.timeTravelHistory = [
            { event_name: 'increment', params: {}, ts: 0, state_before: {}, state_after: {} },
        ];
        panel._bugCaptureShareStatus = 'blob-ready';
        const html = panel.renderTimeTravelTab();
        expect(html).toContain('tt-share-manual');
    });
});

describe('Bug-capture Share button — direct method calls', () => {
    let panel;
    let sentMessages;

    beforeEach(() => {
        panel = createPanel();
        sentMessages = [];
        window.djust = {
            liveViewInstance: {
                sendMessage: (payload) => sentMessages.push(payload),
            },
        };
    });

    afterEach(() => {
        delete window.DjustDebugPanel;
        delete window.djustDebugPanel;
        delete window.djust;
    });

    it('onBugCaptureShareClick sends a bug_capture_share frame with no extra fields', () => {
        panel.onBugCaptureShareClick();
        expect(sentMessages).toEqual([{ type: 'bug_capture_share' }]);
    });

    it('onBugCaptureShareClick resets any stale status from a previous share', () => {
        panel._bugCaptureShareStatus = 'copied';
        panel.onBugCaptureShareClick();
        expect(panel._bugCaptureShareStatus).toBeNull();
    });

    it('_copyBugCaptureBlob returns false for a non-string blob', async () => {
        expect(await panel._copyBugCaptureBlob(null)).toBe(false);
        expect(await panel._copyBugCaptureBlob(undefined)).toBe(false);
        expect(await panel._copyBugCaptureBlob(42)).toBe(false);
        expect(await panel._copyBugCaptureBlob('')).toBe(false);
    });

    it('_copyBugCaptureBlob writes the blob to the clipboard and returns true', async () => {
        const clipboard = mockClipboard();
        const ok = await panel._copyBugCaptureBlob('djbug1.abc123');
        expect(ok).toBe(true);
        expect(clipboard.writeText).toHaveBeenCalledWith('djbug1.abc123');
    });

    it('_copyBugCaptureBlob returns false when the clipboard API is absent (gate-off)', async () => {
        // Gate-off self-test (#1468): removing the primitive under test
        // must flip the assertion — proves the prior "returns true" case
        // isn't tautological.
        removeClipboard();
        const ok = await panel._copyBugCaptureBlob('djbug1.abc123');
        expect(ok).toBe(false);
    });

    it('onBugCaptureShareResult ignores a frame without a blob', async () => {
        const clipboard = mockClipboard();
        panel.onBugCaptureShareResult({});
        panel.onBugCaptureShareResult(null);
        panel.onBugCaptureShareResult({ blob: '' });
        // Allow any pending microtasks to flush.
        await new Promise((r) => setTimeout(r, 0));
        expect(clipboard.writeText).not.toHaveBeenCalled();
        expect(panel._bugCaptureShareStatus).toBeUndefined();
    });

    it('onBugCaptureShareResult copies the blob and sets status=copied', async () => {
        const clipboard = mockClipboard();
        panel.onBugCaptureShareResult({ blob: 'djbug1.xyz' });
        await new Promise((r) => setTimeout(r, 0));
        expect(clipboard.writeText).toHaveBeenCalledWith('djbug1.xyz');
        expect(panel._bugCaptureShareStatus).toBe('copied');
        expect(panel._bugCaptureShareBlob).toBe('djbug1.xyz');
    });

    it('onBugCaptureShareResult falls back to status=blob-ready when clipboard write fails', async () => {
        removeClipboard();
        panel.onBugCaptureShareResult({ blob: 'djbug1.xyz' });
        await new Promise((r) => setTimeout(r, 0));
        expect(panel._bugCaptureShareStatus).toBe('blob-ready');
    });
});

describe('Bug-capture Share button — delegated click handler integration', () => {
    let panel;
    let sentMessages;
    let panelEl;

    beforeEach(() => {
        panel = createPanel();
        sentMessages = [];
        window.djust = {
            liveViewInstance: {
                sendMessage: (payload) => sentMessages.push(payload),
            },
        };
        // Real DOM that registerTimeTravelClickHandlers attaches to —
        // same setup as the existing .tt-jump / .tt-forward-replay
        // delegated-click integration tests in
        // debug_panel_time_travel_ui.test.js.
        panelEl = document.createElement('div');
        document.body.appendChild(panelEl);
        panel.panel = panelEl;
        panel._ttClickBound = false;
        panel.registerTimeTravelClickHandlers();
    });

    afterEach(() => {
        if (panelEl && panelEl.parentNode) panelEl.parentNode.removeChild(panelEl);
        delete window.DjustDebugPanel;
        delete window.djustDebugPanel;
        delete window.djust;
    });

    it('click on .tt-share-btn dispatches a bug_capture_share frame', () => {
        const button = document.createElement('button');
        button.className = 'tt-share-btn';
        button.setAttribute('data-tt-share', '');
        panelEl.appendChild(button);
        button.click();
        expect(sentMessages).toEqual([{ type: 'bug_capture_share' }]);
    });

    it('click on the rendered Share button (from renderTimeTravelTab HTML) dispatches the frame', () => {
        panel.timeTravelHistory = [
            { event_name: 'increment', params: {}, ts: 0, state_before: {}, state_after: {} },
        ];
        panelEl.innerHTML = panel.renderTimeTravelTab();
        const button = panelEl.querySelector('.tt-share-btn');
        expect(button).not.toBeNull();
        button.click();
        expect(sentMessages).toEqual([{ type: 'bug_capture_share' }]);
    });

    it('gate-off: removing the button from the DOM means no frame fires', () => {
        // Non-tautology check (#1200/#1468): a click that never reaches a
        // .tt-share-btn must NOT send anything.
        const button = document.createElement('button');
        button.className = 'unrelated-button';
        panelEl.appendChild(button);
        button.click();
        expect(sentMessages).toHaveLength(0);
    });
});

describe('Bug-capture Share button — end-to-end WS-frame-to-clipboard wiring', () => {
    let panel;

    let clipboard;

    beforeEach(() => {
        panel = createPanel();
        clipboard = mockClipboard();
    });

    afterEach(() => {
        delete window.DjustDebugPanel;
        delete window.djustDebugPanel;
        delete window.djust;
    });

    it('a djust:bug-capture-share-result CustomEvent copies the blob to the clipboard', async () => {
        // registerTabs() (called from the panel constructor via init())
        // already wired the document-level listener per
        // 02-tab-system.js. This simulates what 03-websocket.js does
        // when the server's bug_capture_share_result frame arrives.
        document.dispatchEvent(new CustomEvent('djust:bug-capture-share-result', {
            detail: { type: 'bug_capture_share_result', blob: 'djbug1.end-to-end' },
            bubbles: true,
        }));
        await new Promise((r) => setTimeout(r, 0));
        expect(clipboard.writeText).toHaveBeenCalledWith('djbug1.end-to-end');
        expect(panel._bugCaptureShareStatus).toBe('copied');
    });
});
