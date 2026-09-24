- **Keep HTTP event failures value-free for staged explicit exposure.** When an
  event handler raised on the HTTP-POST path, the view logged the exception with
  its traceback and, under `DEBUG`, returned the exception text, the traceback
  and the posted parameters to the client in a 500 response, for any policy. A
  nonlegacy view now gets the value-free log line and the generic response even
  under `DEBUG`. Streamed-render failures are redacted the same way. Legacy
  behaviour is unchanged.
