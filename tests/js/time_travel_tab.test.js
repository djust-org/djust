/**
 * Time-Travel tab tests (v0.6.1, JSDOM).
 *
 * Exercise the real debug-panel IIFE code paths for the Time Travel
 * tab:
 *   1. renderTimeTravelTab produces empty state with no history
 *   2. renderTimeTravelTab produces a row per captured event
 *   3. onTimeTravelState updates cursor and marks row active
 *
 * Click-to-jump wiring goes through the main djust WS (globalThis.djust.websocket)
 * which isn't present in the harness; we test onTimeTravelJumpClick with
 * a stub WS instead.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { createPanel } from './helpers/debug-panel-harness.js';

describe('Time Travel tab (v0.6.1)', () => {
    let panel;

    beforeEach(() => {
        panel = createPanel();
    });

    afterEach(() => {
        panel.destroy();
        delete globalThis.djust;
    });

    it('renders empty state when no history is captured', () => {
        panel.timeTravelHistory = [];
        const html = panel.renderTimeTravelTab();
        expect(html).toContain('No time-travel snapshots captured');
        expect(html).toContain('time_travel_enabled');
    });

    it('renders one row per captured event', () => {
        panel.timeTravelHistory = [
            { event_name: 'increment', params: { n: 1 }, ref: 1, ts: 1_700_000_000, state_before: {}, state_after: { count: 1 }, error: null },
            { event_name: 'decrement', params: {}, ref: 2, ts: 1_700_000_100, state_before: { count: 1 }, state_after: { count: 0 }, error: null },
            { event_name: 'boom', params: {}, ref: 3, ts: 1_700_000_200, state_before: {}, state_after: {}, error: 'kaboom' },
        ];
        panel.timeTravelCursor = -1;
        const html = panel.renderTimeTravelTab();
        expect(html).toContain('increment');
        expect(html).toContain('decrement');
        expect(html).toContain('boom');
        // Error badge for the failing event.
        expect(html).toContain('tt-error');
        expect(html).toContain('3 events');
        // Cursor label shows "Live" when no jump is active.
        expect(html).toContain('Live');
    });

    it('onTimeTravelState updates cursor + which and re-renders when active', () => {
        panel.timeTravelHistory = [
            { event_name: 'a', params: {}, ref: 1, ts: 1_700_000_000, state_before: {}, state_after: {}, error: null },
            { event_name: 'b', params: {}, ref: 2, ts: 1_700_000_100, state_before: {}, state_after: {}, error: null },
        ];
        // Stub refreshActiveTab so we can observe re-render attempts.
        panel.refreshActiveTab = vi.fn();
        panel.state.activeTab = 'timeTravel';

        panel.onTimeTravelState({ cursor: 1, which: 'after', history_len: 2 });

        expect(panel.timeTravelCursor).toBe(1);
        expect(panel.timeTravelWhich).toBe('after');
        expect(panel.refreshActiveTab).toHaveBeenCalledTimes(1);

        // Active row marker appears in rendered HTML.
        const html = panel.renderTimeTravelTab();
        expect(html).toContain('tt-active');
        expect(html).toContain('Cursor: #1');
        expect(html).toContain('after');
    });

    it('onTimeTravelJumpClick sends the correct WS frame', () => {
        const sendMessage = vi.fn();
        globalThis.djust = { websocket: { sendMessage } };

        panel.onTimeTravelJumpClick(2, 'after');

        expect(sendMessage).toHaveBeenCalledWith({
            type: 'time_travel_jump',
            index: 2,
            which: 'after',
        });
    });

    // v0.6.1 Fix #2 regression: clicking a .tt-jump button in the
    // rendered tab must dispatch onTimeTravelJumpClick with the correct
    // index/which. Previously there was no delegated click handler and
    // clicks were no-ops in the browser.
    it('clicking a .tt-jump button fires the jump via delegation', () => {
        const sendMessage = vi.fn();
        globalThis.djust = { websocket: { sendMessage } };

        panel.timeTravelHistory = [
            { event_name: 'increment', params: {}, ref: 1, ts: 1_700_000_000, state_before: {}, state_after: { count: 1 }, error: null },
        ];
        // Render the tab into the panel's tab-content container so the
        // delegated listener (attached to panel root at init time) can
        // see the click.
        panel.state.activeTab = 'timeTravel';
        panel.renderTabContent();

        const button = panel.panel.querySelector('.tt-jump[data-tt-jump="0"][data-tt-which="before"]');
        expect(button).not.toBeNull();
        button.click();

        expect(sendMessage).toHaveBeenCalledTimes(1);
        expect(sendMessage).toHaveBeenCalledWith({
            type: 'time_travel_jump',
            index: 0,
            which: 'before',
        });
    });

    // v0.6.1 Fix #3b regression: the client receives server-pushed
    // time_travel_event frames and appends them to the history so the
    // timeline populates incrementally.
    it('djust:time-travel-event appends to history and re-renders active tab', () => {
        panel.timeTravelHistory = [];
        panel.state.activeTab = 'timeTravel';
        panel.refreshActiveTab = vi.fn();

        document.dispatchEvent(new CustomEvent('djust:time-travel-event', {
            detail: {
                type: 'time_travel_event',
                entry: {
                    event_name: 'incremented',
                    params: { n: 1 },
                    ref: 7,
                    ts: 1_700_000_000,
                    state_before: { count: 0 },
                    state_after: { count: 1 },
                    error: null,
                },
                history_len: 1,
            },
        }));

        expect(panel.timeTravelHistory).toHaveLength(1);
        expect(panel.timeTravelHistory[0].event_name).toBe('incremented');
        expect(panel.refreshActiveTab).toHaveBeenCalledTimes(1);
    });

    it('djust:time-travel-event is a no-op when entry is missing', () => {
        panel.timeTravelHistory = [];
        panel.refreshActiveTab = vi.fn();

        document.dispatchEvent(new CustomEvent('djust:time-travel-event', {
            detail: { type: 'time_travel_event', history_len: 0 },
        }));

        expect(panel.timeTravelHistory).toHaveLength(0);
        expect(panel.refreshActiveTab).not.toHaveBeenCalled();
    });
});
