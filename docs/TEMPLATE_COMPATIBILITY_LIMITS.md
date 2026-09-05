# Django template compatibility boundaries

Djust implements template rendering with a Rust AST and cache. It does not expose
Django's Python AST or reproduce the contents of Django's loader caches. These are
intentional API boundaries, not unsupported template syntax.

Against Django 5.2.16, the raw upstream result is **1,032 / 1,047 (98.57%)**.
The remaining **15 cases** are listed below. They remain in the runner and its
denominator: documenting an unsupported API does not turn its failures into passes
or skips. The other 409 tests in the full label never reach the djust engine.

## Django node inspection: intentionally unsupported (8 cases)

Djust does not implement `template.nodelist`, Django node classes,
`get_nodes_by_type()`, or Python node representations. Applications needing these
APIs should use Django's template backend for that inspection.

All test identifiers below are under `template_tests`.

| Test | Django API being inspected |
|---|---|
| `syntax_tests.test_cache.CacheTests.test_cache_regression_20130` | `CacheNode.fragment_name` reached through `nodelist` |
| `test_custom.InclusionTagTests.test_no_render_side_effect` | Equality of the Python node list before and after rendering |
| `test_nodelist.NodelistTest.test_for` | Traversal for `VariableNode` inside a loop |
| `test_nodelist.NodelistTest.test_if` | Traversal for `VariableNode` inside a conditional |
| `test_nodelist.NodelistTest.test_ifchanged` | Traversal for `VariableNode` inside `ifchanged` |
| `test_nodelist.TextNodeTest.test_textnode_repr` | Exact Python `TextNode` representation |
| `tests.TemplateTests.test_node_origin` | Per-Python-node `.origin` attributes |
| `tests.DebugTemplateTests.test_node_origin` | The same node-origin API with debugging enabled |

The underlying rendering guarantees still matter. Compiled djust templates retain
an immutable native AST, with per-render state kept separately. Template origins
and runtime exception `template_debug` metadata provide filenames, lines, and
failing token excerpts without exposing a Python node tree. Repeated-render and
state-isolation tests cover those guarantees directly.

## Django loader-cache representation: intentionally unsupported (5 cases)

Djust does not populate Django's `get_template_cache` dictionary. It maintains its
own parsed-template cache, so the number, identity, and representation of objects
inside Django's cache are not part of the djust contract.

| Test | Django representation being inspected |
|---|---|
| `test_extends.ExtendsBehaviorTests.test_extend_cached` | Exact number of entries in Django's cache; its rendering assertions pass |
| `test_loaders.CachedLoaderTests.test_get_template` | The template object stored under a Django cache key |
| `test_loaders.CachedLoaderTests.test_get_template_missing_debug_off` | Caching the exception class for a missing template |
| `test_loaders.CachedLoaderTests.test_get_template_missing_debug_on` | Caching an exception instance in debug mode |
| `test_loaders.CachedLoaderTests.test_cached_exception_no_traceback` | Traceback attributes on that cached exception instance |

Missing templates still raise `TemplateDoesNotExist`. Djust's native parse cache
stores ASTs, not raised exception objects. Tests check that failed rendering does
not retain caller objects through a cached traceback. Loader origin history and
include-state isolation remain behavioral requirements: the upstream adapter
preserves the distinction between cached and explicitly uncached Django loaders,
even though Rust can reuse parsed syntax internally.

## Compiler metadata: not implemented (2 cases)

`template.extra_data`, populated by a custom compiler through `parser.extra_data`,
is not currently exposed by djust. This is a compiler-extension API limitation;
ordinary registered template tags and filters are supported. Integrations that
inspect this metadata must use Django's backend or keep their metadata separately.
This API can be reconsidered when a concrete integration needs it.

| Test | Unsupported API |
|---|---|
| `tests.TemplateTests.test_compile_tag_extra_data` | `template.extra_data` |
| `tests.DebugTemplateTests.test_compile_tag_extra_data` | The same metadata with debugging enabled |

## Behaviors that remain supported

These cases must not be classified as intentional incompatibilities:

- Assignments such as `firstof ... as name` update the caller's context when they
  survive the template's lexical scopes, including assignments before an error.
- A standalone block raises `TemplateSyntaxError` when `block.super` is evaluated;
  an unexecuted branch does not raise merely because the expression appears there.
- Separate include nodes under explicitly uncached loaders retain distinct state,
  while repeated executions of the same include node reuse its state as Django does.
- Template engine access used by Django's variable-resolution logging is available.

See [the backend guide](TEMPLATE_BACKEND.md) for the
runner, baseline, and overall compatibility measurement.
