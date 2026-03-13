/**
 * Unit tests for debug panel state size visualization
 *
 * Since the debug panel is an IIFE, we test the logic by replicating
 * the formatBytes and renderStateSizeSection functions.
 */

import { describe, it, expect } from 'vitest';

// Replicate formatBytes logic from 07-tab-state.js
function formatBytes(bytes) {
    if (bytes === null || bytes === undefined) return 'N/A';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

// Replicate escapeHtml logic
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Replicate renderStateSizeSection logic from 07-tab-state.js
function renderStateSizeSection(state_sizes) {
    if (!state_sizes) return '';

    const keys = Object.keys(state_sizes);
    if (keys.length === 0) return '';

    const rows = keys.map(key => {
        const info = state_sizes[key];
        return `
            <tr>
                <td>${escapeHtml(key)}</td>
                <td>${formatBytes(info.memory)}</td>
                <td>${formatBytes(info.serialized)}</td>
            </tr>
        `;
    }).join('');

    return `
        <div class="state-size-breakdown">
            <div class="state-timeline-header">
                <span>Size Breakdown</span>
                <span class="state-count">${keys.length} variable${keys.length === 1 ? '' : 's'}</span>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>Variable</th>
                        <th>Memory</th>
                        <th>Serialized</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
        </div>
    `;
}

describe('State Size Visualization', () => {
    describe('formatBytes logic', () => {
        it('formats bytes correctly', () => {
            expect(formatBytes(500)).toBe('500 B');
            expect(formatBytes(0)).toBe('0 B');
            expect(formatBytes(1)).toBe('1 B');
        });

        it('formats kilobytes correctly', () => {
            expect(formatBytes(1024)).toBe('1.0 KB');
            expect(formatBytes(1536)).toBe('1.5 KB');
            expect(formatBytes(2048)).toBe('2.0 KB');
        });

        it('formats megabytes correctly', () => {
            expect(formatBytes(1048576)).toBe('1.0 MB');
            expect(formatBytes(2097152)).toBe('2.0 MB');
            expect(formatBytes(1572864)).toBe('1.5 MB');
        });

        it('handles null and undefined', () => {
            expect(formatBytes(null)).toBe('N/A');
            expect(formatBytes(undefined)).toBe('N/A');
        });
    });

    describe('renderStateSizeSection logic', () => {
        it('renders size breakdown table with correct data', () => {
            const state_sizes = {
                count: { memory: 28, serialized: 1 },
                items: { memory: 64, serialized: 2 },
                large_data: { memory: 1536, serialized: 1024 },
                huge_data: { memory: 2097152, serialized: 1048576 }
            };

            const html = renderStateSizeSection(state_sizes);

            expect(html).toContain('Size Breakdown');
            expect(html).toContain('4 variables');
            expect(html).toContain('count');
            expect(html).toContain('items');
            expect(html).toContain('large_data');
            expect(html).toContain('huge_data');
        });

        it('formats memory and serialized sizes', () => {
            const state_sizes = {
                count: { memory: 28, serialized: 1 },
                large_data: { memory: 1536, serialized: 1024 },
                huge_data: { memory: 2097152, serialized: 1048576 }
            };

            const html = renderStateSizeSection(state_sizes);

            // Check byte formatting in table
            expect(html).toContain('28 B');   // count memory
            expect(html).toContain('1 B');    // count serialized
            expect(html).toContain('1.5 KB'); // large_data memory
            expect(html).toContain('1.0 KB'); // large_data serialized
            expect(html).toContain('2.0 MB'); // huge_data memory
            expect(html).toContain('1.0 MB'); // huge_data serialized
        });

        it('returns empty string when no state_sizes', () => {
            expect(renderStateSizeSection(null)).toBe('');
            expect(renderStateSizeSection(undefined)).toBe('');
        });

        it('returns empty string when state_sizes is empty object', () => {
            expect(renderStateSizeSection({})).toBe('');
        });

        it('includes table headers', () => {
            const state_sizes = { count: { memory: 28, serialized: 1 } };
            const html = renderStateSizeSection(state_sizes);

            expect(html).toContain('<th');
            expect(html).toContain('Variable');
            expect(html).toContain('Memory');
            expect(html).toContain('Serialized');
        });

        it('escapes HTML in variable names', () => {
            const state_sizes = {
                '<script>alert("xss")</script>': { memory: 100, serialized: 50 }
            };

            const html = renderStateSizeSection(state_sizes);

            // Should be escaped, not executed
            expect(html).not.toContain('<script>alert("xss")</script>');
            expect(html).toContain('&lt;script&gt;');
        });

        it('handles single variable correctly', () => {
            const state_sizes = { count: { memory: 28, serialized: 1 } };
            const html = renderStateSizeSection(state_sizes);

            expect(html).toContain('1 variable');
            expect(html).not.toContain('variables');
        });

        it('handles multiple variables plural correctly', () => {
            const state_sizes = {
                count: { memory: 28, serialized: 1 },
                items: { memory: 64, serialized: 2 }
            };
            const html = renderStateSizeSection(state_sizes);

            expect(html).toContain('2 variables');
        });
    });
});
