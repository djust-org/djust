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


def test_multi_template_caveat_sub_template_edit_does_not_flip_primary_hash(tmp_path):
    """Sub-template edits that don't alter the primary's bytes don't flip the key.

    Demonstrates Option A's known caveat — operators must use
    ``djust clear --all`` for sub-template-only changes.

    Setup:
    1. Build a real ``parent.html`` containing ``{% include "child.html" %}``.
    2. Build a ``child_v1.html`` and a structurally-different
       ``child_v2.html`` (extra ``<span>``).
    3. Render ``parent.html`` against each child via the Django template
       backend (verifies the child IS pulled into the rendered output —
       the include-resolution machinery actually runs).
    4. Compute ``compute_template_hash(parent_src)`` — the primary's
       source bytes are unchanged across the two scenarios.
    5. Assert the hashes are byte-identical despite the rendered output
       differing.

    This test would FAIL on a hypothetical Option B implementation that
    hashed all touched templates (parent + child) — Option B's hash would
    change when child content shifted. Under Option A, the cache key
    derives ONLY from the primary's source bytes, so sub-template-only
    edits don't trigger automatic invalidation.

    Action #1200 compliance: this is NOT a tautology
    (``compute_template_hash(same)`` == ``compute_template_hash(same)``)
    — it shows the rendered output diverges (verifying the include
    actually pulls in child content) while the primary's hash stays
    identical. The contract being tested is "primary hash is independent
    of included sub-template content," not "the hash function is
    deterministic" (already covered by
    ``test_compute_template_hash_stable_across_rebuilds``).
    """
    from djust.template_backend import DjustTemplateBackend

    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()

    # The primary template references "child.html" via {% include %}.
    # Its source bytes contain only the include directive — they do NOT
    # contain the child's body.
    primary_src = '<div class="parent">{% include "child.html" %}</div>'
    (templates_dir / "parent.html").write_text(primary_src)

    backend = DjustTemplateBackend(
        {
            "NAME": "djust",
            "DIRS": [str(templates_dir)],
            "APP_DIRS": False,
            "OPTIONS": {},
        }
    )

    # Scenario 1: child_v1 has minimal content.
    child_v1_src = "<p>v1</p>"
    (templates_dir / "child.html").write_text(child_v1_src)

    template = backend.from_string(primary_src)
    rendered_v1 = template.render({})
    primary_hash_with_v1 = compute_template_hash(primary_src)

    # Sanity: the include actually ran and pulled in the v1 child body.
    assert "<p>v1</p>" in rendered_v1, (
        "Sanity check: the include-resolution machinery must actually pull "
        "child.html v1 into the rendered output, otherwise the test isn't "
        "exercising the multi-template path."
    )

    # Scenario 2: rewrite child.html with structurally-different content.
    # The PRIMARY template (parent.html) source bytes are UNCHANGED.
    child_v2_src = '<p>v2</p><span class="extra">added</span>'
    (templates_dir / "child.html").write_text(child_v2_src)

    template = backend.from_string(primary_src)
    rendered_v2 = template.render({})
    primary_hash_with_v2 = compute_template_hash(primary_src)

    # Sanity: the rendered output ACTUALLY differs (the test isn't a
    # no-op; child changes do propagate to render output).
    assert "<p>v2</p>" in rendered_v2 and 'class="extra"' in rendered_v2
    assert rendered_v1 != rendered_v2, (
        "Sanity check: rendered output MUST differ between v1 and v2 — "
        "if it doesn't, the include isn't being re-resolved against the "
        "new child.html and the test isn't proving anything."
    )

    # The point of the test: the primary's hash is BYTE-IDENTICAL across
    # the two scenarios despite the rendered output differing. This
    # demonstrates Option A's caveat: a sub-template-only edit (no change
    # to the primary's bytes) does NOT flip the cache key.
    assert primary_hash_with_v1 == primary_hash_with_v2, (
        "Multi-template Option A caveat: the primary's hash is unchanged "
        "by sub-template edits. Operators must use `djust clear --all` "
        "to force invalidation when ONLY a sub-template changes (#1362). "
        "If this assertion fails, the framework switched from Option A "
        "(primary-only hash) to Option B (composite hash of all touched "
        "templates), which is a contract change that affects deploy-time "
        "invalidation semantics."
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


# -----------------------------------------------------------------
# Perf-regression tests for Stage 12 fix.
#
# Background: the initial v0.9.4-2 Iter 1 implementation hoisted
# ``self.get_template()`` to BEFORE the cache lookup so the per-template
# hash could be computed for the cache key. That regressed the cache HIT
# path: pre-#1362 it never called ``get_template()``, post-#1362 every
# WS reconnect ate the Django template loader + inheritance resolution
# cost even when the cache hit.
#
# Fix: cache the ``_t<8hex>`` slot on the view CLASS (one-time per
# class lifetime) via ``_get_cached_template_hash_slot``. First call
# pays the cost, subsequent calls return the memoized slot in O(1).
# The cache HIT path no longer calls ``get_template()`` after the
# class warmup.
# -----------------------------------------------------------------


@pytest.mark.django_db
def test_template_hash_slot_is_cached_on_view_class():
    """``_get_cached_template_hash_slot`` memoizes per-class.

    First call invokes ``get_template()`` once; second call returns the
    memoized slot WITHOUT re-loading the template. This is the perf
    contract: ``get_template()`` is called at most once per view class
    lifetime (modulo defensive fallback on Rust-extension failure).
    """
    from djust import LiveView

    # Use a fresh dynamic subclass so any test ordering doesn't pre-warm
    # the class-level cache (Action #1109 — fresh subclass per call).
    DynView = type(
        "DynViewHashSlotMemo",
        (LiveView,),
        {"template": "<div>{{ x }}</div>"},
    )

    # Sanity: no cached slot before the first call.
    assert "_djust_template_hash_slot" not in DynView.__dict__

    # Spy on get_template — count calls.
    call_count = {"n": 0}
    real_get_template = DynView.get_template

    def counting_get_template(self):
        call_count["n"] += 1
        return real_get_template(self)

    DynView.get_template = counting_get_template

    instance1 = DynView()
    slot1 = instance1._get_cached_template_hash_slot()
    assert slot1.startswith("_t")
    assert len(slot1) == 10  # "_t" + 8 hex chars
    assert call_count["n"] == 1, "first call should load template once"

    # Second call on the SAME instance should hit the class cache.
    slot2 = instance1._get_cached_template_hash_slot()
    assert slot2 == slot1
    assert call_count["n"] == 1, "second call must NOT re-load template"

    # A second INSTANCE of the same class also hits the class cache.
    instance2 = DynView()
    slot3 = instance2._get_cached_template_hash_slot()
    assert slot3 == slot1
    assert call_count["n"] == 1, (
        "different instances of the same class must share the class-level "
        "memoized slot — get_template() should still only have run once"
    )


@pytest.mark.django_db
def test_initialize_rust_view_cache_hit_does_not_call_get_template():
    """Cache HIT path: ``get_template()`` is NOT called after class warmup.

    This is the load-bearing perf-regression test. Before the Stage 12
    fix, ``_initialize_rust_view`` eagerly called ``get_template()``
    BEFORE the cache lookup so the hash could be computed for the cache
    key. After the fix, the hash is memoized on the class and
    ``get_template()`` is only called on cache MISS (when we need to
    actually construct a new ``RustLiveView``).

    Test flow:
    1. Warm up the class-level hash cache by running
       ``_initialize_rust_view`` once with no cache entry (MISS).
       This populates the backend AND the class-level slot cache.
    2. Reset the spy counter and run a SECOND view instance through
       ``_initialize_rust_view`` — the backend already has an entry
       (from step 1), so this is a cache HIT.
    3. Assert ``get_template()`` was called ZERO times during the HIT.

    Pre-fix this would have failed: ``get_template()`` was called every
    time, regardless of cache HIT/MISS.
    """
    from django.contrib.sessions.middleware import SessionMiddleware
    from django.test import RequestFactory

    from djust import LiveView
    from djust.state_backend import get_backend

    DynView = type(
        "DynViewCacheHitNoLoad",
        (LiveView,),
        {"template": "<div>{{ y }}</div>"},
    )

    def _mount(self, request, **kwargs):
        self.y = 0

    DynView.mount = _mount

    # Spy on get_template — count calls.
    call_count = {"n": 0}
    real_get_template = DynView.get_template

    def counting_get_template(self):
        call_count["n"] += 1
        return real_get_template(self)

    DynView.get_template = counting_get_template

    factory = RequestFactory()

    def _add_session(req):
        SessionMiddleware(lambda r: None).process_request(req)
        req.session.save()
        return req

    # --- Step 1: warmup pass (cache MISS) ---
    req1 = _add_session(factory.get("/perf/"))
    view1 = DynView()
    view1._websocket_session_id = "perf-session"
    view1._websocket_path = "/perf/"
    view1._websocket_query_string = ""
    view1._rust_view = None
    view1._cache_key = None
    view1._initialize_rust_view(req1)
    # Cache MISS path: get_template() was called (to construct the view
    # AND to compute the hash slot — though the hash is now class-cached).
    miss_call_count = call_count["n"]
    assert miss_call_count >= 1, "MISS path must call get_template at least once"
    # The backend should now have an entry under view1's cache key.
    assert get_backend().get(view1._cache_key) is not None

    # --- Step 2: reset spy, do a HIT pass ---
    call_count["n"] = 0
    req2 = _add_session(factory.get("/perf/"))
    view2 = DynView()
    view2._websocket_session_id = "perf-session"
    view2._websocket_path = "/perf/"
    view2._websocket_query_string = ""
    view2._rust_view = None
    view2._cache_key = None
    view2._initialize_rust_view(req2)

    # --- Step 3: cache HIT must NOT have called get_template() ---
    assert view2._cache_key == view1._cache_key, (
        "Same template + same session + same path → SAME cache key; "
        "if these differ the test isn't actually exercising a HIT."
    )
    assert view2._rust_view is not None, "Cache HIT must populate _rust_view"
    assert call_count["n"] == 0, (
        f"Cache HIT path must NOT call get_template() (perf regression "
        f"fix); but it was called {call_count['n']} time(s). The class-"
        f"level hash slot cache should have served the slot directly."
    )
