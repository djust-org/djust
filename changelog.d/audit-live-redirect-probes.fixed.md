- **`djust_audit --live` no longer reports `/.git/config`, `/.env` and
  `/__debug__/` as exposed when they redirect to a sign-in page.** The probes
  followed the redirect and read the sign-in page's 200 (L040/L041/L042). They
  now read the path's own response: only a direct 2xx is exposed; a redirect,
  401, 403 or 404 is not. Cases in `python/tests/test_audit_live.py`.
