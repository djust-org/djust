- **`MemoryTracker` retried `import psutil` on every event.** With psutil not
  installed, each failed import re-scanned `sys.path`: about 42 µs per event
  on the event-loop thread. Whether psutil is installed is now checked once, at
  module import. 4 regression cases in
  `python/tests/test_memory_tracker_psutil_probe.py`.
