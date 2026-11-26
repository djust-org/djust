//! Native content digest of plain data, for change detection (#4325).
//!
//! `djust.change_detection` decides "did this value change?" by walking a
//! value into a fingerprint tree and keeping a BLAKE2b digest of the tree's
//! `marshal` encoding. For a 1,000-row list the tree walk and the digest are
//! most of a turn's Python time. This module computes a 128-bit digest of the
//! same value in one walk, with no tree, for exactly the values the Python
//! walk calls PLAIN DATA: exact `dict` / `list` / `tuple` nodes and exact
//! `str` / `int` / `float` / `bool` / `bytes` / `None` leaves, within the
//! depth cap and the node budget.
//!
//! Anything else makes [`plain_digest`] answer `None`, and Python walks the
//! value as before. So a value's kept fingerprint is the native digest when
//! [`plain_digest`] accepts the value and the tree digest otherwise: a function
//! of the value alone, never a mix of the two for one value.
//!
//! ## The encoding
//!
//! Every node writes a one-byte tag, then a fixed-width or length-prefixed
//! body, so the byte string is prefix-free and two values have equal encodings
//! exactly when the Python fingerprint trees are equal:
//!
//! | node    | bytes                                                         |
//! |---------|---------------------------------------------------------------|
//! | `None`  | `0`                                                           |
//! | `bool`  | `b`, 0 or 1 (never the `int` tag: `True == 1` must not hold)  |
//! | `int`   | `n` + 8 bytes when it fits an i64, else `N` + length + hex    |
//! | `float` | `f` + the IEEE bits (so `-0.0` differs from `0.0`)            |
//! | `str`   | `s` + kind + code-point count + the PEP 393 data              |
//! | `bytes` | `y` + length + data                                           |
//! | list    | `L` + length + items; tuple `T`; dict `D` + length + key, value |
//!
//! A `str` is hashed from its code units, never through UTF-8, so a lone
//! surrogate is data like any other. CPython keeps every `str` in the
//! narrowest kind that holds its widest code point and compares kinds before
//! data, so equal strings have equal kind and data.
//!
//! The root of a `list` is hashed from its items' digests (each item hashed
//! alone), so the per-row digests the list delta compares
//! (`rust_bridge._list_item_delta`) come out of the same walk. That shape is
//! used for every root list, asked for rows or not, so the digest stays a
//! function of the value alone.
//!
//! ## The hash
//!
//! Two std SipHash keys drawn once per process, as
//! `LoopItemStore::item_hash` does: a 128-bit keyed digest, no new crate.
//! Digests never leave the process. A domain byte is hashed first, so a native
//! digest is never the hash of the same bytes as anything else.
//!
//! NaN: two distinct NaN objects are unequal in a tree comparison and equal
//! here (same bits). The tree digest has the same property; it can cost an
//! extra sync, never a missed one.

use once_cell::sync::Lazy;
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple};
use std::collections::hash_map::RandomState;
use std::hash::{BuildHasher, Hasher};
use std::sync::atomic::{AtomicU8, Ordering};

/// `change_detection._MAX_DEPTH`: a container this deep is not walked.
pub const MAX_DEPTH: usize = 32;

/// Hashed before anything else (version 1 of the native encoding).
const DOMAIN: u8 = 0xD1;

static KEYS: Lazy<(RandomState, RandomState)> =
    Lazy::new(|| (RandomState::new(), RandomState::new()));

/// Test-only sabotage for the change-detection oracle: 1 hashes a `bool` as
/// the `int` it equals. Never set outside `test_per_turn_oracle.py`.
static SABOTAGE: AtomicU8 = AtomicU8::new(0);

fn digest_of(bytes: &[u8]) -> [u8; 16] {
    let mut a = KEYS.0.build_hasher();
    let mut b = KEYS.1.build_hasher();
    a.write(bytes);
    b.write(bytes);
    let mut out = [0u8; 16];
    out[..8].copy_from_slice(&a.finish().to_le_bytes());
    out[8..].copy_from_slice(&b.finish().to_le_bytes());
    out
}

