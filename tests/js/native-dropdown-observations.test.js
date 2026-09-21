import {it, expect} from 'vitest';
import {JSDOM} from 'jsdom';
import {readFileSync} from 'node:fs';

const client = readFileSync('./python/djust/static/djust/client.js', 'utf8');
const tick = () => new Promise(resolve => setTimeout(resolve, 0));

function menu(id, observed = true) {
    return `<div data-component-id="${id}"><button popovertarget="${id}-menu">Open</button>
        <div id="${id}-menu" popover="auto" data-dj-native-dropdown ${observed ?
    `data-dj-observe-toggle="observe_toggle" data-dj-observe-lifetime="obs_${id}" data-dj-observe-sequence="0"` : ''}>
        <button dj-click="select" data-value="edit">Edit</button></div></div>`;
}

async function setup() {
    const dom = new JSDOM(`<div dj-root>${menu('one')}${menu('two')}${menu('quiet', false)}</div>`, {
        url: 'http://localhost/', runScripts: 'dangerously',
    });
    dom.window.DJUST_USE_WEBSOCKET = false;
    const requests = [];
    dom.window.fetch = async (_url, options) => {
        requests.push(JSON.parse(options.body));
        return {ok: true, json: async () => ({type: 'noop'})};
    };
    dom.window.eval(client);
    await tick();
    dom.window.djust.bindLiveViewEvents();
    function toggle(id, open) {
        const element = dom.window.document.getElementById(id + '-menu');
        element._testOpen = open;
        const event = new dom.window.Event('toggle');
        Object.defineProperty(event, 'newState', {value: open ? 'open' : 'closed'});
        element.dispatchEvent(event);
    }
    for (const element of dom.window.document.querySelectorAll('[popover]')) {
        const matches = element.matches.bind(element);
        element.matches = selector => selector === ':popover-open' ? !!element._testOpen : matches(selector);
        element.hidePopover = () => toggle(element.id.replace('-menu', ''), false);
    }
    return {dom, requests, toggle};
}

it('reports completed visibility with the correct source and no unsolicited traffic', async () => {
    const {dom, requests, toggle} = await setup();
    try {
        expect(requests).toEqual([]);
        toggle('quiet', true);
        toggle('one', true);
        toggle('two', false);
        await tick();
        expect(requests).toEqual([
            {component_id: 'one', open: true, sequence: 1, lifetime: 'obs_one'},
            {component_id: 'two', open: false, sequence: 1, lifetime: 'obs_two'},
        ]);
        await tick();
        expect(requests).toHaveLength(2); // no acknowledgement echo
    } finally { dom.window.close(); }
});

it('coalesces pending visibility to its latest value without blocking another menu', async () => {
    const {dom, requests, toggle} = await setup();
    try {
        let finish;
        dom.window.fetch = (_url, options) => {
            requests.push(JSON.parse(options.body));
            return new Promise(resolve => { finish = () => resolve({ok: true, json: async () => ({type: 'noop'})}); });
        };
        toggle('one', true);
        const finishOne = finish;
        toggle('one', false);
        toggle('one', true);
        toggle('one', false);
        toggle('two', true);
        expect(requests).toHaveLength(2);
        finishOne();
        await tick();
        expect(requests[2]).toEqual({component_id: 'one', open: false, sequence: 2, lifetime: 'obs_one'});
    } finally { dom.window.close(); }
});

it('keeps one listener across rebinds and does not rewind the sequence', async () => {
    const {dom, requests, toggle} = await setup();
    try {
        toggle('one', true);
        await tick();
        dom.window.djust.bindLiveViewEvents();
        dom.window.djust.bindLiveViewEvents();
        toggle('one', false);
        await tick();
        expect(requests.map(value => value.sequence)).toEqual([1, 2]);
        const element = dom.window.document.getElementById('one-menu');
        element.removeAttribute('data-dj-observe-toggle');
        dom.window.djust.bindLiveViewEvents();
        toggle('one', true);
        await tick();
        expect(requests).toHaveLength(2);
    } finally { dom.window.close(); }
});

it('discards pending work when its element is removed', async () => {
    const {dom, requests, toggle} = await setup();
    try {
        let finish;
        dom.window.fetch = (_url, options) => {
            requests.push(JSON.parse(options.body));
            return new Promise(resolve => { finish = () => resolve({ok: true, json: async () => ({})}); });
        };
        toggle('one', true);
        toggle('one', false);
        dom.window.document.getElementById('one-menu').remove();
        finish();
        await tick();
        expect(requests).toHaveLength(1);
    } finally { dom.window.close(); }
});

it('reports only the current value when offline changes are rebound', async () => {
    const {dom, requests, toggle} = await setup();
    try {
        Object.defineProperty(dom.window.navigator, 'onLine', {value: false, configurable: true});
        toggle('one', true);
        toggle('one', false);
        toggle('one', true);
        expect(requests).toEqual([]);
        Object.defineProperty(dom.window.navigator, 'onLine', {value: true, configurable: true});
        dom.window.dispatchEvent(new dom.window.Event('online'));
        await tick();
        expect(requests).toEqual([{component_id: 'one', open: true, sequence: 1, lifetime: 'obs_one'}]);
    } finally { dom.window.close(); }
});

it('rebinding a lifetime cannot drain an old pending report into the new owner', async () => {
    const {dom, requests, toggle} = await setup();
    try {
        const pending = [];
        dom.window.fetch = (_url, options) => {
            requests.push(JSON.parse(options.body));
            return new Promise(resolve => pending.push(() => resolve({ok: true, json: async () => ({})})));
        };
        toggle('one', true);
        toggle('one', false);
        const element = dom.window.document.getElementById('one-menu');
        element.setAttribute('data-dj-observe-lifetime', 'obs_new');
        dom.window.djust.bindLiveViewEvents(element); // root itself must be scanned
        await tick();
        expect(requests[1]).toEqual({component_id: 'one', open: false, sequence: 1, lifetime: 'obs_new'});
        pending[0]();
        pending[1]();
        await tick();
        expect(requests).toHaveLength(2);
    } finally { dom.window.close(); }
});

it('does not flush pending visibility from an outgoing page', async () => {
    const {dom, requests, toggle} = await setup();
    try {
        let finish;
        dom.window.fetch = (_url, options) => {
            requests.push(JSON.parse(options.body));
            return new Promise(resolve => { finish = () => resolve({ok: true, json: async () => ({})}); });
        };
        toggle('one', true);
        toggle('one', false);
        dom.window.dispatchEvent(new dom.window.CustomEvent('djust:before-navigate'));
        finish();
        await tick();
        expect(requests).toHaveLength(1);
    } finally { dom.window.close(); }
});

it('dismisses selection locally before awaiting its server response', async () => {
    const {dom, toggle} = await setup();
    try {
        const element = dom.window.document.getElementById('quiet-menu');
        toggle('quiet', true);
        dom.window.fetch = () => new Promise(() => {});
        element.querySelector('button').click();
        expect(element._testOpen).toBe(false);
    } finally { dom.window.close(); }
});
