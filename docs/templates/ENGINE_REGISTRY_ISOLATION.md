# Template engine registry isolation

Each `DjustTemplateBackend` owns a native registry namespace and its Python library-bridge state. A `DjustTemplate` retains its backend; compilation and synchronous rendering enter that namespace and restore the previous one on exit, including exceptions and nested renders through another backend. Threads select namespaces independently, without a global render lock.

Source caches use `(namespace, source)` keys and publish the parsed template together with its validation generation. Filesystem include caches use `(namespace, resolved path)` keys and validate both modification time and registry generation. Identical files can therefore have different library bindings in different engines. The generation is captured before parsing: a concurrent registration cannot validate a parse against a generation it never observed.

Library loading is idempotent while the library object and its registrations remain unchanged. An initial load can invalidate its own pre-parse generation snapshot; one subsequent parse completes the warm-up. Further unchanged compilations reuse the cache. Explicit registration changes still invalidate cached parses. Registry generations remain process-wide, so changing an unrelated engine can conservatively invalidate another engine's parse cache.

Low-level Rust/LiveView calls outside a Django backend retain namespace zero and the existing process-global registration API. Engine registries fall back to these application-wide registrations, but never to another engine's namespace. Registration and clear operations inside an engine affect only that engine's entries. Treat low-level registration as trusted application configuration, not tenant configuration.

Backend-owned native handlers and cache entries are released when the backend and all its template wrappers are collected. The internal namespace functions are implementation details; applications should use backend template methods instead of setting namespaces manually. Source-located AST nodes retain their compilation namespace, so a compiled parent operand keeps its handler bindings when extended by another engine. Python `DjustTemplate` wrappers retain the backend lifetime.

This isolates different engines. It does not introduce per-node snapshots of same-named libraries loaded successively within one engine. Configure independent backends when libraries require independent meanings for the same name.
