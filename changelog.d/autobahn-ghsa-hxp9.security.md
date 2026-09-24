- **autobahn advisory GHSA-hxp9-w8x3-p566 (permessage-deflate bypasses
  `maxMessagePayloadSize` after inflation): what it means for djust.** djust
  depends on `channels[daphne]`, and daphne depends on autobahn. On Python 3.11
  and later the lock already resolves autobahn 26.7.1, the patched release. On
  Python 3.10 it resolves 24.4.2, because every autobahn release from 25.9 on
  requires Python 3.11, so no patched version installs there. A default djust
  deployment is not exposed: daphne never enables permessage-deflate, and
  autobahn's server rejects every compression offer unless the application
  turns it on. If you run daphne on Python 3.10 and enable WebSocket
  compression yourself, move to Python 3.11 or serve with uvicorn.
