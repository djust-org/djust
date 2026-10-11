"""The per-handler ``@rate_limit`` is enforced on the page-POST fallback too.

F27 unified the per-handler ``@rate_limit`` onto one shared per-caller store and
stated the invariant as "ONE ``@rate_limit`` budget per handler regardless of
connection count or **transport**". Three transports were wired to it — the
WebSocket/SSE chokepoint (``websocket_utils._validate_event_security``) and the
HTTP API (``api/dispatch._rate_limit_check``) — and the page-POST fallback
(``mixins/request.post``) was missed, so a handler throttled everywhere else
accepted unbounded calls over POST.

These tests drive the real ``post()`` path (the same harness
``python/tests/test_http_fallback.py`` uses: a real session attached to the
request, plus an initial ``get()`` to establish state) rather than calling the
check helper directly, so they fail if the wiring is removed even though the
helper still works.
"""

from __future__ import annotations

import json
import sys

import pytest
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

from djust import LiveView
from djust.components.descriptors.base import LiveComponent, TypedState
from djust.decorators import event_handler, rate_limit
from djust.rate_limit import reset_handler_buckets


def _add_session(request):
    """Attach a real session so ``caller_key`` resolves to ``session:<key>``."""
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    return request


@pytest.fixture(autouse=True)
def _clear_shared_store():
    """Each test starts with an empty shared per-caller bucket store."""
    reset_handler_buckets()
    yield
    reset_handler_buckets()


class _PostFallbackThrottled(LiveView):
    """A view whose handler declares ``@rate_limit(rate=1, burst=2)``.

    ``rate=1, burst=2`` makes the accepted count deterministic inside one test
    window: a token refills per second, so at most 3 calls could ever be
    admitted by accident, and only 2 within the assertion's own duration.
    """

    template = (
        '<div dj-view="djust.tests.test_ratelimit_post_fallback._PostFallbackThrottled" '
        'dj-id="0">x</div>'
    )

    def mount(self, request, **kwargs):
        self.calls = 0

    @event_handler()
    @rate_limit(rate=1, burst=2)
    def expensive_send(self, **kwargs):
        self.calls += 1


class _PostFallbackAliased(LiveView):
    """One callable reachable under two names, both with @rate_limit.

    ``send_alias`` is the *same function object* as ``expensive_send`` (bound in
    the class body), so this is one handler with two routes to it — the shape
    that let a caller multiply the limit by using both names.
    """

    template = (
        '<div dj-view="djust.tests.test_ratelimit_post_fallback._PostFallbackAliased" '
        'dj-id="0">x</div>'
    )

    def mount(self, request, **kwargs):
        self.calls = 0

    @event_handler()
    @rate_limit(rate=1, burst=2)
    def expensive_send(self, **kwargs):
        self.calls += 1

    send_alias = expensive_send


def _make_limited(burst):
    """A handler factory — two calls produce two DISTINCT functions.

    Both carry the SAME ``__qualname__`` (``_make_limited.<locals>.handler``),
    because a qualname is a lexical label, not a unique function identity. A key
    derived from the name therefore collides these two; an identity-based key
    does not. ``rate`` is effectively zero so no refill lands inside the test
    window and the admitted count is deterministic.
    """

    @event_handler()
    @rate_limit(rate=0.01, burst=burst)
    def handler(self, **kwargs):
        self.calls += 1

    return handler


class _PostFallbackFactoryCollision(LiveView):
    """Two distinct factory-made handlers whose names collide lexically.

    ``cheap`` is deliberately the LOOSER limit and is exercised first: under a
    name-keyed bucket the stricter ``sensitive`` handler inherits ``cheap``'s
    settings, so the stricter limit is not just shared but WEAKENED.
    """

    template = (
        '<div dj-view="djust.tests.test_ratelimit_post_fallback.'
        '_PostFallbackFactoryCollision" dj-id="0">x</div>'
    )

    def mount(self, request, **kwargs):
        self.calls = 0

    cheap = _make_limited(10)
    sensitive = _make_limited(2)


