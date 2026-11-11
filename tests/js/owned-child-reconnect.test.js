import {it, expect} from 'vitest';
import {JSDOM} from 'jsdom';
import {readFileSync} from 'node:fs';

const client = readFileSync('./python/djust/static/djust/client.js', 'utf8');
const child = '<div dj-view data-djust-embedded="child" dj-id="3"><button dj-click="act">old child</button></div>';

for (const transport of ['ws', 'sse', 'http']) {
    for (const present of [false, true]) {
        it(`${transport} cold mount reconciles owned child (${present}) without resetting its ignored widget`, async () => {
            const dom = new JSDOM('<main dj-root dj-view="test.Page"><section id="widget" dj-update="ignore" dj-id="1"><input id="local" value="initial" dj-id="2">' + child + '</section><p id="status">old</p></main>', {url: 'http://localhost/', runScripts: 'dangerously'});
            try {
                const w = dom.window;
                w.DJUST_USE_WEBSOCKET = false;
                w.eval(client);
                w.djust._isReconnect = true;
                const local = w.document.getElementById('local');
                local.value = 'local state';
                local.widgetReference = {alive: true};
                let connection;
                if (transport === 'ws') {
                    connection = new w.djust.LiveViewWebSocket();
                    connection.primaryViewPath = 'test.Page';
                    connection.skipMountHtml = true;
                } else if (transport === 'sse') {
                    w.EventSource = class { static CLOSED = 2; close() {} };
                    connection = new w.djust.LiveViewSSE();
                    connection.connect('test.Page', {});
                } else {
                    connection = {handleMessage: async (data) => {
                        w.fetch = async () => ({ok: true, status: 200,
                            headers: {get: () => 'application/json'}, json: async () => data});
                        await w.djust.handleEvent('refresh', {});
                    }};
                }
                const owned = present ? [['child', '8']] : [];
                await connection.handleMessage({
                    type: 'mount', view: 'test.Page', version: 1, has_ids: true,
                    owned_children: owned,
                    html: '<section id="widget" dj-update="ignore" dj-id="6"><input id="local" value="server" dj-id="7">' + (present ? child.replace('dj-id="3"', 'dj-id="8"').replace('old child', 'current child') : '') + '</section><p id="status" dj-id="2">current</p>',
                });
                expect(w.document.getElementById('local')).toBe(local);
                expect(local.value).toBe('local state');
                expect(local.widgetReference.alive).toBe(true);
                expect(local.hasAttribute('dj-id')).toBe(false);
                expect(w.document.getElementById('widget').getAttribute('dj-id')).toBe('6');
                const wrapper = w.document.querySelector('[data-djust-embedded]');
                if (present) {
                    expect(wrapper.textContent).toBe('current child');
                    expect(wrapper.getAttribute('dj-id')).toBe('8');
                } else expect(wrapper).toBeNull();
                expect(w.document.querySelector('p').getAttribute('dj-id')).toBe('2');
            } finally { dom.window.close(); }
        });
    }
}

it('legacy mounts preserve ignored child markup and client state', async () => {
    const dom = new JSDOM('<main dj-root dj-view="test.Page"><section id="widget" dj-update="ignore">' + child + '</section></main>', {url:'http://localhost/', runScripts:'dangerously'});
    try {
        dom.window.DJUST_USE_WEBSOCKET = false;
        dom.window.eval(client);
        const old = dom.window.document.querySelector('[data-djust-embedded]');
        const ws = new dom.window.djust.LiveViewWebSocket();
        ws.primaryViewPath = 'test.Page'; ws.skipMountHtml = true;
        await ws.handleMessage({type:'mount',view:'test.Page',has_ids:true,html:'<section id="widget" dj-update="ignore" dj-id="1"></section>'});
        expect(dom.window.document.querySelector('[data-djust-embedded]')).toBe(old);
    } finally {dom.window.close();}
});
