// ADR-034 D4: nested markup targets the nearest owning component. An
// interactive menu rendered inside another component's markup sends its own
// identity, never an ancestor's, and an action on the outer component's own
// element still names the outer one.
import {it, expect} from 'vitest';
import {JSDOM} from 'jsdom';
import {readFileSync} from 'node:fs';

const client = readFileSync('./python/djust/static/djust/client.js', 'utf8');

it('an action inside nested components names the nearest owner', async () => {
    const dom = new JSDOM(`<div dj-root dj-view="test.View">
        <div data-component-id="cmp_outer">
            <button id="outer" dj-click="toggle">Outer</button>
            <div class="dj-dropdown-menu" data-component-id="cmp_inner">
                <button id="inner" dj-click="select" dj-value-value="edit">Edit</button>
            </div>
        </div></div>`, {url: 'http://localhost/', runScripts: 'dangerously'});
    try {
        dom.window.DJUST_USE_WEBSOCKET = false;
        const sent = [];
        dom.window.fetch = async (_url, options) => {
            sent.push([options.headers['X-Djust-Event'], JSON.parse(options.body)]);
            return {ok: true, json: async () => ({patches: []})};
        };
        dom.window.eval(client);
        await new Promise(resolve => setTimeout(resolve, 0));
        dom.window.djust.bindLiveViewEvents();
        dom.window.document.getElementById('inner').click();
        await new Promise(resolve => setTimeout(resolve, 20));
        dom.window.document.getElementById('outer').click();
        await new Promise(resolve => setTimeout(resolve, 20));
        expect(sent.map(([event, params]) => [event, params.component_id])).toEqual([
            ['select', 'cmp_inner'],
            ['toggle', 'cmp_outer'],
        ]);
    } finally {
        dom.window.close();
    }
});