# The two descriptor fixtures below mint a decorated handler ON GET. ADR-037's
# discovery parity test enumerates every LiveView subclass in the process and
# compares the dir walk (which INVOKES properties) against published handlers, so
# a live minting fixture creates exactly the discrepancy that test guards - and a
# class built dynamically inside a test is still alive when a later test in the
# same process runs.
#
# So the minting is gated, and what the gate does is narrower than "the class is
# skipped" - that was my first description and it is wrong. With the flag off the
# property raises AttributeError, and _dir_walk_metadata catches
# (AttributeError, TypeError) PER MEMBER inside its loop, so the member is absent
# from the walk's result. It is absent from published_handlers too, so the two sides
# agree and the parity assertion holds. The class is still compared, no exception
# escapes to the caller, and only the tests that own the fixtures switch the flag on
# (with try/finally, so a failure cannot leave minting enabled for later tests).
_MINTING_ENABLED = False


def _make_dynamic_descriptor_view():
    """Build the property-minting view DYNAMICALLY, per call.

    Building it here keeps the fixture out of this module's namespace, but that is
    NOT sufficient on its own: a class created inside a test is still alive when a
    later test in the same process runs, so ADR-037's parity test still sees it and
    still fails on the discrepancy - which is exactly what happened. The
    ``_MINTING_ENABLED`` gate is what actually keeps that test green; see the note
    on the flag for what the gate does and does not do.
    """

    class _DynamicDescriptorView(LiveView):
        """A handler behind a ``property`` that mints a fresh decorated function.

        The closure is an ordinary ``FunctionType`` with the decorator metadata, so
        it passes every check made on the RESOLVED object - yet it is a different
        object on each access, so keying on it would put every call in its own
        bucket and apply no limit at all.
        """

        template = (
            '<div dj-view="djust.tests.test_ratelimit_post_fallback.'
            '_DynamicDescriptorView" dj-id="0">x</div>'
        )

        def mount(self, request, **kwargs):
            self.calls = 0

        @property
        def fresh_wrapper(self):
            if not _MINTING_ENABLED:
                raise AttributeError("fresh_wrapper mints only inside its own test")
            owner = self

            # No ``self`` parameter ON PURPOSE. The page-POST path resolves this as
            # an INSTANCE attribute and invokes handler(**kwargs), so a minted
            # function that expects self raises TypeError and answers 500 for every
            # call. "Zero admissions" would then pass while proving only that the
            # invocation failed - not that the rate limit refused anything - which is
            # exactly the tautology this fixture had. The owner is captured instead,
            # so the handler is genuinely runnable through the real path.
            @event_handler()
            @rate_limit(rate=0.01, burst=2)
            def minted(**kwargs):
                owner.calls += 1

            return minted

    return _DynamicDescriptorView


class _MintingDescriptor:
    """A NON-data descriptor - the shape that separates provenance from ``getattr``.

    ``property`` is a *data* descriptor, so ``getattr(SomeClass, name)`` returns
    the property object itself without invoking it. A non-data descriptor has no
    ``__set__``, so a plain ``getattr`` on the class DOES invoke ``__get__`` and
    hands back a freshly minted function - indistinguishable from a stable method
    by any check on the resolved object. Only reaching the declaration
    (``getattr_static``) sees this object and refuses it.

    Safe at module scope: it is a plain descriptor, not a ``LiveView`` subclass, so
    discovery never enumerates it.
    """

    def __get__(self, obj, objtype=None):
        if not _MINTING_ENABLED:
            raise AttributeError("minted mints only inside its own test")

        @event_handler()
        @rate_limit(rate=0.01, burst=2)
        def minted(self, **kwargs):
            if obj is not None:
                obj.calls += 1

        return minted


def _make_minting_descriptor_view():
    """Build the non-data-descriptor view dynamically; see the note above."""

    class _MintingDescriptorView(LiveView):
        """A handler behind a non-data descriptor that mints a fresh function."""

        template = (
            '<div dj-view="djust.tests.test_ratelimit_post_fallback.'
            '_MintingDescriptorView" dj-id="0">x</div>'
        )

        def mount(self, request, **kwargs):
            self.calls = 0

        minted = _MintingDescriptor()

    return _MintingDescriptorView


class _ThrottledCounter(LiveComponent):
    """An ADR-031 class-level component whose handler carries @rate_limit."""

    class State(TypedState):
        active: bool = False

    @event_handler()
    @rate_limit(rate=0.01, burst=2)
    def toggle(self, **kwargs):
        self.state.active = not self.state.active


class _ComponentHost(LiveView):
    """A view hosting the component — events route through ``component_id``."""

    template = (
        '<div dj-view="djust.tests.test_ratelimit_post_fallback._ComponentHost" '
        'dj-id="0">{{ counter }}</div>'
    )

    counter = _ThrottledCounter(active=False)


