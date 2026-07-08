/**
 * Tests for debug panel dock position + resize (dockable panel).
 *
 * The fixed full-width bottom dock covers bottom-anchored app UI (e.g. a
 * chat input). The panel is now dockable to bottom/left/right, resizable
 * by dragging its inner edge, and both preferences persist per view via
 * localStorage.
 *
 * Uses the real IIFE source via the harness (no replicated logic).
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { loadPanel } from './helpers/debug-panel-harness.js';

// loadPanel() installs a functional localStorage fallback when the
// environment's own localStorage is broken (Node 22 + happy-dom), so it
// must run BEFORE window.localStorage is touched in beforeEach.
function createIsolatedPanel() {
    const PanelClass = loadPanel();
    return new PanelClass();
}

const STATE_KEY = 'djust-debug-state:global';

function readSavedState() {
    const raw = window.localStorage.getItem(STATE_KEY);
    return raw ? JSON.parse(raw) : null;
}

describe('Debug panel dock position', () => {
    let panel;

    beforeEach(() => {
        document.body.innerHTML = '';
        loadPanel();
        window.localStorage.clear();
        delete window.DJUST_DEBUG_INFO;
        panel = createIsolatedPanel();
    });

    afterEach(() => {
        if (panel) panel.destroy();
        panel = null;
    });

    it('defaults to a bottom dock, full width', () => {
        expect(panel.state.dock).toBe('bottom');
        panel.open();
        const style = panel.panel.getAttribute('style');
        expect(style).toContain('bottom: 0');
        expect(style).toContain('width: 100%');
    });

    it('setDock("right") docks the open panel to the right edge', () => {
        panel.open();
        panel.setDock('right');
        expect(panel.state.dock).toBe('right');
        const style = panel.panel.getAttribute('style');
        expect(style).toContain('right: 0');
        expect(style).toContain('top: 0');
        expect(style).toContain('height: 100%');
        expect(style).toContain(`width: ${panel.state.panelWidth}px`);
        expect(style).not.toContain('width: 100%');
    });

    it('setDock("left") docks the open panel to the left edge', () => {
        panel.open();
        panel.setDock('left');
        const style = panel.panel.getAttribute('style');
        expect(style).toContain('left: 0');
        expect(style).toContain('top: 0');
        expect(style).toContain(`width: ${panel.state.panelWidth}px`);
    });

    it('ignores an invalid dock position', () => {
        panel.setDock('right');
        panel.setDock('diagonal');
        expect(panel.state.dock).toBe('right');
    });

    it('persists dock position and restores it in a new instance', () => {
        panel.setDock('right');
        expect(readSavedState().dock).toBe('right');

        panel.destroy();
        document.body.innerHTML = '';
        panel = createIsolatedPanel();
        expect(panel.state.dock).toBe('right');
    });

    it('renders dock buttons in the header and switches dock on click', () => {
        const rightBtn = panel.panel.querySelector('.djust-btn-dock[data-dock="right"]');
        const bottomBtn = panel.panel.querySelector('.djust-btn-dock[data-dock="bottom"]');
        const leftBtn = panel.panel.querySelector('.djust-btn-dock[data-dock="left"]');
        expect(rightBtn).toBeTruthy();
        expect(bottomBtn).toBeTruthy();
        expect(leftBtn).toBeTruthy();

        rightBtn.click();
        expect(panel.state.dock).toBe('right');
        expect(rightBtn.classList.contains('active')).toBe(true);
        expect(bottomBtn.classList.contains('active')).toBe(false);

        bottomBtn.click();
        expect(panel.state.dock).toBe('bottom');
        expect(bottomBtn.classList.contains('active')).toBe(true);
    });

    it('moves the floating toggle button out of the way of the open panel', () => {
        panel.open();
        // Bottom dock: button lifted above the panel
        expect(panel.button.style.bottom).toBe(`${panel.state.panelHeight + 16}px`);

        panel.setDock('right');
        // Right dock: button pushed left of the panel, vertical offset cleared
        expect(panel.button.style.right).toBe(`${panel.state.panelWidth + 16}px`);
        expect(panel.button.style.bottom).toBe('');

        panel.close();
        // Closed: offsets cleared so the CSS default (20px corner) applies
        expect(panel.button.style.bottom).toBe('');
        expect(panel.button.style.right).toBe('');
    });
});

describe('Debug panel resize', () => {
    let panel;

    beforeEach(() => {
        document.body.innerHTML = '';
        loadPanel();
        window.localStorage.clear();
        delete window.DJUST_DEBUG_INFO;
        panel = createIsolatedPanel();
    });

    afterEach(() => {
        if (panel) panel.destroy();
        panel = null;
    });

    function drag(handle, from, to) {
        handle.dispatchEvent(new MouseEvent('mousedown', {
            bubbles: true, clientX: from.x, clientY: from.y,
        }));
        document.dispatchEvent(new MouseEvent('mousemove', {
            bubbles: true, clientX: to.x, clientY: to.y,
        }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
    }

    it('has a resize handle inside the panel', () => {
        expect(panel.panel.querySelector('.djust-resize-handle')).toBeTruthy();
    });

    it('dragging the handle upward grows a bottom-docked panel', () => {
        panel.open();
        const startHeight = panel.state.panelHeight;
        const handle = panel.panel.querySelector('.djust-resize-handle');

        drag(handle, { x: 500, y: 400 }, { x: 500, y: 300 });

        expect(panel.state.panelHeight).toBe(startHeight + 100);
        expect(panel.panel.getAttribute('style')).toContain(`height: ${startHeight + 100}px`);
        // Persisted on mouseup
        expect(readSavedState().panelHeight).toBe(startHeight + 100);
    });

    it('dragging the handle leftward grows a right-docked panel', () => {
        panel.setDock('right');
        panel.open();
        const startWidth = panel.state.panelWidth;
        const handle = panel.panel.querySelector('.djust-resize-handle');

        drag(handle, { x: 600, y: 300 }, { x: 500, y: 300 });

        expect(panel.state.panelWidth).toBe(startWidth + 100);
        expect(readSavedState().panelWidth).toBe(startWidth + 100);
    });

    it('clamps the panel size to sane bounds', () => {
        panel.open();
        const handle = panel.panel.querySelector('.djust-resize-handle');

        // Drag way past the top of the viewport
        drag(handle, { x: 500, y: 400 }, { x: 500, y: -5000 });
        expect(panel.state.panelHeight).toBeLessThanOrEqual(Math.round(window.innerHeight * 0.9));

        // Drag way below the viewport
        drag(handle, { x: 500, y: 400 }, { x: 500, y: 5000 });
        expect(panel.state.panelHeight).toBeGreaterThanOrEqual(160);
    });

    it('persists panel size and restores it in a new instance', () => {
        panel.open();
        const handle = panel.panel.querySelector('.djust-resize-handle');
        drag(handle, { x: 500, y: 400 }, { x: 500, y: 350 });
        const grown = panel.state.panelHeight;

        panel.destroy();
        document.body.innerHTML = '';
        panel = createIsolatedPanel();
        expect(panel.state.panelHeight).toBe(grown);
    });

    it('rejects non-numeric persisted sizes', () => {
        window.localStorage.setItem(STATE_KEY, JSON.stringify({
            dock: 'bottom', panelHeight: 'huge', panelWidth: null,
        }));
        panel.destroy();
        document.body.innerHTML = '';
        panel = createIsolatedPanel();
        expect(panel.state.panelHeight).toBe(400);
        expect(panel.state.panelWidth).toBe(480);
    });
});
