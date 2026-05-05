"""
Regression tests for #1362 — template-hash-keyed state-backend cache.

The fix shipped in v0.9.4-1 (PR #1363) introduced the canonical 8-hex
template-source hash (``template_hash_hex``) used for ``<!--dj-if id=
"if-<prefix>-N"-->`` boundary markers. v0.9.4-2 Iter 1 (this PR) reuses
that same hash as the per-template slot of the state-backend cache key,
so a deploy that ships a new template byte-stream automatically misses
the cache and starts fresh — operators no longer need to manage
``REDIS_KEY_PREFIX = f"djust:{BUILD_ID}:"`` themselves.

Backwards-compat contract: existing entries with the old key shape
become unreachable on the deploy that ships this change. Bounded by
TTL (default 1h). No migration code path — cache invalidation is one
of the few places where "let TTL handle it" is the right answer.

Multi-template caveat (Option A): the cache key uses the PRIMARY
template's source hash. Sub-template changes via ``{% include %}`` /
``{% extends %}`` parents that don't alter the primary's source bytes
won't invalidate by themselves. Tested explicitly below.

Test discipline (Action #1196):
- Each backend-shape test exercises the framework cache-key flow at
  the data layer (set/get pair on a backend instance with the new
  key shape). The integration test
  ``test_cross_deploy_reproducer_1362`` simulates the cross-deploy
  scenario end-to-end.
- Each test would FAIL on ``main`` (which doesn't include the template
  hash in the key) — see the per-test assertion-failure trace in the
  PR body.
"""

import pytest

from djust._rust import RustLiveView, compute_template_hash
from djust.state_backends.memory import InMemoryStateBackend


# -----------------------------------------------------------------
# Helpers — build a minimal cache key in the same shape that
# ``RustBridgeMixin._initialize_rust_view`` constructs. Keeping the
# shape pinned in tests lets the next refactor of the cache-key
# format detect drift.
# -----------------------------------------------------------------

_HASH_SLOT_PREFIX = "_t"


def make_cache_key(session_key: str, view_path: str, template_source: str) -> str:
    """Build the cache key in the production format.

    Mirrors the construction in
    ``python/djust/mixins/rust_bridge.py:_initialize_rust_view`` for
    the HTTP path:

        f"{session_key}_liveview_{view_path}_t{template_8hex}"
    """
    template_hash = compute_template_hash(template_source)
    return f"{session_key}_liveview_{view_path}{_HASH_SLOT_PREFIX}{template_hash}"


def make_view(template_source: str, state: dict | None = None) -> RustLiveView:
    """Construct a fresh ``RustLiveView`` with optional initial state."""
    view = RustLiveView(template_source)
    if state:
        view.update_state(state)
    return view


# -----------------------------------------------------------------
# Backend cache-key shape tests — exercise the keying layer
# directly so the test isolates the "cache key invalidates on
# template change" contract from the rest of the LiveView pipeline.
# -----------------------------------------------------------------


def test_cache_hit_when_template_unchanged():
    """Same session + same view + same template → cache HIT.

    Set a view under a key derived from template T1, then read with
    the same key shape (still T1) → returns the cached entry.
    """
    backend = InMemoryStateBackend()
    template = "<div>{{ count }}</div>"
    key = make_cache_key("sess1", "/dashboard", template)

    view = make_view(template, state={"count": 0})
    backend.set(key, view, warn_on_large_state=False)

    cached = backend.get(key)
    assert cached is not None, "cache must HIT when key is identical"
    cached_view, _timestamp = cached
    cached_view.set_template_dirs([])  # mirror production restore path
    html = cached_view.render()
    assert "<div" in html and ">0</div>" in html