class _ShadowedAliasHost(LiveView):
    """Declares the alias name as its OWN method, then shadows it with the real one.

    ``send_alias`` is declared as a distinct decorated method, and ``mount``
    rebinds it on the instance to ``expensive_send``. The callable that actually
    runs under the alias name is therefore ``expensive_send`` — so a key derived
    from the *declaration* names a method nothing will execute, and one running
    callable ends up with two budgets.
    """

    template = (
        '<div dj-view="djust.tests.test_ratelimit_post_fallback._ShadowedAliasHost" '
        'dj-id="0">x</div>'
    )

    def mount(self, request, **kwargs):
        self.calls = 0
        self.send_alias = self.expensive_send  # shadow the declaration

    @event_handler()
    @rate_limit(rate=0.01, burst=2)
    def expensive_send(self, **kwargs):
        self.calls += 1

    @event_handler()
    @rate_limit(rate=0.01, burst=2)
    def send_alias(self, **kwargs):
        self.calls += 1


class _PostFallbackUnthrottled(LiveView):
    """A view whose handler declares no ``@rate_limit`` — must stay callable."""

    template = (
        '<div dj-view="djust.tests.test_ratelimit_post_fallback._PostFallbackUnthrottled" '
        'dj-id="0">x</div>'
    )

    def mount(self, request, **kwargs):
        self.calls = 0

    @event_handler()
    def cheap_read(self, **kwargs):
        self.calls += 1


# The view classes are referenced by name inside their own ``dj-view`` template,
# so the module must expose them.
setattr(sys.modules[__name__], "_ThrottledCounter", _ThrottledCounter)
setattr(sys.modules[__name__], "_ComponentHost", _ComponentHost)
setattr(sys.modules[__name__], "_PostFallbackThrottled", _PostFallbackThrottled)
setattr(sys.modules[__name__], "_PostFallbackAliased", _PostFallbackAliased)
setattr(sys.modules[__name__], "_ShadowedAliasHost", _ShadowedAliasHost)
setattr(sys.modules[__name__], "_PostFallbackUnthrottled", _PostFallbackUnthrottled)


def _drive_post(view, factory, session_request, event, n):
    """POST ``event`` n times through the real fallback.

    Returns ``(ran, statuses)`` where ``ran`` counts the 200s — the calls the
    server admitted.
    """
    ran = 0
    statuses = []
    for _ in range(n):
        request = factory.post(
            "/test/",
            data="{}",
            content_type="application/json",
            HTTP_X_DJUST_EVENT=event,
        )
        request.session = session_request.session
        response = view.post(request)
        statuses.append(response.status_code)
        if response.status_code == 200:
            ran += 1
    return ran, statuses


def _fresh(request_cls):
    factory = RequestFactory()
    session_request = _add_session(factory.get("/test/"))
    view = request_cls()
    view.get(session_request)
    return view, factory, session_request


@pytest.mark.django_db
def test_rate_limit_is_enforced_on_the_post_fallback():
    """burst=2: calls 3..6 must be refused, not run."""
    view, factory, session_request = _fresh(_PostFallbackThrottled)

    ran, statuses = _drive_post(view, factory, session_request, "expensive_send", 6)

    assert ran == 2, (
        "the shared per-handler budget must cap the page-POST fallback at burst "
        f"(2); admitted {ran}/6 (statuses={statuses})"
    )
    # The handler ran exactly as often as the server admitted, so the 200s are
    # not an artefact of something else answering.
    assert view.calls == ran
    assert statuses[-1] == 429
    assert statuses[-4:] == [429, 429, 429, 429]


@pytest.mark.django_db
def test_the_post_fallback_budget_is_the_same_bucket_as_the_http_api():
    """The F27 invariant: one budget per (caller, handler) across transports.

    Burn the burst over POST, then ask the HTTP API's check for the same caller
    and handler. If the two transports kept separate buckets, the API would
    still admit calls — which is exactly the "summing across transports" defect
    F27 removed.
    """
    from djust.api.dispatch import _rate_limit_check

    view, factory, session_request = _fresh(_PostFallbackThrottled)

    ran, _ = _drive_post(view, factory, session_request, "expensive_send", 2)
    assert ran == 2, "the burst should have been admitted over POST"

    handler = _PostFallbackThrottled.expensive_send
    assert not _rate_limit_check(session_request, view, "expensive_send", handler), (
        "the HTTP API must see the budget the POST path just consumed — a "
        "separate bucket means a caller can sum allowances across transports"
    )


