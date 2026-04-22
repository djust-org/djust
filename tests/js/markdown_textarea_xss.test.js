/**
 * Regression tests for `js/xss-through-dom` CodeQL alert #1978 in
 * python/djust/components/static/djust_components/markdown-textarea.js.
 *
 * The module is an IIFE that injects markdown -> HTML in the preview pane
 * via innerHTML. Prior to the fix, inlineFormat applied regex-based
 * substitutions on raw user input, so a user typing
 *   # <script>alert(1)</script>
 * saw the raw <script> tag rendered in their preview (self-XSS in most
 * deployments; propagated XSS if the textarea's `data-raw` payload ever
 * lands in another user's view).
 *
 * The fix escapes HTML at the top of `inlineFormat` and validates URL
 * schemes in link targets. These tests exercise the DOM output end-to-end
 * by driving the component's MutationObserver-triggered render path and
 * reading back `innerHTML` on the preview element.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const MD_SRC = fs.readFileSync(
    './python/djust/components/static/djust_components/markdown-textarea.js',
    'utf-8'
);

function setupDom(raw) {
    // Build the DOM first, set data-raw via setAttribute (so newlines /
    // special chars are preserved verbatim), then evaluate the IIFE so the
    // initial render sees the attribute.
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>
            <div dj-hook="MarkdownTextarea" class="dj-md-textarea dj-md-textarea--preview">
                <div class="dj-md-textarea__preview"></div>
            </div>
        </body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/', pretendToBeVisual: true }
    );
    const previewEl = dom.window.document.querySelector(
        '.dj-md-textarea__preview'
    );
    previewEl.setAttribute('data-raw', raw);
    // Evaluate the IIFE inside the JSDOM window so it wires up against our DOM.
    dom.window.eval(MD_SRC);
    // JSDOM's readyState is 'loading' during eval, so initAll was deferred to
    // DOMContentLoaded. Fire it synchronously to trigger the initial render.
    dom.window.document.dispatchEvent(
        new dom.window.Event('DOMContentLoaded', { bubbles: true, cancelable: true })
    );
    return dom;
}

function getPreviewHtml(dom) {
    return dom.window.document
        .querySelector('.dj-md-textarea__preview')
        .innerHTML;
}

describe('markdown-textarea: XSS hardening', () => {
    it('escapes <script> in headings (no raw tag in preview)', () => {
        const dom = setupDom('# <script>alert(1)</script>');
        const html = getPreviewHtml(dom);
        expect(html).toContain('&lt;script&gt;');
        // Must not contain a raw executable script tag:
        expect(html).not.toMatch(/<script\b/i);
        expect(html).toMatch(/^<h1>/);
    });

    it('escapes <img onerror> injected in a paragraph', () => {
        const dom = setupDom('<img src=x onerror=alert(1)>');
        const html = getPreviewHtml(dom);
        expect(html).toContain('&lt;img');
        expect(html).not.toMatch(/<img\b/i);
    });

    it('escapes HTML inside list items', () => {
        const dom = setupDom('- <b>not actually bold</b>');
        const html = getPreviewHtml(dom);
        expect(html).toContain('<ul>');
        expect(html).toContain('<li>');
        expect(html).toContain('&lt;b&gt;');
        expect(html).not.toMatch(/<b>not actually bold<\/b>/);
    });

    it('still renders **bold** and *italic* (functionality preserved)', () => {
        const dom = setupDom('**bold** and *italic*');
        const html = getPreviewHtml(dom);
        expect(html).toContain('<strong>bold</strong>');
        expect(html).toContain('<em>italic</em>');
    });

    it('still renders inline `code` (functionality preserved)', () => {
        const dom = setupDom('use `safe_code` here');
        const html = getPreviewHtml(dom);
        expect(html).toContain('<code>safe_code</code>');
    });

    it('rewrites javascript: link URLs to "#"', () => {
        const dom = setupDom('[click](javascript:alert(1))');
        const html = getPreviewHtml(dom);
        expect(html).toContain('<a href="#">click</a>');
        expect(html).not.toContain('javascript:');
    });

    it('rewrites data: link URLs to "#"', () => {
        const dom = setupDom('[click](data:text/html,<script>alert(1)</script>)');
        const html = getPreviewHtml(dom);
        expect(html).toContain('<a href="#">click</a>');
        expect(html).not.toContain('data:text/html');
    });

    it('rewrites VBScript: link URLs to "#" (case-insensitive)', () => {
        const dom = setupDom('[click](VBScript:msgbox(1))');
        const html = getPreviewHtml(dom);
        expect(html).toContain('<a href="#">click</a>');
        expect(html.toLowerCase()).not.toContain('vbscript:');
    });

    it('preserves safe https:// URLs', () => {
        const dom = setupDom('[click](https://safe.example.com/path)');
        const html = getPreviewHtml(dom);
        expect(html).toContain('<a href="https://safe.example.com/path">click</a>');
    });

    it('preserves relative URLs', () => {
        const dom = setupDom('[click](/relative/path)');
        const html = getPreviewHtml(dom);
        expect(html).toContain('<a href="/relative/path">click</a>');
    });

    it('escapes fenced code block content (pre-existing behavior still works)', () => {
        const dom = setupDom('```\n<script>alert(1)</script>\n```');
        const html = getPreviewHtml(dom);
        expect(html).toContain('<pre><code>');
        expect(html).toContain('&lt;script&gt;alert(1)&lt;/script&gt;');
        expect(html).not.toMatch(/<script\b/i);
    });
});
