
// ============================================================================
// dj-transition — CSS Transition Support
// ============================================================================
// Applies enter/leave CSS transition classes when elements are added/removed
// from the DOM via VDOM patches. Inspired by Vue's <Transition>.
//
// Usage:
//   <div dj-transition="fade">...</div>
//   Applies: fade-enter-from → fade-enter-to (on mount)
//            fade-leave-from → fade-leave-to (on remove)
//
//   <div dj-transition-enter="opacity-0" dj-transition-enter-to="opacity-100"
//        dj-transition-leave="opacity-100" dj-transition-leave-to="opacity-0">
//   Explicit class-based transitions.

const djTransitions = {
    /**
     * Apply enter transition to a newly inserted element.
     * @param {HTMLElement} el - The element being inserted
     */
    applyEnter(el) {
        if (!el || el.nodeType !== Node.ELEMENT_NODE) return;

        const transitionName = el.getAttribute('dj-transition');
        const explicitEnter = el.getAttribute('dj-transition-enter');
        const explicitEnterTo = el.getAttribute('dj-transition-enter-to');

        if (!transitionName && !explicitEnter) return;

        // Determine classes
        const enterFromClasses = explicitEnter
            ? explicitEnter.split(/\s+/)
            : [`${transitionName}-enter-from`];
        const enterToClasses = explicitEnterTo
            ? explicitEnterTo.split(/\s+/)
            : [`${transitionName}-enter-to`];

        // Apply enter-from classes immediately
        enterFromClasses.forEach(cls => cls && el.classList.add(cls));

        // Force reflow to ensure the initial state is painted
        void el.offsetHeight;

        // Next frame: swap to enter-to classes
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                enterFromClasses.forEach(cls => cls && el.classList.remove(cls));
                enterToClasses.forEach(cls => cls && el.classList.add(cls));

                // Clean up enter-to classes after transition ends
                const onEnd = () => {
                    enterToClasses.forEach(cls => cls && el.classList.remove(cls));
                    el.removeEventListener('transitionend', onEnd);
                };
                el.addEventListener('transitionend', onEnd, { once: true });

                // Safety timeout: clean up if transitionend never fires (no CSS transition defined)
                setTimeout(() => {
                    enterToClasses.forEach(cls => cls && el.classList.remove(cls));
                }, 1000);
            });
        });
    },

    /**
     * Apply leave transition to an element being removed.
     * Returns a promise that resolves when the transition completes.
     * @param {HTMLElement} el - The element being removed
     * @returns {Promise<void>}
     */
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
        const leaveToClasses = explicitLeaveTo
            ? explicitLeaveTo.split(/\s+/)
            : [`${transitionName}-leave-to`];

        return new Promise(resolve => {
            // Apply leave-from classes
            leaveFromClasses.forEach(cls => cls && el.classList.add(cls));

            // Force reflow
            void el.offsetHeight;

            // Next frame: swap to leave-to
            requestAnimationFrame(() => {
                requestAnimationFrame(() => {
                    leaveFromClasses.forEach(cls => cls && el.classList.remove(cls));
                    leaveToClasses.forEach(cls => cls && el.classList.add(cls));

                    const onEnd = () => {
                        resolve();
                    };
                    el.addEventListener('transitionend', onEnd, { once: true });

                    // Safety timeout
                    setTimeout(resolve, 1000);
                });
            });
        });
    },

    /**
     * Check if an element has transition directives.
     * @param {HTMLElement} el
     * @returns {boolean}
     */
    hasTransition(el) {
        if (!el || el.nodeType !== Node.ELEMENT_NODE) return false;
        return el.hasAttribute('dj-transition') ||
               el.hasAttribute('dj-transition-enter') ||
               el.hasAttribute('dj-transition-leave');
    }
};

// Expose for use in VDOM patch application
window.djust = window.djust || {};
window.djust.transitions = djTransitions;
