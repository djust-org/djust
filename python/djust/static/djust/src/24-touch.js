// ============================================================================
// Touch Event Handling for Mobile Devices
// ============================================================================
// Provides touch-optimized event directives:
// - dj-tap: Fast tap without 300ms delay
// - dj-longpress: Fire after holding (configurable duration)
// - dj-swipe, dj-swipe-left/right/up/down: Swipe gesture detection
// - dj-pinch: Pinch zoom detection
// - dj-pull-refresh: Pull-to-refresh pattern

(function() {
    'use strict';

    // ========================================================================
    // Configuration
    // ========================================================================
    
    const DEFAULTS = {
        swipeThreshold: 50,       // Minimum pixels for swipe detection
        swipeVelocity: 0.3,       // Minimum velocity (px/ms) for swipe
        longpressDuration: 500,   // Ms before longpress fires
        tapMaxDistance: 10,       // Max movement during tap
        tapMaxDuration: 300,      // Max duration for tap
        pullRefreshThreshold: 80, // Pixels to pull before refresh triggers
        pullRefreshResistance: 2.5, // Pull resistance factor
    };

    // ========================================================================
    // Touch State Tracking
    // ========================================================================
    
    const touchState = {
        startX: 0,
        startY: 0,
        startTime: 0,
        currentX: 0,
        currentY: 0,
        isTracking: false,
        longpressTimer: null,
        longpressFired: false,
        // Pinch state
        initialDistance: 0,
        isPinching: false,
        // Pull-to-refresh state
        pullRefreshElement: null,
        pullDistance: 0,
        isPulling: false,
    };

    // ========================================================================
    // Utility Functions
    // ========================================================================
    
    function getDistance(x1, y1, x2, y2) {
        return Math.sqrt(Math.pow(x2 - x1, 2) + Math.pow(y2 - y1, 2));
    }

    function getPinchDistance(touches) {
        if (touches.length < 2) return 0;
        return getDistance(
            touches[0].clientX, touches[0].clientY,
            touches[1].clientX, touches[1].clientY
        );
    }

    function getConfig(element, name, defaultValue) {
        const attr = element.getAttribute(`dj-${name}`);
        if (attr !== null) {
            const num = parseInt(attr, 10);
            return isNaN(num) ? defaultValue : num;
        }
        return defaultValue;
    }

    function findEventTarget(element) {
        // Walk up to find the element with the dj-* attribute
        let current = element;
        while (current && current !== document.body) {
            if (current.hasAttribute && (
                current.hasAttribute('dj-tap') ||
                current.hasAttribute('dj-longpress') ||
                current.hasAttribute('dj-swipe') ||
                current.hasAttribute('dj-swipe-left') ||
                current.hasAttribute('dj-swipe-right') ||
                current.hasAttribute('dj-swipe-up') ||
                current.hasAttribute('dj-swipe-down') ||
                current.hasAttribute('dj-pinch') ||
                current.hasAttribute('dj-pull-refresh')
            )) {
                return current;
            }
            current = current.parentElement;
        }
        return null;
    }

    // ========================================================================
    // Event Dispatching
    // ========================================================================
    
    async function dispatchTouchEvent(element, handlerName, extraParams = {}) {
        if (!handlerName) return;

        const parsed = parseEventHandler(handlerName);
        const params = extractTypedParams(element);

        // Add positional arguments if present
        if (parsed.args && parsed.args.length > 0) {
            params._args = parsed.args;
        }

        // Add extra params (gesture data)
        Object.assign(params, extraParams);

        // Component and embedded view support
        const componentId = getComponentId(element);
        if (componentId) {
            params.component_id = componentId;
        }
        const embeddedViewId = getEmbeddedViewId(element);
        if (embeddedViewId) {
            params.view_id = embeddedViewId;
        }

        // Pass target element
        params._targetElement = element;

        // Handle dj-target for scoped updates
        const targetSelector = element.getAttribute('dj-target');
        if (targetSelector) {
            params._djTargetSelector = targetSelector;
        }

        if (globalThis.djustDebug) {
            console.log(`[Touch] Dispatching ${parsed.name} with params:`, params);
        }

        await handleEvent(parsed.name, params);
    }

    // ========================================================================
    // Tap Handler (No 300ms delay)
    // ========================================================================
    
    function handleTapStart(e) {
        const element = findEventTarget(e.target);
        if (!element || !element.hasAttribute('dj-tap')) return;

        const touch = e.touches[0];
        touchState.startX = touch.clientX;
        touchState.startY = touch.clientY;
        touchState.startTime = Date.now();
        touchState.isTracking = true;
        touchState.longpressFired = false;

        // Add visual feedback
        element.classList.add('djust-tap-active');
    }

    function handleTapEnd(e) {
        if (!touchState.isTracking) return;

        const element = findEventTarget(e.target);
        if (!element || !element.hasAttribute('dj-tap')) {
            touchState.isTracking = false;
            return;
        }

        // Remove visual feedback
        element.classList.remove('djust-tap-active');

        const touch = e.changedTouches[0];
        const distance = getDistance(
            touchState.startX, touchState.startY,
            touch.clientX, touch.clientY
        );
        const duration = Date.now() - touchState.startTime;

        // Check if it qualifies as a tap
        const maxDistance = getConfig(element, 'tap-distance', DEFAULTS.tapMaxDistance);
        const maxDuration = getConfig(element, 'tap-duration', DEFAULTS.tapMaxDuration);

        if (distance < maxDistance && duration < maxDuration && !touchState.longpressFired) {
            e.preventDefault();
            const handler = element.getAttribute('dj-tap');
            dispatchTouchEvent(element, handler, {
                touchX: touch.clientX,
                touchY: touch.clientY,
            });
        }

        touchState.isTracking = false;
    }

    // ========================================================================
    // Longpress Handler
    // ========================================================================
    
    function handleLongpressStart(e) {
        const element = findEventTarget(e.target);
        if (!element || !element.hasAttribute('dj-longpress')) return;

        const touch = e.touches[0];
        touchState.startX = touch.clientX;
        touchState.startY = touch.clientY;
        touchState.longpressFired = false;

        const duration = getConfig(element, 'longpress-duration', DEFAULTS.longpressDuration);

        // Clear any existing timer
        if (touchState.longpressTimer) {
            clearTimeout(touchState.longpressTimer);
        }

        touchState.longpressTimer = setTimeout(() => {
            // Check if finger hasn't moved too much
            const distance = getDistance(
                touchState.startX, touchState.startY,
                touchState.currentX, touchState.currentY
            );

            if (distance < DEFAULTS.tapMaxDistance) {
                touchState.longpressFired = true;
                element.classList.add('djust-longpress-active');
                
                // Haptic feedback if available
                if (navigator.vibrate) {
                    navigator.vibrate(50);
                }

                const handler = element.getAttribute('dj-longpress');
                dispatchTouchEvent(element, handler, {
                    touchX: touchState.currentX,
                    touchY: touchState.currentY,
                    duration: duration,
                });

                // Remove active class after a short delay
                setTimeout(() => {
                    element.classList.remove('djust-longpress-active');
                }, 200);
            }
        }, duration);
    }

    function handleLongpressMove(e) {
        if (touchState.longpressTimer) {
            const touch = e.touches[0];
            touchState.currentX = touch.clientX;
            touchState.currentY = touch.clientY;

            // Cancel if moved too far
            const distance = getDistance(
                touchState.startX, touchState.startY,
                touch.clientX, touch.clientY
            );

            if (distance > DEFAULTS.tapMaxDistance) {
                clearTimeout(touchState.longpressTimer);
                touchState.longpressTimer = null;
            }
        }
    }

    function handleLongpressEnd() {
        if (touchState.longpressTimer) {
            clearTimeout(touchState.longpressTimer);
            touchState.longpressTimer = null;
        }
    }

    // ========================================================================
    // Swipe Handler
    // ========================================================================
    
    function handleSwipeStart(e) {
        const element = findEventTarget(e.target);
        if (!element) return;
        
        const hasSwipe = element.hasAttribute('dj-swipe') ||
            element.hasAttribute('dj-swipe-left') ||
            element.hasAttribute('dj-swipe-right') ||
            element.hasAttribute('dj-swipe-up') ||
            element.hasAttribute('dj-swipe-down');
        
        if (!hasSwipe) return;

        const touch = e.touches[0];
        touchState.startX = touch.clientX;
        touchState.startY = touch.clientY;
        touchState.startTime = Date.now();
        touchState.isTracking = true;
    }

    function handleSwipeEnd(e) {
        if (!touchState.isTracking) return;

        const element = findEventTarget(e.target);
        if (!element) {
            touchState.isTracking = false;
            return;
        }

        const touch = e.changedTouches[0];
        const deltaX = touch.clientX - touchState.startX;
        const deltaY = touch.clientY - touchState.startY;
        const deltaTime = Date.now() - touchState.startTime;
        
        const threshold = getConfig(element, 'swipe-threshold', DEFAULTS.swipeThreshold);
        const absX = Math.abs(deltaX);
        const absY = Math.abs(deltaY);
        const velocity = Math.max(absX, absY) / deltaTime;

        // Determine swipe direction
        let direction = null;
        if (absX > absY && absX > threshold && velocity > DEFAULTS.swipeVelocity) {
            direction = deltaX > 0 ? 'right' : 'left';
        } else if (absY > absX && absY > threshold && velocity > DEFAULTS.swipeVelocity) {
            direction = deltaY > 0 ? 'down' : 'up';
        }

        if (direction) {
            e.preventDefault();
            
            const gestureData = {
                direction: direction,
                deltaX: deltaX,
                deltaY: deltaY,
                velocity: velocity,
            };

            // Check for specific direction handlers
            const specificHandler = element.getAttribute(`dj-swipe-${direction}`);
            if (specificHandler) {
                dispatchTouchEvent(element, specificHandler, gestureData);
            }

            // Also trigger generic swipe handler if present
            const genericHandler = element.getAttribute('dj-swipe');
            if (genericHandler) {
                dispatchTouchEvent(element, genericHandler, gestureData);
            }
        }

        touchState.isTracking = false;
    }

    // ========================================================================
    // Pinch Handler (for zoom gestures)
    // ========================================================================
    
    function handlePinchStart(e) {
        if (e.touches.length < 2) return;

        const element = findEventTarget(e.target);
        if (!element || !element.hasAttribute('dj-pinch')) return;

        touchState.initialDistance = getPinchDistance(e.touches);
        touchState.isPinching = true;

        e.preventDefault();
    }

    function handlePinchMove(e) {
        if (!touchState.isPinching || e.touches.length < 2) return;

        const element = findEventTarget(e.target);
        if (!element || !element.hasAttribute('dj-pinch')) return;

        const currentDistance = getPinchDistance(e.touches);
        const scale = currentDistance / touchState.initialDistance;

        // Dispatch pinch event with scale
        const handler = element.getAttribute('dj-pinch');
        dispatchTouchEvent(element, handler, {
            scale: scale,
            pinchType: scale > 1 ? 'zoom-in' : 'zoom-out',
            currentDistance: currentDistance,
            initialDistance: touchState.initialDistance,
        });

        e.preventDefault();
    }

    function handlePinchEnd(e) {
        if (!touchState.isPinching) return;

        if (e.touches.length < 2) {
            touchState.isPinching = false;
            touchState.initialDistance = 0;
        }
    }

    // ========================================================================
    // Pull-to-Refresh Handler
    // ========================================================================
    
    let pullRefreshIndicator = null;

    function createPullRefreshIndicator() {
        if (pullRefreshIndicator) return pullRefreshIndicator;

        pullRefreshIndicator = document.createElement('div');
        pullRefreshIndicator.className = 'djust-pull-refresh-indicator';
        pullRefreshIndicator.innerHTML = `
            <div class="djust-pull-refresh-spinner"></div>
            <div class="djust-pull-refresh-text">Pull to refresh</div>
        `;
        pullRefreshIndicator.style.cssText = `
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            height: 0;
            overflow: hidden;
            display: flex;
            align-items: center;
            justify-content: center;
            background: var(--djust-pull-bg, #f5f5f5);
            transition: height 0.2s ease-out;
            z-index: 9999;
        `;
        document.body.prepend(pullRefreshIndicator);

        // Add spinner styles if not already present
        if (!document.getElementById('djust-pull-refresh-styles')) {
            const style = document.createElement('style');
            style.id = 'djust-pull-refresh-styles';
            style.textContent = `
                .djust-pull-refresh-spinner {
                    width: 24px;
                    height: 24px;
                    border: 3px solid #ddd;
                    border-top-color: #333;
                    border-radius: 50%;
                    margin-right: 8px;
                }
                .djust-pull-refresh-indicator.refreshing .djust-pull-refresh-spinner {
                    animation: djust-spin 0.8s linear infinite;
                }
                .djust-pull-refresh-indicator.refreshing .djust-pull-refresh-text {
                    content: 'Refreshing...';
                }
                @keyframes djust-spin {
                    to { transform: rotate(360deg); }
                }
            `;
            document.head.appendChild(style);
        }

        return pullRefreshIndicator;
    }

    function handlePullRefreshStart(e) {
        // Only trigger at top of page
        if (window.scrollY > 0) return;

        const element = findEventTarget(e.target);
        if (!element || !element.hasAttribute('dj-pull-refresh')) return;

        const touch = e.touches[0];
        touchState.startY = touch.clientY;
        touchState.pullRefreshElement = element;
        touchState.isPulling = true;
        touchState.pullDistance = 0;

        createPullRefreshIndicator();
    }

    function handlePullRefreshMove(e) {
        if (!touchState.isPulling || !touchState.pullRefreshElement) return;
        if (window.scrollY > 0) {
            touchState.isPulling = false;
            return;
        }

        const touch = e.touches[0];
        const deltaY = touch.clientY - touchState.startY;

        if (deltaY > 0) {
            e.preventDefault();
            
            const resistance = getConfig(
                touchState.pullRefreshElement,
                'pull-resistance',
                DEFAULTS.pullRefreshResistance
            );
            touchState.pullDistance = deltaY / resistance;

            const threshold = getConfig(
                touchState.pullRefreshElement,
                'pull-threshold',
                DEFAULTS.pullRefreshThreshold
            );

            // Update indicator
            if (pullRefreshIndicator) {
                pullRefreshIndicator.style.height = `${Math.min(touchState.pullDistance, threshold + 20)}px`;
                
                const textEl = pullRefreshIndicator.querySelector('.djust-pull-refresh-text');
                if (textEl) {
                    textEl.textContent = touchState.pullDistance >= threshold 
                        ? 'Release to refresh' 
                        : 'Pull to refresh';
                }
            }
        }
    }

    async function handlePullRefreshEnd(e) {
        if (!touchState.isPulling || !touchState.pullRefreshElement) return;

        const element = touchState.pullRefreshElement;
        const threshold = getConfig(element, 'pull-threshold', DEFAULTS.pullRefreshThreshold);

        if (touchState.pullDistance >= threshold) {
            // Trigger refresh
            if (pullRefreshIndicator) {
                pullRefreshIndicator.classList.add('refreshing');
                const textEl = pullRefreshIndicator.querySelector('.djust-pull-refresh-text');
                if (textEl) textEl.textContent = 'Refreshing...';
            }

            const handler = element.getAttribute('dj-pull-refresh');
            await dispatchTouchEvent(element, handler, {
                pullDistance: touchState.pullDistance,
            });

            // Hide indicator after a short delay
            setTimeout(() => {
                if (pullRefreshIndicator) {
                    pullRefreshIndicator.classList.remove('refreshing');
                    pullRefreshIndicator.style.height = '0';
                }
            }, 500);
        } else {
            // Cancel - hide indicator
            if (pullRefreshIndicator) {
                pullRefreshIndicator.style.height = '0';
            }
        }

        touchState.isPulling = false;
        touchState.pullRefreshElement = null;
        touchState.pullDistance = 0;
    }

    // ========================================================================
    // Touch-friendly Loading States
    // ========================================================================
    
    function applyTouchLoadingStyles() {
        // Add CSS for touch-friendly loading states
        if (!document.getElementById('djust-touch-loading-styles')) {
            const style = document.createElement('style');
            style.id = 'djust-touch-loading-styles';
            style.textContent = `
                /* Larger touch targets during loading */
                .djust-loading[dj-loading\\.touch] {
                    min-height: 44px;
                    min-width: 44px;
                }
                
                /* Tap feedback */
                .djust-tap-active {
                    opacity: 0.7;
                    transform: scale(0.98);
                    transition: opacity 0.1s, transform 0.1s;
                }
                
                /* Longpress feedback */
                .djust-longpress-active {
                    opacity: 0.8;
                    transform: scale(0.95);
                }
                
                /* Touch-specific loading indicator */
                [dj-loading\\.touch].djust-loading::after {
                    content: '';
                    position: absolute;
                    top: 50%;
                    left: 50%;
                    transform: translate(-50%, -50%);
                    width: 20px;
                    height: 20px;
                    border: 2px solid #ccc;
                    border-top-color: #333;
                    border-radius: 50%;
                    animation: djust-spin 0.8s linear infinite;
                }
            `;
            document.head.appendChild(style);
        }
    }

    // ========================================================================
    // Event Binding
    // ========================================================================
    
    function bindTouchEvents() {
        // Use passive listeners where possible for better scroll performance
        const passiveOptions = { passive: true };
        const activeOptions = { passive: false };

        // Tap events
        document.addEventListener('touchstart', handleTapStart, passiveOptions);
        document.addEventListener('touchend', handleTapEnd, activeOptions);

        // Longpress events
        document.addEventListener('touchstart', handleLongpressStart, passiveOptions);
        document.addEventListener('touchmove', handleLongpressMove, passiveOptions);
        document.addEventListener('touchend', handleLongpressEnd, passiveOptions);
        document.addEventListener('touchcancel', handleLongpressEnd, passiveOptions);

        // Swipe events
        document.addEventListener('touchstart', handleSwipeStart, passiveOptions);
        document.addEventListener('touchend', handleSwipeEnd, activeOptions);

        // Pinch events (need non-passive for preventDefault)
        document.addEventListener('touchstart', handlePinchStart, activeOptions);
        document.addEventListener('touchmove', handlePinchMove, activeOptions);
        document.addEventListener('touchend', handlePinchEnd, passiveOptions);

        // Pull-to-refresh (need non-passive for preventDefault)
        document.addEventListener('touchstart', handlePullRefreshStart, passiveOptions);
        document.addEventListener('touchmove', handlePullRefreshMove, activeOptions);
        document.addEventListener('touchend', handlePullRefreshEnd, passiveOptions);

        // Apply touch loading styles
        applyTouchLoadingStyles();

        if (globalThis.djustDebug) {
            console.log('[Touch] Touch event handlers bound');
        }
    }

    // ========================================================================
    // Initialization
    // ========================================================================
    
    // Check if touch is supported
    const isTouchDevice = 'ontouchstart' in window || navigator.maxTouchPoints > 0;

    if (isTouchDevice) {
        // Bind touch events when DOM is ready
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', bindTouchEvents);
        } else {
            bindTouchEvents();
        }
    }

    // ========================================================================
    // Export to djust namespace
    // ========================================================================
    
    window.djust = window.djust || {};
    window.djust.touch = {
        isTouchDevice: isTouchDevice,
        bindTouchEvents: bindTouchEvents,
        DEFAULTS: DEFAULTS,
        // For testing
        _touchState: touchState,
        _dispatchTouchEvent: dispatchTouchEvent,
    };

})();
