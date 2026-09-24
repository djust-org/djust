/** Loading ownership must follow the component, not a globally shared handler name. */
import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'node:fs';

const client = readFileSync('./python/djust/static/djust/client.js', 'utf8');

function setup(attribute) {
    const dom = new JSDOM('<!doctype html><html><body></body></html>', {
        url: 'http://localhost/', runScripts: 'dangerously',
    });
    const { window } = dom;
    window.CSS ??= {};
    window.CSS.escape ??= value => String(value).replace(/([^\w-])/g, '\\$1');
    window.eval(client);
    window.document.body.innerHTML = ['left', 'right'].map(id =>
        `<div ${attribute}="${id}"><button id="${id}" dj-click="save" dj-loading.disable>Save</button>` +
        `<span id="${id}-busy" dj-loading="save">Saving</span></div>`
    ).join('');
    const manager = window.djust.globalLoadingManager;
    manager.scanAndRegister();
    return { dom, manager, document: window.document };
}

describe.each(['data-djust-embedded', 'data-component-id'])('%s loading scope', attribute => {
    it('starting one save does not disable the other component', () => {
        const { dom, manager, document } = setup(attribute);
        try {
            const left = document.getElementById('left');
            const right = document.getElementById('right');
            manager.startLoading('save', left);
            expect(left.disabled).toBe(true);
            expect(right.disabled).toBe(false);
            expect(document.getElementById('right-busy').style.display).toBe('none');
        } finally { dom.window.close(); }
    });

    it('finishing one save keeps the other scope and page pending', () => {
        const { dom, manager, document } = setup(attribute);
        try {
            const left = document.getElementById('left');
            const right = document.getElementById('right');
            manager.startLoading('save', left);
            manager.startLoading('save', right);
            manager.stopLoading('save', left);
            expect(left.disabled).toBe(false);
            expect(right.disabled).toBe(true);
            expect(document.getElementById('right-busy').style.display).toBe('block');
            expect(manager.pendingEvents.has('save')).toBe(true);
            expect(document.body.classList.contains('djust-global-loading')).toBe(true);
            manager.stopLoading('save', right);
            expect(manager.pendingEvents.has('save')).toBe(false);
            expect(document.body.classList.contains('djust-global-loading')).toBe(false);
        } finally { dom.window.close(); }
    });

    it('does not transfer pending work to a replacement wrapper with the same ID', () => {
        const { dom, manager, document } = setup(attribute);
        try {
            const left = document.getElementById('left');
            manager.startLoading('save', left);
            left.parentElement.outerHTML = `<div ${attribute}="left"><button id="new" dj-click="save" dj-loading.disable>New</button></div>`;
            manager.scanAndRegister();
            expect(document.getElementById('new').disabled).toBe(false);
            expect(manager.pendingEvents.has('save')).toBe(false);
        } finally { dom.window.close(); }
    });
});

it('uses the nearest nested owner, not the outer component', () => {
    const { dom, manager, document } = setup('data-djust-embedded');
    try {
        document.getElementById('left').parentElement.append(document.getElementById('right').parentElement);
        manager.startLoading('save', document.getElementById('left'));
        expect(document.getElementById('right').disabled).toBe(false);
    } finally { dom.window.close(); }
});

it('a page-level completion without a trigger does not clear child work', () => {
    const { dom, manager, document } = setup('data-djust-embedded');
    try {
        const left = document.getElementById('left');
        manager.startLoading('save');
        manager.startLoading('save', left);
        manager.stopLoading('save');
        expect(left.disabled).toBe(true);
        expect(manager.pendingEvents.has('save')).toBe(true);
    } finally { dom.window.close(); }
});

it('new controls in the same owner inherit its pending state', () => {
    const { dom, manager, document } = setup('data-djust-embedded');
    try {
        const left = document.getElementById('left');
        const owner = left.parentElement;
        manager.startLoading('save', left);
        owner.innerHTML = '<button id="replacement" dj-click="save" dj-loading.disable>Saving</button>';
        manager.scanAndRegister();
        const replacement = document.getElementById('replacement');
        expect(replacement.disabled).toBe(true);
        manager.stopLoading('save', left);
        expect(replacement.disabled).toBe(false);
    } finally { dom.window.close(); }
});

it('referenced replies isolate same-name events through the real WS client', async () => {
    const { dom, manager, document } = setup('data-djust-embedded');
    try {
        const { window } = dom;
        const transport = new window.djust.LiveViewWebSocket();
        const sent = [];
        transport.ws = { readyState: window.WebSocket.OPEN, send: text => sent.push(JSON.parse(text)) };
        transport.viewMounted = true;
        const left = document.getElementById('left');
        const right = document.getElementById('right');
        manager.startLoading('save', left);
        const leftDone = transport.sendEvent('save', {view_id: 'left'}, left);
        manager.startLoading('save', right);
        const rightDone = transport.sendEvent('save', {view_id: 'right'}, right);
        await transport.handleMessage({
            type: 'embedded_update', view_id: 'left', event_name: 'save', ref: sent[0].ref,
            html: '<button id="left" dj-click="save" dj-loading.disable>Saved</button>',
        });
        await leftDone;
        expect(right.disabled).toBe(true);
        expect(manager.pendingEvents.has('save')).toBe(true);
        expect(transport.lastTriggerElement).toBe(right);
        await transport.handleMessage({
            type: 'embedded_update', view_id: 'right', event_name: 'save', ref: sent[1].ref,
            html: '<button id="right" dj-click="save" dj-loading.disable>Saved</button>',
        });
        await rightDone;
        expect(manager.pendingEvents.has('save')).toBe(false);
        expect(document.getElementById('right').disabled).toBe(false);
    } finally { dom.window.close(); }
});
