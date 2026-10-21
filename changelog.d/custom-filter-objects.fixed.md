- **Custom template filters receive the object, not its `str()`, on the Rust
  engine.** A project `@register.filter` was handed the string form of any
  object in the context (a dataclass, a pydantic model, a domain object), for
  its value and for a resolved argument, so `{{ row|my_filter }}` could not read
  `row`'s attributes. Filters now receive the object the context holds, through
  the same conversion the bridged-tag path uses (model fields the serializer
  protects stay protected).
