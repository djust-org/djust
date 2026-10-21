- **`self.create_task(coro)` / `self.track_task(task)` tie a task the view's
  own async code starts to the view's `start_async` work.** A follower started
  from an `async def mount` or a push handler was unknown to the framework, so
  neither `cancel_async_on_disconnect = True` nor an explicit view's disposal
  could stop it, and apps cancelled it by hand. A registered task is now
  cancelled by `cancel_async_all()`, an explicit view's disposal on disconnect
  or navigation, and a legacy view's close with `cancel_async_on_disconnect =
  True`. 7 cases in `python/djust/tests/test_view_tracked_tasks.py`.
