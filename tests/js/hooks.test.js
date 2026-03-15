/**
 * Tests for dj-hook — client-side JavaScript hooks (src/19-hooks.js)
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createEnv(bodyHtml = '') {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>
            <div dj-root>
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

describe('hooks', () => {
    describe('mountHooks', () => {
        it('calls mounted() on hook elements', () => {
            const { window, document } = createEnv('<div dj-hook="MyHook" dj-view="test"></div>');
            const mountedFn = vi.fn();

            window.djust.hooks = {
                MyHook: { mounted: mountedFn },
            };

            window.djust.mountHooks(document);

            expect(mountedFn).toHaveBeenCalledTimes(1);
        });

        it('does not remount already mounted hooks', () => {
            const { window, document } = createEnv('<div dj-hook="MyHook" dj-view="test"></div>');
            const mountedFn = vi.fn();

            window.djust.hooks = {
                MyHook: { mounted: mountedFn },
            };

            window.djust.mountHooks(document);
            window.djust.mountHooks(document);

            expect(mountedFn).toHaveBeenCalledTimes(1);
        });

        it('sets el property on hook instance', () => {
            const { window, document } = createEnv('<div dj-hook="MyHook" dj-view="test"></div>');
            let instanceEl = null;

            window.djust.hooks = {
                MyHook: {
                    mounted() { instanceEl = this.el; },
                },
            };

            window.djust.mountHooks(document);

            const hookEl = document.querySelector('[dj-hook="MyHook"]');
            expect(instanceEl).toBe(hookEl);
        });
    });

    describe('updateHooks', () => {
        it('calls updated() for existing hook elements', () => {
            const { window, document } = createEnv('<div dj-hook="MyHook" dj-view="test"></div>');
            const updatedFn = vi.fn();

            window.djust.hooks = {
                MyHook: { mounted() {}, updated: updatedFn },
            };

            // Mount first
            window.djust.mountHooks(document);
            // Then update
            window.djust.updateHooks(document);

            expect(updatedFn).toHaveBeenCalledTimes(1);
        });

        it('calls destroyed() for removed elements', () => {
            const { window, document } = createEnv('<div dj-hook="MyHook" dj-view="test"></div>');
            const destroyedFn = vi.fn();

            window.djust.hooks = {
                MyHook: { mounted() {}, destroyed: destroyedFn },
            };

            // Mount
            window.djust.mountHooks(document);

            // Remove the element
            const el = document.querySelector('[dj-hook="MyHook"]');
            el.parentNode.removeChild(el);

            // Update should detect removal
            window.djust.updateHooks(document);

            expect(destroyedFn).toHaveBeenCalledTimes(1);
        });

        it('mounts new elements added after initial mount', () => {
            const { window, document } = createEnv('<div id="container" dj-view="test"></div>');
            const mountedFn = vi.fn();

            window.djust.hooks = {
                NewHook: { mounted: mountedFn },
            };

            window.djust.mountHooks(document);
            expect(mountedFn).not.toHaveBeenCalled();

            // Add new element with hook
            const newEl = document.createElement('div');
            newEl.setAttribute('dj-hook', 'NewHook');
            document.querySelector('#container').appendChild(newEl);

            // Update should discover and mount new element
            window.djust.updateHooks(document);

            expect(mountedFn).toHaveBeenCalledTimes(1);
        });
    });

    describe('notifyHooksDisconnected', () => {
        it('calls disconnected() on all active hooks', () => {
            const { window, document } = createEnv('<div dj-hook="MyHook" dj-view="test"></div>');
            const disconnectedFn = vi.fn();

            window.djust.hooks = {
                MyHook: { mounted() {}, disconnected: disconnectedFn },
            };

            window.djust.mountHooks(document);
            window.djust.notifyHooksDisconnected();

            expect(disconnectedFn).toHaveBeenCalledTimes(1);
        });
    });

    describe('notifyHooksReconnected', () => {
        it('calls reconnected() on all active hooks', () => {
            const { window, document } = createEnv('<div dj-hook="MyHook" dj-view="test"></div>');
            const reconnectedFn = vi.fn();

            window.djust.hooks = {
                MyHook: { mounted() {}, reconnected: reconnectedFn },
            };

            window.djust.mountHooks(document);
            window.djust.notifyHooksReconnected();

            expect(reconnectedFn).toHaveBeenCalledTimes(1);
        });
    });

    describe('dispatchPushEventToHooks', () => {
        it('delivers events to hooks that registered handleEvent', () => {
            const { window, document } = createEnv('<div dj-hook="MyHook" dj-view="test"></div>');
            const receivedPayloads = [];

            window.djust.hooks = {
                MyHook: {
                    mounted() {
                        this.handleEvent('my_event', (payload) => {
                            receivedPayloads.push(payload);
                        });
                    },
                },
            };

            window.djust.mountHooks(document);
            window.djust.dispatchPushEventToHooks('my_event', { data: 'test' });

            expect(receivedPayloads.length).toBe(1);
            expect(receivedPayloads[0].data).toBe('test');
        });

        it('does not deliver events to hooks without matching handler', () => {
            const { window, document } = createEnv('<div dj-hook="MyHook" dj-view="test"></div>');
            const receivedPayloads = [];

            window.djust.hooks = {
                MyHook: {
                    mounted() {
                        this.handleEvent('other_event', (payload) => {
                            receivedPayloads.push(payload);
                        });
                    },
                },
            };

            window.djust.mountHooks(document);
            window.djust.dispatchPushEventToHooks('my_event', { data: 'test' });

            expect(receivedPayloads.length).toBe(0);
        });
    });

    describe('destroyAllHooks', () => {
        it('calls destroyed() on all hooks and clears activeHooks', () => {
            const { window, document } = createEnv(`
                <div dj-hook="HookA" dj-view="test"></div>
                <div dj-hook="HookB" dj-view="test"></div>
            `);
            const destroyedA = vi.fn();
            const destroyedB = vi.fn();

            window.djust.hooks = {
                HookA: { mounted() {}, destroyed: destroyedA },
                HookB: { mounted() {}, destroyed: destroyedB },
            };

            window.djust.mountHooks(document);
            expect(window.djust._activeHooks.size).toBe(2);

            window.djust.destroyAllHooks();

            expect(destroyedA).toHaveBeenCalledTimes(1);
            expect(destroyedB).toHaveBeenCalledTimes(1);
            expect(window.djust._activeHooks.size).toBe(0);
        });
    });

    describe('_activeHooks', () => {
        it('tracks mounted hook instances', () => {
            const { window, document } = createEnv('<div dj-hook="MyHook" dj-view="test"></div>');

            window.djust.hooks = {
                MyHook: { mounted() {} },
            };

            expect(window.djust._activeHooks.size).toBe(0);
            window.djust.mountHooks(document);
            expect(window.djust._activeHooks.size).toBe(1);
        });

        it('is a Map', () => {
            const { window } = createEnv();
            expect(window.djust._activeHooks).toBeInstanceOf(window.Map);
        });
    });

    describe('pushEvent API', () => {
        it('instance has pushEvent method', () => {
            const { window, document } = createEnv('<div dj-hook="MyHook" dj-view="test"></div>');
            let hasPushEvent = false;

            window.djust.hooks = {
                MyHook: {
                    mounted() { hasPushEvent = typeof this.pushEvent === 'function'; },
                },
            };

            window.djust.mountHooks(document);
            expect(hasPushEvent).toBe(true);
        });

        it('sends params (not data) in the WS message', () => {
            const { window, document } = createEnv('<div dj-hook="MyHook" dj-view="test"></div>');
            const sentMessages = [];
            let hookInstance = null;

            // Mock WebSocket on the liveViewInstance
            window.djust.liveViewInstance = {
                ws: {
                    send(msg) { sentMessages.push(JSON.parse(msg)); },
                },
            };

            window.djust.hooks = {
                MyHook: {
                    mounted() { hookInstance = this; },
                },
            };

            window.djust.mountHooks(document);
            hookInstance.pushEvent('my_event', { foo: 42 });

            expect(sentMessages.length).toBe(1);
            expect(sentMessages[0]).toEqual({
                type: 'event',
                event: 'my_event',
                params: { foo: 42 },
            });
            // Ensure "data" key is NOT present
            expect(sentMessages[0].data).toBeUndefined();
        });
    });

    describe('hooks after mount message (live_redirect_mount)', () => {
        it('calls updateHooks after innerHTML mount via WS', async () => {
            // Simulate: initial page has a hook element, then WS mount message
            // replaces innerHTML with new HTML containing a different hook element.
            const { window, document } = createEnv(
                '<div dj-view="myapp.views.OldView"><canvas dj-hook="OldChart"></canvas></div>'
            );

            const oldMounted = vi.fn();
            const oldDestroyed = vi.fn();
            const newMounted = vi.fn();

            window.djust.hooks = {
                OldChart: { mounted: oldMounted, destroyed: oldDestroyed },
                NewChart: { mounted: newMounted },
            };

            // Mount initial hooks
            window.djust.mountHooks(document);
            expect(oldMounted).toHaveBeenCalledTimes(1);

            // Simulate WS mount message: replace container innerHTML
            const container = document.querySelector('[dj-view]') || document.querySelector('[dj-root]');
            container.innerHTML = '<canvas dj-hook="NewChart"></canvas>';

            // After innerHTML replacement, updateHooks should mount new + destroy old
            window.djust.updateHooks(document);

            expect(oldDestroyed).toHaveBeenCalledTimes(1);
            expect(newMounted).toHaveBeenCalledTimes(1);
        });

        it('calls updateHooks after skipMountHtml branch', () => {
            // When pre-rendered content is kept (skipMountHtml), updateHooks
            // should still run to initialize any hook elements already in DOM.
            const { window, document } = createEnv(
                '<div dj-view="myapp.views.TestView"><div dj-hook="PreRenderedHook"></div></div>'
            );

            const mountedFn = vi.fn();
            window.djust.hooks = {
                PreRenderedHook: { mounted: mountedFn },
            };

            // updateHooks on pre-rendered content should mount hooks
            window.djust.updateHooks(document);
            expect(mountedFn).toHaveBeenCalledTimes(1);
        });
    });

    describe('hooks after html_update (full DOM replacement)', () => {
        it('re-mounts hooks when html_update replaces DOM content', () => {
            // Simulate: initial page has a chart hook, then an event triggers
            // html_update which replaces the DOM with new content containing
            // a different chart hook (e.g., filter changed table + chart).
            const { window, document } = createEnv(
                '<div dj-view="myapp.views.RevenueView"><canvas dj-hook="OldChart" data-chart="old"></canvas></div>'
            );

            const oldMounted = vi.fn();
            const oldDestroyed = vi.fn();
            const oldUpdated = vi.fn();
            const newMounted = vi.fn();

            window.djust.hooks = {
                OldChart: { mounted: oldMounted, destroyed: oldDestroyed, updated: oldUpdated },
                NewChart: { mounted: newMounted },
            };

            // Mount initial hooks
            window.djust.mountHooks(document);
            expect(oldMounted).toHaveBeenCalledTimes(1);

            // Simulate html_update: replace liveview root innerHTML
            const root = document.querySelector('[dj-root]');
            root.innerHTML = '<div dj-view="myapp.views.RevenueView"><canvas dj-hook="NewChart" data-chart="new"></canvas></div>';

            // updateHooks should destroy old hooks and mount new ones
            window.djust.updateHooks(document);

            expect(oldDestroyed).toHaveBeenCalledTimes(1);
            expect(newMounted).toHaveBeenCalledTimes(1);
        });

        it('calls updated() when hook element stays but data-chart changes', () => {
            // Simulate: same hook element exists but its data attribute changed
            // (e.g., chart data updated after filter change)
            const { window, document } = createEnv(
                '<canvas dj-hook="MyChart" data-chart="olddata" dj-view="test"></canvas>'
            );

            const mountedFn = vi.fn();
            const updatedFn = vi.fn();

            window.djust.hooks = {
                MyChart: { mounted: mountedFn, updated: updatedFn },
            };

            // Mount initial
            window.djust.mountHooks(document);
            expect(mountedFn).toHaveBeenCalledTimes(1);

            // Change data attribute (simulating server sending new chart data)
            document.querySelector('[dj-hook="MyChart"]').setAttribute('data-chart', 'newdata');

            // updateHooks should call updated() on the existing hook
            window.djust.updateHooks(document);

            expect(updatedFn).toHaveBeenCalledTimes(1);
            expect(mountedFn).toHaveBeenCalledTimes(1); // not re-mounted
        });
    });

    describe('exports', () => {
        it('exposes all expected functions', () => {
            const { window } = createEnv();
            expect(typeof window.djust.mountHooks).toBe('function');
            expect(typeof window.djust.updateHooks).toBe('function');
            expect(typeof window.djust.beforeUpdateHooks).toBe('function');
            expect(typeof window.djust.notifyHooksDisconnected).toBe('function');
            expect(typeof window.djust.notifyHooksReconnected).toBe('function');
            expect(typeof window.djust.dispatchPushEventToHooks).toBe('function');
            expect(typeof window.djust.destroyAllHooks).toBe('function');
            expect(window.djust._activeHooks).toBeDefined();
        });
    });
});