/// One walk's state: the encoding so far and the nodes counted.
struct Walker {
    buf: Vec<u8>,
    used: i64,
    budget: i64,
    bool_as_int: bool,
}

impl Walker {
    fn len(&mut self, n: usize) {
        self.buf.extend_from_slice(&(n as u64).to_le_bytes());
    }

    /// Count one node; false once the budget is spent (Python: truncated).
    fn count(&mut self) -> bool {
        self.used += 1;
        self.used <= self.budget
    }

    /// Encode a scalar leaf. `Some(false)` refuses, `None` is not a scalar.
    fn scalar(&mut self, value: &Bound<'_, PyAny>) -> Option<bool> {
        let py = value.py();
        if value.is_none() {
            self.buf.push(b'0');
            return Some(true);
        }
        if value.is_exact_instance_of::<PyString>() {
            // SAFETY: an exact `str` (checked above), alive for the call.
            unsafe {
                let ptr = value.as_ptr();
                let kind = ffi::PyUnicode_KIND(ptr) as usize;
                let n = ffi::PyUnicode_GET_LENGTH(ptr) as usize;
                let data = ffi::PyUnicode_DATA(ptr) as *const u8;
                self.buf.push(b's');
                self.buf.push(kind as u8);
                self.len(n);
                self.buf
                    .extend_from_slice(std::slice::from_raw_parts(data, n * kind));
            }
            return Some(true);
        }
        if value.is_exact_instance_of::<PyBool>() {
            let truth = value.is(PyBool::new(py, true));
            if self.bool_as_int {
                self.buf.push(b'n');
                self.buf.extend_from_slice(&(truth as i64).to_le_bytes());
            } else {
                self.buf.push(b'b');
                self.buf.push(truth as u8);
            }
            return Some(true);
        }
        if value.is_exact_instance_of::<PyInt>() {
            let mut overflow: std::os::raw::c_int = 0;
            // SAFETY: an exact `int`; this call runs no Python code.
            let small = unsafe { ffi::PyLong_AsLongLongAndOverflow(value.as_ptr(), &mut overflow) };
            if overflow == 0 {
                if small == -1 && PyErr::take(py).is_some() {
                    return Some(false);
                }
                self.buf.push(b'n');
                self.buf.extend_from_slice(&small.to_le_bytes());
                return Some(true);
            }
            // A big int: its hex spelling is exact and has no digit limit.
            // SAFETY: `PyNumber_ToBase` returns a new reference or NULL.
            let hex = unsafe {
                Bound::from_owned_ptr_or_opt(py, ffi::PyNumber_ToBase(value.as_ptr(), 16))
            };
            let Some(hex) = hex else {
                let _ = PyErr::take(py);
                return Some(false);
            };
            let Ok(text) = hex.cast_into::<PyString>() else {
                return Some(false);
            };
            let Ok(text) = text.to_str() else {
                return Some(false);
            };
            self.buf.push(b'N');
            self.len(text.len());
            self.buf.extend_from_slice(text.as_bytes());
            return Some(true);
        }
        if value.is_exact_instance_of::<PyFloat>() {
            // SAFETY: an exact `float`.
            let x = unsafe { ffi::PyFloat_AS_DOUBLE(value.as_ptr()) };
            self.buf.push(b'f');
            self.buf.extend_from_slice(&x.to_bits().to_le_bytes());
            return Some(true);
        }
        if value.is_exact_instance_of::<PyBytes>() {
            // SAFETY: an exact `bytes`, immutable.
            let data = unsafe { value.cast_unchecked::<PyBytes>() }.as_bytes();
            self.buf.push(b'y');
            self.len(data.len());
            self.buf.extend_from_slice(data);
            return Some(true);
        }
        None
    }

