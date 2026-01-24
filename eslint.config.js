/**
 * ESLint Configuration for djust
 *
 * This configuration focuses on security-related linting rules
 * to catch potential vulnerabilities in JavaScript code.
 */

import security from "eslint-plugin-security";

export default [
  {
    // Apply to all JavaScript files
    files: ["**/*.js"],

    // Ignore build artifacts and dependencies
    ignores: [
      "node_modules/**",
      "dist/**",
      "build/**",
      "target/**",
      ".venv/**",
      "*.min.js",
    ],

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

      // Disallow unused variables (helps catch mistakes)
      "no-unused-vars": ["warn", {
        "argsIgnorePattern": "^_",
        "varsIgnorePattern": "^_",
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

  // Test files can have relaxed rules
  {
    files: ["**/*.test.js", "**/*.spec.js", "tests/**/*.js"],
    rules: {
      "security/detect-object-injection": "off",
      "security/detect-non-literal-regexp": "off",
    },
  },
];
