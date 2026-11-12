---
table: python
covers: djust.PersistentLiveView, djust.live_view.PersistentLiveView
removed: `PersistentLiveView` (`djust.PersistentLiveView`, `djust.live_view.PersistentLiveView`)
use: `LiveView`, with the fields to keep declared as above. Importing it raises `ImportError` with this advice.
---
