/**
 * Tests for page metadata — 25-page-metadata.js
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createEnv(headHtml = '') {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head>${headHtml}</head><body></body></html>`,
        { url: 'http://localhost:8000/', runScripts: 'dangerously', pretendToBeVisual: true }
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

describe('page metadata title update', () => {
    it('updates document.title', () => {
        const { window, document } = createEnv();

        window.djust.pageMetadata.handlePageMetadata({ action: 'title', value: 'New Title' });

        expect(document.title).toBe('New Title');
    });

    it('updates document.title multiple times', () => {
        const { window, document } = createEnv();

        window.djust.pageMetadata.handlePageMetadata({ action: 'title', value: 'First' });
        window.djust.pageMetadata.handlePageMetadata({ action: 'title', value: 'Second' });

        expect(document.title).toBe('Second');
    });
});

describe('page metadata meta tag update', () => {
    it('updates existing meta tag content', () => {
        const { window, document } = createEnv(
            '<meta name="description" content="Old description">'
        );

        window.djust.pageMetadata.handlePageMetadata({
            action: 'meta', name: 'description', content: 'New description'
        });

        const meta = document.querySelector('meta[name="description"]');
        expect(meta).not.toBeNull();
        expect(meta.getAttribute('content')).toBe('New description');
    });

    it('creates new meta tag when not found', () => {
        const { window, document } = createEnv();

        window.djust.pageMetadata.handlePageMetadata({
            action: 'meta', name: 'keywords', content: 'test, page'
        });

        const meta = document.querySelector('meta[name="keywords"]');
        expect(meta).not.toBeNull();
        expect(meta.getAttribute('content')).toBe('test, page');
    });

    it('uses property attribute for og: tags', () => {
        const { window, document } = createEnv();

        window.djust.pageMetadata.handlePageMetadata({
            action: 'meta', name: 'og:image', content: 'https://example.com/img.png'
        });

        const meta = document.querySelector('meta[property="og:image"]');
        expect(meta).not.toBeNull();
        expect(meta.getAttribute('content')).toBe('https://example.com/img.png');
        // Should not use name attribute
        expect(meta.getAttribute('name')).toBeNull();
    });

    it('uses property attribute for twitter: tags', () => {
        const { window, document } = createEnv();

        window.djust.pageMetadata.handlePageMetadata({
            action: 'meta', name: 'twitter:card', content: 'summary'
        });

        const meta = document.querySelector('meta[property="twitter:card"]');
        expect(meta).not.toBeNull();
        expect(meta.getAttribute('content')).toBe('summary');
    });

    it('updates existing og: meta tag', () => {
        const { window, document } = createEnv(
            '<meta property="og:title" content="Old">'
        );

        window.djust.pageMetadata.handlePageMetadata({
            action: 'meta', name: 'og:title', content: 'New OG Title'
        });

        const meta = document.querySelector('meta[property="og:title"]');
        expect(meta.getAttribute('content')).toBe('New OG Title');
    });
});

describe('page metadata debug logging', () => {
    it('logs when djustDebug is enabled', () => {
        const { window } = createEnv();

        const logs = [];
        window.console = { log: (...args) => logs.push(args.join(' ')), error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };
        window.djustDebug = true;

        window.djust.pageMetadata.handlePageMetadata({ action: 'title', value: 'Test' });

        expect(logs.some(l => l.includes('page_metadata'))).toBe(true);
    });

    it('does not log when djustDebug is not set', () => {
        const { window } = createEnv();

        const logs = [];
        window.console = { log: (...args) => logs.push(args.join(' ')), error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };

        window.djust.pageMetadata.handlePageMetadata({ action: 'title', value: 'Test' });

        expect(logs.some(l => l.includes('page_metadata'))).toBe(false);
    });
});