    /// Encode *value* at *depth*; false when it is not plain data or the
    /// budget ran out.
    fn walk(&mut self, value: &Bound<'_, PyAny>, depth: usize) -> bool {
        if !self.count() {
            return false;
        }
        if let Some(ok) = self.scalar(value) {
            return ok;
        }
        if depth >= MAX_DEPTH {
            // Python fingerprints it by identity: not plain. This is also
            // what stops a cycle (a cycle is a path deeper than the cap).
            return false;
        }
        if let Ok(list) = value.cast_exact::<PyList>() {
            self.buf.push(b'L');
            self.len(list.len());
            for item in list.iter() {
                if !self.walk(&item, depth + 1) {
                    return false;
                }
            }
            return true;
        }
        if let Ok(tuple) = value.cast_exact::<PyTuple>() {
            self.buf.push(b'T');
            self.len(tuple.len());
            for item in tuple.iter() {
                if !self.walk(&item, depth + 1) {
                    return false;
                }
            }
            return true;
        }
        if let Ok(dict) = value.cast_exact::<PyDict>() {
            self.buf.push(b'D');
            self.len(dict.len());
            for (k, v) in dict.iter() {
                if !self.walk(&k, depth + 1) || !self.walk(&v, depth + 1) {
                    return false;
                }
            }
            return true;
        }
        // A subclass, a set, a model, a component's state: not plain.
        false
    }
}

/// The native digest of a plain-data root, its node count, and (for a root
/// list, when asked) the items' digests end to end. `None` when the value is
/// not plain data or has more than `budget` nodes.
pub fn digest_value(
    value: &Bound<'_, PyAny>,
    budget: i64,
    want_rows: bool,
) -> Option<([u8; 16], i64, Option<Vec<u8>>)> {
    let mut w = Walker {
        buf: Vec::with_capacity(256),
        used: 0,
        budget,
        bool_as_int: SABOTAGE.load(Ordering::Relaxed) == 1,
    };
    if let Ok(list) = value.cast_exact::<PyList>() {
        if !w.count() {
            return None;
        }
        let n = list.len();
        let mut rows = Vec::with_capacity(n * 16);
        for item in list.iter() {
            w.buf.clear();
            w.buf.push(DOMAIN);
            w.buf.push(b'r');
            if !w.walk(&item, 1) {
                return None;
            }
            rows.extend_from_slice(&digest_of(&w.buf));
        }
        w.buf.clear();
        w.buf.push(DOMAIN);
        w.buf.push(b'R');
        w.len(n);
        w.buf.extend_from_slice(&rows);
        let digest = digest_of(&w.buf);
        return Some((digest, w.used, want_rows.then_some(rows)));
    }
    if !(value.is_exact_instance_of::<PyDict>() || value.is_exact_instance_of::<PyTuple>()) {
        return None;
    }
    w.buf.push(DOMAIN);
    if !w.walk(value, 0) {
        return None;
    }
    Some((digest_of(&w.buf), w.used, None))
}

/// `plain_digest(value, budget, rows=False) -> (digest, nodes, row_digests) | None`.
///
/// See the module docs. `row_digests` is one `bytes` of 16 bytes per item of
/// a root list when `rows` is true, else `None`.
#[pyfunction]
#[pyo3(signature = (value, budget, rows=false))]
pub fn plain_digest<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
    budget: i64,
    rows: bool,
) -> Option<(Bound<'py, PyBytes>, i64, Option<Bound<'py, PyBytes>>)> {
    let (digest, used, row_digests) = digest_value(value, budget, rows)?;
    Some((
        PyBytes::new(py, &digest),
        used,
        row_digests.map(|r| PyBytes::new(py, &r)),
    ))
}

/// The indices whose 16-byte digests differ between two `row_digests`, or
/// `None` when the two do not hold the same number of rows.
#[pyfunction]
pub fn digest_rows_changed(a: &[u8], b: &[u8]) -> Option<Vec<usize>> {
    if a.len() != b.len() || !a.len().is_multiple_of(16) {
        return None;
    }
    Some(
        a.chunks_exact(16)
            .zip(b.chunks_exact(16))
            .enumerate()
            .filter(|(_, (x, y))| x != y)
            .map(|(i, _)| i)
            .collect(),
    )
}

/// Oracle sabotage (tests only): 1 hashes `bool` as `int`, 0 restores.
#[pyfunction]
pub fn _set_plain_digest_sabotage(mode: u8) {
    SABOTAGE.store(mode, Ordering::Relaxed);
}

