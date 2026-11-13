---
table: settings
covers: view_state_in_session
removed: `view_state_in_session` in `LIVEVIEW_CONFIG` or `DJUST_CONFIG`
use: Delete the key. No view state is kept in the session any more ([below](#state-a-legacy-view-no-longer-keeps)), so there is nothing to switch off. System check `djust.C027`, which validated it, is retired.
---
