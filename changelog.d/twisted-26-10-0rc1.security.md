- **Apps served by daphne should upgrade Twisted to 26.10.0rc1 or later
  (CVE-2026-115877 / GHSA-q7qq-6mhp-9rc4).** Twisted 26.4.0's
  `twisted.web.http.HTTPChannel` accepts a bare CR or LF inside a header
  value, which allows HTTP request smuggling behind a front end that treats
  the bare character as a line end. daphne parses HTTP with that class.
  djust's published dependencies (`channels[daphne]`, which allows
  `twisted[tls]>=22.4`) still resolve to 26.4.0, because installers skip
  pre-releases. If you serve with daphne, run
  `pip install "twisted[tls]>=26.10.0rc1"` or serve with uvicorn, as djust's
  docs and scaffolds already do. djust's own `uv.lock` now pins 26.10.0rc1
  through a `[tool.uv]` constraint until 26.10.0 final is out. Four more
  Twisted 26.4.0 advisories (CVE-2026-115774, -115775, -115776 and -115778)
  are in `twisted.mail`, `twisted.words` and `twisted.conch`, which djust
  never imports, so they are allowlisted in the release `pip-audit` gate
  until the final is locked.
