### `djust.auth.social.social_auth_providers` → `{% auth_providers auth %}`

The context processor and the `djust.auth.social` module are gone (deprecated
since 1.3). A `TEMPLATES` entry that still lists the processor fails startup
with `ImproperlyConfigured` naming the replacement. Delete the entry and render
the buttons with the account kit's tag:

```html
<!-- Before: settings listed "djust.auth.social.social_auth_providers" -->
{% for provider in oauth_providers %}
  <a href="{{ provider.login_url }}">{{ provider.icon }} {{ provider.label }}</a>
{% endfor %}

<!-- After -->
{% load djust_auth %}
{% auth_context as auth %}
{% auth_providers auth %}
```

`auth.providers` is the same list for a template that renders its own buttons.
Each item has `id` (was `name`), `label` and `login_url`. The built-in backends
leave its `icon` empty, so take the icon from the `auth_provider_icon` filter
(`{{ provider.id|auth_provider_icon }}`).
Python code that called the function can read
`get_account_backend().providers(request)` from `djust.auth.accounts.registry`.
