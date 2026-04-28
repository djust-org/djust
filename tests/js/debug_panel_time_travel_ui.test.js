/**
 * Debug Panel — Time-Travel UI tests (#1151 PR-B, v0.9.4).
 *
 * Exercises the v0.9.4 additions:
 * - Branch indicator badge (main vs branched).
 * - "X / max" event count.
 * - Forward-replay button + click handler dispatch.
 * - Per-component scrubber row with expand-toggle.
 * - onTimeTravelState consumes branch_id / forward_replay_enabled / max_events.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { createPanel, loadPanel } from './helpers/debug-panel-harness.js';

describe('Time-Travel tab — v0.9.4 augmented frame fields', () => {
    let panel;

    beforeEach(() => {
        panel = createPanel();
    });

    afterEach(() => {
        delete window.DjustDebugPanel;
        delete window.djustDebugPanel;
        delete window.djust;
    });

    it('onTimeTravelState captures branch_id, forward_replay_enabled, max_events', () => {
        panel.onTimeTravelState({
            cursor: 2,
            which: 'before',
            history_len: 5,
            branch_id: 'branch-3',
            forward_replay_enabled: true,
            max_events: 100,
        });

        expect(panel.timeTravelCursor).toBe(2);
        expect(panel.timeTravelWhich).toBe('before');
        expect(panel.timeTravelBranchId).toBe('branch-3');
        expect(panel.timeTravelForwardReplayEnabled).toBe(true);
        expect(panel.timeTravelMaxEvents).toBe(100);
    });

    it('onTimeTravelState tolerates missing v0.9.4 fields (backwards compat)', () => {
        // Pre-v0.9.4 server frame: only cursor / which / history_len.
        panel.onTimeTravelState({ cursor: 0, which: 'before', history_len: 1 });
        expect(panel.timeTravelCursor).toBe(0);
        // New fields stay undefined — render path will treat them as defaults.
        expect(panel.timeTravelBranchId).toBeUndefined();
        expect(panel.timeTravelForwardReplayEnabled).toBeUndefined();
        expect(panel.timeTravelMaxEvents).toBeUndefined();
    });

    it('renderTimeTravelTab shows main badge when branch_id is "main"', () => {
        panel.timeTravelHistory = [
            { event_name: 'increment', params: {}, ts: 0, state_after: {} },
        ];
        panel.timeTravelBranchId = 'main';
        const html = panel.renderTimeTravelTab();
        expect(html).toContain('tt-branch-main');
        expect(html).toContain('main');
        expect(html).not.toContain('tt-branch-fork');
    });

    it('renderTimeTravelTab shows fork badge when on a branch', () => {
        panel.timeTravelHistory = [
            { event_name: 'increment', params: {}, ts: 0, state_after: {} },
        ];
        panel.timeTravelBranchId = 'branch-2';
        const html = panel.renderTimeTravelTab();
        expect(html).toContain('tt-branch-fork');
        expect(html).toContain('branch-2');
    });

    it('renderTimeTravelTab shows "X / max" count when max_events known', () => {
        panel.timeTravelHistory = [
            { event_name: 'a', params: {}, ts: 0, state_after: {} },
            { event_name: 'b', params: {}, ts: 0, state_after: {} },
        ];
        panel.timeTravelMaxEvents = 100;
        const html = panel.renderTimeTravelTab();
        expect(html).toContain('2 / 100 events');
    });

    it('renderTimeTravelTab shows replay-hint when forward_replay_enabled', () => {
        panel.timeTravelHistory = [
            { event_name: 'a', params: {}, ts: 0, state_after: {} },
        ];
        panel.timeTravelForwardReplayEnabled = true;
        const html = panel.renderTimeTravelTab();
        expect(html).toContain('tt-replay-hint');
        expect(html).toContain('Replay-enabled at cursor');
    });

    it('renderTimeTravelTab shows expand-toggle only when entry has components', () => {
        panel.timeTravelHistory = [
            {
                event_name: 'with-comp',
                params: {},
                ts: 0,
                state_after: { __components__: { 'comp-a': { value: 1 } } },
            },
            {
                event_name: 'no-comp',
                params: {},
                ts: 0,
                state_after: { count: 5 },
            },
        ];
        const html = panel.renderTimeTravelTab();
        // Entry #0 has components → expand button rendered.
        expect(html).toContain('data-tt-expand="0"');
        // Entry #1 has no components → no expand button for it.
        expect(html).not.toContain('data-tt-expand="1"');
    });

    it('toggleTimeTravelExpandRow flips the expanded state', () => {
        expect(panel.timeTravelExpandedRows).toBeFalsy();
        panel.toggleTimeTravelExpandRow(2);
        expect(panel.timeTravelExpandedRows[2]).toBe(true);
        panel.toggleTimeTravelExpandRow(2);
        expect(panel.timeTravelExpandedRows[2]).toBe(false);
    });

    it('renderTimeTravelTab shows component sub-rows when expanded', () => {
        panel.timeTravelHistory = [
            {
                event_name: 'increment',
                params: {},
                ts: 0,
                state_after: {
                    __components__: {
                        'comp-a': { value: 1 },
                        'comp-b': { value: 7 },
                    },
                },
            },
        ];
        panel.timeTravelExpandedRows = { 0: true };
        const html = panel.renderTimeTravelTab();
        expect(html).toContain('tt-components');
        expect(html).toContain('data-tt-comp-jump="0"');
        expect(html).toContain('data-tt-comp-id="comp-a"');
        expect(html).toContain('data-tt-comp-id="comp-b"');
    });

    it('renderTimeTravelTab forward-replay button renders for every entry', () => {
        panel.timeTravelHistory = [
            { event_name: 'a', params: {}, ts: 0, state_after: {} },
            { event_name: 'b', params: {}, ts: 0, state_after: {} },
        ];
        const html = panel.renderTimeTravelTab();
        expect(html).toContain('data-tt-forward-replay="0"');
        expect(html).toContain('data-tt-forward-replay="1"');
        expect(html).toContain('tt-forward-replay');
    });
});

describe('Time-Travel tab — click dispatch (v0.9.4)', () => {
    let panel;
    let sentMessages;

    beforeEach(() => {
        panel = createPanel();
        sentMessages = [];
        // Stub the canonical send path the handlers route through.
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

    it('onTimeTravelComponentJumpClick sends the component_jump frame', () => {
        panel.onTimeTravelComponentJumpClick(3, 'comp-a', 'before');
        expect(sentMessages).toEqual([
            { type: 'time_travel_component_jump', index: 3, component_id: 'comp-a', which: 'before' },
        ]);
    });

    it('onTimeTravelComponentJumpClick rejects non-numeric index', () => {
        panel.onTimeTravelComponentJumpClick('not-a-number', 'comp-a', 'before');
        expect(sentMessages).toHaveLength(0);
    });

    it('onTimeTravelComponentJumpClick rejects empty component_id', () => {
        panel.onTimeTravelComponentJumpClick(3, '', 'before');
        expect(sentMessages).toHaveLength(0);
    });

    it('onTimeTravelForwardReplayClick sends the forward_replay frame without override_params', () => {
        panel.onTimeTravelForwardReplayClick(2, null);
        expect(sentMessages).toEqual([
            { type: 'forward_replay', from_index: 2 },
        ]);
        // override_params should NOT be present when null is passed —
        // the server defaults to "use original params" when the field
        // is absent.
        expect(sentMessages[0].override_params).toBeUndefined();
    });

    it('onTimeTravelForwardReplayClick passes override_params when non-null', () => {
        panel.onTimeTravelForwardReplayClick(2, { n: 99 });
        expect(sentMessages).toEqual([
            { type: 'forward_replay', from_index: 2, override_params: { n: 99 } },
        ]);
    });

    it('onTimeTravelForwardReplayClick rejects non-numeric from_index', () => {
        panel.onTimeTravelForwardReplayClick('not-a-number', null);
        expect(sentMessages).toHaveLength(0);
    });
});

describe('Time-Travel tab — onTimeTravelEvent picks up branch_id', () => {
    let panel;

    beforeEach(() => {
        panel = createPanel();
    });

    afterEach(() => {
        delete window.DjustDebugPanel;
        delete window.djustDebugPanel;
    });

    it('event push frame updates timeTravelBranchId', () => {
        panel.timeTravelBranchId = 'main';
        panel.onTimeTravelEvent({
            entry: { event_name: 'a', params: {}, ts: 0 },
            branch_id: 'branch-1',
        });
        expect(panel.timeTravelBranchId).toBe('branch-1');
    });
});
