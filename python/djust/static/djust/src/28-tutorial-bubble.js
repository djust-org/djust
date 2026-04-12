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
//   - Smart positioning next to detail.target (above, below, left, right)
//   - Auto-scroll to bring the target into view
//   - Arrow/pointer connecting bubble to target
//   - Backdrop overlay dimming everything except the target
//   - Show/hide via data-visible attribute (CSS-styled)
//
// The bubble is a "dumb" DOM surface — all tour logic lives server-side in
// TutorialMixin. This file is purely the presentation layer.
// ============================================================================

(function() {
    var BUBBLE_ID = 'dj-tutorial-bubble';
    var BACKDROP_ID = 'dj-tutorial-backdrop';
    var ARROW_CLASS = 'dj-tutorial-bubble__arrow';
    var GAP = 14; // px between target and bubble

    function getBubble() {
        return document.getElementById(BUBBLE_ID);
    }

    // --- Backdrop overlay (dims everything except the target) ---

    function ensureBackdrop() {
        var existing = document.getElementById(BACKDROP_ID);
        if (existing) return existing;
        var el = document.createElement('div');
        el.id = BACKDROP_ID;
        el.style.cssText = 'position:fixed;inset:0;z-index:9998;pointer-events:none;' +
            'background:rgba(0,0,0,0.4);opacity:0;transition:opacity 0.3s ease;';
        document.body.appendChild(el);
        return el;
    }

    function showBackdrop(targetEl) {
        var backdrop = ensureBackdrop();
        backdrop.style.opacity = '1';

        // Cut out the target element with a box-shadow trick:
        // The backdrop is transparent, and we use a massive box-shadow
        // on a highlight overlay to dim everything else.
        if (targetEl) {
            var rect = targetEl.getBoundingClientRect();
            var pad = 6;
            backdrop.style.clipPath = 'polygon(' +
                '0% 0%, 0% 100%, ' +
                (rect.left - pad) + 'px 100%, ' +
                (rect.left - pad) + 'px ' + (rect.top - pad) + 'px, ' +
                (rect.right + pad) + 'px ' + (rect.top - pad) + 'px, ' +
                (rect.right + pad) + 'px ' + (rect.bottom + pad) + 'px, ' +
                (rect.left - pad) + 'px ' + (rect.bottom + pad) + 'px, ' +
                (rect.left - pad) + 'px 100%, ' +
                '100% 100%, 100% 0%)';
        }
    }

    function hideBackdrop() {
        var backdrop = document.getElementById(BACKDROP_ID);
        if (backdrop) {
            backdrop.style.opacity = '0';
            backdrop.style.clipPath = '';
        }
    }

    // --- Arrow element ---

    function ensureArrow(bubble) {
        var existing = bubble.querySelector('.' + ARROW_CLASS);
        if (existing) return existing;
        var arrow = document.createElement('div');
        arrow.className = ARROW_CLASS;
        arrow.style.cssText = 'position:absolute;width:12px;height:12px;' +
            'background:inherit;transform:rotate(45deg);z-index:-1;';
        bubble.appendChild(arrow);
        return arrow;
    }

    function positionArrow(arrow, position) {
        // Reset
        arrow.style.top = '';
        arrow.style.bottom = '';
        arrow.style.left = '';
        arrow.style.right = '';

        switch (position) {
            case 'top':
                arrow.style.bottom = '-6px';
                arrow.style.left = '50%';
                arrow.style.marginLeft = '-6px';
                break;
            case 'bottom':
                arrow.style.top = '-6px';
                arrow.style.left = '50%';
                arrow.style.marginLeft = '-6px';
                break;
            case 'left':
                arrow.style.right = '-6px';
                arrow.style.top = '50%';
                arrow.style.marginTop = '-6px';
                break;
            case 'right':
                arrow.style.left = '-6px';
                arrow.style.top = '50%';
                arrow.style.marginTop = '-6px';
                break;
        }
    }

    // --- Content update ---

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
                stepEl.textContent = 'Step ' + String(step + 1) + ' of ' + String(total);
            } else {
                stepEl.textContent = '';
            }
        }
    }

    // --- Smart positioning ---

    function positionBubble(bubble, detail) {
        var targetSelector = detail && detail.target;
        var preferredPosition = (detail && detail.position) ||
            bubble.getAttribute('data-default-position') ||
            'bottom';

        if (!targetSelector) {
            // No target — show centered at top
            bubble.style.position = 'fixed';
            bubble.style.top = '80px';
            bubble.style.left = '50%';
            bubble.style.transform = 'translateX(-50%)';
            bubble.style.bottom = '';
            bubble.style.right = '';
            hideBackdrop();
            return;
        }

        var target = null;
        try {
            target = document.querySelector(targetSelector);
        } catch (err) {
            if (globalThis.djustDebug) console.log('[tutorial-bubble] bad selector', targetSelector);
        }

        if (!target) {
            bubble.style.position = 'fixed';
            bubble.style.top = '80px';
            bubble.style.left = '50%';
            bubble.style.transform = 'translateX(-50%)';
            bubble.style.bottom = '';
            bubble.style.right = '';
            hideBackdrop();
            return;
        }

        // Auto-scroll target into view
        target.scrollIntoView({ behavior: 'smooth', block: 'center' });

        // Wait a tick for scroll to settle before positioning
        requestAnimationFrame(function() {
            var rect = target.getBoundingClientRect();
            var bubbleRect = bubble.getBoundingClientRect();
            var viewW = window.innerWidth;
            var viewH = window.innerHeight;
            var position = preferredPosition;

            // Auto-flip if bubble would go off-screen
            if (position === 'bottom' && rect.bottom + GAP + bubbleRect.height > viewH) {
                position = 'top';
            } else if (position === 'top' && rect.top - GAP - bubbleRect.height < 0) {
                position = 'bottom';
            }

            // Use fixed positioning relative to viewport
            bubble.style.position = 'fixed';
            bubble.style.transform = '';
            bubble.style.bottom = '';
            bubble.style.right = '';

            // Center bubble horizontally on the target, clamp to viewport
            var bubbleLeft = rect.left + rect.width / 2 - bubbleRect.width / 2;
            bubbleLeft = Math.max(12, Math.min(bubbleLeft, viewW - bubbleRect.width - 12));

            switch (position) {
                case 'top':
                    bubble.style.top = (rect.top - GAP - bubbleRect.height) + 'px';
                    bubble.style.left = bubbleLeft + 'px';
                    break;
                case 'left':
                    bubble.style.top = (rect.top + rect.height / 2 - bubbleRect.height / 2) + 'px';
                    bubble.style.left = (rect.left - GAP - bubbleRect.width) + 'px';
                    break;
                case 'right':
                    bubble.style.top = (rect.top + rect.height / 2 - bubbleRect.height / 2) + 'px';
                    bubble.style.left = (rect.right + GAP) + 'px';
                    break;
                case 'bottom':
                default:
                    bubble.style.top = (rect.bottom + GAP) + 'px';
                    bubble.style.left = bubbleLeft + 'px';
                    break;
            }

            bubble.setAttribute('data-position', position);

            // Position arrow
            var arrow = ensureArrow(bubble);
            positionArrow(arrow, position);

            // Show backdrop with cutout around target
            showBackdrop(target);
        });
    }

    // --- Show/hide ---

    function showBubble(bubble) {
        bubble.setAttribute('data-visible', 'true');
    }

    function hideBubble(bubble) {
        bubble.setAttribute('data-visible', 'false');
        hideBackdrop();
    }

    // --- Event handler ---

    function handleNarrate(event) {
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
        showBubble(bubble);
        positionBubble(bubble, detail);
    }

    // --- Listeners ---

    document.addEventListener('tour:narrate', handleNarrate);

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
