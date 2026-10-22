- **An HTTP GET normalizes its template source once, not three times.**
  `get_template()` runs several times per GET (the GET body, the
  template-hash slot, the Rust view's initialization), and each run passed the
  same template source through the egress normalizer again — four passes over
  ~100 KB for one demo homepage load. The normalized source is now cached on
  the view class, keyed by the source and the normalizer, so an edited
  template or an overridden normalizer is never served a stale entry. A GET
  now normalizes its response once (plus its template source the first time
  that source is seen). Tests in `tests/unit/test_egress_normalizer_calls.py`.
