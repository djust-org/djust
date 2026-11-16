/**
 * The State tab's Share button (#4184): a bug capture built in the browser from
 * the history the tab already keeps — the selected (or latest) event's state,
 * the state before it, and the patches that event's response carried.
 * Time travel and its server-side buffer were removed; Share never needed them.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createPanel } from './helpers/debug-panel-harness.js';

function decode(blob) {
    expect(blob.startsWith('djbug1.')).toBe(true);
    const b64 = blob.slice('djbug1.'.length).replace(/-/g, '+').replace(/_/g, '/');
    const binary = atob(b64 + '='.repeat((4 - (b64.length % 4)) % 4));
    const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
    return JSON.parse(new TextDecoder().decode(bytes));
}

describe('State tab Share', () => {
    let panel;
    let writeText;

    beforeEach(() => {
        window.DJUST_DEBUG_INFO = { config: { maxHistory: 50, bugCaptureScrub: ['password'] } };
        panel = createPanel();
        writeText = vi.fn(() => Promise.resolve());
        window.navigator.clipboard.writeText = writeText;
        // Mount, then one event whose response carried variables and patches.
        panel.processDebugInfo({
            _isMounted: true,
            variables: { count: { name: 'count', type: 'int', value: '0' } },
        });
        panel.stateHistory[0].timestamp -= 1000; // outside the 100 ms de-dup window
        panel.processDebugInfo({
            _eventName: 'bump',
            variables: {
                count: { name: 'count', type: 'int', value: '1' },
                password: { name: 'password', type: 'str', value: '"hunter2"' },
                secret: { name: 'secret', type: 'redacted', value: '"[redacted]"' },
            },
            patches: [{ type: 'SetText', path: [0, 1], text: 'count=1' }],
        });
    });

    afterEach(() => {
        panel.destroy();
        delete window.DJUST_DEBUG_INFO;
    });

    it('keeps the patches each event carried on its history entry', () => {
        expect(panel.stateHistory[0].patches).toEqual([
            { type: 'SetText', path: [0, 1], text: 'count=1' },
        ]);
        expect(panel.stateHistory[1].patches).toBeNull();
    });

    it('renders a Share latest button and a Share button per entry', () => {
        const html = panel.renderStateTab();
        expect(html).toContain('shareStateEntry(0)');
        expect(html).toContain('Share latest');
        expect(html).toContain('shareStateEntry(1)');
    });

    it('copies a capture with the before and after state and the real patches', () => {
        const blob = panel.shareStateEntry(0);
        expect(writeText).toHaveBeenCalledWith(blob);
        expect(panel.lastBugCapture).toBe(blob);
        const capture = decode(blob);
        expect(capture).toEqual({
            v: 'djbug1',
            event_name: 'bump',
            state_before: { count: '0' },
            state_after: { count: '1', secret: '"[redacted]"' },
            vdom_patches: [{ type: 'SetText', path: [0, 1], text: 'count=1' }],
            scrubbed_fields: ['password'],
        });
    });

    it('applies the scrub list before encoding: the value never enters the blob', () => {
        const blob = panel.shareStateEntry(0);
        expect(atob(blob.slice(7).replace(/-/g, '+').replace(/_/g, '/') + '==')).not.toContain('hunter2');
    });

    it('shares an earlier entry too, and the oldest one has an empty before state', () => {
        const capture = decode(panel.shareStateEntry(1));
        expect(capture.state_before).toEqual({});
        expect(capture.state_after).toEqual({ count: '0' });
        expect(capture.vdom_patches).toEqual([]);
    });

    it('sends nothing to the server', () => {
        const send = vi.fn();
        window.djust = { liveViewInstance: { sendMessage: send, ws: { send } } };
        panel.shareStateEntry(0);
        expect(send).not.toHaveBeenCalled();
        delete window.djust;
    });

    it('returns null for an index with no entry', () => {
        expect(panel.shareStateEntry(9)).toBeNull();
        expect(writeText).not.toHaveBeenCalled();
    });
});
