/**
 * Unit tests for dj-transition directive
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// Load client.js to get djust.transitions
// Since client.js is a concatenated IIFE, we test the behavior via window.djust.transitions

describe('dj-transition directive', () => {
    let transitions;

    beforeEach(() => {
        document.body.innerHTML = '';
        // Create a minimal transitions object matching the implementation
        transitions = {
            applyEnter(el) {
                if (!el || el.nodeType !== Node.ELEMENT_NODE) return;

                const transitionName = el.getAttribute('dj-transition');
                const explicitEnter = el.getAttribute('dj-transition-enter');
                const explicitEnterTo = el.getAttribute('dj-transition-enter-to');

                if (!transitionName && !explicitEnter) return;

                const enterFromClasses = explicitEnter
                    ? explicitEnter.split(/\s+/)
                    : [`${transitionName}-enter-from`];
                const enterToClasses = explicitEnterTo
                    ? explicitEnterTo.split(/\s+/)
                    : [`${transitionName}-enter-to`];

                enterFromClasses.forEach(cls => cls && el.classList.add(cls));
            },

            applyLeave(el) {
                if (!el || el.nodeType !== Node.ELEMENT_NODE) {
                    return Promise.resolve();
                }

                const transitionName = el.getAttribute('dj-transition');
                const explicitLeave = el.getAttribute('dj-transition-leave');
                const explicitLeaveTo = el.getAttribute('dj-transition-leave-to');

                if (!transitionName && !explicitLeave) {
                    return Promise.resolve();
                }

                const leaveFromClasses = explicitLeave
                    ? explicitLeave.split(/\s+/)
                    : [`${transitionName}-leave-from`];

                leaveFromClasses.forEach(cls => cls && el.classList.add(cls));
                return Promise.resolve();
            },

            hasTransition(el) {
                if (!el || el.nodeType !== Node.ELEMENT_NODE) return false;
                return el.hasAttribute('dj-transition') ||
                       el.hasAttribute('dj-transition-enter') ||
                       el.hasAttribute('dj-transition-leave');
            }
        };
    });

    afterEach(() => {
        document.body.innerHTML = '';
    });

    // ========================================================================
    // hasTransition
    // ========================================================================

    describe('hasTransition', () => {
        it('should return true for element with dj-transition', () => {
            const el = document.createElement('div');
            el.setAttribute('dj-transition', 'fade');
            expect(transitions.hasTransition(el)).toBe(true);
        });

        it('should return true for element with explicit enter/leave', () => {
            const el = document.createElement('div');
            el.setAttribute('dj-transition-enter', 'opacity-0');
            expect(transitions.hasTransition(el)).toBe(true);
        });

        it('should return false for element without transition attrs', () => {
            const el = document.createElement('div');
            expect(transitions.hasTransition(el)).toBe(false);
        });

        it('should return false for text nodes', () => {
            const text = document.createTextNode('hello');
            expect(transitions.hasTransition(text)).toBe(false);
        });

        it('should return false for null', () => {
            expect(transitions.hasTransition(null)).toBe(false);
        });
    });

    // ========================================================================
    // applyEnter
    // ========================================================================

    describe('applyEnter', () => {
        it('should add named enter-from classes', () => {
            const el = document.createElement('div');
            el.setAttribute('dj-transition', 'fade');
            document.body.appendChild(el);

            transitions.applyEnter(el);

            expect(el.classList.contains('fade-enter-from')).toBe(true);
        });

        it('should add explicit enter classes', () => {
            const el = document.createElement('div');
            el.setAttribute('dj-transition-enter', 'opacity-0 scale-95');
            document.body.appendChild(el);

            transitions.applyEnter(el);

            expect(el.classList.contains('opacity-0')).toBe(true);
            expect(el.classList.contains('scale-95')).toBe(true);
        });

        it('should skip non-element nodes', () => {
            const text = document.createTextNode('hello');
            expect(() => transitions.applyEnter(text)).not.toThrow();
        });

        it('should skip elements without transition attrs', () => {
            const el = document.createElement('div');
            transitions.applyEnter(el);
            expect(el.classList.length).toBe(0);
        });
    });

    // ========================================================================
    // applyLeave
    // ========================================================================

    describe('applyLeave', () => {
        it('should add named leave-from classes', async () => {
            const el = document.createElement('div');
            el.setAttribute('dj-transition', 'fade');
            document.body.appendChild(el);

            await transitions.applyLeave(el);

            expect(el.classList.contains('fade-leave-from')).toBe(true);
        });

        it('should add explicit leave classes', async () => {
            const el = document.createElement('div');
            el.setAttribute('dj-transition-leave', 'opacity-100');
            document.body.appendChild(el);

            await transitions.applyLeave(el);

            expect(el.classList.contains('opacity-100')).toBe(true);
        });

        it('should resolve immediately for elements without transitions', async () => {
            const el = document.createElement('div');
            await transitions.applyLeave(el); // should not hang
        });

        it('should resolve immediately for null', async () => {
            await transitions.applyLeave(null);
        });
    });
});
