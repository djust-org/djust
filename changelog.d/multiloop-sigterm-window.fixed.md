- **`djust serve --loops N --uds` left a stale socket when it was stopped just as it started.**
  The server created the socket file before it installed its SIGINT/SIGTERM
  handlers, so a signal in that window ended the process with status -15 and left
  the file behind, and the next start failed with "address in use". The handlers
  are now installed first; a signal received before the event loops exist is
  recorded and the server shuts down cleanly (exit status 0, socket file
  removed). The shutdown never removes a socket path it did not create: after a
  failed bind the path belongs to another server and is left alone.
