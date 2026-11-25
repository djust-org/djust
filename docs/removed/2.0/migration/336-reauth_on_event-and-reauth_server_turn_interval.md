---
table: settings
covers: reauth_on_event, reauth_server_turn_interval
removed: `reauth_on_event` and `reauth_server_turn_interval` in `LIVEVIEW_CONFIG` or `DJUST_CONFIG`
use: Delete the keys; djust refuses them with `ImproperlyConfigured` at startup. Every event and server turn is re-authorized against a freshly loaded session, with no interval, so there is nothing to switch on.
---