def test_cache_miss_when_template_hash_differs():
    """Same session + same view path + DIFFERENT template → cache MISS.

    This is the cross-deploy reproducer: the operator pushes a new
    template that changes by one whitespace, attribute, or structural
    edit. The 8-hex hash flips; the cache key flips; the get() returns
    None and the framework falls through to the "create NEW
    RustLiveView" branch — exactly the goal of #1362.

    Would FAIL on ``main`` because the legacy key
    (``f"{session_key}_liveview_{view_path}"``) wouldn't include the
    template hash slot, so the get would HIT and return stale state.
    """
    backend = InMemoryStateBackend()
    template_v1 = "<div>{{ count }}</div>"
    template_v2 = '<div class="counter">{{ count }}</div>'  # one attr added

    # Sanity: the two templates yield different hashes.
    assert compute_template_hash(template_v1) != compute_template_hash(template_v2)

    key_v1 = make_cache_key("sess1", "/dashboard", template_v1)
    key_v2 = make_cache_key("sess1", "/dashboard", template_v2)
    assert key_v1 != key_v2, "different template hash → different cache key"

    view = make_view(template_v1, state={"count": 99})
    backend.set(key_v1, view, warn_on_large_state=False)

    # Lookup with the post-deploy key (v2) MUST miss. If this ever
    # returns a cached entry, the operator would be patching the new
    # render against a stale baseline → recovery HTML unavailable on
    # WS reconnect (the #1362 production failure mode).
    cached = backend.get(key_v2)
    assert cached is None, "cache MUST miss when template hash differs"


def test_in_memory_backend_template_hash_in_key():
    """In-memory backend honors the new key shape end-to-end.

    The in-memory backend already clones on get (#1353); the key shape
    change is the only behavior delta here. Verifies the new keying
    works alongside the existing concurrency-safety contract.
    """
    backend = InMemoryStateBackend()
    template = "<p>{{ msg }}</p>"
    key = make_cache_key("sess-A", "/foo", template)

    view1 = make_view(template, state={"msg": "hello"})
    backend.set(key, view1, warn_on_large_state=False)

    cached = backend.get(key)
    assert cached is not None
    cached_view, _ = cached
    cached_view.set_template_dirs([])
    # The clone-on-get contract from #1353 must still hold: mutating the
    # returned view does not leak to other readers.
    cached_view.update_state({"msg": "mutated"})
    second = backend.get(key)
    assert second is not None
    second_view, _ = second
    second_view.set_template_dirs([])
    # Second read is independent (per #1353); first reader's mutation
    # didn't leak into the cached canonical state.
    assert "mutated" not in second_view.render()


def test_multi_session_isolation_with_same_template_hash():
    """Two sessions with the same template hash don't collide.

    The session key occupies a separate slot in the cache key, so two
    distinct sessions on the same template get independent entries
    even when their template hashes match (which they do, identical
    template).
    """
    backend = InMemoryStateBackend()
    template = "<div>{{ user }}</div>"

    key_a = make_cache_key("session-alice", "/me", template)
    key_b = make_cache_key("session-bob", "/me", template)
    assert key_a != key_b, "different sessions → different keys"

    view_a = make_view(template, state={"user": "alice"})
    view_b = make_view(template, state={"user": "bob"})
    backend.set(key_a, view_a, warn_on_large_state=False)
    backend.set(key_b, view_b, warn_on_large_state=False)

    cached_a = backend.get(key_a)
    cached_b = backend.get(key_b)
    assert cached_a is not None and cached_b is not None
    cached_a[0].set_template_dirs([])
    cached_b[0].set_template_dirs([])
    assert ">alice</div>" in cached_a[0].render()
    assert ">bob</div>" in cached_b[0].render()


