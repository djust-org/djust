/**
 * Tests for whitespace preservation in VDOM operations.
 *
 * Verifies that whitespace (including newlines) inside <pre> tags
 * is preserved during DOM patching operations.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';

// Load the client module
const clientCode = await import('fs').then(fs =>
    fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8')
);

// Execute in JSDOM environment
const dom = new JSDOM('<!DOCTYPE html><html><body><div id="test"></div></body></html>', {
    runScripts: 'dangerously',
});

// Execute the client code
dom.window.eval(clientCode);

// Get exposed functions
const { getSignificantChildren, _stampDjIds } = dom.window.djust;

describe('getSignificantChildren', () => {
    let document;

    beforeEach(() => {
        document = dom.window.document;
    });

    it('should include non-whitespace text nodes', () => {
        const div = document.createElement('div');
        div.innerHTML = '<span>Hello</span> World <span>Foo</span>';

        const children = getSignificantChildren(div);

        // Should include: span, text(" World "), span
        expect(children.length).toBe(3);
    });

    it('should exclude whitespace-only text nodes in regular elements', () => {
        const div = document.createElement('div');
        div.innerHTML = '<span>A</span>\n    \n<span>B</span>';

        const children = getSignificantChildren(div);

        // Should only include the two spans, not the whitespace between
        expect(children.length).toBe(2);
    });

    describe('whitespace preservation in <pre> tags', () => {
        it('should preserve newline text nodes inside <pre> elements', () => {
            const pre = document.createElement('pre');
            pre.innerHTML = 'line1\nline2\nline3';

            const children = getSignificantChildren(pre);

            // Pre tag has one text node with newlines - should be preserved
            expect(children.length).toBe(1);
            expect(children[0].textContent).toContain('\n');
        });

        it('should preserve whitespace-only text nodes inside <pre> elements', () => {
            const pre = document.createElement('pre');
            // Create structure with explicit whitespace text nodes
            const text1 = document.createTextNode('def foo():');
            const newline = document.createTextNode('\n');
            const text2 = document.createTextNode('    return 42');
            pre.appendChild(text1);
            pre.appendChild(newline);
            pre.appendChild(text2);

            const children = getSignificantChildren(pre);

            // Should preserve ALL text nodes including the newline-only node
            expect(children.length).toBe(3);
            expect(children[1].textContent).toBe('\n');
        });

        it('should preserve whitespace in nested <pre><code> structure', () => {
            const pre = document.createElement('pre');
            const code = document.createElement('code');
            code.textContent = 'function foo() {\n    return 42;\n}';
            pre.appendChild(code);

            const preChildren = getSignificantChildren(pre);
            expect(preChildren.length).toBe(1);
            expect(preChildren[0]).toBe(code);

            // The code element should have its text preserved
            expect(code.textContent).toContain('\n');
        });

        it('should preserve whitespace-only nodes between elements inside <pre>', () => {
            const pre = document.createElement('pre');
            pre.innerHTML = '<span class="keyword">def</span>\n<span class="name">foo</span>';

            const children = getSignificantChildren(pre);

            // Should include: span, newline text node, span
            // THIS IS THE FAILING TEST - currently getSignificantChildren strips the newline
            expect(children.length).toBe(3);
        });
    });

    describe('whitespace preservation in <code> tags', () => {
        it('should preserve whitespace-only nodes inside <code> elements', () => {
            const code = document.createElement('code');
            const text1 = document.createTextNode('x');
            const whitespace = document.createTextNode('  '); // Indentation
            const text2 = document.createTextNode('= 1');
            code.appendChild(text1);
            code.appendChild(whitespace);
            code.appendChild(text2);

            const children = getSignificantChildren(code);

            // Should preserve all three text nodes
            expect(children.length).toBe(3);
        });
    });

    describe('whitespace preservation in <textarea> tags', () => {
        it('should preserve whitespace inside <textarea> elements', () => {
            const textarea = document.createElement('textarea');
            textarea.textContent = '  indented\n  text  ';

            const children = getSignificantChildren(textarea);

            expect(children.length).toBe(1);
            expect(children[0].textContent).toBe('  indented\n  text  ');
        });
    });
});

describe('innerHTML hydration preserves newlines', () => {
    let document;

    beforeEach(() => {
        document = dom.window.document;
    });

    it('should preserve newlines when setting innerHTML on container with <pre> content', () => {
        const container = document.createElement('div');
        const htmlWithNewlines = '<pre><code>line1\nline2\nline3</code></pre>';

        container.innerHTML = htmlWithNewlines;

        const code = container.querySelector('code');
        expect(code.textContent).toBe('line1\nline2\nline3');
        expect(code.textContent).toContain('\n');
    });

    it('should preserve newlines after JSON parse and innerHTML set', () => {
        // Simulate the exact WebSocket message flow
        const jsonMessage = JSON.stringify({
            type: 'mount',
            html: '<div data-djust-root><pre><code>def foo():\n    return 42</code></pre></div>'
        });

        // Parse JSON (this should convert \\n to \n)
        const data = JSON.parse(jsonMessage);

        // Verify JSON parsing preserved the newline
        expect(data.html).toContain('\n');
        expect(data.html).toBe('<div data-djust-root><pre><code>def foo():\n    return 42</code></pre></div>');

        // Now set innerHTML like the client does
        const container = document.createElement('div');
        container.innerHTML = data.html;

        const code = container.querySelector('code');
        expect(code.textContent).toContain('\n');
        expect(code.textContent).toBe('def foo():\n    return 42');
    });

    it('should preserve newlines with Pygments-style syntax highlighting spans', () => {
        // This mimics the actual HTML structure from Pygments
        const htmlWithSpans = `<pre><code><span class="k">def</span> <span class="nf">foo</span><span class="p">():</span>
    <span class="k">return</span> <span class="mi">42</span></code></pre>`;

        const container = document.createElement('div');
        container.innerHTML = htmlWithSpans;

        const code = container.querySelector('code');
        // The text content should preserve the newline
        expect(code.textContent).toContain('\n');
    });

    it('should preserve newlines with complex nested structure like code_tabs', () => {
        // This mimics the actual code_tabs structure used in djust.org
        const htmlWithTabs = `
            <div class="code-container">
                <pre class="language-python"><code><span class="c1"># Comment</span>
<span class="k">def</span> <span class="nf">hello</span><span class="p">():</span>
    <span class="k">return</span> <span class="s2">"world"</span></code></pre>
            </div>
        `;

        const container = document.createElement('div');
        container.innerHTML = htmlWithTabs;

        const code = container.querySelector('code');
        expect(code.textContent).toContain('\n');

        // Count the newlines
        const newlineCount = (code.textContent.match(/\n/g) || []).length;
        expect(newlineCount).toBeGreaterThanOrEqual(2);
    });

    it('should handle escaped newlines correctly from WebSocket message', () => {
        // The server sends JSON with escaped newlines (\\n in the raw string)
        // When received by WebSocket, it's parsed and \\n becomes \n
        const rawServerJson = '{"type":"mount","html":"<pre>line1\\nline2</pre>"}';

        const data = JSON.parse(rawServerJson);

        // After parsing, the \n should be a real newline
        expect(data.html).toBe('<pre>line1\nline2</pre>');

        const container = document.createElement('div');
        container.innerHTML = data.html;

        const pre = container.querySelector('pre');
        expect(pre.textContent).toBe('line1\nline2');
    });
});

describe('_stampDjIds', () => {
    let document;

    beforeEach(() => {
        document = dom.window.document;
    });

    afterEach(() => {
        // Clean up any containers added to body
        const container = document.querySelector('[data-djust-root]');
        if (container) container.remove();
    });

    it('should stamp data-dj-id attributes onto existing DOM elements', () => {
        const container = document.createElement('div');
        container.setAttribute('data-djust-root', '');
        container.innerHTML = '<main><div><span>Hello</span></div></main>';
        document.body.appendChild(container);

        const serverHtml = '<main data-dj-id="1"><div data-dj-id="2"><span data-dj-id="3">Hello</span></div></main>';
        _stampDjIds(serverHtml);

        expect(container.querySelector('main').getAttribute('data-dj-id')).toBe('1');
        expect(container.querySelector('div').getAttribute('data-dj-id')).toBe('2');
        expect(container.querySelector('span').getAttribute('data-dj-id')).toBe('3');
    });

    it('should preserve whitespace in code blocks while stamping IDs', () => {
        const container = document.createElement('div');
        container.setAttribute('data-djust-root', '');
        container.innerHTML = '<pre><code><span class="w">\u00a0\u00a0\u00a0\u00a0</span><span class="k">return</span></code></pre>';
        const originalText = container.querySelector('code').textContent;
        document.body.appendChild(container);

        const serverHtml = '<pre data-dj-id="1"><code data-dj-id="2"><span data-dj-id="3"></span><span data-dj-id="4">return</span></code></pre>';
        _stampDjIds(serverHtml);

        // Whitespace must be preserved from original DOM
        expect(container.querySelector('code').textContent).toBe(originalText);
        // IDs should be stamped
        expect(container.querySelector('pre').getAttribute('data-dj-id')).toBe('1');
    });

    it('should handle mismatched child counts gracefully', () => {
        const container = document.createElement('div');
        container.setAttribute('data-djust-root', '');
        container.innerHTML = '<div><span>A</span><span>B</span><span>C</span></div>';
        document.body.appendChild(container);

        const serverHtml = '<div data-dj-id="1"><span data-dj-id="2">A</span><span data-dj-id="3">B</span></div>';
        _stampDjIds(serverHtml);

        const spans = container.querySelectorAll('span');
        expect(spans[0].getAttribute('data-dj-id')).toBe('2');
        expect(spans[1].getAttribute('data-dj-id')).toBe('3');
        expect(spans[2].getAttribute('data-dj-id')).toBeNull();
    });

    it('should bail out on tag-name mismatch', () => {
        const container = document.createElement('div');
        container.setAttribute('data-djust-root', '');
        container.innerHTML = '<div><span>A</span></div>';
        document.body.appendChild(container);

        // Server has a <p> where DOM has <span> — should not stamp
        const serverHtml = '<div data-dj-id="1"><p data-dj-id="2">A</p></div>';
        _stampDjIds(serverHtml);

        expect(container.querySelector('div').getAttribute('data-dj-id')).toBe('1');
        expect(container.querySelector('span').getAttribute('data-dj-id')).toBeNull();
    });

    it('should handle no container in DOM gracefully', () => {
        // Remove any existing containers
        const existing = document.querySelector('[data-djust-root]');
        if (existing) existing.remove();

        // Should not throw
        expect(() => _stampDjIds('<div data-dj-id="1">test</div>')).not.toThrow();
    });
});
