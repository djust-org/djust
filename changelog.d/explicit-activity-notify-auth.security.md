- **Freshly authorize NOTIFY-released activity events for staged explicit
  exposure.** An event queued on a hidden activity is validated when it is
  dispatched. A `db_notify` that made the activity visible dispatched the queued
  event through the WebSocket consumer without the fresh authorization explicit
  views require, so it ran even after the session was deleted. The consumer now
  applies the runtime's check and fail-closed outcome (static error, close
  4403). Legacy views are unchanged.
