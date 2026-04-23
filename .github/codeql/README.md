# CodeQL configuration for djust

This directory holds the CodeQL scan configuration and a custom
**MaD** (Models as Data) extension pack that teaches CodeQL about
djust-specific sanitizers.

## Files

- `codeql-config.yml` — scan config referenced from
  `.github/workflows/codeql.yml`. Controls `paths-ignore`,
  `query-filters` (rules we suppress with documented justification),
  and `packs:` (pointer to our local extension pack).
- `models/qlpack.yml` — declares a library qlpack that extends
  `codeql/python-queries`.
- `models/djust-sanitizers.model.yml` — sanitizer tuples loaded as
  data extensions into the Python taint-flow queries.

## What the sanitizer model does (#934)

`djust._log_utils.sanitize_for_log()` strips CR, LF, and other control
chars from values before they reach `logger.*` calls. Without a model,
CodeQL's `py/log-injection` query treats this helper as a no-op and
flags every call like:

```python
logger.info("user %s did X", sanitize_for_log(request.GET["user"]))
```

as a log-injection finding, forcing us to dismiss each one individually.

The data-extension tuple:

```yaml
- ["djust._log_utils", "", False, "sanitize_for_log", "", "", "Argument[0]", "ReturnValue", "log-injection"]
```

says: "for the log-injection query, the return value of
`djust._log_utils.sanitize_for_log` is a sanitizer for the value passed
as `Argument[0]`". Any log-injection alert whose flow terminates at a
call wrapped in `sanitize_for_log(...)` is then suppressed by the flow
analyzer itself rather than being emitted and later dismissed.

## Verifying

There is no way to fully verify a MaD model without running the CodeQL
CLI. The next scheduled scan (weekly, Sunday 00:00 UTC) on `main` is the
canonical verification — compare the open `py/log-injection` count
before and after this PR lands.

If the YAML syntax is rejected by the scanner, the CodeQL workflow run
will fail at the init step with a message about the extension pack.
Fix by inspecting the CLI log and consulting
https://codeql.github.com/docs/codeql-language-guides/customizing-library-models-for-python/
— the tuple shape varies slightly between CodeQL library versions.

## Fallback plan

If the MaD format does not work in production, the fallback is a
hand-written `LogInjectionFlowConfiguration` override in
`.github/codeql/queries/djust-sanitizers.ql` with an `isAdditionalSanitizer`
predicate matching calls to `djust._log_utils.sanitize_for_log`.
