- **A template library that registers a tag djust already provides is no
  longer bridged again on every `{% load %}`.** djust keeps its own tag and
  does not bridge the library's version, but it still recorded the library as
  the tag's owner, so its "already bridged" check never passed. Each
  `{% load %}` of such a library (`djust_components`, for one) then bridged it
  again, logged the refusal again (about 150 warnings per page) and moved the
  registry generation during the parse, which dropped every cached template.
  The refused names are now tracked per engine and skipped by that check, and
  the warning is logged once per library and tag per process. If the built-in
  tag is later removed, the next `{% load %}` bridges the library's version. 5
  regression cases in `python/tests/test_refused_tag_rebridge.py`.
