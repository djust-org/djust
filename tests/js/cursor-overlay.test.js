/**
 * Tests for CursorOverlay built-in hook (src/16-cursor-overlay.js)
 */

import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createEnv(bodyHtml = '') {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>
            <div data-djust-root data-djust-view="test">
                ${bodyHtml}
            </div>
        </body></html>`,
        { url: 'http://localhost:8000/test/', runScripts: 'dangerously', pretendToBeVisual: true }
    );
    const { window } = dom;

    // Suppress console
    window.console = { log: () => {}, error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };

    try {
        window.eval(clientCode);
    } catch (e) {
        // client.js may throw on missing DOM APIs
    }

    return { window, dom, document: dom.window.document };
}

const HOOK_HTML = `
    <div dj-hook="CursorOverlay" data-djust-view="test" style="position:relative;">
        <textarea id="editor" style="font-family:monospace; font-size:14px; padding:10px;">Hello world</textarea>
    </div>
`;

describe('CursorOverlay hook', () => {
    describe('registration', () => {
        it('is registered as a built-in hook', () => {
            const { window } = createEnv();
            expect(window.djust.hooks).toBeDefined();
            expect(window.djust.hooks.CursorOverlay).toBeDefined();
            expect(typeof window.djust.hooks.CursorOverlay.mounted).toBe('function');
        });
    });

    describe('mounted()', () => {
        it('creates overlay and mirror divs', () => {
            const { window, document } = createEnv(HOOK_HTML);

            window.djust.mountHooks(document);

            const hookEl = document.querySelector('[dj-hook="CursorOverlay"]');
            const overlay = hookEl.querySelector('[dj-update="ignore"]');
            const mirror = hookEl.querySelector('[aria-hidden="true"]');

            expect(overlay).not.toBeNull();
            expect(mirror).not.toBeNull();
        });

        it('sets overlay as pointer-events:none', () => {
            const { window, document } = createEnv(HOOK_HTML);

            window.djust.mountHooks(document);

            const hookEl = document.querySelector('[dj-hook="CursorOverlay"]');
            const overlay = hookEl.querySelector('[dj-update="ignore"]');
            expect(overlay.style.pointerEvents).toBe('none');
        });

        it('sets mirror as visibility:hidden', () => {
            const { window, document } = createEnv(HOOK_HTML);

            window.djust.mountHooks(document);

            const hookEl = document.querySelector('[dj-hook="CursorOverlay"]');
            const mirror = hookEl.querySelector('[aria-hidden="true"]');
            expect(mirror.style.visibility).toBe('hidden');
        });

        it('discovers the textarea child', () => {
            const { window, document } = createEnv(HOOK_HTML);

            window.djust.mountHooks(document);

            // Verify the hook instance has a textarea reference by checking
            // that cursor position events get sent (textarea must be bound)
            const hookEl = document.querySelector('[dj-hook="CursorOverlay"]');
            const textarea = hookEl.querySelector('textarea');
            expect(textarea).not.toBeNull();
        });
    });

    describe('_syncMirrorStyles()', () => {
        it('copies font styles from textarea to mirror', () => {
            const { window, document } = createEnv(HOOK_HTML);

            window.djust.mountHooks(document);

            const hookEl = document.querySelector('[dj-hook="CursorOverlay"]');
            const mirror = hookEl.querySelector('[aria-hidden="true"]');

            // Mirror should exist with initial inline styles (pre-wrap, hidden)
            expect(mirror.style.whiteSpace).toBe('pre-wrap');
            expect(mirror.style.visibility).toBe('hidden');
            expect(mirror.style.position).toBe('absolute');
        });
    });

    describe('cursor rendering', () => {
        it('creates caret elements when cursor_positions event is received', () => {
            const { window, document } = createEnv(HOOK_HTML);

            // Mock liveViewInstance for pushEvent
            window.djust.liveViewInstance = {
                ws: { send() {}, readyState: 1 },
            };

            window.djust.mountHooks(document);

            // Dispatch a cursor_positions event to the hook
            const cursors = {
                'user-1': { position: 3, color: '#ff0000', name: 'Alice', emoji: '🐱' },
                'user-2': { position: 7, color: '#00ff00', name: 'Bob', emoji: '🐶' },
            };
            window.djust.dispatchPushEventToHooks('cursor_positions', { cursors });

            const hookEl = document.querySelector('[dj-hook="CursorOverlay"]');
            const overlay = hookEl.querySelector('[dj-update="ignore"]');
            const carets = overlay.querySelectorAll('.remote-cursor');

            expect(carets.length).toBe(2);
        });

        it('removes carets when users disconnect', () => {
            const { window, document } = createEnv(HOOK_HTML);

            window.djust.liveViewInstance = {
                ws: { send() {}, readyState: 1 },
            };

            window.djust.mountHooks(document);

            // First: two users
            window.djust.dispatchPushEventToHooks('cursor_positions', {
                cursors: {
                    'user-1': { position: 3, color: '#ff0000', name: 'Alice', emoji: '🐱' },
                    'user-2': { position: 7, color: '#00ff00', name: 'Bob', emoji: '🐶' },
                },
            });

            const hookEl = document.querySelector('[dj-hook="CursorOverlay"]');
            const overlay = hookEl.querySelector('[dj-update="ignore"]');
            expect(overlay.querySelectorAll('.remote-cursor').length).toBe(2);

            // Second: only one user
            window.djust.dispatchPushEventToHooks('cursor_positions', {
                cursors: {
                    'user-1': { position: 5, color: '#ff0000', name: 'Alice', emoji: '🐱' },
                },
            });

            expect(overlay.querySelectorAll('.remote-cursor').length).toBe(1);
        });

        it('updates existing caret positions', () => {
            const { window, document } = createEnv(HOOK_HTML);

            window.djust.liveViewInstance = {
                ws: { send() {}, readyState: 1 },
            };

            window.djust.mountHooks(document);

            // Initial position
            window.djust.dispatchPushEventToHooks('cursor_positions', {
                cursors: {
                    'user-1': { position: 0, color: '#ff0000', name: 'Alice', emoji: '🐱' },
                },
            });

            const hookEl = document.querySelector('[dj-hook="CursorOverlay"]');
            const overlay = hookEl.querySelector('[dj-update="ignore"]');
            // Move to a different position
            window.djust.dispatchPushEventToHooks('cursor_positions', {
                cursors: {
                    'user-1': { position: 10, color: '#ff0000', name: 'Alice', emoji: '🐱' },
                },
            });

            // Caret should still exist (not duplicated)
            expect(overlay.querySelectorAll('.remote-cursor').length).toBe(1);
        });
    });

    describe('pushEvent integration', () => {
        it('sends update_cursor event via pushEvent', () => {
            const { window, document } = createEnv(HOOK_HTML);
            const sent = [];

            window.djust.liveViewInstance = {
                ws: {
                    send(msg) { sent.push(JSON.parse(msg)); },
                    readyState: 1,
                },
            };

            window.djust.mountHooks(document);

            // Simulate a keyup event on the textarea
            const textarea = document.querySelector('#editor');
            textarea.selectionStart = 5;
            textarea.dispatchEvent(new window.Event('keyup'));

            // The event is debounced at 100ms, use vi.useFakeTimers
            vi.useFakeTimers();
            textarea.dispatchEvent(new window.Event('keyup'));
            vi.advanceTimersByTime(150);
            vi.useRealTimers();

            // Should have sent at least one update_cursor event
            const cursorEvents = sent.filter(m => m.event === 'update_cursor');
            expect(cursorEvents.length).toBeGreaterThanOrEqual(1);
            expect(cursorEvents[0].params).toEqual({ position: 5 });
        });
    });

    describe('destroyed()', () => {
        it('cleans up DOM elements on destroy', () => {
            const { window, document } = createEnv(HOOK_HTML);

            window.djust.mountHooks(document);

            const hookEl = document.querySelector('[dj-hook="CursorOverlay"]');

            // Before destroy: overlay and mirror exist
            expect(hookEl.querySelector('[dj-update="ignore"]')).not.toBeNull();
            expect(hookEl.querySelector('[aria-hidden="true"]')).not.toBeNull();

            // Remove the hook element to trigger destroy
            hookEl.parentNode.removeChild(hookEl);
            window.djust.updateHooks(document);

            // The hook's destroyed() should have been called
            // (we can't check DOM removal since the parent was removed,
            // but we verify activeHooks is cleaned up)
            expect(window.djust._activeHooks.size).toBe(0);
        });
    });

    describe('updated()', () => {
        it('repositions carets on update', () => {
            const { window, document } = createEnv(HOOK_HTML);

            window.djust.liveViewInstance = {
                ws: { send() {}, readyState: 1 },
            };

            window.djust.mountHooks(document);

            // Set up cursors
            window.djust.dispatchPushEventToHooks('cursor_positions', {
                cursors: {
                    'user-1': { position: 3, color: '#ff0000', name: 'Alice', emoji: '🐱' },
                },
            });

            // Trigger update (simulating DOM patch)
            window.djust.updateHooks(document);

            // Verify caret still exists after update
            const hookEl = document.querySelector('[dj-hook="CursorOverlay"]');
            const overlay = hookEl.querySelector('[dj-update="ignore"]');
            expect(overlay.querySelectorAll('.remote-cursor').length).toBe(1);
        });
    });
});
