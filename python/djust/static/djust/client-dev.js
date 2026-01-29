// djust - Development Utilities
// This file contains development-only features and should NOT be loaded in production

(function() {
    'use strict';

    // ============================================================================
    // Toast Notification System
    // ============================================================================

    /**
     * Show a toast notification for hot reload events
     * @param {string} message - Message to display
     * @param {string} type - 'success' or 'warning'
     * @param {number} duration - Duration in ms (default: 2000)
     */
    function showToast(message, type = 'success', duration = 2000) {
        // Create toast container if it doesn't exist
        let container = document.getElementById('djust-toast-container');
        if (!container) {
            container = document.createElement('div');
            container.id = 'djust-toast-container';
            container.style.cssText = `
                position: fixed;
                bottom: 20px;
                right: 20px;
                z-index: 10000;
                pointer-events: none;
            `;
            document.body.appendChild(container);
        }

        // Create toast element
        const toast = document.createElement('div');
        toast.className = 'djust-toast';

        // Set styles based on type
        const colors = {
            success: { bg: '#10b981', icon: '✓' },
            warning: { bg: '#f59e0b', icon: '⚠' },
            error: { bg: '#ef4444', icon: '✕' }
        };
        const color = colors[type] || colors.success;

        toast.style.cssText = `
            background: ${color.bg};
            color: white;
            padding: 12px 16px;
            border-radius: 8px;
            margin-top: 8px;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
            display: flex;
            align-items: center;
            gap: 8px;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            font-size: 14px;
            font-weight: 500;
            opacity: 0;
            transform: translateX(20px);
            transition: all 0.3s ease;
            pointer-events: auto;
        `;

        // Set content with icon
        toast.innerHTML = `
            <span style="font-size: 16px;">${color.icon}</span>
            <span>${message}</span>
        `;

        container.appendChild(toast);

        // Trigger animation
        requestAnimationFrame(() => {
            toast.style.opacity = '1';
            toast.style.transform = 'translateX(0)';
        });

        // Remove after duration
        setTimeout(() => {
            toast.style.opacity = '0';
            toast.style.transform = 'translateX(20px)';
            setTimeout(() => {
                container.removeChild(toast);
            }, 300);
        }, duration);
    }

    // ============================================================================
    // Hot Reload Support
    // ============================================================================

    /**
     * Enhances the LiveView WebSocket to handle hot reload patches
     */
    function initHotReload(retryCount = 0) {
        // Wait for LiveViewWebSocket to be defined by client.js
        if (typeof window.LiveViewWebSocket === 'undefined') {
            if (retryCount < 20) {  // Max 1 second (20 * 50ms)
                console.log('[djust:dev] Waiting for LiveViewWebSocket to load...');
                // Retry after a short delay
                setTimeout(() => initHotReload(retryCount + 1), 50);
                return;
            } else {
                console.error('[djust:dev] Failed to load LiveViewWebSocket after 1 second');
                return;
            }
        }

        // Store original handleMessage
        const originalHandleMessage = window.LiveViewWebSocket.prototype.handleMessage;

        // Override handleMessage to add hot reload logging and visual feedback
        window.LiveViewWebSocket.prototype.handleMessage = function(data) {
            // Log hot reload patches
            if (data.type === 'patch' && data.hotreload) {
                const fileName = data.file.split('/').pop();
                const patchCount = data.patches?.length || 0;

                console.log('[HotReload] File changed:', data.file);
                console.log('[HotReload] Applying', patchCount, 'patches...');

                // Show toast notification
                showToast(`${fileName} updated (${patchCount} changes)`, 'success', 2000);

                // Add hot reload marker to debug panel if available
                if (window.djustDebugPanel) {
                    window.djustDebugPanel.logEvent(
                        'hot_reload',
                        { file: data.file, patch_count: patchCount },
                        { type: 'success', message: 'Template reloaded' },
                        0
                    );
                }

                // Call original handler
                const result = originalHandleMessage.call(this, data);

                // Log success
                console.log('[HotReload] ✅ Patches applied successfully');
                return result;
            }

            // Log full reload
            if (data.type === 'reload') {
                const fileName = data.file.split('/').pop();

                console.log('[HotReload] File changed:', data.file);
                console.log('[HotReload] Reloading page...');

                // Show toast notification before reload
                showToast(`${fileName} changed - reloading...`, 'warning', 1500);

                // Add to debug panel before reload
                if (window.djustDebugPanel) {
                    window.djustDebugPanel.logEvent(
                        'hot_reload',
                        { file: data.file, type: 'full_reload' },
                        { type: 'warning', message: 'Full page reload' },
                        0
                    );
                }
            }

            // Call original handler for all other cases
            return originalHandleMessage.call(this, data);
        };

        console.log('[djust:dev] Hot reload enabled');
    }

    // ============================================================================
    // Debug Logging
    // ============================================================================

    /**
     * Enable verbose debug logging controlled by window.djustDebug
     */
    function initDebugLogging() {
        // Check if debug mode is enabled
        if (!window.djustDebug) {
            return;
        }

        console.log('[djust:dev] Debug mode enabled');
        console.log('[djust:dev] Set window.djustDebug = false to disable');

        // Add global error handler for better debugging
        window.addEventListener('error', function(event) {
            console.error('[djust:dev] Global error:', event.error);
        });

        // Log unhandled promise rejections
        window.addEventListener('unhandledrejection', function(event) {
            console.error('[djust:dev] Unhandled promise rejection:', event.reason);
        });
    }

    // ============================================================================
    // Performance Monitoring
    // ============================================================================

    /**
     * Monitor and log LiveView performance metrics
     */
    function initPerformanceMonitoring() {
        if (!window.djustDebug) {
            return;
        }

        // Store original applyPatches
        const originalApplyPatches = window.applyPatches;
        if (originalApplyPatches) {
            window.applyPatches = function(patches) {
                const start = performance.now();
                const result = originalApplyPatches.call(this, patches);
                const duration = performance.now() - start;

                if (duration > 16) { // Slower than 60fps
                    console.warn(`[djust:perf] Slow patch application: ${duration.toFixed(2)}ms for ${patches.length} patches`);
                }

                return result;
            };
        }
    }

    // ============================================================================
    // Development Warnings
    // ============================================================================

    /**
     * Show helpful development warnings
     */
    function initDevelopmentWarnings() {
        // Warn if Channels not configured
        setTimeout(function() {
            if (window.liveview && !window.liveview.ws) {
                console.warn('[djust:dev] WebSocket not connected. Check Channels configuration.');
            }

            // Log debug panel availability
            if (window.djustDebugPanel) {
                const shortcut = navigator.userAgent.includes('Mac') ? 'Cmd+Shift+D' : 'Ctrl+Shift+D';
                console.log(`[djust:dev] Debug panel available - Press ${shortcut} to toggle`);
            } else {
                console.log('[djust:dev] Debug panel not available (set DJUST_DEBUG_INFO)');
            }
        }, 2000);
    }

    // ============================================================================
    // Debug Panel
    // ============================================================================
    // Debug panel is now loaded from debug-panel.js to avoid duplication

    // ============================================================================
    // Debug Panel Initialization
    // ============================================================================

    function initDebugPanel() {
        // Initialize global debug panel if debug info is available
        if (typeof window.DJUST_DEBUG_INFO !== 'undefined' && typeof window.DjustDebugPanel !== 'undefined') {
            // Clean up existing debug panel if it exists
            if (window.djustDebugPanel && typeof window.djustDebugPanel.destroy === 'function') {
                window.djustDebugPanel.destroy();
            }

            // Create new instance from the external debug-panel.js
            window.djustDebugPanel = new window.DjustDebugPanel();

            // Hook into event sending to log events
            if (typeof handleEvent !== 'undefined') {
                const originalHandleEvent = handleEvent;

                // Replace with wrapper that logs events with timing
                window.handleEvent = handleEvent = async function (eventName, params) {
                    const startTime = performance.now();
                    let result = null;
                    let error = null;

                    try {
                        // Call original implementation
                        result = await originalHandleEvent(eventName, params);
                    } catch (e) {
                        error = e;
                        throw e;
                    } finally {
                        // Calculate duration
                        const duration = performance.now() - startTime;

                        // Extract element information for logging
                        let elementInfo = null;
                        if (params._targetElement) {
                            const elem = params._targetElement;
                            elementInfo = {
                                tagName: elem.tagName?.toLowerCase(),
                                id: elem.id || null,
                                className: elem.className || null,
                                text: elem.textContent?.substring(0, 50) || null,
                                attributes: {}
                            };

                            // Capture key attributes
                            ['name', 'type', 'value', '@click', '@input', '@change', '@submit'].forEach(attr => {
                                const value = elem.getAttribute(attr);
                                if (value) {
                                    elementInfo.attributes[attr] = value;
                                }
                            });
                        }

                        // Create clean params without internal fields
                        const cleanParams = { ...params };
                        delete cleanParams._targetElement;
                        delete cleanParams._skipDecorators;

                        // Log event to debug panel (skip internal/decorator events)
                        if (window.djustDebugPanel && !params._skipDecorators) {
                            window.djustDebugPanel.logEvent(
                                eventName,
                                cleanParams,
                                error ? { type: 'error', error: error.message } : result,
                                duration,
                                elementInfo
                            );
                        }
                    }

                    return result;
                };
            }

            // Hook into patch application to log patches
            if (typeof applyPatches !== 'undefined' || typeof window.applyPatches !== 'undefined') {
                const originalApplyPatches = window.applyPatches || applyPatches;

                // Replace with wrapper that logs patches with timing
                window.applyPatches = applyPatches = function (patches) {
                    const startTime = performance.now();

                    // Call original implementation
                    const result = originalApplyPatches(patches);

                    // Calculate client-side duration
                    const clientDuration = performance.now() - startTime;

                    // Log patches to debug panel with both client and server timing
                    if (window.djustDebugPanel && patches && patches.length > 0) {
                        // Combine server timing (if available) with client timing
                        const timing = {
                            client: clientDuration,
                            ...(window._lastPatchTiming || {})
                        };

                        // Get comprehensive performance data if available
                        const performance = window._lastPerformanceData || null;

                        // Clear last patch timing and performance data after use
                        window._lastPatchTiming = null;
                        window._lastPerformanceData = null;

                        window.djustDebugPanel.logPatches(patches, timing, performance);
                    }

                    return result;
                };
            }
        }
    }

    // ============================================================================
    // Initialize Development Tools
    // ============================================================================

    // Wait for DOM to be ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    function initErrorOverlay() {
        window.addEventListener('djust:error', (e) => {
            const { error, event: eventName, validation_details } = e.detail;

            // Show toast
            showToast(error, 'error', 4000);

            // Log to debug panel
            if (window.djustDebugPanel) {
                window.djustDebugPanel.logEvent(
                    eventName || 'unknown',
                    validation_details || {},
                    { type: 'error', error: error },
                    0
                );
            }
        });
    }

    function init() {
        console.log('[djust:dev] Initializing development tools...');
        console.log('[djust:dev] Debug mode:', window.djustDebug ? 'enabled' : 'disabled');

        initHotReload();
        initDebugLogging();
        initPerformanceMonitoring();
        initDevelopmentWarnings();
        initDebugPanel();
        initErrorOverlay();

        console.log('[djust:dev] Development tools ready');
        console.log('[djust:dev] -----------------------------------------');
    }

})();
