/**
 * Tests for dj-transition-group — declarative enter/leave animation
 * orchestration for lists (v0.6.0, phase 2c).
 *
 * This module wires existing `dj-transition` (enter) and `dj-remove`
 * (leave) primitives onto children of a marked parent. It does not
 * re-implement the phase cycling or the removal-deferral path —
 * those are covered by dj_transition.test.js and dj_remove.test.js.
 * The tests here focus on the attribute-plumbing layer.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom(bodyHtml = '') {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head></head><body>
            <div dj-view="test.V" dj-root>${bodyHtml}</div>
        </body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/' }
    );
    class MockWebSocket {
        static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
        constructor() { this.readyState = MockWebSocket.OPEN; }
        send() {} close() {}
    }
    dom.window.WebSocket = MockWebSocket;
    dom.window.DJUST_USE_WEBSOCKET = false;
    dom.window.eval(clientCode);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    return dom;
}

function nextFrame(dom) {
    // requestAnimationFrame in jsdom is backed by setTimeout(_, 16) in
    // the client module's fallback path. Under vitest parallel load
    // the 16 ms can stretch; 60 ms is a generous margin that matches
    // the conventions used elsewhere in this suite.
    return new Promise((resolve) => dom.window.setTimeout(resolve, 60));
}

describe('dj-transition-group', () => {
    let dom;

    it('_parseGroupAttr splits on | into {enter, leave}', () => {
        dom = createDom('');
        const api = dom.window.djust.djTransitionGroup;
        expect(api._parseGroupAttr('a b c | x y z')).toEqual({
            enter: 'a b c',
            leave: 'x y z',
        });
        // Single-token halves are also accepted — the downstream
        // dj-transition / dj-remove parsers decide what to do with them.
        expect(api._parseGroupAttr('fade-in | fade-out')).toEqual({
            enter: 'fade-in',
            leave: 'fade-out',
        });
    });

    it('_parseGroupAttr returns null on invalid input', () => {
        dom = createDom('');
        const api = dom.window.djust.djTransitionGroup;
        expect(api._parseGroupAttr(null)).toBeNull();
        expect(api._parseGroupAttr(undefined)).toBeNull();
        expect(api._parseGroupAttr('')).toBeNull();
        expect(api._parseGroupAttr('   ')).toBeNull();
        // Missing pipe — ambiguous, not supported.
        expect(api._parseGroupAttr('fade-in')).toBeNull();
        // Empty half — invalid.
        expect(api._parseGroupAttr('fade-in |')).toBeNull();
        expect(api._parseGroupAttr('| fade-out')).toBeNull();
        // Three pipes — also invalid.
        expect(api._parseGroupAttr('a | b | c')).toBeNull();
    });

    it('_handleChildAdded sets dj-remove from the long-form leave spec (no enter when applyEnter=false)', () => {
        dom = createDom('');
        const doc = dom.window.document;
        const ul = doc.createElement('ul');
        ul.setAttribute('dj-transition-group', '');
        ul.setAttribute('dj-group-enter', 'opacity-0 transition-opacity-300 opacity-100');
        ul.setAttribute('dj-group-leave', 'opacity-100 transition-opacity-300 opacity-0');
        const li = doc.createElement('li');
        ul.appendChild(li);
        // Call the internal hook directly — no observer timing involved.
        dom.window.djust.djTransitionGroup._handleChildAdded(li, ul, { applyEnter: false });
        expect(li.getAttribute('dj-remove')).toBe('opacity-100 transition-opacity-300 opacity-0');
        expect(li.hasAttribute('dj-transition')).toBe(false);
    });

    it('never overwrites pre-existing dj-transition / dj-remove on a child', () => {
        dom = createDom('');
        const doc = dom.window.document;
        const ul = doc.createElement('ul');
        ul.setAttribute('dj-transition-group', '');
        ul.setAttribute('dj-group-enter', 'e1 e2 e3');
        ul.setAttribute('dj-group-leave', 'l1 l2 l3');
        const li = doc.createElement('li');
        li.setAttribute('dj-transition', 'custom-enter');
        li.setAttribute('dj-remove', 'custom-leave');
        ul.appendChild(li);
        dom.window.djust.djTransitionGroup._handleChildAdded(li, ul, { applyEnter: true });
        // Author-specified values are preserved verbatim.
        expect(li.getAttribute('dj-transition')).toBe('custom-enter');
        expect(li.getAttribute('dj-remove')).toBe('custom-leave');
    });

    it('initial children get leave-only by default (no dj-group-appear)', async () => {
        dom = createDom(
            '<ul id="list" dj-transition-group="fade-in | fade-out">' +
            '  <li id="a">A</li>' +
            '  <li id="b">B</li>' +
            '</ul>'
        );
        // _installRootObserver fires on DOMContentLoaded, which
        // createDom() dispatches synchronously. _rescan()'s initial pass
        // walks [dj-transition-group] and calls _install on the ul,
        // which in turn calls _handleChildAdded on each initial child.
        await new Promise((r) => setTimeout(r, 0));
        const a = dom.window.document.getElementById('a');
        const b = dom.window.document.getElementById('b');
        expect(a.getAttribute('dj-remove')).toBe('fade-out');
        expect(b.getAttribute('dj-remove')).toBe('fade-out');
        // Enter NOT applied on initial children without appear.
        expect(a.hasAttribute('dj-transition')).toBe(false);
        expect(b.hasAttribute('dj-transition')).toBe(false);
    });

    it('dj-group-appear opts initial children into enter animation', async () => {
        dom = createDom(
            '<ul id="list" dj-transition-group dj-group-appear' +
            '    dj-group-enter="start-cls active-cls end-cls"' +
            '    dj-group-leave="fade-out">' +
            '  <li id="a">A</li>' +
            '</ul>'
        );
        await new Promise((r) => setTimeout(r, 0));
        const a = dom.window.document.getElementById('a');
        // Both attrs wired.
        expect(a.getAttribute('dj-remove')).toBe('fade-out');
        expect(a.getAttribute('dj-transition')).toBe('start-cls active-cls end-cls');
        // dj-transition's document-level observer picks up the attribute
        // mutation. Phase 1 (start-cls) is applied synchronously by
        // _installDjTransitionFor; phases 2+3 land on the next frame.
        await nextFrame(dom);
        expect(a.classList.contains('active-cls')).toBe(true);
        expect(a.classList.contains('end-cls')).toBe(true);
    });

    it('MutationObserver wires enter + leave on children appended post-mount', async () => {
        dom = createDom(
            '<ul id="list" dj-transition-group' +
            '    dj-group-enter="es ea ee"' +
            '    dj-group-leave="slide-out"></ul>'
        );
        await new Promise((r) => setTimeout(r, 0));
        const ul = dom.window.document.getElementById('list');
        const li = dom.window.document.createElement('li');
        li.id = 'new';
        ul.appendChild(li);
        // MutationObserver is microtask-scheduled; wait a tick so the
        // per-parent observer processes the childList mutation.
        await new Promise((r) => setTimeout(r, 20));
        expect(li.getAttribute('dj-remove')).toBe('slide-out');
        expect(li.getAttribute('dj-transition')).toBe('es ea ee');
        // And the dj-transition runner picks it up on the next frame.
        await nextFrame(dom);
        expect(li.classList.contains('ea')).toBe(true);
        expect(li.classList.contains('ee')).toBe(true);
    });

    it('_uninstall disconnects the per-parent observer', async () => {
        dom = createDom(
            '<ul id="list" dj-transition-group' +
            '    dj-group-leave="x y z"></ul>'
        );
        // Wait for the root observer / _rescan pass to install the ul.
        await new Promise((r) => setTimeout(r, 0));
        const ul = dom.window.document.getElementById('list');
        const api = dom.window.djust.djTransitionGroup;
        expect(api._installedParents.has(ul)).toBe(true);
        // Tear down.
        api._uninstall(ul);
        expect(api._installedParents.has(ul)).toBe(false);
        // Append a new child; because the per-parent observer is gone,
        // dj-remove should NOT be wired.
        const li = dom.window.document.createElement('li');
        ul.appendChild(li);
        await new Promise((r) => setTimeout(r, 20));
        expect(li.hasAttribute('dj-remove')).toBe(false);
    });

    it('parent removed from DOM via childList mutation triggers _uninstall', async () => {
        dom = createDom(
            '<div id="container">' +
            '  <ul id="list" dj-transition-group dj-group-leave="x"></ul>' +
            '</div>'
        );
        await new Promise((r) => setTimeout(r, 0));
        const container = dom.window.document.getElementById('container');
        const ul = dom.window.document.getElementById('list');
        const api = dom.window.djust.djTransitionGroup;
        expect(api._installedParents.has(ul)).toBe(true);
        // Remove the parent from the DOM — the root observer's childList
        // handler should notice and call _uninstall on it.
        container.removeChild(ul);
        // MutationObserver is microtask-scheduled; wait a tick.
        await new Promise((r) => setTimeout(r, 20));
        expect(api._installedParents.has(ul)).toBe(false);
    });

    it('VDOM RemoveChild of a group child defers via wired dj-remove', async () => {
        // End-to-end: a child WITHOUT its own dj-remove attribute is
        // appended to a group, the group wires dj-remove onto it, and
        // when the VDOM patch later removes it, dj-remove defers the
        // physical detach (single-token fade-out is applied on the next
        // frame; the dj-remove-duration fallback finalizes the removal).
        // We pin dj-remove-duration="50" on the child so the fallback
        // fires in ~50 ms instead of the 600 ms default — keeps this
        // integration test's wallclock under 100 ms per run.
        dom = createDom(
            '<ul id="list" dj-transition-group="fade-in | fade-out">' +
            '  <li id="a" dj-remove-duration="50">A</li>' +
            '</ul>'
        );
        await new Promise((r) => setTimeout(r, 0));
        const list = dom.window.document.getElementById('list');
        const item = dom.window.document.getElementById('a');
        // The group already wired dj-remove on the initial child.
        expect(item.getAttribute('dj-remove')).toBe('fade-out');
        // Apply a RemoveChild patch targeting the <ul> (path=[0] — the
        // first element child of [dj-view]) and index 0 (the <li>).
        dom.window.djust._applySinglePatch({
            type: 'RemoveChild',
            path: [0],
            index: 0,
        });
        // dj-remove deferral: the item is still mounted right after the
        // patch (the RemoveChild handler called maybeDeferRemoval and
        // skipped the physical removeChild).
        expect(item.parentNode).toBe(list);
        // Phase 2 fade-out class lands on the next frame (~16 ms), well
        // before the 50 ms fallback fires.
        await new Promise((r) => dom.window.setTimeout(r, 30));
        expect(item.classList.contains('fade-out')).toBe(true);
        // After the 50 ms fallback, physically gone. Give it some margin.
        await new Promise((r) => setTimeout(r, 80));
        expect(item.parentNode).toBeNull();
    });

    it('stripping dj-transition-group at runtime uninstalls the per-parent observer', async () => {
        // Symmetric with dj-remove's cancel-on-strip behavior: when the
        // dj-transition-group attribute is removed at runtime, the root
        // observer's attribute-mutation branch should uninstall the
        // per-parent observer so it stops wiring new children.
        dom = createDom('');
        const doc = dom.window.document;
        const api = dom.window.djust.djTransitionGroup;
        const ul = doc.createElement('ul');
        ul.id = 'u';
        ul.setAttribute('dj-transition-group', '');
        ul.setAttribute('dj-group-leave', 'x y z');
        doc.querySelector('[dj-view]').appendChild(ul);
        // Wait for the root observer to pick up the inserted [dj-transition-group]
        // and call _install on it.
        await new Promise((r) => setTimeout(r, 20));
        expect(api._installedParents.has(ul)).toBe(true);
        // Strip the attribute — the root observer's attribute-mutation
        // branch should call _uninstall.
        ul.removeAttribute('dj-transition-group');
        await new Promise((r) => setTimeout(r, 20));
        expect(api._installedParents.has(ul)).toBe(false);
        // Append a new child; because the per-parent observer has been
        // uninstalled, dj-remove should NOT be wired.
        const li = doc.createElement('li');
        ul.appendChild(li);
        await new Promise((r) => setTimeout(r, 20));
        expect(li.hasAttribute('dj-remove')).toBe(false);
    });

    it('nested [dj-transition-group] parents each have their own observer', async () => {
        // Regression: a nested group inside another group must wire new
        // children against the NEAREST parent's enter/leave specs. Each
        // [dj-transition-group] installs its own per-parent observer with
        // subtree:false, so a mutation at the inner group is not seen by
        // the outer group's observer — otherwise the outer's spec would
        // win and override the inner's.
        dom = createDom(`
            <div id="outer" dj-transition-group
                 dj-group-enter="s-outer a-outer e-outer"
                 dj-group-leave="x-outer y-outer z-outer">
                <div id="inner" dj-transition-group
                     dj-group-enter="s-inner a-inner e-inner"
                     dj-group-leave="x-inner y-inner z-inner">
                </div>
            </div>
        `);
        await new Promise((r) => setTimeout(r, 30));

        // Append a child to the INNER group.
        const inner = dom.window.document.getElementById('inner');
        const newChild = dom.window.document.createElement('span');
        inner.appendChild(newChild);
        await new Promise((r) => setTimeout(r, 30));

        // The new child must get the INNER group's specs, not the outer's.
        expect(newChild.getAttribute('dj-remove')).toBe('x-inner y-inner z-inner');
        expect(newChild.getAttribute('dj-transition')).toBe('s-inner a-inner e-inner');
    });
});
