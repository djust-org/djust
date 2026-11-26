"""
PushEventMixin — Server-to-client event pushing for LiveView.

Allows handlers to push arbitrary events to client-side JS hooks:

    class MyView(LiveView):
        def handle_save(self):
            self.save_data()
            self.push_event("flash", {"message": "Saved!", "type": "success"})
            self.push_event("scroll_to", {"selector": "#bottom"})

Also provides ``push_commands()`` for server-initiated JS Command
execution — see ADR-002 Phase 1a.
"""

from typing import TYPE_CHECKING, Any, Dict, List, Tuple

from .commands import CommandsMixin

from .._surviving_work import refuse_if_disposed

if TYPE_CHECKING:
    from djust.js import JSChain


class PushEventMixin(CommandsMixin):
    """
    Mixin that provides push_event() for sending events to client JS.

    Events are queued during handler execution on the view's one outbound
    queue (:class:`~djust.mixins.commands.CommandsMixin`) and sent with the
    turn's answer.
    """

    @property
    def _pending_push_events(self) -> List[Tuple[str, Dict[str, Any]]]:
        """The queued push events as ``(event, payload)`` pairs (a copy).

        Assigning a list replaces them (``= []`` drops them all).
        """
        return [(p["event"], p["payload"]) for p in self._pending("push_event")]

    @_pending_push_events.setter
    def _pending_push_events(self, events: Any) -> None:
        self._replace_pending("push_event", [{"event": e, "payload": p} for e, p in (events or [])])

    def push_event(self, event: str, payload: Dict[str, Any] = None) -> None:
        """
        Push an event to the connected client(s).

        The event will be dispatched to:
        - All dj-hook instances that registered for this event via handleEvent()
        - A CustomEvent on document for non-hook listeners

        Args:
            event: Event name (e.g. "chart_update", "djust:scroll_to")
            payload: Dict of data to send with the event

        Example::

            def handle_save(self):
                self.save_data()
                self.push_event("flash", {"message": "Saved!", "type": "success"})
                self.push_event("scroll_to", {"selector": "#result"})
        """
        refuse_if_disposed(self, "push events")
        if payload is None:
            payload = {}
        self._enqueue("push_event", {"event": event, "payload": payload})

    def trigger_submit(self, selector: str) -> None:
        """Trigger a native HTML form POST on the client after this handler.

        Partner API to the ``dj-trigger-action`` client-side form attribute
        (v0.5.1). After the handler completes (validation passed, etc.), the
        client finds ``document.querySelector(selector)``, verifies it carries
        ``dj-trigger-action``, and calls its native ``.submit()`` — bypassing
        djust's WebSocket handler for this final step. Essential for OAuth
        flows, payment gateway handoffs, and any path that needs a real
        browser POST.

        The selector is sent to the client as-is; keep it simple (``"#form-id"``)
        and match it with ``dj-trigger-action`` on the target form. Forms
        without that attribute are refused client-side, so the attribute
        doubles as an explicit opt-in for native submission.

        Args:
            selector: A CSS selector (typically ``"#form-id"``) for the form
                to submit natively.

        Example::

            @event_handler
            def complete_payment(self, **kwargs):
                self._process_validation()
                # Hand off to the payment gateway via a real browser POST.
                self.trigger_submit("#payment-form")
        """
        self.push_event("djust:trigger-submit", {"selector": selector})

    def push_commands(self, chain: "JSChain") -> None:
        """
        Push a JS Command chain to the current session for immediate execution.

        This is the server-side half of backend-driven UI automation
        (ADR-002 Phase 1a). Pass any ``djust.js.JSChain`` (built via
        ``JS.show(...).add_class(...)`` etc) and the framework sends it to
        the client as a ``djust:exec`` push event. A framework-provided
        auto-executor on every page receives the event and runs the chain
        via ``window.djust.js._executeOps(ops, null)``. No client-side hook
        registration is required — the auto-executor ships with ``client.js``.

        The chain is serialized as its ``ops`` list (a JSON-safe array of
        ``[op_name, args]`` pairs) and piggybacks on the existing
        ``push_event`` transport. No new wire protocol is involved.

        Use ``push_commands()`` when you want the server to drive DOM
        operations directly — tutorials, guided tours, AI assistants,
        support handoffs, etc. For plain server-initiated events (flash
        messages, scroll-to, custom analytics) keep using ``push_event()``.

        Args:
            chain: A :class:`djust.js.JSChain` instance. Build with the
                module-level ``JS`` factory, e.g.
                ``JS.add_class("tour-highlight", to="#btn-new").focus("#btn-new")``.

        Example::

            from djust.js import JS

            class Onboarding(LiveView):
                @event_handler
                def highlight_next_button(self, **kwargs):
                    self.push_commands(
                        JS.add_class("tour-highlight", to="#btn-new")
                          .focus("#btn-new")
                          .dispatch("tour:step", detail={"step": 1})
                    )

        See :doc:`/guides/server-driven-ui` for the full guide and ADR-002
        for the underlying design decisions.
        """
        # Lazy import to avoid circular dependency between mixins.push_events
        # and the top-level djust.js module.
        from djust.js import JSChain

        if not isinstance(chain, JSChain):
            raise TypeError(
                f"push_commands() expects a djust.js.JSChain, got {type(chain).__name__}. "
                f"Build one with djust.js.JS.<op>(...).<op>(...) and pass the result."
            )
        self.push_event("djust:exec", {"ops": chain.ops})

    async def flush_push_events(self) -> None:
        """
        Flush pending push events immediately via the consumer callback.

        Public API for ``@background`` handlers that need to send
        ``push_commands`` / ``push_event`` to the client mid-task rather
        than waiting for the handler to return.  This is the mechanism
        that makes ``TutorialMixin``'s per-step highlights arrive in real
        time instead of all at once when the task completes (#693).

        Resolves the flush dynamically from ``_ws_consumer`` so that it
        works after WebSocket reconnects without re-wiring anything (#698).
        Over SSE the queued events are drained and sent, in order, through
        the session's runtime transport while that session still serves
        this view (#4256).

        No-op if neither is available (e.g. in tests, or on the HTTP
        fallback, where the queued events ride the POST response instead).

        Usage::

            @event_handler
            @background
            async def long_running(self, **kwargs):
                self.push_event("step", {"n": 1})
                await self.flush_push_events()   # client sees step 1 NOW
                await asyncio.sleep(5)
                self.push_event("step", {"n": 2})
                await self.flush_push_events()   # client sees step 2 NOW
        """
        if not self._has_pending("push_event"):
            return
        # Prefer dynamic lookup via _ws_consumer — always current, even
        # after reconnect (#698).
        consumer = getattr(self, "_ws_consumer", None)
        flush_fn = getattr(consumer, "_flush_push_events", None)
        if flush_fn is not None:
            await flush_fn()
            return
        if consumer is not None:
            return
        from .._live_target import sse_runtime_for

        runtime = sse_runtime_for(self)
        if runtime is None:
            return  # HTTP fallback / tests: events stay queued for the response
        send = runtime.transport.send
        # Drain (never iterate in place): the SSE turn end drains too, so each
        # event is sent exactly once.
        for name, payload in self._drain_push_events():
            await send({"type": "push_event", "event": name, "payload": payload})

    def clear_push_events(self) -> List[Tuple[str, Dict[str, Any]]]:
        """Drop the push events queued so far in this turn, and return them.

        Events pushed after this call are sent as usual. Useful when a handler
        queued events (directly or through a helper) and then decided they
        should not reach the client::

            @event_handler
            def save(self, **kwargs):
                self.push_event("flash", {"message": "Saved"})
                if not self.form_ok():
                    self.clear_push_events()   # no "Saved" flash

        Replaces assigning ``self._pending_push_events = []``, which keeps
        working.

        Returns:
            The dropped ``(event, payload)`` pairs, oldest first.
        """
        return self._drain_push_events()

    def _drain_push_events(self) -> List[Tuple[str, Dict[str, Any]]]:
        """Drain and return the queued push events as ``(event, payload)`` pairs.

        A view of the one outbound queue (#3658): only ``push_event`` effects
        are taken; the other kinds stay queued.
        """
        return [(p["event"], p["payload"]) for _k, p in self._drain_outbound(("push_event",))]
