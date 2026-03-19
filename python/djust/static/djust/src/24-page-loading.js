
// ============================================================================
// Page Loading Bar — NProgress-style loading indicator for navigation
// ============================================================================

(function () {
    // Inject CSS for the loading bar
    const style = document.createElement('style');
    style.textContent = `
        #djust-page-loading-bar {
            position: fixed;
            top: 0;
            left: 0;
            height: 3px;
            background: linear-gradient(90deg, #818cf8, #6366f1, #4f46e5);
            z-index: 99999;
            transition: width 2s ease-out, opacity 0.3s ease;
            pointer-events: none;
        }
    `;
    document.head.appendChild(style);

    let barElement = null;
    let finishTimeout = null;

    function start() {
        // Clean up any existing bar
        if (barElement) {
            barElement.remove();
            barElement = null;
        }
        if (finishTimeout) {
            clearTimeout(finishTimeout);
            finishTimeout = null;
        }

        barElement = document.createElement('div');
        barElement.id = 'djust-page-loading-bar';
        barElement.style.width = '0%';
        barElement.style.opacity = '1';
        document.body.appendChild(barElement);

        // Animate to 90% (never completes until finish() is called)
        requestAnimationFrame(() => {
            if (barElement) {
                barElement.style.width = '90%';
            }
        });
    }

    function finish() {
        if (!barElement) return;

        // Snap to 100%
        barElement.style.transition = 'width 0.2s ease, opacity 0.3s ease 0.2s';
        barElement.style.width = '100%';
        barElement.style.opacity = '0';

        const bar = barElement;
        finishTimeout = setTimeout(() => {
            bar.remove();
            if (barElement === bar) {
                barElement = null;
            }
            finishTimeout = null;
        }, 500);
    }

    window.djust.pageLoading = {
        start: start,
        finish: finish,
        enabled: true,
    };

    // Hook into TurboNav: start bar before navigation
    window.addEventListener('turbo:before-visit', function () {
        if (window.djust.pageLoading.enabled) {
            start();
        }
    });

    // Hook into TurboNav: finish bar after load
    window.addEventListener('turbo:load', function () {
        if (window.djust.pageLoading.enabled) {
            finish();
        }
    });
})();
