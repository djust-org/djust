- **`djust.auth.social.social_auth_providers` is removed (#4025).** The context
  processor and the `djust.auth.social` module are gone; they had warned since
  1.3. A `TEMPLATES` entry that still lists the processor fails startup with
  `ImproperlyConfigured` naming the replacement. Use `{% auth_providers auth %}`
  from `{% load djust_auth %}`; `auth.providers` holds the same providers, with
  `id` in place of `name`, and the icon from the `auth_provider_icon` filter.
