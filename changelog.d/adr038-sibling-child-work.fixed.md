- **Explicit views: background work a child queues on another child runs.**
  After a routed child event, only that child's own `start_async` queue was
  drained. Work its handler queued on a sibling or descendant waited for that
  child's next event. The whole owned explicit tree is now swept after child
  events, as it already is after parent turns.