#[cfg(test)]
mod tests {
    //! Run with `cargo test -p djust_live --no-default-features fingerprint`.
    use super::*;

    fn digest(py: Python<'_>, src: &str) -> Option<[u8; 16]> {
        let value = py
            .eval(&std::ffi::CString::new(src).unwrap(), None, None)
            .unwrap();
        digest_value(&value, 20_000, false).map(|d| d.0)
    }

    #[test]
    fn equal_values_have_equal_digests() {
        Python::initialize();
        Python::attach(|py| {
            for src in [
                "[{'a': 1, 'b': [1, 2.5, None]}]",
                "{'k': ('x', b'y', True)}",
                "('\\ud800', 2**100, -2**70)",
            ] {
                assert_eq!(digest(py, src), digest(py, src), "{src}");
                assert!(digest(py, src).is_some(), "{src}");
            }
        });
    }

    #[test]
    fn values_that_compare_equal_across_types_differ() {
        Python::initialize();
        Python::attach(|py| {
            let groups: &[&[&str]] = &[
                &["[1]", "[True]", "[1.0]", "['1']", "[b'1']"],
                &["[0.0]", "[-0.0]", "[0]", "[False]", "[None]", "['']"],
                &["[[1]]", "[(1,)]", "[{1: 1}]"],
                &["{'a': 1, 'b': 2}", "{'b': 2, 'a': 1}", "{'a': 2, 'b': 1}"],
                &[
                    "[2**64]",
                    "[2**64 + 1]",
                    "[-2**64]",
                    "[2**63 - 1]",
                    "[-2**63]",
                ],
                &["['ab', 'c']", "['a', 'bc']", "['abc']"],
                &["['\\ud800']", "['\\ud801']", "['\\u00e9']", "['e']"],
            ];
            for group in groups {
                let digests: Vec<_> = group.iter().map(|s| digest(py, s).unwrap()).collect();
                for i in 0..digests.len() {
                    for j in (i + 1)..digests.len() {
                        assert_ne!(digests[i], digests[j], "{} vs {}", group[i], group[j]);
                    }
                }
            }
        });
    }

    #[test]
    fn what_is_not_plain_data_is_refused() {
        Python::initialize();
        Python::attach(|py| {
            for src in [
                "[{1, 2}]",
                "[frozenset()]",
                "[object()]",
                "1",
                "'root str'",
                "[type('S', (str,), {})('x')]",
                "{(1, frozenset()): 1}",
            ] {
                assert!(digest(py, src).is_none(), "{src}");
            }
        });
    }

    #[test]
    fn the_depth_cap_and_the_budget_refuse_as_python_truncates() {
        Python::initialize();
        Python::attach(|py| {
            // A root list plus 31 nested lists: the deepest is at depth 31.
            let ok = format!("{}{}", "[".repeat(32), "]".repeat(32));
            assert!(digest(py, &ok).is_some());
            let deep = format!("{}{}", "[".repeat(33), "]".repeat(33));
            assert!(digest(py, &deep).is_none());
            let value = py.eval(c"[1, 2, 3]", None, None).unwrap();
            assert_eq!(digest_value(&value, 4, false).map(|d| d.1), Some(4));
            assert!(digest_value(&value, 3, false).is_none());
        });
    }

    #[test]
    fn row_digests_name_the_changed_rows() {
        Python::initialize();
        Python::attach(|py| {
            let a = py
                .eval(c"[{'q': 1}, {'q': 2}, {'q': 3}]", None, None)
                .unwrap();
            let b = py
                .eval(c"[{'q': 1}, {'q': 5}, {'q': 3}]", None, None)
                .unwrap();
            let (da, _, ra) = digest_value(&a, 100, true).unwrap();
            let (db, _, rb) = digest_value(&b, 100, true).unwrap();
            assert_ne!(da, db);
            assert_eq!(
                digest_rows_changed(&ra.unwrap(), &rb.unwrap()),
                Some(vec![1])
            );
            // Asked for rows or not, the list's digest is the same.
            assert_eq!(digest_value(&a, 100, false).unwrap().0, da);
            assert_eq!(digest_rows_changed(&[0; 16], &[0; 32]), None);
        });
    }
}
