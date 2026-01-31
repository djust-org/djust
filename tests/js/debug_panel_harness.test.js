/**
 * Integration tests for the debug panel using the real IIFE code (Issue #184)
 *
 * Unlike the replicated-logic tests in debug_event_filter.test.js etc.,
 * these tests evaluate the actual debug-panel.js IIFE and exercise
 * the real DjustDebugPanel class methods.
 */

import { readFileSync } from 'node:fs';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createPanel, loadPanel, PANEL_SOURCE_PATH } from './helpers/debug-panel-harness.js';

describe('Debug Panel Harness — IIFE loading', () => {
    afterEach(() => {
        delete window.DjustDebugPanel;
        delete window.djustDebugPanel;
    });

    it('loads DjustDebugPanel class onto window', () => {
        const PanelClass = loadPanel();
        expect(PanelClass).toBeDefined();
        expect(typeof PanelClass).toBe('function');
        expect(window.DjustDebugPanel).toBe(PanelClass);
    });

    it('does not export class when DEBUG_MODE is false', () => {
        window.DEBUG_MODE = false;
        delete window.DjustDebugPanel;
        const source = readFileSync(PANEL_SOURCE_PATH, 'utf-8');
        const fn = new Function(source);
        fn.call(window);
        expect(window.DjustDebugPanel).toBeUndefined();
    });

    it('creates a panel instance with DOM elements', () => {
        const panel = createPanel();
        expect(panel).toBeDefined();
        expect(panel.state).toBeDefined();
        expect(panel.eventHistory).toEqual([]);
        expect(panel.button).toBeTruthy();
        expect(panel.panel).toBeTruthy();
    });
});

describe('Debug Panel Harness — Event Filtering (real code)', () => {
    let panel;

    beforeEach(() => {
        panel = createPanel();
        // Add sample events — last entry uses `name` instead of `handler`
        // to test the fallback path (event.handler || event.name)
        panel.eventHistory = [
            { handler: 'increment', timestamp: Date.now(), params: { amount: 1 } },
            { handler: 'decrement', timestamp: Date.now() },
            { handler: 'search', timestamp: Date.now(), error: 'Not found' },
            { handler: 'fetch_data', timestamp: Date.now(), params: { id: 5 } },
            { name: 'click_handler', timestamp: Date.now(), error: 'Timeout' },
        ];
    });

    afterEach(() => {
        panel.destroy();
    });

    it('renders all events when no filters active', () => {
        panel.state.filters.eventName = '';
        panel.state.filters.eventStatus = 'all';
        const html = panel.renderEventsTab();
        // All 5 events should appear
        expect(html).toContain('increment');
        expect(html).toContain('decrement');
        expect(html).toContain('search');
        expect(html).toContain('fetch_data');
        expect(html).toContain('click_handler');
    });

    it('filters by name via onEventNameFilter', () => {
        panel.onEventNameFilter('inc');
        // State should be updated
        expect(panel.state.filters.eventName).toBe('inc');
        const html = panel.renderEventsTab();
        expect(html).toContain('increment');
        expect(html).not.toContain('fetch_data');
    });

    it('filters by status via onEventStatusFilter', () => {
        panel.onEventStatusFilter('errors');
        expect(panel.state.filters.eventStatus).toBe('errors');
        const html = panel.renderEventsTab();
        expect(html).toContain('search');
        expect(html).toContain('click_handler');
        // Success events should not appear in the event items
        // (they may appear in filter controls, so check the events-list section)
        expect(html).toContain('2 / 5');
    });

    it('combines name and status filters', () => {
        panel.onEventNameFilter('search');
        panel.onEventStatusFilter('errors');
        const html = panel.renderEventsTab();
        expect(html).toContain('1 / 5');
    });

    it('clears filters via clearEventFilters', () => {
        panel.onEventNameFilter('test');
        panel.onEventStatusFilter('errors');
        panel.clearEventFilters();
        expect(panel.state.filters.eventName).toBe('');
        expect(panel.state.filters.eventStatus).toBe('all');
    });

    it('shows empty state when no events match', () => {
        panel.onEventNameFilter('nonexistent');
        const html = panel.renderEventsTab();
        expect(html).toContain('No events match the current filters');
    });
});

