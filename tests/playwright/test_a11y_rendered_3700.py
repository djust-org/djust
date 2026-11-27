#!/usr/bin/env python3
"""Real-browser check of ``djust_audit --a11y --url`` (#3700).

Serves two static pages from a local HTTP server and runs the rendered-page
pass over them in headless Chromium with the real axe-core:

* ``bad.html`` has light-grey text on white (axe ``color-contrast``,
  serious) and no landmark (``region``, moderate). The pass must report the
  contrast violation, and the exit mapping must fail ``--strict`` and pass
  normal mode.
* ``clean.html`` has a ``<main>`` landmark, a heading and dark text. The pass
  must report no violation and pass ``--strict``.

Both pages are also run through ``manage.py djust_audit`` as a subprocess so
the CLI's exit codes are checked end to end. Self-contained:

    pip install 'djust[a11y]' && playwright install chromium
    python tests/playwright/test_a11y_rendered_3700.py

``DJUST_SERVER_PYTHON`` optionally names the interpreter for the CLI run
(default: this one). Exits 0 on success, non-zero with the list of
failures. Not part of the CI suite (see README.md).
"""

import functools
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "python"))

from djust.audit_a11y import exit_code, run_rendered_audit  # noqa: E402

BAD = """<!DOCTYPE html><html lang="en"><head><title>bad</title></head><body>
<div><p id="low" style="color:#cccccc;background:#ffffff">Barely readable text</p></div>
</body></html>"""

CLEAN = """<!DOCTYPE html><html lang="en"><head><title>clean</title></head><body>
<main><h1>Readable</h1><p style="color:#111111;background:#ffffff">Plain dark text.</p></main>
</body></html>"""

FAILURES = []
SEEN = []  # (port, path, X-Audit-Token header) for every request served


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        FAILURES.append(what)


def serve(root):
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            SEEN.append(
                (self.server.server_address[1], self.path, self.headers.get("X-Audit-Token"))
            )
            super().do_GET()

    handler = functools.partial(Quiet, directory=root)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def run_cli(base, *args):
    python = os.environ.get("DJUST_SERVER_PYTHON", sys.executable)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "python"), str(REPO / "examples/demo_project"), env.get("PYTHONPATH", "")]
    )
    env.setdefault("DJANGO_SETTINGS_MODULE", "demo_project.settings")
    cmd = [python, "-m", "django", "djust_audit", "--skip-checks", "--a11y", "--json", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(REPO))
    return proc.returncode, proc.stdout, proc.stderr


def main():
    with tempfile.TemporaryDirectory() as root:
        Path(root, "bad.html").write_text(BAD)
        Path(root, "clean.html").write_text(CLEAN)
        server = serve(root)
        base = "http://127.0.0.1:%d" % server.server_address[1]
        try:
            bad_url, clean_url = base + "/bad.html", base + "/clean.html"
            report = run_rendered_audit([bad_url, clean_url, base + "/missing.html"])
            bad, clean, missing = report.pages
            rules = {v.rule_id: v for v in bad.violations}
            check(
                report.axe_version.startswith("4."),
                "axe-core version reported (%s)" % report.axe_version,
            )
            check(
                "color-contrast" in rules, "bad page: color-contrast reported (%s)" % sorted(rules)
            )
            check(
                "color-contrast" in rules and rules["color-contrast"].impact == "serious",
                "bad page: color-contrast is serious",
            )
            check(
                "color-contrast" in rules
                and rules["color-contrast"].nodes[0]["target"] == ["#low"],
                "bad page: the low-contrast node is named (#low)",
            )
            check("region" in rules, "bad page: missing landmark reported (region)")
            check(clean.violations == [] and not clean.error, "clean page: no violation")
            check(missing.error == "HTTP 404", "missing page: not audited (%r)" % missing.error)

            # --header goes to the audited origin only, never to another one.
            other = serve(root)
            other_port = other.server_address[1]
            try:
                Path(root, "hdr.html").write_text(
                    CLEAN.replace(
                        "</head>",
                        '<link rel="stylesheet" href="/same.css">'
                        '<link rel="stylesheet" href="http://127.0.0.1:%d/other.css"></head>'
                        % other_port,
                    )
                )
                Path(root, "same.css").write_text("p{}")
                Path(root, "other.css").write_text("p{}")
                SEEN.clear()
                run_rendered_audit([base + "/hdr.html"], extra_headers={"X-Audit-Token": "t0k"})
                tokens = {path: token for _, path, token in SEEN}
                check(tokens.get("/hdr.html") == "t0k", "header sent to the audited page")
                check(tokens.get("/same.css") == "t0k", "header sent to same-origin subresource")
                check(
                    "/other.css" in tokens and tokens["/other.css"] is None,
                    "header NOT sent to another origin (%r)"
                    % tokens.get("/other.css", "not fetched"),
                )
            finally:
                other.shutdown()

            only_bad = run_rendered_audit([bad_url])
            only_clean = run_rendered_audit([clean_url])
            check(exit_code(only_bad, strict=True) == 1, "bad page fails --strict")
            check(exit_code(only_bad, strict=False) == 0, "bad page passes normal mode")
            check(exit_code(only_clean, strict=True) == 0, "clean page passes --strict")

            code, out, err = run_cli(base, "--strict", "--url", bad_url)
            check(
                code == 1,
                "CLI: bad page --strict exits 1 (got %s; %s)" % (code, err.strip()[-300:]),
            )
            if code in (0, 1):
                data = json.loads(out)
                check(data["summary"]["failing"] >= 1, "CLI: JSON counts the failing violation")
            code, _, err = run_cli(base, "--url", bad_url)
            check(code == 0, "CLI: bad page normal mode exits 0 (got %s)" % code)
            code, _, err = run_cli(base, "--strict", "--url", clean_url)
            check(
                code == 0,
                "CLI: clean page --strict exits 0 (got %s; %s)" % (code, err.strip()[-300:]),
            )
        finally:
            server.shutdown()

    if FAILURES:
        print("\n%d failure(s):" % len(FAILURES))
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