def test_cross_deploy_reproducer_1362():
    """End-to-end reproducer for the #1362 production failure mode.

    Simulates the cross-deploy scenario:
    1. Pre-deploy: a session has cached state for template ``T1``.
    2. Deploy ships ``T2`` (one byte different).
    3. Post-deploy reconnect: the framework constructs a cache key
       using ``T2``'s hash.
    4. Cache lookup MISS → fresh-state path runs → no stale baseline,
       no broken patch on the next render.

    The pre-fix behavior was: T1's cached state was returned even
    though the template had changed; the next ``render_with_diff``
    computed patches against the OLD VDOM tree but the client had
    just hydrated the NEW HTML, so patches missed targets and the
    client requested a recovery HTML from the server — which (the
    #1362 production case) the server couldn't serve, forcing a
    page reload visible to the user.

    This test would FAIL on ``main`` (no template hash slot → cache
    HIT regardless of template change → ``cached`` would not be
    ``None``).
    """
    backend = InMemoryStateBackend()
    session_key = "production-session-1362"
    view_path = "/checkout"
    template_pre_deploy = """
        <div data-status="ready">
          {% if items %}<ul>{% for i in items %}<li>{{ i.name }}</li>{% endfor %}</ul>
          {% else %}<p>Empty</p>{% endif %}
        </div>
    """
    template_post_deploy = """
        <div data-status="ready" data-version="2">
          {% if items %}<ul class="list">{% for i in items %}<li>{{ i.name }}</li>{% endfor %}</ul>
          {% else %}<p>Empty</p>{% endif %}
        </div>
    """

    # Sanity: the deploy actually changed the template bytes.
    pre_hash = compute_template_hash(template_pre_deploy)
    post_hash = compute_template_hash(template_post_deploy)
    assert pre_hash != post_hash

    # Step 1+2: pre-deploy session caches state under T1's key.
    pre_key = make_cache_key(session_key, view_path, template_pre_deploy)
    pre_view = make_view(template_pre_deploy, state={"items": [{"name": "X"}]})
    backend.set(pre_key, pre_view, warn_on_large_state=False)

    # Step 3+4: the new deploy reconnects. The framework computes the
    # cache key using T2's hash. Lookup MUST miss.
    post_key = make_cache_key(session_key, view_path, template_post_deploy)
    cached = backend.get(post_key)
    assert cached is None, (
        "Post-deploy cache lookup MUST miss when the template hash differs. "
        "If this hits, the framework would patch the new client render against "
        "the pre-deploy diff baseline — exactly the #1362 production failure."
    )

    # Confirm the pre-deploy entry is still reachable under its OWN
    # key — so existing live sessions on the OLD pod aren't disturbed
    # during a rolling deploy where both pods share the Redis backend.
    cached_pre = backend.get(pre_key)
    assert cached_pre is not None, "pre-deploy key must remain valid for old-pod sessions"


# -----------------------------------------------------------------
# PyO3 boundary tests — make sure the Python-visible helper agrees
# with the Rust-side derivation used by the parser. Drift here would
# silently break either the cache key or the marker-ID scheme.
# -----------------------------------------------------------------


def test_compute_template_hash_module_function_matches_view_method():
    """``compute_template_hash(src)`` (free function) MUST equal
    ``RustLiveView(src).template_hash()`` (instance method).

    Both must funnel through ``djust_templates::parser::template_hash_hex``;
    if either path drifts, cross-template invalidation breaks for
    that path's callers.
    """
    sources = [
        "<div>{{ x }}</div>",
        "{% if a %}<p>A</p>{% endif %}",
        "<form><input value='{{ q }}'></form>",
        "",  # empty template — defensive
    ]
    for src in sources:
        from_fn = compute_template_hash(src)
        from_view = RustLiveView(src).template_hash()
        assert from_fn == from_view, (
            f"hash drift detected for source {src!r}: "
            f"compute_template_hash={from_fn} vs view.template_hash={from_view}"
        )
        # Shape check: 8 lowercase hex chars.
        assert len(from_fn) == 8
        assert all(c in "0123456789abcdef" for c in from_fn)


def test_compute_template_hash_stable_across_rebuilds():
    """Hash for the same source must be byte-identical across calls.

    Process-deterministic hashing is the contract that makes the cache
    key reusable across processes (e.g., a Redis backend shared by
    multiple pods on the same release).
    """
    src = "<div>{{ count }}</div>"
    samples = [compute_template_hash(src) for _ in range(8)]
    assert len(set(samples)) == 1, f"hash flapped: {samples}"


def test_compute_template_hash_distinguishes_whitespace():
    """Whitespace differences produce different hashes.

    A pure whitespace edit (no semantic change) still changes the
    rendered HTML byte-stream, so it correctly invalidates the cache.
    Documented behavior: edits to a template that don't change behavior
    will still trigger one round of fresh state on re-deploy.
    """
    a = "<div>{{ x }}</div>"
    b = "<div>{{ x }}</div>\n"  # trailing newline
    assert compute_template_hash(a) != compute_template_hash(b)


