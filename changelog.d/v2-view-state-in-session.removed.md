- **BREAKING: `LIVEVIEW_CONFIG['view_state_in_session']`.** The setting chose
  whether a legacy view kept its state in the session; nothing is kept now, so
  every value is refused with `ImproperlyConfigured` at startup, in
  `LIVEVIEW_CONFIG` or `DJUST_CONFIG`. Delete the key.
