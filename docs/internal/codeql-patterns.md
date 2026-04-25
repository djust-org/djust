# CodeQL Taint-Flow Cheat Sheet (Internal)

This document captures the taint-flow patterns the djust team has encountered most across the v0.5.x–v0.7.x security and code-scanning cleanup arcs. It is intended for two audiences:

1. **Contributors writing new code** — read before adding any code path that takes user input through to a logger, template, response body, or filesystem call.
2. **Reviewers** — when CodeQL flags a site, grep adjacent files for the same pattern. The cleanup PRs below all surfaced 2-5 additional sites once one was found.

> Status: living document. Add to it whenever a CodeQL pattern recurs across more than one PR.

## How CodeQL taint-flow works (very briefly)

CodeQL traces tainted data from **sources** (HTTP request, env var, file content) through **flow steps** (assignments, function calls, attribute access) to **sinks** (logger, HTML output, redirect URL, subprocess argument). Each pattern below is a (source → sink) class. The fix idiom is whatever cuts the flow at a step CodeQL can recognize.

---

## 1. log-injection (`py/log-injection`)

**Source → sink**: any user-controlled string → `logger.error("...")` / `logger.warning("...")` etc, especially via f-strings or `%`-format.

**Bad**:
```python
logger.error(f"Auth failed for user {request.user.username}: {request.GET['next']}")
```

User-controlled `next` (and `username` if logged-in is unverified) reaches the log message body. An attacker can inject newlines + forged log lines (CRLF injection) or pollute log analytics.

**Fix idiom**: `%s`-style formatting with user input as a positional argument. CodeQL recognizes the format-arg boundary as a flow-cut.

```python
logger.error("Auth failed for user %s: next=%s", request.user.username, request.GET["next"])
```

This is also a hard project rule — see `CLAUDE.md` Security Rules section.

**Representative PRs**: #898 (initial 9 alerts, 2 files), #918 (8 more), #921 (auth views).

**Reviewer pattern**: grep for `logger\.(debug|info|warning|error|exception|critical)\(f["']` — every f-string log is a candidate.

---

## 2. reflective-XSS via template strings (`py/reflective-xss`)

**Source → sink**: user-controlled string → HTML output not escaped at render. Most often via `mark_safe()` with a value that traversed the request, or `format_html()` with an interpolated user value passed without escaping.

**Bad**:
```python
return mark_safe(f"<a href='/back?next={request.GET['next']}'>Back</a>")
```

`request.GET['next']` reaches HTML attribute output. Attacker injects `'><script>...</script>` and the script renders.

**Fix idiom**: use `format_html` with `{}` placeholders and pass the raw value — Django escapes per-arg.

```python
from django.utils.html import format_html
return format_html("<a href='/back?next={}'>Back</a>", request.GET['next'])
```

For JavaScript string contexts, use `json.dumps()` not `escape()` — the former handles all JS-string escape rules including U+2028/2029 line terminators that HTML escape misses.

**Representative PRs**: #920 (gallery views, 6 alerts), #923 (admin views).

**Reviewer pattern**: grep for `mark_safe\(f` — anything with f-string interpolation inside `mark_safe` is suspect.

---

## 3. stack-trace exposure (`py/stack-trace-exposure`)

**Source → sink**: raw exception (`str(exc)`, `traceback.format_exc()`, `repr(exc)`) → response body, WebSocket frame, or template context.

**Bad**:
```python
try:
    obj = ExternalAPI.get(id)
except ExternalAPI.Error as e:
    return JsonResponse({"error": str(e)}, status=500)
```

The exception message may contain S3 ARNs, internal paths, secret URLs — anything the underlying library felt useful to put in `__str__`. That string is now in the response body.

**Fix idiom**: log the full exception server-side, return a generic message to the client.

```python
try:
    obj = ExternalAPI.get(id)
except ExternalAPI.Error:
    logger.exception("ExternalAPI.get failed for id=%s", id)
    return JsonResponse({"error": "External service unavailable"}, status=500)
```

The same rule applies to **WebSocket error frames**, **template context** (`{"error": str(e)}` reaches the rendered page), and **AJAX/SSE bodies**.

