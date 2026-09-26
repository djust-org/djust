// Every third-party browser asset djust ships (ADR-040). build.mjs builds each
// one and regenerates the owning app's djust_assets.json from what esbuild
// actually bundled; never edit those manifests by hand.
export const APPS = {
  components: "../../python/djust/components",
  admin_ext: "../../python/djust/admin_ext",
};

export const THEMES = [
  "github", "github-dark", "atom-one-dark", "atom-one-light", "monokai", "vs2015", "nord",
  "default", "dark", "a11y-dark", "a11y-light", "stackoverflow-light", "stackoverflow-dark",
];

export const BUNDLES = [
  {
    asset: "markdown-visual",
    app: "components",
    kind: "esbuild",
    entry: "visual.js",
    format: "iife",
    out: "djust_components/markdown-visual.js",
    license: true,
  },
  {
    asset: "highlight.js",
    app: "components",
    kind: "esbuild",
    entry: "highlight-entry.js",
    format: "iife",
    out: "djust_components/vendor/highlight/highlight.js",
    license: true,
    variants: THEMES.map((theme) => ({
      variant: theme,
      from: `node_modules/highlight.js/styles/${theme}.min.css`,
      out: `djust_components/vendor/highlight/styles/${theme}.css`,
    })),
  },
  {
    asset: "xterm",
    app: "components",
    kind: "esbuild",
    entry: "xterm-entry.js",
    format: "esm",
    out: "djust_components/vendor/xterm/xterm.mjs",
    license: true,
    extra: [{ from: "node_modules/@xterm/xterm/css/xterm.css", out: "djust_components/vendor/xterm/xterm.css" }],
  },
  {
    asset: "admin-css",
    app: "admin_ext",
    kind: "tailwind",
    input: "admin.tailwind.css",
    config: "tailwind.config.cjs",
    out: "djust_admin/admin.css",
    packages: ["tailwindcss"],
  },
];
