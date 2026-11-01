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
| #3711 | 28 `assert`s in 20 test files bounded a compute or a wait with an absolute number of seconds (the issue named 16 lines); two more were found by hand because the clock reading came from a helper's return value or the product's own `latency_ms` | `djust.tests._growth.assert_scales_linearly`, or an ordering; `test_no_absolute_time_bounds_3711.py` keeps the count at zero | yes, missed |
| #3711 | `test_save_block_timeout_logs_and_continues_end_to_end` asserted `elapsed < 2.0` as the proof that the wrapper stops waiting at 150 ms, but the slow body costs 0.25 s, so that bound could not tell the wrapper's timeout from no timeout (the WARNING assertion beside it was the load-bearing one; with the timeout pushed to 600 s the old test fails there, not on the bound) | the WARNING must appear while the slow body is still blocked on a test-owned event | yes, missed |
| #3711 | `test_large_template` asserted `< 0.5` s for a template that costs ~6 ms, which hid that `extract_template_variables` grows ~6-8x per doubling of the blocks; `_blank_template_comments` is quadratic on `{% comment %}` openers (the bound was 2 s at 20 000 of them, which costs 0.07 s) | both are marked `xfail` with the measurement, in their test files; the cost is a product matter, not fixed here | yes, missed |
| #3709 | explicit sub-10 s `timeout=` on Channels communicator waits override the 30 s default (404 outside an absence window, in 152 test files) | `timeout=` removed from the awaited call; the ratchet keeps it from regrowing. The first pass of the converter also stripped `disconnect(timeout=0.01)` from `test_ws_communicator_defaults_3464.py`, whose whole point is that an explicit timeout still wins, and that test went red: the one baseline entry | yes, missed |
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
