- **The per-IP WebSocket refusal (close code 4429) now says which limit
  applied.** The log names `max_connections_per_ip` or `reconnect_cooldown`,
  and when the request carries `X-Forwarded-For` but `DJUST_TRUSTED_PROXY_COUNT`
  is 0 it says every client behind the proxy shares one address. The defaults
  (10 sockets per address per process, 5 s cooldown after a rate-limit
  disconnect) are unchanged; the security guide now explains sizing them behind
  a proxy. 3 cases in `python/tests/test_ws_per_ip_refusal_message.py`.
