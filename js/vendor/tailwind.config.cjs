// The admin templates load the Tailwind v3 play CDN with no inline
// tailwind.config, so the default theme is the whole configuration.
module.exports = {
  content: [
    "../../python/djust/admin_ext/templates/**/*.html",
    "../../python/djust/admin_ext/**/*.py",
  ],
};
