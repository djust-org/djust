/**
 * Tests that internal/client-only properties (_targetElement, _optimisticUpdateId,
 * _skipLoading, _djTargetSelector) are stripped from params before sending to server.
 *
 * Bug: HTMLElement references in params (e.g., _targetElement) corrupt the JSON
 * payload when serialized via JSON.stringify(), causing form field data to be lost
 * or overwritten by the element's indexed children.
 *
 * Issue: https://github.com/djust-org/djust/issues/308
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { readFileSync } from 'fs';

// Load just the event handler source (not the full bundled client)
const eventHandlerCode = readFileSync('./python/djust/static/djust/src/11-event-handler.js', 'utf-8');

describe('handleEvent strips internal params before sending', () => {
    let handleEvent;
    let capturedParams;
    let capturedTriggerElement;
    let capturedEventName;

    beforeEach(() => {
        capturedParams = null;
        capturedTriggerElement = null;
        capturedEventName = null;

        // Minimal stubs for handleEvent's dependencies
        const mockEnv = {
            // Cache stubs
            cacheConfig: new Map(),
            buildCacheKey: (name, params) => name,
            getCachedResult: () => null,
            generateCacheRequestId: () => 'req-1',
            pendingCacheRequests: new Map(),
            PENDING_CACHE_TIMEOUT: 10000,

            // Loading state stub
            globalLoadingManager: {
                startLoading: () => {},
                stopLoading: () => {},
            },

            // VDOM stubs
            applyPatches: () => {},
            initReactCounters: () => {},
            initTodoItems: () => {},
            bindLiveViewEvents: () => {},

            // Mock WebSocket that captures what's sent
            liveViewWS: {
                enabled: true,
                ws: { readyState: 1 },
                viewMounted: true,
                lastEventName: null,
                lastTriggerElement: null,
                sendEvent(eventName, params, triggerElement) {
                    capturedEventName = eventName;
                    // JSON round-trip to verify serialization safety
                    capturedParams = JSON.parse(JSON.stringify(params));
                    capturedTriggerElement = triggerElement;
                    return true;
                },
            },

            // Window/djust namespace
            window: { djust: {} },
            globalThis: {},
        };

        // Build a function that has all stubs in scope and returns handleEvent
        const stubVars = Object.keys(mockEnv)
            .map(k => `var ${k} = __env.${k};`)
            .join('\n');

        const wrappedCode = `
            (function(__env) {
                ${stubVars}
                ${eventHandlerCode}
                return handleEvent;
            })
        `;

        const factory = eval(wrappedCode);
        handleEvent = factory(mockEnv);
    });

    it('should strip _targetElement from dj-click params', async () => {
        const button = document.createElement('button');
        button.setAttribute('data-item-id', '42');

        await handleEvent('delete_item', {
            item_id: '42',
            _targetElement: button,
            _optimisticUpdateId: null,
        });

        expect(capturedParams).not.toBeNull();
        expect(capturedParams.item_id).toBe('42');
        expect(capturedParams).not.toHaveProperty('_targetElement');
        expect(capturedParams).not.toHaveProperty('_optimisticUpdateId');
    });

    it('should strip _targetElement from dj-submit params (form element)', async () => {
        // Create a form with named inputs — this is the exact bug scenario.
        const form = document.createElement('form');
        const titleInput = document.createElement('input');
        titleInput.name = 'title';
        titleInput.value = 'Test Idea';
        form.appendChild(titleInput);

        const descInput = document.createElement('textarea');
        descInput.name = 'description';
        descInput.value = 'A great idea';
        form.appendChild(descInput);

        await handleEvent('create_idea', {
            title: 'Test Idea',
            description: 'A great idea',
            category: 'idea',
            priority: 'P2',
            _targetElement: form,
        });

        expect(capturedParams).not.toBeNull();
        expect(capturedParams.title).toBe('Test Idea');
        expect(capturedParams.description).toBe('A great idea');
        expect(capturedParams.category).toBe('idea');
        expect(capturedParams.priority).toBe('P2');
        expect(capturedParams).not.toHaveProperty('_targetElement');
    });

    it('should strip all internal properties', async () => {
        const el = document.createElement('button');

        await handleEvent('some_action', {
            value: 'hello',
            _targetElement: el,
            _optimisticUpdateId: 'opt-123',
            _skipLoading: true,
            _djTargetSelector: '#my-target',
        });

        expect(capturedParams).not.toBeNull();
        expect(capturedParams.value).toBe('hello');
        expect(capturedParams).not.toHaveProperty('_targetElement');
        expect(capturedParams).not.toHaveProperty('_optimisticUpdateId');
        expect(capturedParams).not.toHaveProperty('_skipLoading');
        expect(capturedParams).not.toHaveProperty('_djTargetSelector');
    });

    it('should preserve _args and component_id (server-needed props)', async () => {
        const el = document.createElement('button');

        await handleEvent('set_period', {
            _args: ['month'],
            component_id: 'sidebar',
            _targetElement: el,
        });

        expect(capturedParams).not.toBeNull();
        expect(capturedParams._args).toEqual(['month']);
        expect(capturedParams.component_id).toBe('sidebar');
        expect(capturedParams).not.toHaveProperty('_targetElement');
    });

    it('should still pass triggerElement to sendEvent for loading state', async () => {
        const button = document.createElement('button');
        button.id = 'my-button';

        await handleEvent('click_action', {
            count: '5',
            _targetElement: button,
        });

        expect(capturedTriggerElement).toBe(button);
        expect(capturedTriggerElement.id).toBe('my-button');
    });

    it('should not corrupt params when _targetElement is a form with named inputs', async () => {
        // Reproduce the exact bug: a form element with inputs named "title", "description".
        // When JSON.stringify'd, HTMLFormElement serializes its children which can
        // overwrite same-named keys in the params object.
        const form = document.createElement('form');

        ['title', 'description', 'category', 'priority'].forEach(name => {
            const input = document.createElement('input');
            input.name = name;
            input.value = `value_for_${name}`;
            form.appendChild(input);
        });

        await handleEvent('create_idea', {
            title: 'My Title',
            description: 'My Description',
            category: 'idea',
            priority: 'P1',
            _targetElement: form,
        });

        expect(capturedParams).not.toBeNull();
        expect(capturedParams.title).toBe('My Title');
        expect(capturedParams.description).toBe('My Description');
        expect(capturedParams.category).toBe('idea');
        expect(capturedParams.priority).toBe('P1');

        // No numeric keys from form element serialization
        expect(capturedParams).not.toHaveProperty('0');
        expect(capturedParams).not.toHaveProperty('1');
        expect(capturedParams).not.toHaveProperty('_targetElement');
    });
});
