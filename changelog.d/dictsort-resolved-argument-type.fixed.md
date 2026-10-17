- **`dictsort` and `dictsortreversed` type a variable argument by its value.**
  `{{ p|dictsort:k }}` read a context string such as `k = "3"` as if it were
  an unquoted integer literal, and sorted a list of strings by character.
  Django's `itemgetter("3")` is a key lookup there, which raises `TypeError`,
  and the filter renders `""`; djust now does the same. A context `int` or
  `bool` still indexes, and a non-integer number (`1.5`, `Decimal("0")`)
  renders `""`, as in Django.
