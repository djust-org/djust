---
table: settings
covers: DJUST_STATE_BACKEND
removed: `DJUST_STATE_BACKEND`
use: `DJUST_CONFIG['STATE_BACKEND']`, set to `'memory'` or `'redis'`. A `redis://` URL goes in `DJUST_CONFIG['REDIS_URL']`, not in the backend setting.
---