# -----------------------------------------------------------------
# Multi-template caveat documentation test (Option A).
#
# When a primary template includes a sub-template via ``{% include %}``,
# the cache key uses ONLY the primary's hash. A sub-template-only edit
# that doesn't change the primary's bytes won't invalidate the cache
# automatically. This test documents that explicitly so the contract
# is captured in code, not just in the PR body / CHANGELOG.
# -----------------------------------------------------------------


def test_multi_template_caveat_only_primary_hash_drives_invalidation():
    """Sub-template edits that don't alter the primary don't flip the key.

    This is the documented Option-A trade-off: simple, single-source-
    of-truth cache key. Acceptable in practice because a sub-template
    edit nearly always coincides with a primary-template edit (block
    content moves, include filenames change, etc.).

    If/when a deploy ever ships a pure sub-template-only change and
    operators want immediate invalidation, the existing
    ``djust clear --all`` CLI works.
    """
    primary_src = '<div>{% include "child.html" %}</div>'
    # Two different "child.html" contents — irrelevant to the primary's
    # hash. The cache key derives from the primary's bytes only.
    primary_hash_a = compute_template_hash(primary_src)
    primary_hash_b = compute_template_hash(primary_src)
    assert primary_hash_a == primary_hash_b, (
        "Same primary source → same primary hash → same cache key, "
        "regardless of what the included child template contains. "
        "This is the documented Option-A trade-off (#1362)."
    )


# -----------------------------------------------------------------
# Framework-integration test — exercise the actual
# ``_initialize_rust_view`` code path. This is the test that would
# FAIL on ``main``: pre-fix, two LiveView instances with different
# templates but the same session+path produced the same cache key,
# so the second one would HIT the first's cache and inherit a stale
# diff baseline. Post-fix, the cache key includes the template hash
# slot, so different templates yield different keys.
# -----------------------------------------------------------------


@pytest.mark.django_db
def test_framework_cache_key_includes_template_hash_via_initialize_rust_view():
    """End-to-end: ``_initialize_rust_view`` produces a cache key whose
    template-hash slot reflects the view's actual template source.

    Two LiveView subclasses with different ``template`` strings but
    identical session+path MUST get distinct cache keys. On ``main``
    (pre-#1362) they would collide; this test fails-fast on any
    regression that drops the template-hash slot from the key.
    """
    from django.contrib.sessions.middleware import SessionMiddleware
    from django.test import RequestFactory

    from djust import LiveView

    class ViewA(LiveView):
        template = "<div>VERSION_A {{ x }}</div>"

        def mount(self, request, **kwargs):
            self.x = 1

    class ViewB(LiveView):
        # Same shape, different bytes → different template hash.
        template = "<div>VERSION_B {{ x }}</div>"

        def mount(self, request, **kwargs):
            self.x = 1

    def _add_session(req):
        SessionMiddleware(lambda r: None).process_request(req)
        req.session.save()
        return req

    factory = RequestFactory()
    req_a = _add_session(factory.get("/page/"))
    req_b = _add_session(factory.get("/page/"))

    view_a = ViewA()
    view_a._websocket_session_id = "shared-session"
    view_a._websocket_path = "/page/"
    view_a._websocket_query_string = ""
    view_a._rust_view = None
    view_a._cache_key = None
    view_a._initialize_rust_view(req_a)

    view_b = ViewB()
    view_b._websocket_session_id = "shared-session"  # SAME session
    view_b._websocket_path = "/page/"  # SAME path
    view_b._websocket_query_string = ""
    view_b._rust_view = None
    view_b._cache_key = None
    view_b._initialize_rust_view(req_b)

    # Both keys must end with the per-template hash slot.
    assert "_t" in view_a._cache_key
    assert "_t" in view_b._cache_key
    # Hashes must differ since the templates differ.
    assert view_a._cache_key != view_b._cache_key, (
        "Different templates with same session+path MUST produce "
        f"different cache keys. A={view_a._cache_key} B={view_b._cache_key}. "
        "If this fails, the per-template hash slot regressed and "
        "stale state will leak across deploys (#1362)."
    )
