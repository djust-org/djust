// ============================================================================
// dj-chart — official Chart.js adapter (ADR-025 milestone C, issue #2063)
// ============================================================================
//
// "User brings the library, djust ships the morph-safe glue."
//
// This file is NOT part of client.js. It is served only when opted in:
//
//     # settings.py
//     DJUST_CONFIG = {"extensions": ["chart"]}
//
// You supply Chart.js yourself (CDN, vendored file, whatever) — djust never
// bundles or downloads it. Load it BEFORE djust's client script.
//
// Template recipe:
//
//     <canvas dj-hook="Chart"
//             dj-update="ignore"
//             dj-hook-value-type='"bar"'
//             dj-hook-value-data='{{ chart_data_json }}'></canvas>
//
// `dj-update="ignore"` is required: Chart.js owns the canvas internals, and
// the server must not morph them. Without it the server's idea of the
// canvas subtree fights the library's — the #1724 teardown class.
//
// Values are ADR-025 typed values (JSON-first, live-read on each access), so
// a server re-render that changes dj-hook-value-data flows into updated()
// with no staleness machinery.
// ============================================================================

(function initDjChart() {


    globalThis.djust = globalThis.djust || {};
    const djust = globalThis.djust;

    // Element -> live Chart instance. WeakMap so a removed canvas cannot
    // leak its chart, and so JS.ext commands can find the instance for a
    // target element without reaching into the hook registry.
    const charts = new WeakMap();

    /** The user-supplied library, or null when they forgot to load it. */
    function resolveLib() {
        return globalThis.Chart || null;
    }

    function warnMissingLib(el) {
        // %s parameterized rather than interpolated — CodeQL
        // js/tainted-format-string (#1124); el ids are DOM-controlled.
        console.error(
            '[dj-chart] window.Chart is not loaded. dj-chart ships only the glue — ' +
                'include Chart.js yourself before djust\'s client script. Element: %s',
            (el && el.id) || '(no id)'
        );
    }

    /** Build the Chart.js config object from ADR-025 typed values. */
    function readConfig(values) {
        return {
            type: values.type || 'line',
            data: values.data || { labels: [], datasets: [] },
            options: values.options || {},
        };
    }

    djust.hooks = djust.hooks || {};

    // Per-key assignment: never `djust.hooks = {...}`, which would clobber
    // hooks the user registered before this file loaded. _getHookDefs()
    // (19-hooks.js) re-reads the registry on every sweep, so this is
    // additive regardless of load order.
    djust.hooks.Chart = {
        mounted() {
            const Lib = resolveLib();
            if (!Lib) {
                warnMissingLib(this.el);
                return;
            }
            const cfg = readConfig(this.values);
            const ctx = this.el.getContext ? this.el.getContext('2d') : this.el;
            const chart = new Lib(ctx, cfg);
            charts.set(this.el, chart);
            this._chart = chart;
        },

        updated() {
            const chart = this._chart || charts.get(this.el);
            if (!chart) {
                // Library arrived late, or mount failed — try once more so a
                // server re-render can recover rather than stay blank.
                this.mounted();
                return;
            }
            // Mutate IN PLACE. Destroying and reconstructing here is exactly
            // the #1724 class: it throws away animation state, event
            // handlers, and any client-side interaction the user had.
            const cfg = readConfig(this.values);
            chart.data = cfg.data;
            if (cfg.options && Object.keys(cfg.options).length) {
                chart.options = cfg.options;
            }
            chart.update();
        },

        destroyed() {
            const chart = this._chart || charts.get(this.el);
            if (!chart) return;
            chart.destroy();
            charts.delete(this.el);
            // Null the ref so a double lifecycle dispatch cannot
            // double-destroy (Chart.js throws on destroy-after-destroy).
            this._chart = null;
        },
    };

    // ------------------------------------------------------------------
    // JS.ext commands — let the server drive the chart from a JS chain:
    //     self.refresh = JS.ext.chart_update(to="#sales")
    // ------------------------------------------------------------------
    if (djust.commands && typeof djust.commands.register === 'function') {
        djust.commands.register('chart_update', (targets) => {
            targets.forEach((el) => {
                const chart = charts.get(el);
                if (chart) chart.update();
            });
        });

        djust.commands.register('chart_set_data', (targets, args) => {
            targets.forEach((el) => {
                const chart = charts.get(el);
                if (!chart || !args || !args.data) return;
                chart.data = args.data;
                chart.update();
            });
        });
    }
})();