describe('Debug Panel Harness — Event Replay (real code)', () => {
    let panel;
    let mockSendEvent;

    beforeEach(() => {
        mockSendEvent = vi.fn().mockReturnValue(true);
        window.liveView = { sendEvent: mockSendEvent };
        panel = createPanel();
        panel.eventHistory = [
            { handler: 'increment', timestamp: Date.now(), params: { amount: 1 } },
            { handler: 'decrement', timestamp: Date.now() },
            { name: 'search', timestamp: Date.now(), params: { query: 'test' } },
            { timestamp: Date.now() }, // no handler or name
        ];
    });

    afterEach(() => {
        panel.destroy();
        delete window.liveView;
    });

    it('replays event with handler and params via real replayEvent', () => {
        const btn = document.createElement('button');
        btn.textContent = '⟳';
        btn.className = 'event-replay-btn';
        panel.replayEvent(0, btn);
        expect(mockSendEvent).toHaveBeenCalledWith('increment', { amount: 1 });
    });

    it('replays event without params', () => {
        const btn = document.createElement('button');
        btn.textContent = '⟳';
        btn.className = 'event-replay-btn';
        panel.replayEvent(1, btn);
        expect(mockSendEvent).toHaveBeenCalledWith('decrement', {});
    });

    it('uses name field as fallback', () => {
        const btn = document.createElement('button');
        btn.textContent = '⟳';
        btn.className = 'event-replay-btn';
        panel.replayEvent(2, btn);
        expect(mockSendEvent).toHaveBeenCalledWith('search', { query: 'test' });
    });

    it('does nothing for event without handler or name', () => {
        const btn = document.createElement('button');
        btn.textContent = '⟳';
        btn.className = 'event-replay-btn';
        panel.replayEvent(3, btn);
        expect(mockSendEvent).not.toHaveBeenCalled();
    });

    it('shows error when no liveView connection', () => {
        delete window.liveView;
        const btn = document.createElement('button');
        btn.textContent = '⟳';
        btn.className = 'event-replay-btn';
        panel.replayEvent(0, btn);
        expect(mockSendEvent).not.toHaveBeenCalled();
        // Button should show error state
        expect(btn.classList.contains('replay-error')).toBe(true);
    });

    it('shows error when sendEvent fails', () => {
        mockSendEvent.mockReturnValue(false);
        const btn = document.createElement('button');
        btn.textContent = '⟳';
        btn.className = 'event-replay-btn';
        panel.replayEvent(0, btn);
        expect(btn.classList.contains('replay-error')).toBe(true);
    });
});

describe('Debug Panel Harness — State Persistence (real code)', () => {
    let panel;

    beforeEach(() => {
        localStorage.clear();
    });

    afterEach(() => {
        if (panel) panel.destroy();
        localStorage.clear();
    });

    it('generates scoped state key from view class', () => {
        window.DJUST_DEBUG_INFO = { view_class: 'CounterView' };
        panel = createPanel();
        expect(panel._stateKey()).toBe('djust-debug-state:CounterView');
    });

    it('falls back to global key when no view class', () => {
        delete window.DJUST_DEBUG_INFO;
        panel = createPanel();
        expect(panel._stateKey()).toBe('djust-debug-state:global');
    });

    it('saves only UI preferences via saveState', () => {
        delete window.DJUST_DEBUG_INFO;
        panel = createPanel();
        panel.state.isOpen = true;
        panel.state.activeTab = 'network';
        panel.state.filters.eventName = 'should-not-persist';
        panel.saveState();

        const saved = JSON.parse(localStorage.getItem('djust-debug-state:global'));
        expect(saved.isOpen).toBe(true);
        expect(saved.activeTab).toBe('network');
        expect(saved.filters).toBeUndefined();
        expect(saved.eventName).toBeUndefined();
    });

    it('loads saved state on construction', () => {
        delete window.DJUST_DEBUG_INFO;
        localStorage.setItem(
            'djust-debug-state:global',
            JSON.stringify({ isOpen: false, activeTab: 'patches' })
        );
        panel = createPanel();
        expect(panel.state.activeTab).toBe('patches');
    });

    it('handles corrupt saved state gracefully', () => {
        delete window.DJUST_DEBUG_INFO;
        localStorage.setItem('djust-debug-state:global', 'not-json');
        // Should not throw
        panel = createPanel();
        expect(panel.state.activeTab).toBe('events'); // default
    });
});

describe('Debug Panel Harness — Network Message Inspection (real code)', () => {
    let panel;
    let originalClipboard;

    beforeEach(() => {
        originalClipboard = navigator.clipboard;
        panel = createPanel();
        panel.networkHistory = [
            { direction: 'sent', payload: { type: 'event', event: 'increment', params: { amount: 1 } }, size: 64, timestamp: Date.now() },
            { direction: 'received', payload: { type: 'response', patches: [{ op: 'replace' }], _debug: {} }, size: 128, timestamp: Date.now() },
            { direction: 'sent', payload: {}, size: 2, timestamp: Date.now() },
            { direction: 'received', data: 'binary-data', size: 32, timestamp: Date.now() },
        ];
    });

    afterEach(() => {
        panel.destroy();
        // Restore original clipboard to prevent leaking between tests
        Object.defineProperty(navigator, 'clipboard', {
            value: originalClipboard,
            writable: true,
            configurable: true,
        });
    });

    it('renders network tab with messages', () => {
        const html = panel.renderNetworkTab();
        expect(html).toContain('event');
        expect(html).toContain('response');
        expect(html).not.toContain('No WebSocket messages captured yet');
    });

    it('renders empty state when no messages', () => {
        panel.networkHistory = [];
        const html = panel.renderNetworkTab();
        expect(html).toContain('No WebSocket messages captured yet');
    });

    it('copyNetworkPayload calls clipboard API', async () => {
        const writeText = vi.fn().mockResolvedValue(undefined);
        Object.defineProperty(navigator, 'clipboard', {
            value: { writeText },
            writable: true,
            configurable: true,
        });

        const btn = document.createElement('button');
        btn.textContent = 'Copy JSON';
        await panel.copyNetworkPayload(btn, 0);
        // Flush microtasks for the clipboard promise chain
        await new Promise(r => setTimeout(r, 0));

        expect(writeText).toHaveBeenCalled();
        const arg = writeText.mock.calls[0][0];
        const parsed = JSON.parse(arg);
        expect(parsed.event).toBe('increment');
    });
});
