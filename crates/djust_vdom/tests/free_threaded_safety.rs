//! Free-threaded-safety concurrency stress test for `djust_vdom` (#1432).
//!
//! Companion to `crates/djust_templates/tests/free_threaded_safety.rs`.
//! `djust._rust` is declared `#[pymodule(gil_used = false)]` — on a
//! free-threaded interpreter CPython will NOT auto-re-enable the GIL, so
//! the pure-Rust VDOM paths (`parse_html`, `diff`) must be sound under
//! genuine OS-thread parallelism.
//!
//! The interesting `djust_vdom`-specific shared state per the #1432
//! thread-safety audit is the
//! `thread_local!` `ID_COUNTER` (`src/lib.rs:55`). Because it is
//! `thread_local!`, each OS thread gets its own independent counter:
//! `parse_html` resets the *calling thread's* counter only, and the
//! within-render `dj-id`s it allocates are scoped to that one render on
//! that one thread. This file pins that per-thread independence — a
//! regression that moved `ID_COUNTER` to a process-global `static`
//! without synchronization would make `concurrent_parse_html_ids_are_per_thread`
//! flake.
//!
//! `std::thread` gives real no-GIL parallelism; this is the genuine test
//! of the `Send`/`Sync` contracts for the VDOM crate.

use djust_vdom::{diff, get_id_counter, next_djust_id, parse_html, reset_id_counter};
use std::sync::{Arc, Barrier};
use std::thread;

const N_THREADS: usize = 12;
const ITERS: usize = 200;

/// Case 1 — concurrent `parse_html` + `diff` across distinct documents.
///
/// N threads each parse their own HTML document and diff it against a
/// mutated version in a tight loop. `parse_html` reads the HTML parser
/// internals and resets the thread-local `ID_COUNTER`; `diff` is a pure
/// tree comparison. Concurrent execution must produce deterministic
/// patch sets with no panic.
#[test]
fn concurrent_parse_and_diff_is_deterministic() {
    let barrier = Arc::new(Barrier::new(N_THREADS));

    let handles: Vec<_> = (0..N_THREADS)
        .map(|t| {
            let barrier = Arc::clone(&barrier);
            thread::spawn(move || {
                let old_html = format!("<div><p>thread {t}</p><span>old</span></div>");
                let new_html = format!("<div><p>thread {t}</p><span>new</span></div>");
                barrier.wait();
                for _ in 0..ITERS {
                    let old_vdom = parse_html(&old_html).expect("old parses");
                    let new_vdom = parse_html(&new_html).expect("new parses");
                    let patches = diff(&old_vdom, &new_vdom);
                    // The only change between old and new is the <span>
                    // text — the diff must be non-empty and stable.
                    assert!(
                        !patches.is_empty(),
                        "thread {t}: diff lost the span-text change"
                    );
                    // Re-diffing identical trees yields no patches —
                    // a torn parse would break this invariant.
                    let same = parse_html(&old_html).expect("reparse");
                    assert!(
                        diff(&old_vdom, &same).is_empty(),
                        "thread {t}: identical trees produced spurious patches"
                    );
                }
            })
        })
        .collect();

    for h in handles {
        h.join().expect("no thread panicked during parse/diff");
    }
}

/// Case 2 — `thread_local! ID_COUNTER` is genuinely per-thread.
///
/// N threads each independently reset the counter, allocate a fixed
/// run of IDs, and assert they see a clean 0,1,2,... sequence. If
/// `ID_COUNTER` regressed to an unsynchronized process-global `static`,
/// concurrent threads would interleave their increments and a thread
/// would observe gaps or duplicates — this test would then flake.
/// With the `thread_local!` it is deterministic regardless of thread
/// count.
#[test]
fn concurrent_id_counter_is_per_thread() {
    const IDS_PER_THREAD: u64 = 64;
    let barrier = Arc::new(Barrier::new(N_THREADS));

    let handles: Vec<_> = (0..N_THREADS)
        .map(|_| {
            let barrier = Arc::clone(&barrier);
            thread::spawn(move || {
                barrier.wait();
                for _ in 0..ITERS {
                    reset_id_counter();
                    // After reset, this thread's counter starts at 0
                    // and must increment monotonically with no
                    // interference from the other 11 threads.
                    for expected in 0..IDS_PER_THREAD {
                        assert_eq!(
                            get_id_counter(),
                            expected,
                            "ID_COUNTER leaked across threads"
                        );
                        let _ = next_djust_id();
                    }
                    assert_eq!(get_id_counter(), IDS_PER_THREAD);
                }
            })
        })
        .collect();

    for h in handles {
        h.join().expect("no thread panicked during ID counter test");
    }
}
