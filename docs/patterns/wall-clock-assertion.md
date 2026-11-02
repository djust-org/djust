# `wall-clock-assertion`

**Rule** (the line the rule sheet carries): A test asserts what the code does, never how
fast the machine is: judge growth for a complexity claim, an ordering for a concurrency
claim, and wait with the 30 s default for output that will arrive.
→ this page

**Status**: `skill` · **gate**: yes — `python/djust/tests/test_no_absolute_time_bounds_3711.py`
(absolute duration bounds, per file, only falls), `python/djust/tests/test_communicator_timeouts_ratchet_3709.py`
(explicit short communicator timeouts, per file, only falls),
`python/djust/tests/test_ws_communicator_defaults_3464.py` (the 30 s default itself) ·
**candidate for mechanical gate**: done for the two shapes named; a third shape (a polling
deadline that is really a stall-sized window) is not covered
**Origin**: #3692 (four intermittent `python-tests` failures that were timing races in the
tests, not framework bugs), generalised by #3710 / #3711.

## Instances

| PR | what drifted / what was missed | cure | rule in force? |
|---|---|---|---|
| #3708 (#3692, #3707) | four `main` failures that were timing races in tests: an explicit `timeout=3` overriding the 30 s communicator default, `connect()` left at Channels' 1 s default, a `rate=1` bucket asserted to stay empty across wall-clock seconds | `_ws_frames.receive_until`; the default list derived from the Channels classes; `gc.freeze()` after collection | no (origin of this class page) |
| #3712 (#3710) | `test_the_walk_is_linear_on_tag_soup` asserted `< 1.0` s over a corpus that costs 0.009-0.029 s; main's `python-tests (py3.13)` shard 1 failed on a runner that stalled for ~100 s | growth: 4x the input must cost under 10x, best of three | yes, missed |
| #3711 | 31 `assert`s in 23 test files bounded a compute or a wait with an absolute number of seconds (the issue named 16 lines). The first matcher missed a few by shape: a clock reading that came from a helper's return value, the product's own `latency_ms`, and two `timezone.now()` spans (the wizard's pinned timestamp, a replacement session's expiry), found when the review asked for `datetime` and aliased clocks | `djust.tests._growth.assert_scales_linearly`, or an ordering; `test_no_absolute_time_bounds_3711.py` keeps the count at zero, with a positive test per spelling | yes, missed |
| #3711 | `test_save_block_timeout_logs_and_continues_end_to_end` asserted `elapsed < 2.0` as the proof that the wrapper stops waiting at 150 ms, but the slow body costs 0.25 s, so that bound could not tell the wrapper's timeout from no timeout (the WARNING assertion beside it was the load-bearing one; with the timeout pushed to 600 s the old test fails there, not on the bound) | the WARNING must appear while the slow body is still blocked on a test-owned event | yes, missed |
| #3711 | `test_large_template` asserted `< 0.5` s for a template that costs ~6 ms, which hid that `extract_template_variables` grows ~6-8x per doubling of the blocks; `_blank_template_comments` is quadratic on `{% comment %}` openers (the bound was 2 s at 20 000 of them, which costs 0.07 s) | both are `xfail(strict=True)` with the measurement and their own issues (#3723, #3724), in their test files, so a fix fails the run and the marker goes; the cost is a product matter, not fixed here | yes, missed |
| #3709 | explicit sub-10 s `timeout=` on Channels communicator waits override the 30 s default: 433 short awaited waits in 156 test files (404 plain, 29 inside a `try`: 26 `except: pass` drains, 2 `except: break` loops, 1 `except: response = {}` fallback), plus 34 helper definitions whose short default is forwarded to a wait (`_receive_until(..., timeout=3)`) | `timeout=` removed or the default raised to 30; the ratchet keeps it from regrowing. A scripted conversion also stripped `disconnect(timeout=0.01)` from `test_ws_communicator_defaults_3464.py` (the pin that an explicit timeout still wins; the one baseline entry) and a `try: receive_json_from(timeout=1) / except Exception: pass` in `test_ws_auth_close_socket.py` that reads a `websocket.close`: both went red, so a `pass` drain is a judgement, not a pattern. Not covered, only counted in `UNCOVERED_BASELINE`: variable timeouts, `asyncio.wait_for(..., <10)`, imported helpers, and 6 kept drains of which only 2 are absence windows (`test_ws_auth_close_socket.py`, `test_sync_turn_wrapper_more_hops_3637.py`); the other 4 (live-auth x2, model-form route change, mount object permission) read until a frame that arrives in the positive case and fail 68 tests under a 3.5 s per-frame delay, so converting them is follow-up work | yes, missed |
| #3715 | CI logs carried no GC or stall data, so a timing failure could not be attributed | `tests/timing_report.py` prints each process's worst gen-2 GC pause and longest stall in the terminal summary | no (a diagnostic, not a rule) |

## Detection

- **A complexity claim** ("linear on tag soup", "bounded") is a ratio: `assert_scales_linearly`
  measures the work at 1x and 4x, interleaved, best of three, on this thread's CPU time, and
  asks that 4x the input cost under 10x as much (linear is ~4x, quadratic ~16x). Its own tests
  (`test_growth_helper_3711.py`) run it against a linear stand-in, a quadratic one and a one-run
  stall.
- **A concurrency or "did not block" claim** is an ordering: an event set, a frame received
  while another call is still blocked. Give the blocked call a test-owned `threading.Event`
  and a generous valve that only a hung run reaches.
- **An absence** ("nothing arrived") is the one place a short timeout is the assertion:
  `receive_nothing`, or a receive inside `pytest.raises` / `try`.
- **A named constant is not a defence.** The matcher follows `budget_ms = ...` and
  `elapsed = _took(fn)` to the clock reading they came from.
- **A stall on a CI run** now says so: `tests/timing_report.py` names the test that was running
  when a gen-2 pause or a late tick happened.

## Rejected shapes

- **Raising the number.** #1016 raised a 10 ms budget to 30 ms; #1189 raised 100 ms to 500 ms. Both
  still asserted how fast the machine is, and the 500 ms one sat at ~80x the cost it bounded while
  the cost grew super-linearly.
- **`time.process_time()` or `thread_time()` against a constant.** Immune to a descheduled
  process, not to a GC pause or cache contention; `test_audit_ui_3448.py` already retried three
  times with the collector off and still asserted `< 2`. The ratio is what is robust.
- **A mean in place of a median** for a latency gate (v1.0.6-1): one outlier moves it.
