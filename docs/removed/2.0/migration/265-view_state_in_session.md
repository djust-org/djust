---
table: settings
covers: view_state_in_session
removed: `view_state_in_session` in `LIVEVIEW_CONFIG` or `DJUST_CONFIG`
use: Delete the key. A view's state is saved in the server-side session by default. To save nothing, as `view_state_in_session = False` did, set `DJUST_AUTOMATIC_STATE = False` in settings (the whole project) or `automatic_state = False` on a view ([Switching saving off](state-survival.md#switching-saving-off)). System check `djust.C027`, which validated the old key, is retired.
---
