- **HTTP navigation cleanup:** Navigation and page exit abort ordinary HTTP
  fallback requests and release their loading state. Intentional aborts do not
  log request failures; keepalive teardown sends remain independent.
