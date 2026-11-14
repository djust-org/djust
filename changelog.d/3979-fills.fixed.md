- **More bars draw under a strict CSP (#3979).** The token bar
  (`TokenCounter` and `{% token_counter %}`), the meter's segments (`Meter`
  and `{% meter %}`), the data table's data bar and loading skeleton, the
  skeleton placeholders (`SkeletonFactory` and `{% skeleton_for %}`) and the
  gauge's box (`Gauge` and `{% gauge %}`) no longer write a `style="width:…"`
  attribute, which a `style-src` without `'unsafe-inline'` refuses. They use
  the width classes `{% progress %}` uses (`dj-progress-w-0` … `-100`, rounded
  to a whole percent, now also in `components-classes.css`) and
  `gauge--sm` / `--md` / `--lg`. A meter segment with a custom colour still
  carries that colour as a style, and its legend swatch no longer writes an
  empty `style=""`. The demo project's progress bars and the
  component best-practices example use a native `<progress>`. New cases in
  `python/djust/tests/test_progress_widths_csp_3979.py`.