@pytest.mark.django_db
def test_a_distinct_caller_gets_its_own_budget():
    """The cap is per caller, not a global switch: a second caller is admitted."""
    view, factory, session_request = _fresh(_PostFallbackThrottled)
    ran_first, _ = _drive_post(view, factory, session_request, "expensive_send", 4)
    assert ran_first == 2

    other_view, other_factory, other_session = _fresh(_PostFallbackThrottled)
    ran_second, _ = _drive_post(other_view, other_factory, other_session, "expensive_send", 2)

    assert ran_second == 2, (
        "a second caller must get the full burst; a shared bucket would be a "
        "mutual-denial bug rather than a per-caller limit"
    )


def test_handler_identity_coalesces_aliases_and_separates_distinct_functions():
    """The function that RUNS is the key, provided it is a declaration."""
    from djust.rate_limit import handler_identity

    aliased = _PostFallbackAliased()
    throttled = _PostFallbackThrottled()

    assert handler_identity(aliased, aliased.expensive_send) == handler_identity(
        aliased, aliased.send_alias
    ), "two names for one function must share one identity, or each gets a budget"
    assert handler_identity(aliased, aliased.expensive_send) != handler_identity(
        throttled, throttled.expensive_send
    ), "distinct functions must NOT share an identity, or they share one budget"
    collision = _PostFallbackFactoryCollision()
    assert type(collision).cheap.__qualname__ == type(collision).sensitive.__qualname__, (
        "the fixture must actually collide lexically, or this test proves nothing"
    )
    assert handler_identity(collision, collision.cheap) != handler_identity(
        collision, collision.sensitive
    ), "factory-made functions collide by name but must NOT by identity"
    assert handler_identity(aliased, aliased.expensive_send) == handler_identity(
        aliased, aliased.expensive_send
    )


def test_a_dynamic_descriptor_is_refused_rather_than_keyed_on_the_resolved_object():
    """A per-access wrapper is a FunctionType, so it must be caught by provenance.

    ``dyn`` is a property returning a freshly decorated function on every access.
    Keying on the resolved object would give every call its own bucket and apply
    NO limit, while looking correct — the closure is an ordinary function. The
    identity therefore comes from the declaration, which is the property, and a
    property is refused.
    """
    from djust.rate_limit import UNKEYABLE, handler_identity, handler_rate_check

    global _MINTING_ENABLED
    previous = _MINTING_ENABLED
    _MINTING_ENABLED = True
    try:
        view = _make_dynamic_descriptor_view()()
        resolved = view.fresh_wrapper
        assert callable(resolved), "fixture sanity: the property must return a callable"
        assert view.fresh_wrapper is not resolved, "fixture sanity: it must be minted per access"

        assert handler_identity(view, view.fresh_wrapper) is UNKEYABLE, (
            "a descriptor that mints a callable per access must not be keyed"
        )
        assert handler_rate_check("c", UNKEYABLE, {"rate": 1, "burst": 2}) is False

        # The non-data descriptor is the case a plain getattr CANNOT catch:
        # accessing it on the class invokes __get__ and yields a fresh function, so
        # provenance must reach the declaration to see the descriptor at all.
        minting = _make_minting_descriptor_view()()
        assert callable(type(minting).minted), "fixture sanity: getattr mints a callable"
        assert type(minting).minted is not type(minting).minted, "fixture sanity: per access"
        assert handler_identity(minting, minting.minted) is UNKEYABLE, (
            "a non-data descriptor that mints per access must not be keyed"
        )
    finally:
        _MINTING_ENABLED = previous


@pytest.mark.django_db
def test_one_callable_under_two_names_shares_one_budget_over_post():
    """burst=2 split across an alias must total 2, not 2 per name."""
    view, factory, session_request = _fresh(_PostFallbackAliased)

    first, _ = _drive_post(view, factory, session_request, "expensive_send", 2)
    alias_ran, alias_statuses = _drive_post(view, factory, session_request, "send_alias", 2)

    assert first == 2, "the burst should have been admitted under the first name"
    assert alias_ran == 0, (
        "the alias must consume the SAME budget: a callable answering to two "
        f"names must not get a burst per name (alias admitted {alias_ran}/2, "
        f"statuses={alias_statuses})"
    )
    assert view.calls == 2, f"the handler must have run twice total, not 4; ran {view.calls}"


