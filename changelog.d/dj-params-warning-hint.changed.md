- **The `dj-params` deprecation warning now shows a typed replacement.** Its example
  is `dj-value-todo-id:int="{{ todo.id }}"` rather than an untyped `dj-value-*`
  attribute, because `dj-params` carried JSON types and a plain `dj-value-*`
  value arrives as a string. `client.js` and `client.min.js` are regenerated.
