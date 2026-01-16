/**
 * Unit tests for DraftMode (localStorage auto-save)
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
    DraftManager,
    globalDraftManager,
    collectFormData,
    restoreFormData,
    clearAllState
} from '../../python/djust/static/djust/decorators.js';

describe('DraftManager', () => {
    let manager;
    let mockLocalStorage;

    beforeEach(() => {
        // Create mock localStorage
        mockLocalStorage = {};
        global.localStorage = {
            getItem: vi.fn(key => mockLocalStorage[key] || null),
            setItem: vi.fn((key, value) => { mockLocalStorage[key] = value; }),
            removeItem: vi.fn(key => { delete mockLocalStorage[key]; }),
            clear: vi.fn(() => { mockLocalStorage = {}; }),
            get length() { return Object.keys(mockLocalStorage).length; },
            key: vi.fn(index => Object.keys(mockLocalStorage)[index] || null)
        };

        manager = new DraftManager();
        vi.useFakeTimers();
    });

    // ========================================================================
    // Save Draft
    // ========================================================================

    describe('saveDraft', () => {
        it('should save draft to localStorage with debouncing', () => {
            const data = { title: 'Test Article', content: 'Hello world' };

            manager.saveDraft('article_1', data);

            // Should not save immediately (debounced)
            expect(localStorage.setItem).not.toHaveBeenCalled();

            // Fast-forward 500ms
            vi.advanceTimersByTime(500);

            // Should save after debounce delay
            expect(localStorage.setItem).toHaveBeenCalledWith(
                'djust_draft_article_1',
                expect.stringContaining('"title":"Test Article"')
            );
        });

        it('should debounce multiple save calls', () => {
            manager.saveDraft('article_1', { title: 'Draft 1' });
            vi.advanceTimersByTime(200);

            manager.saveDraft('article_1', { title: 'Draft 2' });
            vi.advanceTimersByTime(200);

            manager.saveDraft('article_1', { title: 'Draft 3' });
            vi.advanceTimersByTime(500);

            // Should only save once with final data
            expect(localStorage.setItem).toHaveBeenCalledTimes(1);
            expect(localStorage.setItem).toHaveBeenCalledWith(
                'djust_draft_article_1',
                expect.stringContaining('"title":"Draft 3"')
            );
        });

        it('should save with timestamp', () => {
            const data = { title: 'Test' };
            const now = Date.now();

            manager.saveDraft('article_1', data);
            vi.advanceTimersByTime(500);

            const saved = mockLocalStorage['djust_draft_article_1'];
            const parsed = JSON.parse(saved);

            expect(parsed).toHaveProperty('data');
            expect(parsed).toHaveProperty('timestamp');
            expect(parsed.timestamp).toBeGreaterThanOrEqual(now);
            expect(parsed.data).toEqual(data);
        });

        it('should handle localStorage errors gracefully', () => {
            const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
            localStorage.setItem.mockImplementation(() => {
                throw new Error('QuotaExceededError');
            });

            manager.saveDraft('article_1', { title: 'Test' });
            vi.advanceTimersByTime(500);

            expect(consoleSpy).toHaveBeenCalled();
            consoleSpy.mockRestore();
        });

        it('should save multiple drafts with different keys', () => {
            manager.saveDraft('article_1', { title: 'Article 1' });
            manager.saveDraft('article_2', { title: 'Article 2' });

            vi.advanceTimersByTime(500);

            expect(localStorage.setItem).toHaveBeenCalledWith(
                'djust_draft_article_1',
                expect.anything()
            );
            expect(localStorage.setItem).toHaveBeenCalledWith(
                'djust_draft_article_2',
                expect.anything()
            );
        });
    });

    // ========================================================================
    // Load Draft
    // ========================================================================

    describe('loadDraft', () => {
        it('should load draft from localStorage', () => {
            const data = { title: 'Test Article' };
            const draftData = {
                data,
                timestamp: Date.now()
            };
            mockLocalStorage['djust_draft_article_1'] = JSON.stringify(draftData);

            const loaded = manager.loadDraft('article_1');

            expect(loaded).toEqual(data);
        });

        it('should return null if draft not found', () => {
            const loaded = manager.loadDraft('nonexistent');

            expect(loaded).toBeNull();
        });

        it('should handle JSON parse errors', () => {
            const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
            mockLocalStorage['djust_draft_article_1'] = 'invalid json';

            const loaded = manager.loadDraft('article_1');

            expect(loaded).toBeNull();
            expect(consoleSpy).toHaveBeenCalled();
            consoleSpy.mockRestore();
        });

        it('should handle localStorage errors', () => {
            const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
            localStorage.getItem.mockImplementation(() => {
                throw new Error('localStorage error');
            });

            const loaded = manager.loadDraft('article_1');

            expect(loaded).toBeNull();
            expect(consoleSpy).toHaveBeenCalled();
            consoleSpy.mockRestore();
        });
    });

    // ========================================================================
    // Clear Draft
    // ========================================================================

    describe('clearDraft', () => {
        it('should clear draft from localStorage', () => {
            mockLocalStorage['djust_draft_article_1'] = JSON.stringify({
                data: { title: 'Test' },
                timestamp: Date.now()
            });

            manager.clearDraft('article_1');

            expect(localStorage.removeItem).toHaveBeenCalledWith('djust_draft_article_1');
        });

        it('should clear pending save timer', () => {
            manager.saveDraft('article_1', { title: 'Test' });

            // Clear before debounce fires
            manager.clearDraft('article_1');
            vi.advanceTimersByTime(500);

            // Should not save
            expect(localStorage.setItem).not.toHaveBeenCalled();
        });

        it('should handle localStorage errors gracefully', () => {
            const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
            localStorage.removeItem.mockImplementation(() => {
                throw new Error('localStorage error');
            });

            manager.clearDraft('article_1');

            expect(consoleSpy).toHaveBeenCalled();
            consoleSpy.mockRestore();
        });
    });

    // ========================================================================
    // Get All Draft Keys
    // ========================================================================

    describe('getAllDraftKeys', () => {
        it('should return all draft keys', () => {
            mockLocalStorage['djust_draft_article_1'] = 'data1';
            mockLocalStorage['djust_draft_article_2'] = 'data2';
            mockLocalStorage['other_key'] = 'data3';

            const keys = manager.getAllDraftKeys();

            expect(keys).toEqual(['article_1', 'article_2']);
        });

        it('should return empty array if no drafts', () => {
            const keys = manager.getAllDraftKeys();

            expect(keys).toEqual([]);
        });

        it('should handle localStorage access gracefully', () => {
            // When localStorage.length throws, getAllDraftKeys should return empty array
            Object.defineProperty(global.localStorage, 'length', {
                get: () => { throw new Error('localStorage error'); }
            });

            const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
            const keys = manager.getAllDraftKeys();

            expect(keys).toEqual([]);
            consoleSpy.mockRestore();
        });
    });

    // ========================================================================
    // Clear All Drafts
    // ========================================================================

    describe('clearAllDrafts', () => {
        it('should clear all drafts from localStorage', () => {
            mockLocalStorage['djust_draft_article_1'] = 'data1';
            mockLocalStorage['djust_draft_article_2'] = 'data2';
            mockLocalStorage['other_key'] = 'data3';

            manager.clearAllDrafts();

            expect(localStorage.removeItem).toHaveBeenCalledWith('djust_draft_article_1');
            expect(localStorage.removeItem).toHaveBeenCalledWith('djust_draft_article_2');
            expect(mockLocalStorage['other_key']).toBe('data3'); // Not removed
        });

        it('should handle empty localStorage', () => {
            manager.clearAllDrafts();

            expect(localStorage.removeItem).not.toHaveBeenCalled();
        });
    });
});

describe('Form Data Helpers', () => {
    beforeEach(() => {
        // Set up DOM
        document.body.innerHTML = `
            <div id="form-container">
                <input type="text" name="title" value="" />
                <textarea name="content"></textarea>
                <input type="checkbox" name="published" />
                <input type="radio" name="category" value="tech" />
                <input type="radio" name="category" value="business" checked />
                <select name="status">
                    <option value="draft">Draft</option>
                    <option value="published">Published</option>
                </select>
                <div id="editor" contenteditable="true"></div>
            </div>
        `;
    });

    // ========================================================================
    // Collect Form Data
    // ========================================================================

    describe('collectFormData', () => {
        it('should collect text input values', () => {
            const container = document.getElementById('form-container');
            container.querySelector('[name="title"]').value = 'Test Title';

            const data = collectFormData(container);

            expect(data.title).toBe('Test Title');
        });

        it('should collect textarea values', () => {
            const container = document.getElementById('form-container');
            container.querySelector('[name="content"]').value = 'Test Content';

            const data = collectFormData(container);

            expect(data.content).toBe('Test Content');
        });

        it('should collect checkbox state', () => {
            const container = document.getElementById('form-container');
            const checkbox = container.querySelector('[name="published"]');

            checkbox.checked = true;
            let data = collectFormData(container);
            expect(data.published).toBe(true);

            checkbox.checked = false;
            data = collectFormData(container);
            expect(data.published).toBe(false);
        });

        it('should collect selected radio button', () => {
            const container = document.getElementById('form-container');

            const data = collectFormData(container);

            expect(data.category).toBe('business'); // Second radio is checked
        });

        it('should collect select values', () => {
            const container = document.getElementById('form-container');
            const select = container.querySelector('[name="status"]');
            select.value = 'published';

            const data = collectFormData(container);

            expect(data.status).toBe('published');
        });

        it('should collect contenteditable content', () => {
            const container = document.getElementById('form-container');
            const editor = container.querySelector('#editor');
            editor.innerHTML = '<p>Rich text content</p>';

            const data = collectFormData(container);

            expect(data.editor).toBe('<p>Rich text content</p>');
        });

        it('should return empty object for empty form', () => {
            document.body.innerHTML = '<div id="empty"></div>';
            const container = document.getElementById('empty');

            const data = collectFormData(container);

            expect(data).toEqual({});
        });
    });

    // ========================================================================
    // Restore Form Data
    // ========================================================================

    describe('restoreFormData', () => {
        it('should restore text input values', () => {
            const container = document.getElementById('form-container');
            const data = { title: 'Restored Title' };

            restoreFormData(container, data);

            expect(container.querySelector('[name="title"]').value).toBe('Restored Title');
        });

        it('should restore textarea values', () => {
            const container = document.getElementById('form-container');
            const data = { content: 'Restored Content' };

            restoreFormData(container, data);

            expect(container.querySelector('[name="content"]').value).toBe('Restored Content');
        });

        it('should restore checkbox state', () => {
            const container = document.getElementById('form-container');
            const data = { published: true };

            restoreFormData(container, data);

            expect(container.querySelector('[name="published"]').checked).toBe(true);
        });

        it('should restore radio button selection', () => {
            const container = document.getElementById('form-container');
            const data = { category: 'tech' };

            restoreFormData(container, data);

            const techRadio = container.querySelector('[name="category"][value="tech"]');
            expect(techRadio.checked).toBe(true);
        });

        it('should restore select values', () => {
            const container = document.getElementById('form-container');
            const data = { status: 'published' };

            restoreFormData(container, data);

            expect(container.querySelector('[name="status"]').value).toBe('published');
        });

        it('should restore contenteditable content', () => {
            const container = document.getElementById('form-container');
            const data = { editor: '<p>Restored rich text</p>' };

            restoreFormData(container, data);

            expect(container.querySelector('#editor').innerHTML).toBe('<p>Restored rich text</p>');
        });

        it('should handle null data gracefully', () => {
            const container = document.getElementById('form-container');

            restoreFormData(container, null);

            // Should not throw error
            expect(container.querySelector('[name="title"]').value).toBe('');
        });

        it('should handle missing fields gracefully', () => {
            const container = document.getElementById('form-container');
            const data = { nonexistent: 'value' };

            restoreFormData(container, data);

            // Should not throw error
            expect(container.querySelector('[name="title"]').value).toBe('');
        });
    });
});

describe('globalDraftManager', () => {
    it('should be a DraftManager instance', () => {
        expect(globalDraftManager).toBeInstanceOf(DraftManager);
    });
});
