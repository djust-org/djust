- Correlate foreground WebSocket and SSE requests individually so overlapping
  requests from the same control keep loading active until their replies arrive.
  SSE event sends now await the server response, while teardown sends remain
  fire-and-forget. Duplicate acknowledgements, targeted failures and disconnects
  no longer consume another transport's outstanding request.
