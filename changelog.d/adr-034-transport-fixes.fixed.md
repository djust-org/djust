- **SSE and the HTTP fallback keep up with the page (found by ADR-034 C2's
  real-transport browser runs).**
  - **SSE mount.** The mount now morphs an HTTP-prerendered page against the
    mount HTML, as the WebSocket mount does (#1610). Before, it only stamped
    `dj-id`s, so values that differ per mount stayed stale, including the
    identities of interactive components. Every event on such a component
    then failed with "Component not found".
  - **HTTP zero-patch renders.** A render that changed nothing no longer
    resets the server's diff baseline. That reset restarted the version at 1,
    the client's version check failed, and the page reloaded, losing its
    state. The answer is now an empty patch list with the new version.
  - **HTTP event ordering.** Events are now sent one at a time, in order,
    like frames on a socket. Two in flight at once each restored and saved
    the session, so a form save could store stale values.