**Representative PRs**: #929 (10 alerts across 4 files).

**Reviewer pattern**: grep for `str\(e\)`, `repr\(e\)`, `traceback\.format` — track each occurrence to a response body or template context.

---

## 4. URL-redirection (`py/url-redirection`) and path-injection (`py/path-injection`)

**Source → sink**: user-controlled URL/path → `HttpResponseRedirect(...)` or `open(...)` / `Path(...)` filesystem operation.

These are *usually* false positives in djust (Django's own `is_safe_url` covers redirect; the staticfiles handler covers path). When the FP volume gets high, **bulk-dismiss** with a written rationale rather than ignoring; the `bandit` baseline file in the project root is the canonical place.

**Real-bug indicator**: redirects that take the URL from `request.GET` without going through `is_safe_url` or an explicit allow-list. Path operations that take a filename from `request.POST` and join it directly to a base directory.

**Fix idiom**:
```python
# redirect
from django.utils.http import url_has_allowed_host_and_scheme
next_url = request.GET.get("next", "/")
if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
    next_url = "/"
return HttpResponseRedirect(next_url)
```

**Representative PRs**: #927 (URL redirect FPs dismissed), #928 (path-injection FPs dismissed), #934 (real path-injection bug fixed).

---

## 5. JS-side: `innerHTML`, `eval`, `Function()` from message data

**Source → sink**: WebSocket message data → `el.innerHTML = ...`, `eval(...)`, or `new Function(...)`.

Wire-protocol messages carry server-controlled strings, but if a malicious user can influence them (cross-tenant data leakage, malformed broadcast), an XSS goes straight to the DOM.

**Fix idiom for HTML output**: use the Rust VDOM engine's escaped path, not `innerHTML =`. For service-worker fetch handlers, validate `event.origin` against a whitelist.

**Representative PRs**: #921 (JS XSS in markdown-textarea, fixed via VDOM path), #921 (service-worker `event.origin` check added).

---

## 6. py/undefined-export — module-level `__all__` referencing items not in module

**Source → sink**: doesn't follow the data-flow shape; this is a static-analysis check that flags `__all__ = ["X"]` when `X` is not defined in the module.

**Fix idioms** (most-preferred first):

1. **Define the symbol**: import or declare it before adding to `__all__`.
2. **Wrap the import in `TYPE_CHECKING`**: when the symbol is only needed for type hints, use `if TYPE_CHECKING: from ... import X` and CodeQL accepts the conditional import.
3. **`# noqa: F822`**: only as a last resort — see Action Tracker #146 for the open follow-up to add a pre-push grep that flags new `noqa: F822` so it doesn't accumulate.

**Representative PR**: #930 (21 alerts closed via TYPE_CHECKING block).

---

## Workflow when CodeQL flags a site

1. **Read the alert + the path**. Don't fix in isolation — note the source/sink pair.
2. **Grep adjacent files for the same pattern**. If the alert is `f"...{request.X}..."` in `views/foo.py`, grep `views/*.py` for `f"`. CodeQL's incremental scan rarely catches every site at once; the same pattern usually exists in 2-5 places.
3. **Fix the flagged site + every adjacent hit you found**. If a hit is genuinely safe, comment with the reason inline (one line max).
4. **If the alert is a class FP** (urlpatterns / staticfiles / similar): add a project-level dismissal in `bandit` baseline or the appropriate CodeQL config, with a rationale comment. Don't dismiss alert-by-alert.
5. **File a tech-debt issue if the pattern recurs**: 3+ occurrences across PRs is a sign the pattern needs centralization (regex helper, shared utility, lint rule). Action Tracker #138 (Stage 11 grep-adjacent-files) tracks the broader review-time discipline.

## Cross-references

- **`docs/PULL_REQUEST_CHECKLIST.md`** — Stage 11 review framework (the canonical pre-merge security gate).
- **`CLAUDE.md` Security Rules section** — hard project requirements (no `mark_safe` on f-strings, no f-string loggers, etc).
- **Action Tracker** in `RETRO.md` — open follow-ups, including #138 (grep-adjacent bullet) and #146 (`noqa: F822` pre-push hook).