@pytest.mark.django_db
def test_one_callable_under_two_names_shares_one_budget_across_post_and_api():
    """The cross-transport form of the same defect: POST one name, API the other."""
    from djust.api.dispatch import _rate_limit_check

    view, factory, session_request = _fresh(_PostFallbackAliased)

    ran, _ = _drive_post(view, factory, session_request, "expensive_send", 2)
    assert ran == 2, "the burst should have been admitted over POST"

    assert not _rate_limit_check(
        session_request, view, "send_alias", _PostFallbackAliased.send_alias
    ), "the API must see the alias as the same handler and the same spent budget"


@pytest.mark.django_db
def test_factory_made_handlers_do_not_share_a_budget():
    """Two distinct functions from one factory share a NAME but not an identity.

    Under a name-derived key (module+qualname) both resolve to
    ``_make_limited.<locals>.handler`` and collide; because a bucket keeps the
    settings it was created with, the collision lets the second handler inherit
    the first's. ``cheap`` (burst=10) runs first and ``sensitive`` (burst=2) must
    still be capped at 2 — the stricter limit must not be WEAKENED.
    """
    view, factory, session_request = _fresh(_PostFallbackFactoryCollision)

    cheap_ran, _ = _drive_post(view, factory, session_request, "cheap", 12)
    assert cheap_ran == 10, "the looser handler should admit its own burst of 10"

    strict_ran, strict_statuses = _drive_post(view, factory, session_request, "sensitive", 6)
    assert strict_ran == 2, (
        "the stricter handler must keep its OWN burst of 2; inheriting the looser "
        f"handler's bucket would admit up to 10 (admitted {strict_ran}/6, "
        f"statuses={strict_statuses})"
    )
    assert view.calls == 12, f"10 looser + 2 stricter expected; ran {view.calls}"


@pytest.mark.django_db
def test_alternating_loose_and_strict_never_mints_a_fresh_burst():
    """Repeated alternation across two limits must not create budget.

    Guards the fix against the tempting shortcut of keying on the settings (which
    would mint one budget per settings value) or of resetting the bucket on a
    settings mismatch (which would hand out a fresh burst each switch).
    """
    view, factory, session_request = _fresh(_PostFallbackFactoryCollision)

    cheap_total = strict_total = 0
    for _ in range(4):
        cheap_ran, _ = _drive_post(view, factory, session_request, "cheap", 3)
        strict_ran, _ = _drive_post(view, factory, session_request, "sensitive", 3)
        cheap_total += cheap_ran
        strict_total += strict_ran

    assert strict_total == 2, (
        f"the stricter limit must hold across every alternation; admitted {strict_total}"
    )
    assert cheap_total == 10, (
        f"the looser limit must not gain budget from alternating; admitted {cheap_total}"
    )
    assert view.calls == 12


def test_an_unkeyable_handler_shape_fails_closed():
    """Shapes with no reliable declaration identity are refused, not bucketed.

    Bucketing one would be worse than refusing: a callable instance may define
    ``__eq__``/``__hash__`` that collapse two distinct instances into one budget
    (or be unhashable), and a name that is not declared on the class at all is
    instance state the view may rewrite between calls.
    """
    import functools

    from djust.rate_limit import UNKEYABLE, handler_identity, handler_rate_check

    class CallableInstance:
        def __call__(self, **kwargs):
            pass

    def plain():
        pass

    class Owner:
        # A callable instance as a declared member: hashable-or-not, collapsing
        # __eq__/__hash__ cannot be ruled out, so it is not accepted.
        as_instance = CallableInstance()
        as_partial = functools.partial(plain)
        real = staticmethod(plain)

    owner = Owner()
    for shape in (owner.as_instance, owner.as_partial):
        assert handler_identity(owner, shape) is UNKEYABLE, (
            f"{shape!r} must not be keyed — it is not a declaration of this owner"
        )
    # A staticmethod IS a stable declaration and must be unwrapped, not refused.
    assert handler_identity(owner, owner.real) is plain

    assert handler_rate_check("caller", UNKEYABLE, {"rate": 1, "burst": 5}) is False, (
        "an unkeyable shape must fail closed, not be bucketed"
    )


