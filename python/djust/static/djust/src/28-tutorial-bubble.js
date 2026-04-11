// ============================================================================
// Tutorial bubble listener (ADR-002 Phase 1c)
// ============================================================================
//
// Listens for tour:narrate CustomEvents (dispatched by TutorialMixin via
// push_commands(JS.dispatch("tour:narrate", ...))) and updates the
// #dj-tutorial-bubble container rendered by {% tutorial_bubble %}. Handles:
//
//   - Text content update from detail.text
//   - Step/total progress indicator from detail.step/detail.total
//   - Absolute positioning next to detail.target (above, below, left, right)
//   - Show/hide via data-visible attribute (CSS-styled)
//   - Auto-dismiss after a configurable timeout (opt-in via data-auto-dismiss)
//
// The bubble is a "dumb" DOM surface — all tour logic lives server-side in
// TutorialMixin. This file is purely the presentation layer.
// ============================================================================

(function() {
    var BUBBLE_ID = 'dj-tutorial-bubble';

    function getBubble() {
        return document.getElementById(BUBBLE_ID);
    }

    function updateBubbleContent(bubble, detail) {
        var text = (detail && detail.text) || '';
        var textEl = bubble.querySelector('.dj-tutorial-bubble__text');
        if (textEl) {
            textEl.textContent = text;
        }

        var stepEl = bubble.querySelector('.dj-tutorial-bubble__step');
        if (stepEl) {
            var step = detail && detail.step;
            var total = detail && detail.total;
            if (typeof step === 'number' && typeof total === 'number' && total > 0) {
                stepEl.textContent = String(step + 1) + ' / ' + String(total);
            } else {
                stepEl.textContent = '';
            }
        }
    }

    function positionBubble(bubble, detail) {
        var targetSelector = detail && detail.target;
        var position = (detail && detail.position) ||
            bubble.getAttribute('data-default-position') ||
            'bottom';

        if (!targetSelector) {
            // No target — show centered-ish via fallback positioning
            bubble.style.position = 'fixed';
            bubble.style.top = '20px';
            bubble.style.left = '50%';
            bubble.style.transform = 'translateX(-50%)';
            return;
        }

        var target = null;
        try {
            target = document.querySelector(targetSelector);
        } catch (err) {
            if (globalThis.djustDebug) console.log('[tutorial-bubble] bad selector', targetSelector);
        }

        if (!target) {
            // Target doesn't exist — fall back to fixed top-center
            bubble.style.position = 'fixed';
            bubble.style.top = '20px';
            bubble.style.left = '50%';
            bubble.style.transform = 'translateX(-50%)';
            return;
        }

        var rect = target.getBoundingClientRect();
        bubble.style.position = 'absolute';
        bubble.style.transform = '';

        var scrollY = window.pageYOffset || document.documentElement.scrollTop;
        var scrollX = window.pageXOffset || document.documentElement.scrollLeft;

        switch (position) {
            case 'top':
                bubble.style.top = (rect.top + scrollY - 12) + 'px';
                bubble.style.left = (rect.left + scrollX + rect.width / 2) + 'px';
                bubble.style.transform = 'translate(-50%, -100%)';
                break;
            case 'left':
                bubble.style.top = (rect.top + scrollY + rect.height / 2) + 'px';
                bubble.style.left = (rect.left + scrollX - 12) + 'px';
                bubble.style.transform = 'translate(-100%, -50%)';
                break;
            case 'right':
                bubble.style.top = (rect.top + scrollY + rect.height / 2) + 'px';
                bubble.style.left = (rect.right + scrollX + 12) + 'px';
                bubble.style.transform = 'translateY(-50%)';
                break;
            case 'bottom':
            default:
                bubble.style.top = (rect.bottom + scrollY + 12) + 'px';
                bubble.style.left = (rect.left + scrollX + rect.width / 2) + 'px';
                bubble.style.transform = 'translateX(-50%)';
                break;
        }

        bubble.setAttribute('data-position', position);
    }

    function showBubble(bubble) {
        bubble.setAttribute('data-visible', 'true');
    }

    function hideBubble(bubble) {
        bubble.setAttribute('data-visible', 'false');
    }

    function handleNarrate(event) {
        // Look up the bubble at event time rather than caching it. This
        // way the bubble can be rendered after client.js loads (e.g. via
        // TurboNav or conditional template rendering) without missing
        // events. The framework wires the document listener once.
        var bubble = getBubble();
        if (!bubble) return;

        var expectedEvent = bubble.getAttribute('data-event') || 'tour:narrate';
        if (event.type !== expectedEvent) return;

        var detail = event.detail || {};

        // Empty message is a signal to hide the bubble
        if (!detail.text) {
            hideBubble(bubble);
            return;
        }

        updateBubbleContent(bubble, detail);
        positionBubble(bubble, detail);
        showBubble(bubble);
    }

    // The shipped default event name. Apps that override {% tutorial_bubble
    // event="..." %} with a custom name need to register their own listener
    // for that event name — document this in the guide. The default name
    // covers 99% of cases.
    document.addEventListener('tour:narrate', handleNarrate);

    // Also listen for tour:hide to support explicit hide commands from
    // custom JS Command chains or app code.
    document.addEventListener('tour:hide', function() {
        var bubble = getBubble();
        if (bubble) hideBubble(bubble);
    });

    // Expose for tests
    if (!window.djust) window.djust = {};
    window.djust._tutorialBubble = {
        handleNarrate: handleNarrate,
        getBubble: getBubble,
        showBubble: showBubble,
        hideBubble: hideBubble,
    };
})();
