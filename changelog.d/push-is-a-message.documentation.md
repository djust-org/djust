- **Server push: a push is a message, not a call.** `push_to_view()` /
  `apush_to_view()` return once the message is sent; the handler runs later in
  each receiving session (later still in a busy one), so code after the push
  cannot read back state the handler sets. The guide shows the shape to use
  instead.
