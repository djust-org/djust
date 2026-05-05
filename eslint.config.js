/**
 * ESLint Configuration for djust
 *
 * This configuration focuses on security-related linting rules
 * to catch potential vulnerabilities in JavaScript code.
 */

import security from "eslint-plugin-security";

export default [
  // Global ignores — applies to all configs. Must be in its own config
  // block (no `files` key) to be a true global ignore in v9+ flat config.
  {
    ignores: [
      "node_modules/**",
      "dist/**",
      "build/**",
      "target/**",
      ".venv/**",
      "**/*.min.js",
      "**/*.min.js.map",
    ],
  },
  {
    // Apply to all JavaScript files
    files: ["**/*.js"],

    plugins: {
      security,
    },

    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        // Browser globals
        window: "readonly",
        document: "readonly",
        console: "readonly",
        setTimeout: "readonly",
        clearTimeout: "readonly",
        setInterval: "readonly",
        clearInterval: "readonly",
        fetch: "readonly",
        WebSocket: "readonly",
        DOMParser: "readonly",
        HTMLElement: "readonly",
        MutationObserver: "readonly",
        FormData: "readonly",
        URLSearchParams: "readonly",
        CustomEvent: "readonly",
        // djust globals
        globalThis: "readonly",
        djustSecurity: "readonly",
      },
    },

    rules: {
      // =======================================================================
      // Security Rules (eslint-plugin-security)
      // =======================================================================

      // Detect eval() usage
      "security/detect-eval-with-expression": "error",

      // Detect non-literal require() (potential code injection)
      "security/detect-non-literal-require": "warn",

      // Detect non-literal RegExp (potential ReDoS)
      "security/detect-non-literal-regexp": "warn",

      // Detect non-literal fs methods (path traversal)
      "security/detect-non-literal-fs-filename": "warn",

      // Detect child_process usage
      "security/detect-child-process": "warn",

      // Detect object injection (prototype pollution)
      "security/detect-object-injection": "warn",

      // Detect possible timing attacks
      "security/detect-possible-timing-attacks": "warn",

      // Detect pseudoRandomBytes (use crypto.randomBytes)
      "security/detect-pseudoRandomBytes": "warn",

      // Detect buffer() with noAssert
      "security/detect-buffer-noassert": "error",

      // Detect unsafe regex
      "security/detect-unsafe-regex": "warn",

      // =======================================================================
      // General Best Practices
      // =======================================================================

      // Disallow use of eval()
      "no-eval": "error",

      // Disallow use of implied eval()
      "no-implied-eval": "error",

      // Disallow new Function()
      "no-new-func": "error",

      // Disallow script URLs
      "no-script-url": "error",

      // Disallow with statements
      "no-with": "error",

      // Require strict mode
      "strict": ["error", "global"],

      // Disallow unused variables (helps catch mistakes).
      // `caughtErrors` covers `catch (_e) { ... }` parameters; without
      // an explicit `caughtErrorsIgnorePattern` the rule ignores the
      // top-level patterns for catch bindings.
      "no-unused-vars": ["warn", {
        "argsIgnorePattern": "^_",
        "varsIgnorePattern": "^_",
        "caughtErrors": "all",
        "caughtErrorsIgnorePattern": "^_",
      }],

      // Require const for variables that aren't reassigned
      "prefer-const": "warn",

      // Disallow var
      "no-var": "warn",

      // =======================================================================
      // djust-Specific Rules
      // =======================================================================

      // Console logs are OK in djust (debug mode uses them)
      "no-console": "off",
    },
  },

  // Top-level scripts loaded as <script src="…"> by Django templates
  // (NOT ES modules). These wrap their bodies in IIFEs and use
  // `'use strict'` directives — turn off the strict rule here so the
  // IIFE-internal directive isn't flagged as redundant. Includes the
  // built bundles (client.js, debug-panel.js) plus stand-alone helpers
  // (client-dev.js, security.js, etc.). NOTE: decorators.js is excluded
  // — it uses `import/export` and is loaded as a module.
  //
  // `reportUnusedDisableDirectives: off` for the BUNDLES — the source
  // modules carry `// eslint-disable-next-line prefer-const` for the
  // few cross-file reassigned globals (e.g. `liveViewWS`). In the
  // concatenated bundle ESLint sees the cross-file reassign and the
  // disable becomes "unused" (the rule doesn't fire). Without this
  // override the bundle would re-trip on every build (#1351).
  {
    files: [
      "python/djust/static/djust/client.js",
      "python/djust/static/djust/client-dev.js",
      "python/djust/static/djust/debug-panel.js",
      "python/djust/static/djust/react-client.js",
      "python/djust/static/djust/security.js",
      "python/djust/static/djust/service-worker.js",
    ],
    languageOptions: {
      sourceType: "script",
    },
    linterOptions: {
      reportUnusedDisableDirectives: "off",
    },
    rules: {
      "strict": "off",
    },
  },

  // Concatenation fragments — these files are NOT standalone modules.
  //
  // For client.js:
  //   00-namespace.js opens an `if/else { ... }` double-load guard block;
  //   21-guard-close.js closes it. All other modules execute inside the
  //   block.
  //
  // For debug-panel.js (src/debug/*.js):
  //   The whole debug panel is a single class `DjustDebugPanel` declared
  //   in 00-panel-core.js (after a feature-detect early-return). All
  //   subsequent files contribute methods to that class body and the
  //   class closes at the end of the concat. Each file individually is
  //   a syntactic fragment.
  //
  // Skip lint on the fragments themselves — they're audited as part of
  // the bundle (client.js / debug-panel.js).
  {
    ignores: [
      "python/djust/static/djust/src/00-namespace.js",
      "python/djust/static/djust/src/21-guard-close.js",
      "python/djust/static/djust/src/debug/**/*.js",
    ],
  },

  // src/*.js source modules are concatenated into client.js (#1351). A
  // function or const declared in 01-dom-helpers-turbo.js is "used" only
  // by 09-event-binding.js after concat, but ESLint parses each src
  // file independently and can't see the cross-file reference. Disable
  // `no-unused-vars` for source modules — the bundled client.js (which
  // IS what the pre-commit hook checks) still enforces the rule with
  // full cross-file visibility. Catch-error / arg patterns still
  // benefit from the global rule because the bundle config is what
  // catches them in concat'd output.
  {
    files: ["python/djust/static/djust/src/**/*.js"],
    rules: {
      "no-unused-vars": "off",
    },
  },

  // Test files can have relaxed rules
  {
    files: ["**/*.test.js", "**/*.spec.js", "tests/**/*.js"],
    rules: {
      "security/detect-object-injection": "off",
      "security/detect-non-literal-regexp": "off",
    },
  },
];
