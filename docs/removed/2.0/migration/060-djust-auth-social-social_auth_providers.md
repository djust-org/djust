---
table: python
covers: djust.auth.social.social_auth_providers
removed: `djust.auth.social.social_auth_providers` (a context processor)
use: `{% load djust_auth %}` and `{% auth_providers auth %}` in the template. Remove the processor from `TEMPLATES`.
---
