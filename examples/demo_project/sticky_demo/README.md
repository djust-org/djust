# Sticky LiveViews demo — app-shell pattern

Runnable demo of v0.6.0 Sticky LiveViews. Shows two persistent widgets
(an audio player and a notification center) surviving `live_redirect`
navigation between a Dashboard, Settings, and Reports page.

## To run

```bash
cd examples/demo_project
make start
# then visit http://localhost:8002/sticky_demo/dashboard/
```

Add to `examples/demo_project/demo_project/settings.py`:

```python
INSTALLED_APPS = [
    ...,
    "sticky_demo",
]
```

And to `examples/demo_project/demo_project/urls.py`:

```python
urlpatterns += [
    path("sticky_demo/", include("sticky_demo.urls")),
]
```

## What to observe

1. **Dashboard** → click **Play** on the audio player and **Add demo**
   on the notification center. Note the `elapsed_seconds` counter and
   the list of notifications.
2. Navigate to **Settings** (nav link). Both widgets re-attach at
   their slots in the new layout. `is_playing` state, elapsed counter,
   notification list — all preserved. The Python instance is the
   same object on both sides of the navigation.
3. Navigate to **Reports**. The notification center still preserves;
   the audio player UNMOUNTS (Reports deliberately omits the
   `dj-sticky-slot="audio-player"` element) and you will see a
   `djust:sticky-unmounted` event with `reason='no-slot'` in devtools
   if you have `globalThis.djustDebug = true` set.
4. Click back to Dashboard — the audio player is re-mounted fresh
   (its state is gone — the server unmounted it on step 3), but the
   notification center carries on.

## Files

| File | Purpose |
|---|---|
| `views.py` | `AudioPlayerView` (sticky), `NotificationCenterView` (sticky), `DashboardView`, `SettingsView`, `ReportsView` |
| `urls.py` | Three page routes |
| `templates/sticky_demo/base.html` | Shared app-shell with slots |
| `templates/sticky_demo/{dashboard,settings,reports}.html` | Page-specific content; Dashboard instantiates the stickies |
| `templates/sticky_demo/{audio_player,notification_center}.html` | Sticky views' templates |

## Further reading

* [ADR-011 — Sticky LiveViews](../../../docs/adr/011-sticky-liveviews.md)
* [User guide](../../../docs/website/guides/sticky-liveviews.md)
