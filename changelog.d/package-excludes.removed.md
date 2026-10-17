- **The wheel and sdist no longer ship djust's own test suite
  (`djust/tests/`), the client's source modules (`djust/static/djust/src/`)
  or three unreferenced images** (`djust2_logo.pxd`, `djust3_logo.pxd`,
  `static/images/djust-icon-original.png`, about 1.4 MB). Nothing at runtime
  imported `djust.tests` or read the source modules; the shipped `client.js` /
  `client.min.js` are their concatenation. The component catalogue's
  "hook shipped" lookup now scans `client.js` instead of the source modules.
  CI builds both artifacts and fails if either ships the excluded paths
  (`scripts/check-package-contents.py`).
