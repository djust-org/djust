- **A signal for the name an explicit view holds but does not supply.** Under explicit
  exposure a template name that nothing supplies renders empty, and a view written for 1.x
  (`self.count = 0` in `mount()`, `{{ count }}` in the template) does exactly that. Two things
  now say so. With `DEBUG = True`, the first time such a view renders, djust logs one warning
  per view class and name on the `djust.exposure` logger: the view, the name, the fix
  (return it from `get_context_data()`; declare it with `state()` to persist it or send it
  to the browser) and the migration guide. It names attributes and never logs a value, and
  with `DEBUG` off it does nothing. And system check `djust.T027` (Warning) reports the same
  from `manage.py check`, for a view whose `get_context_data()` returns a literal dict or
  `super()`'s and whose template does not use `{% extends %}`.
