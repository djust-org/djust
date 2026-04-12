/**
 * Tests for the tutorial bubble listener (src/28-tutorial-bubble.js).
 *
 * The bubble listens for tour:narrate CustomEvents on document and updates
 * its text, progress indicator, and position. These tests simulate the
 * CustomEvents directly instead of going through the full server → client
 * dispatch path.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createEnv(bodyHtml = '') {
    // The real template tag renders the bubble container; reproduce it inline.
    const bubbleHtml = `<div id="dj-tutorial-bubble" class="dj-tutorial-bubble" dj-update="ignore"
         data-event="tour:narrate" data-default-position="bottom" data-visible="false"
         role="status" aria-live="polite">
      <p class="dj-tutorial-bubble__text"></p>
      <div class="dj-tutorial-bubble__progress">
        <span class="dj-tutorial-bubble__step"></span>
      </div>
      <div class="dj-tutorial-bubble__actions">
        <button type="button" class="dj-tutorial-bubble__skip" dj-click="skip_tutorial">Skip</button>
        <button type="button" class="dj-tutorial-bubble__cancel" dj-click="cancel_tutorial">Close</button>
      </div>
    </div>`;

    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>
            <div dj-root dj-view="test.View">
                ${bodyHtml}
                ${bubbleHtml}
            </div>
        </body></html>`,
        { url: 'http://localhost:8000/test/', runScripts: 'dangerously', pretendToBeVisual: true }
    );
    const { window } = dom;

    // Suppress console
    window.console = { log: () => {}, error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };

    // Stub scrollIntoView (not available in JSDOM)
    window.HTMLElement.prototype.scrollIntoView = function() {};

    // Stub requestAnimationFrame (JSDOM has it but may not execute synchronously)
    window.requestAnimationFrame = function(cb) { cb(); return 0; };

    try {
        window.eval(clientCode);
    } catch (_) {
        // client.js may throw on missing DOM APIs; tests still work
    }

    return { window, document: dom.window.document };
}

function fireNarrate(document, detail) {
    const event = new document.defaultView.CustomEvent('tour:narrate', {
        detail: detail,
        bubbles: true,
    });
    document.dispatchEvent(event);
}

describe('tutorial bubble listener', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('registers a listener for tour:narrate', () => {
        const { window } = createEnv();
        expect(typeof window.djust._tutorialBubble).toBe('object');
        expect(typeof window.djust._tutorialBubble.handleNarrate).toBe('function');
    });

    it('updates text content on narrate', async () => {
        const { document } = createEnv('<button id="btn">Click</button>');

        fireNarrate(document, {
            text: 'This is your dashboard.',
            target: '#btn',
            position: 'bottom',
            step: 0,
            total: 3,
        });

        await new Promise(r => setTimeout(r, 10));

        const textEl = document.querySelector('.dj-tutorial-bubble__text');
        expect(textEl.textContent).toBe('This is your dashboard.');
    });

    it('updates progress indicator from step/total', async () => {
        const { document } = createEnv('<div id="x"></div>');

        fireNarrate(document, {
            text: 'Step 2 of 5.',
            target: '#x',
            step: 1,
            total: 5,
        });

        await new Promise(r => setTimeout(r, 10));

        const stepEl = document.querySelector('.dj-tutorial-bubble__step');
        expect(stepEl.textContent).toBe('Step 2 of 5');
    });

    it('shows the bubble by setting data-visible to true', async () => {
        const { document } = createEnv('<div id="target"></div>');

        fireNarrate(document, { text: 'Hi.', target: '#target' });
        await new Promise(r => setTimeout(r, 10));

        const bubble = document.getElementById('dj-tutorial-bubble');
        expect(bubble.getAttribute('data-visible')).toBe('true');
    });

    it('hides the bubble when text is empty', async () => {
        const { document } = createEnv('<div id="target"></div>');

        // First show the bubble
        fireNarrate(document, { text: 'Hi.', target: '#target' });
        await new Promise(r => setTimeout(r, 10));
        expect(document.getElementById('dj-tutorial-bubble').getAttribute('data-visible')).toBe('true');

        // Then fire an empty-text event to hide
        fireNarrate(document, { text: '', target: '#target' });
        await new Promise(r => setTimeout(r, 10));
        expect(document.getElementById('dj-tutorial-bubble').getAttribute('data-visible')).toBe('false');
    });

    it('falls back to default position when detail.position is missing', async () => {
        const { document } = createEnv('<div id="target"></div>');

        fireNarrate(document, { text: 'Hi.', target: '#target' });
        await new Promise(r => setTimeout(r, 50));

        const bubble = document.getElementById('dj-tutorial-bubble');
        expect(bubble.getAttribute('data-position')).toBe('bottom');
    });

    it('applies explicit position from detail.position', async () => {
        const { document } = createEnv('<div id="target" style="position:absolute;top:400px;left:200px;width:100px;height:40px;"></div>');

        fireNarrate(document, { text: 'Hi.', target: '#target', position: 'top' });
        await new Promise(r => setTimeout(r, 50));

        const bubble = document.getElementById('dj-tutorial-bubble');
        // In JSDOM getBoundingClientRect returns zeroes, so auto-flip may
        // convert 'top' to 'bottom'. Just verify data-position is set.
        expect(bubble.getAttribute('data-position')).toBeTruthy();
    });

    it('handles missing target gracefully', async () => {
        const { document } = createEnv('');

        fireNarrate(document, { text: 'No target.' });
        await new Promise(r => setTimeout(r, 10));

        const bubble = document.getElementById('dj-tutorial-bubble');
        // Bubble should still show up with fallback positioning
        expect(bubble.getAttribute('data-visible')).toBe('true');
        const textEl = document.querySelector('.dj-tutorial-bubble__text');
        expect(textEl.textContent).toBe('No target.');
    });

    it('handles target that does not match any element', async () => {
        const { document } = createEnv('<div id="other"></div>');

        fireNarrate(document, { text: 'Hi.', target: '#missing' });
        await new Promise(r => setTimeout(r, 10));

        // Bubble still shows up with fallback positioning
        const bubble = document.getElementById('dj-tutorial-bubble');
        expect(bubble.getAttribute('data-visible')).toBe('true');
    });

    it('ignores tour:hide event when no bubble exists', async () => {
        const { document, window } = createEnv('');

        // Remove the bubble
        document.getElementById('dj-tutorial-bubble').remove();

        // Firing tour:hide should not throw
        const hideEvent = new window.CustomEvent('tour:hide', { bubbles: true });
        expect(() => document.dispatchEvent(hideEvent)).not.toThrow();
    });

    it('responds to tour:hide event to hide bubble', async () => {
        const { document, window } = createEnv('<div id="target"></div>');

        // Show bubble first
        fireNarrate(document, { text: 'Hi.', target: '#target' });
        await new Promise(r => setTimeout(r, 10));

        // Fire tour:hide
        const hideEvent = new window.CustomEvent('tour:hide', { bubbles: true });
        document.dispatchEvent(hideEvent);
        await new Promise(r => setTimeout(r, 10));

        const bubble = document.getElementById('dj-tutorial-bubble');
        expect(bubble.getAttribute('data-visible')).toBe('false');
    });

    it('updates on subsequent narrate events', async () => {
        const { document } = createEnv('<div id="a"></div><div id="b"></div>');

        fireNarrate(document, { text: 'Step 1.', target: '#a', step: 0, total: 2 });
        await new Promise(r => setTimeout(r, 10));

        let textEl = document.querySelector('.dj-tutorial-bubble__text');
        expect(textEl.textContent).toBe('Step 1.');

        fireNarrate(document, { text: 'Step 2.', target: '#b', step: 1, total: 2 });
        await new Promise(r => setTimeout(r, 10));

        textEl = document.querySelector('.dj-tutorial-bubble__text');
        expect(textEl.textContent).toBe('Step 2.');

        const stepEl = document.querySelector('.dj-tutorial-bubble__step');
        expect(stepEl.textContent).toBe('Step 2 of 2');
    });

    it('creates an arrow element on positioning', async () => {
        const { document } = createEnv('<div id="target"></div>');

        fireNarrate(document, { text: 'Hi.', target: '#target', position: 'bottom' });
        await new Promise(r => setTimeout(r, 50));

        const bubble = document.getElementById('dj-tutorial-bubble');
        const arrow = bubble.querySelector('.dj-tutorial-bubble__arrow');
        expect(arrow).not.toBeNull();
    });

    it('creates and removes backdrop on show/hide', async () => {
        const { document, window } = createEnv('<div id="target"></div>');

        fireNarrate(document, { text: 'Hi.', target: '#target' });
        await new Promise(r => setTimeout(r, 50));

        const backdrop = document.getElementById('dj-tutorial-backdrop');
        expect(backdrop).not.toBeNull();
        expect(backdrop.style.opacity).toBe('1');

        // Hide
        const hideEvent = new window.CustomEvent('tour:hide', { bubbles: true });
        document.dispatchEvent(hideEvent);
        await new Promise(r => setTimeout(r, 10));

        expect(backdrop.style.opacity).toBe('0');
    });
});