@pytest.mark.django_db
def test_a_per_access_wrapper_handler_is_refused_over_the_real_post_path():
    """The measured case, at the real path: burst=2 must not admit six calls.

    The wrapper is a fresh FunctionType each access, so a check on the resolved
    object accepts it and a per-access bucket would let every call through. The
    declaration is a property, which is refused, so no call is admitted.
    """
    global _MINTING_ENABLED
    previous = _MINTING_ENABLED
    _MINTING_ENABLED = True
    try:
        view, factory, session_request = _fresh(_make_dynamic_descriptor_view())
        ran, statuses = _drive_post(view, factory, session_request, "fresh_wrapper", 6)
    finally:
        _MINTING_ENABLED = previous

    assert ran == 0, (
        "a per-access wrapper must not be keyed and admitted — every call would "
        f"land in its own bucket and the limit would not bind (admitted {ran}/6, "
        f"statuses={statuses})"
    )
    # The STATUS matters as much as the count: a refusal is 429. A 500 would mean
    # the handler could not be invoked at all, and then "ran == 0" would hold with
    # the refusal disabled too - a tautology rather than a real pin.
    assert statuses == [429] * 6, (
        f"every call must be refused with 429, not fail on invocation: {statuses}"
    )
    assert view.calls == 0, f"the handler must not have run at all; ran {view.calls}"


def test_a_settings_mismatch_fails_closed_without_resetting_tokens():
    """Same key, different settings: refuse — and leave the consumed budget intact.

    Resetting would hand out a fresh burst; keying on the settings would let
    alternating limits mint a budget per value.
    """
    from djust.rate_limit import handler_rate_check

    def stable_handler():
        pass

    # Rate ~0 so no refill lands inside the test window; burst=2. The bucket is
    # deliberately left with ONE token, so the mismatch call is distinguishable:
    # with the guard it is refused while a token remains, without it the call
    # would spend that token and return True (which is the shadowing this test
    # exists to catch — an exhausted-bucket fixture would pass either way).
    strict = {"rate": 0.01, "burst": 2}
    assert handler_rate_check("c", stable_handler, strict) is True

    assert handler_rate_check("c", stable_handler, {"rate": 0.01, "burst": 99}) is False, (
        "a settings mismatch must fail closed even though a token remains"
    )
    # The mismatch must NOT have consumed or reset anything: the token is intact
    # under the original settings, and is spent by this call.
    assert handler_rate_check("c", stable_handler, strict) is True, (
        "the mismatch path must leave consumed budget intact, not reset the bucket"
    )
    assert handler_rate_check("c", stable_handler, strict) is False, "budget now exhausted"


@pytest.mark.django_db
def test_a_handler_without_a_rate_limit_is_unaffected():
    """No regression for the ordinary case: no decorator, no cap."""
    view, factory, session_request = _fresh(_PostFallbackUnthrottled)

    ran, statuses = _drive_post(view, factory, session_request, "cheap_read", 6)

    assert ran == 6, f"an undecorated handler must not be throttled; got {statuses}"


def _drive_component_post(view, factory, session_request, component_id, event, n):
    """POST a COMPONENT event — the body carries ``component_id`` to route it."""
    ran = 0
    statuses = []
    for _ in range(n):
        request = factory.post(
            "/test/",
            data=json.dumps({"component_id": component_id}),
            content_type="application/json",
            HTTP_X_DJUST_EVENT=event,
        )
        request.session = session_request.session
        response = view.post(request)
        statuses.append(response.status_code)
        if response.status_code == 200:
            ran += 1
    return ran, statuses


@pytest.mark.django_db
def test_an_adr031_component_handler_is_limited_not_refused():
    """A component handler must be THROTTLED, not refused outright.

    ADR-031 makes ``view.counter`` a per-view ``BoundComponent`` proxy; the
    handler is declared on the class behind its descriptor
    (``type(owner._descriptor)``), not on ``type(owner)``. A declaration lookup
    that only tried ``type(owner)`` found nothing and returned UNKEYABLE, so
    EVERY call was refused — six 429s including the first, which is a functional
    regression (the handler became unusable) rather than a stricter limit. The
    assertion that distinguishes the two is that the first two calls SUCCEED.
    """
    view, factory, session_request = _fresh(_ComponentHost)
    assert getattr(view, "_components", None), (
        "fixture sanity: rendering must register the bound component in "
        "view._components, or the event never routes to it"
    )

    ran, statuses = _drive_component_post(view, factory, session_request, "counter", "toggle", 6)

    assert ran == 2, (
        "a component handler must be admitted up to its burst and then throttled "
        f"(got {ran}/6, statuses={statuses}); six 429s would mean it was refused, "
        "not limited"
    )
    assert statuses[:2] == [200, 200], f"the first two calls must succeed: {statuses}"
    assert statuses[2:] == [429, 429, 429, 429], f"then throttle: {statuses}"


