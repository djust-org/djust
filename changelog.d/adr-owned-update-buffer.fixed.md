- **Buffered socket updates:** Connections now drain and discard only their own
  buffered server updates. Old connection errors/disconnects and unrelated
  pending requests cannot erase or strand replacement-connection work, and
  unknown error references do not discard updates.
