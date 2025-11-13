/**
 * Tests for @optimistic decorator
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
    applyOptimisticUpdate,
    saveOptimisticState,
    clearOptimisticState,
    revertOptimisticUpdate,
    optimisticToggle,
    optimisticInputUpdate,
    optimisticSelectUpdate,
    optimisticButtonUpdate,
    optimisticUpdates,
    pendingEvents,
    clearAllState
} from '../../python/djust/static/djust/decorators.js';

describe('Optimistic Update Decorator', () => {
    beforeEach(() => {
        // Clear state before each test
        clearAllState();

        // Reset DOM
        document.body.innerHTML = '';

        // Use fake timers for revert animation
        vi.useFakeTimers();
    });

    afterEach(() => {
        vi.restoreAllMocks();
        vi.useRealTimers();
    });

    describe('Checkbox/Radio Optimistic Updates', () => {
        it('should toggle checkbox immediately', () => {
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.checked = false;
            document.body.appendChild(checkbox);

            applyOptimisticUpdate('toggle_item', { checked: true }, checkbox);

            expect(checkbox.checked).toBe(true);
            expect(checkbox.classList.contains('optimistic-pending')).toBe(true);
        });

        it('should toggle radio button', () => {
            const radio = document.createElement('input');
            radio.type = 'radio';
            radio.checked = false;
            document.body.appendChild(radio);

            applyOptimisticUpdate('select_option', { checked: true }, radio);

            expect(radio.checked).toBe(true);
        });

        it('should toggle checkbox without explicit checked param', () => {
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.checked = false;
            document.body.appendChild(checkbox);

            optimisticToggle(checkbox, {});

            expect(checkbox.checked).toBe(true);

            optimisticToggle(checkbox, {});

            expect(checkbox.checked).toBe(false);
        });

        it('should save and restore checkbox state', () => {
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.checked = false;
            document.body.appendChild(checkbox);

            applyOptimisticUpdate('toggle', { checked: true }, checkbox);
            expect(checkbox.checked).toBe(true);

            revertOptimisticUpdate('toggle');
            expect(checkbox.checked).toBe(false);
        });
    });

    describe('Input/Textarea Optimistic Updates', () => {
        it('should update input value immediately', () => {
            const input = document.createElement('input');
            input.type = 'text';
            input.value = '';
            document.body.appendChild(input);

            applyOptimisticUpdate('on_input', { value: 'test' }, input);

            expect(input.value).toBe('test');
            expect(input.classList.contains('optimistic-pending')).toBe(true);
        });

        it('should update textarea value', () => {
            const textarea = document.createElement('textarea');
            textarea.value = '';
            document.body.appendChild(textarea);

            applyOptimisticUpdate('on_change', { value: 'multiline\ntext' }, textarea);

            expect(textarea.value).toBe('multiline\ntext');
        });

        it('should save and restore input value', () => {
            const input = document.createElement('input');
            input.type = 'text';
            input.value = 'original';
            document.body.appendChild(input);

            applyOptimisticUpdate('on_input', { value: 'changed' }, input);
            expect(input.value).toBe('changed');

            revertOptimisticUpdate('on_input');
            expect(input.value).toBe('original');
        });

        it('should handle empty input values', () => {
            const input = document.createElement('input');
            input.value = 'test';
            document.body.appendChild(input);

            optimisticInputUpdate(input, { value: '' });

            expect(input.value).toBe('');
        });
    });

    describe('Select Optimistic Updates', () => {
        it('should update select value immediately', () => {
            const select = document.createElement('select');
            select.innerHTML = `
                <option value="1">Option 1</option>
                <option value="2">Option 2</option>
                <option value="3">Option 3</option>
            `;
            document.body.appendChild(select);

            applyOptimisticUpdate('on_change', { value: '2' }, select);

            expect(select.value).toBe('2');
            expect(select.classList.contains('optimistic-pending')).toBe(true);
        });

        it('should save and restore select value', () => {
            const select = document.createElement('select');
            select.innerHTML = `
                <option value="1">Option 1</option>
                <option value="2">Option 2</option>
            `;
            select.value = '1';
            document.body.appendChild(select);

            applyOptimisticUpdate('on_change', { value: '2' }, select);
            expect(select.value).toBe('2');

            revertOptimisticUpdate('on_change');
            expect(select.value).toBe('1');
        });
    });

    describe('Button Optimistic Updates', () => {
        it('should disable button immediately', () => {
            const button = document.createElement('button');
            button.textContent = 'Click Me';
            button.disabled = false;
            document.body.appendChild(button);

            applyOptimisticUpdate('increment', {}, button);

            expect(button.disabled).toBe(true);
            expect(button.classList.contains('optimistic-pending')).toBe(true);
        });

        it('should update button text with data-loading-text', () => {
            const button = document.createElement('button');
            button.textContent = 'Submit';
            button.setAttribute('data-loading-text', 'Saving...');
            document.body.appendChild(button);

            applyOptimisticUpdate('submit', {}, button);

            expect(button.disabled).toBe(true);
            expect(button.textContent).toBe('Saving...');
        });

        it('should restore button state after clear', () => {
            const button = document.createElement('button');
            button.textContent = 'Submit';
            button.disabled = false;
            button.setAttribute('data-loading-text', 'Saving...');
            document.body.appendChild(button);

            applyOptimisticUpdate('submit', {}, button);
            expect(button.disabled).toBe(true);
            expect(button.textContent).toBe('Saving...');

            clearOptimisticState('submit');
            expect(button.disabled).toBe(false);
            expect(button.textContent).toBe('Submit');
        });

        it('should restore button on revert', () => {
            const button = document.createElement('button');
            button.textContent = 'Delete';
            button.disabled = false;
            button.setAttribute('data-loading-text', 'Deleting...');
            document.body.appendChild(button);

            applyOptimisticUpdate('delete', {}, button);
            expect(button.disabled).toBe(true);

            revertOptimisticUpdate('delete');
            expect(button.disabled).toBe(false);
            expect(button.textContent).toBe('Delete');
        });
    });

    describe('State Management', () => {
        it('should track optimistic updates', () => {
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            document.body.appendChild(checkbox);

            expect(optimisticUpdates.has('toggle')).toBe(false);

            applyOptimisticUpdate('toggle', { checked: true }, checkbox);

            expect(optimisticUpdates.has('toggle')).toBe(true);
            const state = optimisticUpdates.get('toggle');
            expect(state).toHaveProperty('element');
            expect(state).toHaveProperty('originalState');
        });

        it('should track pending events', () => {
            const button = document.createElement('button');
            document.body.appendChild(button);

            expect(pendingEvents.has('submit')).toBe(false);

            applyOptimisticUpdate('submit', {}, button);

            expect(pendingEvents.has('submit')).toBe(true);
        });

        it('should clear state after clearOptimisticState', () => {
            const button = document.createElement('button');
            document.body.appendChild(button);

            applyOptimisticUpdate('submit', {}, button);
            expect(optimisticUpdates.has('submit')).toBe(true);
            expect(pendingEvents.has('submit')).toBe(true);

            clearOptimisticState('submit');
            expect(optimisticUpdates.has('submit')).toBe(false);
            expect(pendingEvents.has('submit')).toBe(false);
        });

        it('should handle multiple independent optimistic updates', () => {
            const checkbox1 = document.createElement('input');
            checkbox1.type = 'checkbox';
            document.body.appendChild(checkbox1);

            const checkbox2 = document.createElement('input');
            checkbox2.type = 'checkbox';
            document.body.appendChild(checkbox2);

            applyOptimisticUpdate('toggle1', { checked: true }, checkbox1);
            applyOptimisticUpdate('toggle2', { checked: true }, checkbox2);

            expect(optimisticUpdates.size).toBe(2);
            expect(pendingEvents.size).toBe(2);
        });
    });

    describe('saveOptimisticState', () => {
        it('should save checkbox state', () => {
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.checked = true;
            document.body.appendChild(checkbox);

            saveOptimisticState('toggle', checkbox);

            const state = optimisticUpdates.get('toggle');
            expect(state.originalState.checked).toBe(true);
        });

        it('should save input value', () => {
            const input = document.createElement('input');
            input.value = 'test value';
            document.body.appendChild(input);

            saveOptimisticState('on_input', input);

            const state = optimisticUpdates.get('on_input');
            expect(state.originalState.value).toBe('test value');
        });

        it('should save button state', () => {
            const button = document.createElement('button');
            button.textContent = 'Click Me';
            button.disabled = false;
            document.body.appendChild(button);

            saveOptimisticState('click', button);

            const state = optimisticUpdates.get('click');
            expect(state.originalState.disabled).toBe(false);
            expect(state.originalState.text).toBe('Click Me');
        });

        it('should save select value', () => {
            const select = document.createElement('select');
            select.innerHTML = '<option value="1">One</option>';
            select.value = '1';
            document.body.appendChild(select);

            saveOptimisticState('change', select);

            const state = optimisticUpdates.get('change');
            expect(state.originalState.value).toBe('1');
        });
    });

    describe('clearOptimisticState', () => {
        it('should remove optimistic-pending class', () => {
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            document.body.appendChild(checkbox);

            applyOptimisticUpdate('toggle', { checked: true }, checkbox);
            expect(checkbox.classList.contains('optimistic-pending')).toBe(true);

            clearOptimisticState('toggle');
            expect(checkbox.classList.contains('optimistic-pending')).toBe(false);
        });

        it('should restore button disabled state', () => {
            const button = document.createElement('button');
            button.disabled = false;
            document.body.appendChild(button);

            applyOptimisticUpdate('submit', {}, button);
            expect(button.disabled).toBe(true);

            clearOptimisticState('submit');
            expect(button.disabled).toBe(false);
        });

        it('should handle clearing non-existent event', () => {
            expect(() => {
                clearOptimisticState('non_existent');
            }).not.toThrow();
        });
    });

    describe('revertOptimisticUpdate', () => {
        it('should restore all original values', () => {
            const input = document.createElement('input');
            input.type = 'text';
            input.value = 'original';
            document.body.appendChild(input);

            applyOptimisticUpdate('on_input', { value: 'changed' }, input);
            expect(input.value).toBe('changed');

            revertOptimisticUpdate('on_input');
            expect(input.value).toBe('original');
        });

        it('should add optimistic-error class', () => {
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.checked = false;
            document.body.appendChild(checkbox);

            applyOptimisticUpdate('toggle', { checked: true }, checkbox);
            revertOptimisticUpdate('toggle');

            expect(checkbox.classList.contains('optimistic-error')).toBe(true);
        });

        it('should remove optimistic-error class after 2 seconds', () => {
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            document.body.appendChild(checkbox);

            applyOptimisticUpdate('toggle', { checked: true }, checkbox);
            revertOptimisticUpdate('toggle');

            expect(checkbox.classList.contains('optimistic-error')).toBe(true);

            vi.advanceTimersByTime(2000);

            expect(checkbox.classList.contains('optimistic-error')).toBe(false);
        });

        it('should handle reverting non-existent update', () => {
            expect(() => {
                revertOptimisticUpdate('non_existent');
            }).not.toThrow();
        });

        it('should restore button text and disabled state', () => {
            const button = document.createElement('button');
            button.textContent = 'Submit';
            button.disabled = false;
            button.setAttribute('data-loading-text', 'Saving...');
            document.body.appendChild(button);

            applyOptimisticUpdate('submit', {}, button);
            expect(button.textContent).toBe('Saving...');
            expect(button.disabled).toBe(true);

            revertOptimisticUpdate('submit');
            expect(button.textContent).toBe('Submit');
            expect(button.disabled).toBe(false);
        });
    });

    describe('Helper Functions', () => {
        describe('optimisticToggle', () => {
            it('should use explicit checked param if provided', () => {
                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.checked = false;

                optimisticToggle(checkbox, { checked: true });
                expect(checkbox.checked).toBe(true);

                optimisticToggle(checkbox, { checked: false });
                expect(checkbox.checked).toBe(false);
            });

            it('should toggle if no checked param provided', () => {
                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.checked = false;

                optimisticToggle(checkbox, {});
                expect(checkbox.checked).toBe(true);

                optimisticToggle(checkbox, {});
                expect(checkbox.checked).toBe(false);
            });
        });

        describe('optimisticInputUpdate', () => {
            it('should update value if provided', () => {
                const input = document.createElement('input');
                input.value = '';

                optimisticInputUpdate(input, { value: 'new value' });
                expect(input.value).toBe('new value');
            });

            it('should not update if value not provided', () => {
                const input = document.createElement('input');
                input.value = 'original';

                optimisticInputUpdate(input, {});
                expect(input.value).toBe('original');
            });
        });

        describe('optimisticSelectUpdate', () => {
            it('should update value if provided', () => {
                const select = document.createElement('select');
                select.innerHTML = `
                    <option value="1">One</option>
                    <option value="2">Two</option>
                `;
                select.value = '1';

                optimisticSelectUpdate(select, { value: '2' });
                expect(select.value).toBe('2');
            });
        });

        describe('optimisticButtonUpdate', () => {
            it('should disable button', () => {
                const button = document.createElement('button');
                button.disabled = false;

                optimisticButtonUpdate(button, {});
                expect(button.disabled).toBe(true);
            });

            it('should update text if data-loading-text present', () => {
                const button = document.createElement('button');
                button.textContent = 'Click';
                button.setAttribute('data-loading-text', 'Loading...');

                optimisticButtonUpdate(button, {});
                expect(button.textContent).toBe('Loading...');
            });

            it('should not update text if data-loading-text absent', () => {
                const button = document.createElement('button');
                button.textContent = 'Click';

                optimisticButtonUpdate(button, {});
                expect(button.textContent).toBe('Click');
            });
        });
    });

    describe('Edge Cases', () => {
        it('should handle null target element', () => {
            expect(() => {
                applyOptimisticUpdate('event', {}, null);
            }).not.toThrow();
        });

        it('should handle undefined target element', () => {
            expect(() => {
                applyOptimisticUpdate('event', {}, undefined);
            }).not.toThrow();
        });

        it('should handle element without matching type', () => {
            const div = document.createElement('div');
            document.body.appendChild(div);

            // Should not throw, just won't apply any specific updates
            expect(() => {
                applyOptimisticUpdate('event', {}, div);
            }).not.toThrow();

            // Should still add pending class
            expect(div.classList.contains('optimistic-pending')).toBe(true);
        });

        it('should handle multiple updates to same event', () => {
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.checked = false;
            document.body.appendChild(checkbox);

            applyOptimisticUpdate('toggle', { checked: true }, checkbox);
            applyOptimisticUpdate('toggle', { checked: false }, checkbox);

            // Should track latest update
            expect(checkbox.checked).toBe(false);
            expect(optimisticUpdates.size).toBe(1);
        });
    });

    describe('Real-World Scenarios', () => {
        it('should handle todo checkbox toggle', () => {
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.checked = false;
            checkbox.setAttribute('data-id', '123');
            document.body.appendChild(checkbox);

            applyOptimisticUpdate('toggle_todo', { id: 123, checked: true }, checkbox);

            expect(checkbox.checked).toBe(true);
            expect(checkbox.classList.contains('optimistic-pending')).toBe(true);

            // Server confirms
            clearOptimisticState('toggle_todo');
            expect(checkbox.checked).toBe(true);
            expect(checkbox.classList.contains('optimistic-pending')).toBe(false);
        });

        it('should handle todo checkbox toggle with server error', () => {
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.checked = false;
            document.body.appendChild(checkbox);

            applyOptimisticUpdate('toggle_todo', { checked: true }, checkbox);
            expect(checkbox.checked).toBe(true);

            // Server rejects
            revertOptimisticUpdate('toggle_todo');
            expect(checkbox.checked).toBe(false);
            expect(checkbox.classList.contains('optimistic-error')).toBe(true);
        });

        it('should handle form submission', () => {
            const button = document.createElement('button');
            button.type = 'submit';
            button.textContent = 'Save';
            button.disabled = false;
            button.setAttribute('data-loading-text', 'Saving...');
            document.body.appendChild(button);

            applyOptimisticUpdate('submit_form', {}, button);

            expect(button.disabled).toBe(true);
            expect(button.textContent).toBe('Saving...');
            expect(button.classList.contains('optimistic-pending')).toBe(true);

            // Server confirms
            clearOptimisticState('submit_form');
            expect(button.disabled).toBe(false);
            expect(button.textContent).toBe('Save');
        });

        it('should handle multiple concurrent optimistic updates', () => {
            // Todo list with multiple items
            const checkboxes = [];
            for (let i = 0; i < 5; i++) {
                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.checked = false;
                document.body.appendChild(checkbox);
                checkboxes.push(checkbox);
            }

            // User rapidly toggles all items
            checkboxes.forEach((cb, i) => {
                applyOptimisticUpdate(`toggle_${i}`, { checked: true }, cb);
            });

            // All should be checked and pending
            checkboxes.forEach(cb => {
                expect(cb.checked).toBe(true);
                expect(cb.classList.contains('optimistic-pending')).toBe(true);
            });

            expect(optimisticUpdates.size).toBe(5);
            expect(pendingEvents.size).toBe(5);

            // Server confirms all
            checkboxes.forEach((cb, i) => {
                clearOptimisticState(`toggle_${i}`);
            });

            expect(optimisticUpdates.size).toBe(0);
            expect(pendingEvents.size).toBe(0);
        });
    });

    describe('Integration with clearAllState', () => {
        it('should clear all optimistic state', () => {
            const checkbox1 = document.createElement('input');
            checkbox1.type = 'checkbox';
            document.body.appendChild(checkbox1);

            const checkbox2 = document.createElement('input');
            checkbox2.type = 'checkbox';
            document.body.appendChild(checkbox2);

            applyOptimisticUpdate('toggle1', { checked: true }, checkbox1);
            applyOptimisticUpdate('toggle2', { checked: true }, checkbox2);

            expect(optimisticUpdates.size).toBe(2);
            expect(pendingEvents.size).toBe(2);

            clearAllState();

            expect(optimisticUpdates.size).toBe(0);
            expect(pendingEvents.size).toBe(0);
        });

        it('should not revert updates when clearAllState called', () => {
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.checked = false;
            document.body.appendChild(checkbox);

            applyOptimisticUpdate('toggle', { checked: true }, checkbox);
            expect(checkbox.checked).toBe(true);

            clearAllState();

            // State cleared but DOM not reverted
            expect(checkbox.checked).toBe(true);
        });
    });
});