def test_an_unrelated_descriptor_attribute_does_not_decide_a_handler_identity():
    """A ``_descriptor`` on an ORDINARY owner must not be followed.

    The alias is set on the INSTANCE, which is what makes the hop observable: a
    class-level alias is declared on the owner and is found without any hop. With
    the hop unrestricted, the lookup for the instance alias fell through to the
    unrelated class's same-named function — a second declaration, so the alias and
    its target would each hold a budget.

    The correct outcome is NOT refusal, and not a second budget either: the
    instance alias resolves to the very function its target declares, so it shares
    that function's budget. Refusing would make a legitimate alias unusable;
    diverting gives it its own budget.
    """
    from djust.rate_limit import handler_identity

    class Unrelated:
        # A DIFFERENT function that merely shares the alias's name.
        send_alias = _make_limited(99)

    class Host(LiveView):
        template = (
            '<div dj-view="djust.tests.test_ratelimit_post_fallback.'
            "test_an_unrelated_descriptor_attribute_does_not_decide_a_handler_identity."
            '<locals>.Host" dj-id="0">x</div>'
        )

        _descriptor = Unrelated()

        @event_handler()
        @rate_limit(rate=0.01, burst=2)
        def expensive_send(self, **kwargs):
            pass

    view = Host()
    view.send_alias = view.expensive_send  # an INSTANCE alias to a declared method

    assert handler_identity(view, view.send_alias) is handler_identity(view, view.expensive_send), (
        "an instance alias to a declared method must share ITS budget"
    )
    assert handler_identity(view, view.send_alias) is not handler_identity(
        view, Unrelated().send_alias
    ), "the unrelated class's same-named function must not be involved"


def test_a_descriptor_property_on_an_ordinary_owner_is_not_executed():
    """Computing the identity must not RUN a ``_descriptor`` property.

    A plain ``getattr(owner, "_descriptor", None)`` executes that property on any
    owner that has one, so merely enforcing a rate limit would have a side effect
    on an unrelated view.
    """
    from djust.rate_limit import handler_identity

    touched = []

    class Host(LiveView):
        template = (
            '<div dj-view="djust.tests.test_ratelimit_post_fallback.'
            "test_a_descriptor_property_on_an_ordinary_owner_is_not_executed."
            '<locals>.Host" dj-id="0">x</div>'
        )

        @property
        def _descriptor(self):
            touched.append(1)
            return object()

        @event_handler()
        @rate_limit(rate=0.01, burst=2)
        def act(self, **kwargs):
            pass

    assert handler_identity(Host(), "act") is not None
    assert touched == [], "the identity check must not invoke a `_descriptor` property"


@pytest.mark.django_db
def test_a_shadowed_declaration_does_not_buy_a_second_budget():
    """The rubric: key what RUNS, not what the name is declared as.

    ``_ShadowedAliasHost`` declares ``send_alias`` as its own method and then
    shadows it on the instance with ``expensive_send``. Both names therefore
    execute the SAME function, so they must share ONE budget. Keying on the
    declaration gave the shadowed name a key belonging to a method that never
    runs: measured [200,200,429] under the primary name and [200,200,429] again
    under the alias — four executions at a burst of two.
    """
    from djust.rate_limit import handler_identity

    view, factory, session_request = _fresh(_ShadowedAliasHost)

    assert handler_identity(view, view.send_alias) is handler_identity(view, view.expensive_send), (
        "the shadowed name runs the same function, so it must share its identity"
    )

    primary, _ = _drive_post(view, factory, session_request, "expensive_send", 2)
    alias, alias_statuses = _drive_post(view, factory, session_request, "send_alias", 2)

    assert primary == 2, "the burst should be admitted under the primary name"
    assert alias == 0, (
        "the shadowed alias runs the SAME function and must find the budget spent "
        f"(admitted {alias}/2, statuses={alias_statuses})"
    )
    assert view.calls == 2, f"two executions total, not four; got {view.calls}"


