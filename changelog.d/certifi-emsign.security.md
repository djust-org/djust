- **The locked `certifi` is now 2026.10.19 (PYSEC-2026-4320 /
  GHSA-6q48-6c7w-9gw2).** 2026.2.25 bundled the `emSign ECC Root CA - G3`
  root, under which eight revoked eMudhra intermediates have compromised keys;
  an on-path attacker holding one could pass TLS verification for any host in
  clients that trust certifi (`requests`, `httpx`). 2026.10.19 drops the
  affected root. Only `uv.lock` changes: djust does not depend on certifi
  directly. It was the finding failing the push-to-main `pip-audit` gate.
