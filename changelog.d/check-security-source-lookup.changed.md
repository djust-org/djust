- **`djust.S012`'s system check no longer parses the source of every imported
  LiveView.** It looked up each class's source line (which reads and parses the
  class's whole module) before filtering to the project's app directories, so
  `manage.py check` paid for every installed and test-defined view. The line is
  now looked up only for a view the check actually reports. Measured under the
  test suite: the S009/S012 check tests went from 14.8s to 0.6s.