@pytest.mark.django_db
def test_an_instance_alias_shares_the_budget_of_the_method_it_aliases():
    """The instance-alias contract at the BUDGET level, with a success control.

    Identity equality alone would not show that the two names share one budget —
    it is a statement about the helper. This drives the real path: the primary
    name must be ADMITTED (the control that the handler works at all), and the
    alias must then find that budget spent.
    """

    class Host(LiveView):
        template = (
            '<div dj-view="djust.tests.test_ratelimit_post_fallback.'
            "test_an_instance_alias_shares_the_budget_of_the_method_it_aliases."
            '<locals>.Host" dj-id="0">x</div>'
        )

        @event_handler()
        @rate_limit(rate=0.01, burst=2)
        def expensive_send(self, **kwargs):
            self.calls += 1

    view, factory, session_request = _fresh(Host)
    view.calls = 0
    view.send_alias = view.expensive_send

    primary, primary_statuses = _drive_post(view, factory, session_request, "expensive_send", 2)
    alias, alias_statuses = _drive_post(view, factory, session_request, "send_alias", 2)

    assert primary == 2, f"control: the primary name must be admitted ({primary_statuses})"
    assert alias == 0, (
        "an instance alias to a declared method shares its budget rather than "
        f"getting its own (admitted {alias}/2, statuses={alias_statuses})"
    )
    assert view.calls == 2, f"two executions total, not four; got {view.calls}"


def test_a_custom_eq_and_hash_cannot_collapse_two_declarations():
    """A class value's own ``__eq__``/``__hash__`` must never be consulted.

    The declared-function set is built from ``id()`` and compared by ``id``, so an
    always-equal / always-same-hash object cannot collapse two declarations into
    one identity — and unsupported shapes are refused rather than merged.
    """
    from djust.rate_limit import UNKEYABLE, handler_identity

    class AlwaysEqual:
        def __eq__(self, other):  # noqa: D105 - deliberately always equal
            return True

        def __hash__(self):
            return 0

        def __call__(self, **kwargs):
            pass

    class Owner:
        always_a = AlwaysEqual()
        always_b = AlwaysEqual()

        @event_handler()
        @rate_limit(rate=0.01, burst=2)
        def one(self, **kwargs):
            pass

        @event_handler()
        @rate_limit(rate=0.01, burst=2)
        def two(self, **kwargs):
            pass

    owner = Owner()
    assert handler_identity(owner, owner.always_a) is UNKEYABLE, (
        "an unsupported shape must be refused, not merged"
    )
    assert handler_identity(owner, owner.always_b) is UNKEYABLE
    # Real handlers still key separately even though always-equal values share the class.
    assert handler_identity(owner, owner.one) is not handler_identity(owner, owner.two)


def test_a_staticmethod_subclass_cannot_mint_a_declared_function():
    """A wrapper subclass that redefines ``__func__`` must be refused, not unwrapped.

    ``isinstance(v, staticmethod)`` accepts a *subclass*, and a subclass can
    override ``__get__`` to mint a callable per access and override ``__func__``
    as a property. Reading ``__func__`` then runs that property and returns the
    minted closure, which an isinstance-based unwrap would accept as a declared
    function — so every call would land in its own bucket and the limit would
    apply not at all. Gating on the exact type refuses the subclass while leaving
    a genuine wrapper working.
    """
    from djust.rate_limit import UNKEYABLE, handler_identity

    minted = []

    def _mint():
        minted.append(1)

        @event_handler()
        @rate_limit(rate=0.01, burst=2)
        def closure(self, **kwargs):
            pass

        return closure

    class MintingWrapper(staticmethod):
        """Mints ONCE, and answers both __get__ and __func__ with that same object.

        The caching is what makes the case real: if each access minted a fresh
        closure, the identity comparison would fail on its own and the test would
        pass with or without the gate — pinning nothing. Answering both paths with
        the SAME object is what lets a minted closure be mistaken for a declared
        function.
        """

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._target = None

        def _minted(self):
            if self._target is None:
                self._target = _mint()
            return self._target

        def __get__(self, obj, objtype=None):
            return self._minted()

        @property
        def __func__(self):
            return self._minted()

    def real():
        pass

    class Owner:
        minting = MintingWrapper(real)

        @event_handler()
        @rate_limit(rate=0.01, burst=2)
        def good(self, **kwargs):
            pass

    class PlainOwner:
        declared = staticmethod(real)

    owner = Owner()
    assert handler_identity(owner, owner.minting) is UNKEYABLE, (
        "a staticmethod SUBCLASS must be refused, not unwrapped — its __func__ is "
        "subclass code returning a minted closure"
    )
    assert minted, "fixture sanity: the subclass's __func__ property does mint"
    assert handler_identity(owner, owner.good) is not UNKEYABLE, (
        "the ordinary method control must still resolve"
    )
    # A genuine staticmethod wrapper still unwraps to the declared function.
    assert handler_identity(PlainOwner(), PlainOwner().declared) is real
