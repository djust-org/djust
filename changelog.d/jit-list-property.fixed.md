- **A property returning a list of dicts serialized as `{}` when the template
  read inside its items.** `{% for tool in agent.tools %}{{ tool.name }}`, with
  `tools` a property returning a list, gave `agent.tools == {}` on the codegen
  path (a single model, a `list[Model]`): the generated serializer took
  `tools.name` as an attribute of the list. The Rust QuerySet path already kept
  the list. The generated code now takes a list, tuple, set or QuerySet item by
  item by the rest of the paths (`codegen.take_by_tree`): dict items whole,
  object items through the same `emittable_names` gate. 6 regression cases in
  `python/tests/test_jit_list_property.py`.
