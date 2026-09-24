- **Component background loading:** Component-event noop, subtree patch and
  full-page responses now retain loading until their captured background batch
  completes. Async queue bookkeeping no longer forces unnecessary page renders,
  and empty batches no longer create an uninitialized task queue.
