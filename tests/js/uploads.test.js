/**
 * Tests for file upload support (src/15-uploads.js)
 */

import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import { randomBytes } from 'node:crypto';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createEnv(bodyHtml = '') {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>
            <div data-djust-root>
                ${bodyHtml}
            </div>
        </body></html>`,
        { url: 'http://localhost:8000/test/', runScripts: 'dangerously', pretendToBeVisual: true }
    );
    const { window } = dom;

    // Suppress console
    window.console = { log: () => {}, error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };

    // Provide crypto.getRandomValues (JSDOM may not have it)
    if (!window.crypto || !window.crypto.getRandomValues) {
        Object.defineProperty(window, 'crypto', {
            value: {
                getRandomValues: (arr) => {
                    const bytes = randomBytes(arr.length);
                    arr.set(bytes);
                    return arr;
                }
            },
            configurable: true,
        });
    }

    try {
        window.eval(clientCode);
    } catch (e) {
        // client.js may throw on missing DOM APIs
    }

    return { window, dom, document: dom.window.document };
}

describe('uploads', () => {
    describe('setConfigs', () => {
        it('stores upload configs without error', () => {
            const { window } = createEnv();
            const configs = {
                avatar: { max_file_size: 5 * 1024 * 1024, accept: 'image/*' },
                document: { max_file_size: 10 * 1024 * 1024 },
            };
            expect(() => window.djust.uploads.setConfigs(configs)).not.toThrow();
        });

        it('handles null configs gracefully', () => {
            const { window } = createEnv();
            expect(() => window.djust.uploads.setConfigs(null)).not.toThrow();
        });

        it('handles empty configs', () => {
            const { window } = createEnv();
            expect(() => window.djust.uploads.setConfigs({})).not.toThrow();
        });
    });

    describe('activeUploads', () => {
        it('is a Map', () => {
            const { window } = createEnv();
            expect(window.djust.uploads.activeUploads).toBeInstanceOf(window.Map);
        });

        it('starts empty', () => {
            const { window } = createEnv();
            expect(window.djust.uploads.activeUploads.size).toBe(0);
        });
    });

    describe('handleProgress', () => {
        it('updates progress bar DOM elements', () => {
            const { window, document } = createEnv(`
                <div data-upload-ref="test-ref-123">
                    <div class="upload-progress-bar" style="width: 0%"></div>
                    <span class="upload-progress-text">0%</span>
                </div>
            `);

            window.djust.uploads.handleProgress({ ref: 'test-ref-123', progress: 50, status: 'in_progress' });

            const bar = document.querySelector('.upload-progress-bar');
            expect(bar.style.width).toBe('50%');
            expect(bar.getAttribute('aria-valuenow')).toBe('50');
        });

        it('updates progress text', () => {
            const { window, document } = createEnv(`
                <div data-upload-ref="ref-456">
                    <div class="upload-progress-bar" style="width: 0%"></div>
                    <span class="upload-progress-text">0%</span>
                </div>
            `);

            window.djust.uploads.handleProgress({ ref: 'ref-456', progress: 75, status: 'in_progress' });

            const text = document.querySelector('.upload-progress-text');
            expect(text.textContent).toBe('75%');
        });

        it('dispatches djust:upload:progress custom event', () => {
            const { window } = createEnv();
            const events = [];
            window.addEventListener('djust:upload:progress', (e) => events.push(e.detail));

            window.djust.uploads.handleProgress({ ref: 'ref-789', progress: 30, status: 'in_progress' });

            expect(events.length).toBe(1);
            expect(events[0].ref).toBe('ref-789');
            expect(events[0].progress).toBe(30);
        });

        it('removes from activeUploads on complete status', () => {
            const { window } = createEnv();
            window.djust.uploads.activeUploads.set('ref-complete', {
                resolve: () => {}, reject: () => {}, uploadName: 'test'
            });
            expect(window.djust.uploads.activeUploads.has('ref-complete')).toBe(true);

            window.djust.uploads.handleProgress({ ref: 'ref-complete', progress: 100, status: 'complete' });

            expect(window.djust.uploads.activeUploads.has('ref-complete')).toBe(false);
        });

        it('removes from activeUploads on error status', () => {
            const { window } = createEnv();
            window.djust.uploads.activeUploads.set('ref-error', {
                resolve: () => {}, reject: () => {}, uploadName: 'test'
            });

            window.djust.uploads.handleProgress({ ref: 'ref-error', progress: 0, status: 'error' });

            expect(window.djust.uploads.activeUploads.has('ref-error')).toBe(false);
        });
    });

    describe('cancelUpload', () => {
        it('is a function', () => {
            const { window } = createEnv();
            expect(typeof window.djust.uploads.cancelUpload).toBe('function');
        });
    });

    describe('bindHandlers', () => {
        it('binds dj-upload file inputs', () => {
            const { window, document } = createEnv('<input type="file" dj-upload="avatar" />');

            expect(() => window.djust.uploads.bindHandlers()).not.toThrow();

            const input = document.querySelector('[dj-upload]');
            expect(input._djUploadBound).toBe(true);
        });

        it('sets accept attribute from config', () => {
            const { window, document } = createEnv('<input type="file" dj-upload="avatar" />');

            window.djust.uploads.setConfigs({
                avatar: { max_file_size: 5 * 1024 * 1024, accept: 'image/*' },
            });
            window.djust.uploads.bindHandlers();

            const input = document.querySelector('[dj-upload]');
            expect(input.getAttribute('accept')).toBe('image/*');
        });

        it('sets multiple attribute when max_entries > 1', () => {
            const { window, document } = createEnv('<input type="file" dj-upload="photos" />');

            window.djust.uploads.setConfigs({
                photos: { max_file_size: 10 * 1024 * 1024, max_entries: 5 },
            });
            window.djust.uploads.bindHandlers();

            const input = document.querySelector('[dj-upload]');
            expect(input.hasAttribute('multiple')).toBe(true);
        });

        it('does not double-bind inputs', () => {
            const { window, document } = createEnv('<input type="file" dj-upload="avatar" />');

            window.djust.uploads.bindHandlers();
            window.djust.uploads.bindHandlers();

            const input = document.querySelector('[dj-upload]');
            expect(input._djUploadBound).toBe(true);
        });

        it('binds drop zones', () => {
            const { window, document } = createEnv('<div dj-upload-drop="docs">Drop files here</div>');

            window.djust.uploads.bindHandlers();

            const zone = document.querySelector('[dj-upload-drop]');
            expect(zone._djDropBound).toBe(true);
        });
    });

    describe('exports', () => {
        it('exposes all expected methods', () => {
            const { window } = createEnv();
            expect(typeof window.djust.uploads.setConfigs).toBe('function');
            expect(typeof window.djust.uploads.handleProgress).toBe('function');
            expect(typeof window.djust.uploads.bindHandlers).toBe('function');
            expect(typeof window.djust.uploads.cancelUpload).toBe('function');
            expect(window.djust.uploads.activeUploads).toBeDefined();
        });
    });
});
