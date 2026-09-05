//! Django's `stringformat`, which is CPython's `%`-formatting (#2358).
//!
//! # Why this is a scanner and not a `match` on the last character
//!
//! Django's filter body is two lines::
//!
//! ```python
//! if isinstance(value, tuple):
//!     value = str(value)
//! try:
//!     return ("%" + str(arg)) % value
//! except (ValueError, TypeError):
//!     return ""
//! ```
//!
//! So the argument is not a conversion character — it is the TAIL of a
//! printf format string, and everything CPython's `%`-grammar says about
//! flags, width, precision, length modifiers, literal text and `%%` applies
//! to it. What this replaced dispatched on `spec.chars().last()` and fell to
//! `_ => value.to_string()` for every character it had no arm for. That one
//! arm held two disjoint groups and was wrong for both:
//!
//! * specs CPython **rejects** — `"5"`, `"."`, `"-"`, `"0"`, `".2"`, `"l"`,
//!   `"%"`, and a bare `True` — where Django answers `""` and djust echoed
//!   the value. djust was MORE PERMISSIVE than Django on every one: it
//!   rendered where Django renders nothing.
//! * conversions CPython **supports** and djust did not implement — `x`,
//!   `X`, `o`, `c`, `r`, `a`, `g`, `G`, `u` — plus the trailing LITERAL
//!   (`"ss"` is `%s` followed by the letter `s`, so Django answers `'42s'`).
//!
//! Turning the catch-all into `""` fixes the first group and breaks the
//! second; leaving it fixes neither. Value-by-value patching of that arm is
//! the non-convergence CLAUDE.md's #2129 rule names, so the shape here is
//! the grammar itself: scan `"%" + spec` the way CPython scans a format
//! string, and let each conversion's own argument rule decide.
//!
//! # Everything below was measured, not recalled
//!
//! The grammar was pinned against live CPython 3.12 / Django 5.2.16 with a
//! prototype scanner before any Rust was written, over ~197,000 (spec,
//! value) pairs — a random sweep plus an exhaustive sweep of every spec up
//! to length 3 over a 41-character alphabet. Four rules that sweep found and
//! that reading the docs would not have:
//!
//! 1. **`%%` is an early-out, checked BEFORE the flags.** `"%+%"` is not a
//!    flagged literal percent, it is `ValueError: unsupported format
//!    character '%'`. There is no `%` arm in the conversion switch.
//! 2. **A LIST suppresses the unconsumed-argument check, exactly as a dict
//!    does.** CPython's guard is `PyMapping_Check(args) && !PyTuple_Check &&
//!    !PyUnicode_Check`, and `PyMapping_Check` is "has `mp_subscript`",
//!    which a list has. So `{{ p|stringformat:"%" }}` renders `'%'` for a
//!    list and `''` for an int.
//! 3. **The mapping key resolves immediately after it is parsed**, before
//!    the flags — `"%()" % {'a': 1}` is a `KeyError`, not `incomplete
//!    format`.
//! 4. **Python's `0` flag is not C's.** `"%08.5d" % 42` is `'00000042'`:
//!    zero-padding still applies when a precision is given, where C ignores
//!    it. And `"%05.2f" % inf` is `'00inf'` — the zero pad reaches a
//!    non-finite, which C also declines to do.
//!
//! # The bounded residue, named rather than silent
//!
//! Two shapes make Django **raise** (a 500) where this renders `""`:
//!
//! * `*` as a width or precision. It consumes an argument, and Django has
//!   exactly one to give, so the conversion itself then has none —
//!   `TypeError: not enough arguments`, which Django catches, so `""` is
//!   right for every value that fits a machine integer. For a value larger
//!   than `isize` the `*` conversion itself raises `OverflowError`, which
//!   Django does NOT catch.
//! * `%d` / `%i` / `%u` / `%c` on a value CPython cannot make an integer of
//!   without overflowing: `inf`, and an `int` past `0x10FFFF` for `%c`.
//!
//! Both directions of this residue render `""`, which is strictly LESS
//! permissive than a raise, and both predate this change. Pinned in
//! `python/tests/test_stringformat_grammar_2358.py::TestTheRaiseResidueIsNamed`.

use djust_core::Value;

/// Every outcome Django's `except (ValueError, TypeError)` swallows.
///
/// Deliberately carries no message: the caller's only response is `""`, and
/// a message no one reads is a message that drifts out of date.
struct Reject;

type Formatted = Result<String, Reject>;

/// The conversion characters CPython's `%`-formatting accepts for a `str`.
///
/// `%` is NOT here — it never reaches the switch, because the `%%` early-out
/// above the flag parse consumes it. `b` and `n` are not here either: `%b`
/// is bytes-formatting only and `%n` does not exist in Python at all, so
/// both are `unsupported format character`.
const CONVERSIONS: &str = "diouxXeEfFgGcrsa";

/// One parsed conversion.
struct Spec {
    conv: char,
    minus: bool,
    plus: bool,
    space: bool,
    alt: bool,
    zero: bool,
    width: Option<usize>,
    prec: Option<usize>,
    /// `%(name)s`. Resolved during the scan, not here — see rule 3 above.
    keyed: Option<Value>,
}

enum Piece {
    Lit(String),
    Conv(Spec),
}

/// `PyMapping_Check(args) && !PyTuple_Check(args) && !PyUnicode_Check(args)`.
///
/// True for a dict AND for a list, because `PyMapping_Check` asks only
/// whether the type has `mp_subscript`. Django has already replaced a tuple
/// with its `str()` by the time this runs, and a `DictView` is a Python
/// `dict_keys`/`dict_items`/`dict_values`, none of which is subscriptable.
///
/// The single effect: when true, an unconsumed argument is NOT an error, so
/// a spec with no conversion in it renders its literal text instead of `""`.
fn suppresses_unconsumed_check(value: &Value) -> bool {
    matches!(value, Value::List(_) | Value::Object(_))
}

/// Django's `stringformat` filter, whole.
pub fn apply(value: &Value, spec: &str) -> String {
    // `if isinstance(value, tuple): value = str(value)`. Load-bearing beyond
    // `%s`: it is what makes `{{ t|stringformat:"r" }}` render `"'(1, 2)'"`
    // — the repr of the tuple's STRING — rather than the tuple's own repr.
    let stringified;
    let value = match value {
        Value::Tuple(_) => {
            stringified = Value::String(value.to_string());
            &stringified
        }
        other => other,
    };

    let Ok(pieces) = scan(spec, value) else {
        return String::new();
    };
    let mut out = String::new();
    for piece in pieces {
        match piece {
            Piece::Lit(text) => out.push_str(&text),
            Piece::Conv(spec) => match format_one(value, &spec) {
                Ok(text) => out.push_str(&text),
                Err(Reject) => return String::new(),
            },
        }
    }
    out
}

/// Scan `"%" + spec` into literal chunks and conversions.
fn scan(spec: &str, value: &Value) -> Result<Vec<Piece>, Reject> {
    let src: Vec<char> = std::iter::once('%').chain(spec.chars()).collect();
    let n = src.len();
    let mut pieces: Vec<Piece> = Vec::new();
    let mut lit = String::new();
    let mut consumed = 0usize;
    let mut i = 0usize;

    while i < n {
        if src[i] != '%' {
            lit.push(src[i]);
            i += 1;
            continue;
        }
        i += 1;
        // Rule 1: the `%%` early-out, ABOVE the flag parse.
        if i < n && src[i] == '%' {
            lit.push('%');
            i += 1;
            continue;
        }

        // Rule 3: `%(name)` — parsed and RESOLVED here, before the flags.
        let mut keyed = None;
        if i < n && src[i] == '(' {
            let mut depth = 0usize;
            let start = i + 1;
            while i < n {
                match src[i] {
                    '(' => depth += 1,
                    ')' => {
                        depth -= 1;
                        if depth == 0 {
                            break;
                        }
                    }
                    _ => {}
                }
                i += 1;
            }
            if i >= n {
                return Err(Reject); // `incomplete format key`
            }
            let key: String = src[start..i].iter().collect();
            i += 1;
            let Value::Object(map) = value else {
                return Err(Reject); // `TypeError: format requires a mapping`
            };
            // A MISSING key is a `KeyError`, which Django does not catch —
            // see the bounded residue in the module docs. `""` here is the
            // less-permissive answer.
            let Some(found) = map.get(key.as_str()) else {
                return Err(Reject);
            };
            keyed = Some(found.clone());
        }

        let (mut minus, mut plus, mut space, mut alt, mut zero) =
            (false, false, false, false, false);
        while i < n {
            match src[i] {
                '-' => minus = true,
                '+' => plus = true,
                ' ' => space = true,
                '#' => alt = true,
                '0' => zero = true,
                _ => break,
            }
            i += 1;
        }

        // `*` takes its value from the argument list. Django passes exactly
        // one argument, so the conversion after it has none — `TypeError:
        // not enough arguments`, caught, `""`.
        if i < n && src[i] == '*' {
            return Err(Reject);
        }
        let digits_start = i;
        while i < n && src[i].is_ascii_digit() {
            i += 1;
        }
        let mut width: Option<usize> = None;
        if i > digits_start {
            width = Some(parse_c_int(&src[digits_start..i], i64::MAX as u64).ok_or(Reject)?);
        }

        let mut prec: Option<usize> = None;
        if i < n && src[i] == '.' {
            i += 1;
            if i < n && src[i] == '*' {
                return Err(Reject);
            }
            // A bare `.` is precision ZERO, not an absent precision:
            // `"%.f" % 1.5` is `'2'`.
            let digits_start = i;
            while i < n && src[i].is_ascii_digit() {
                i += 1;
            }
            prec = Some(parse_c_int(&src[digits_start..i], i32::MAX as u64).ok_or(Reject)?);
        }

        // A length modifier — `h`, `l`, `L` — is consumed and ignored, and
        // at most ONE is. `"%ld"` is `'42'`; `"%l"` alone is `incomplete
        // format`, which is the `l` row of #2358's group 1.
        if i < n && matches!(src[i], 'h' | 'l' | 'L') {
            i += 1;
        }

        if i >= n {
            return Err(Reject); // `ValueError: incomplete format`
        }
        let conv = src[i];
        i += 1;
        if !CONVERSIONS.contains(conv) {
            return Err(Reject); // `ValueError: unsupported format character`
        }

        consumed += 1;
        if consumed > 1 {
            return Err(Reject); // `TypeError: not enough arguments`
        }
        pieces.push(Piece::Lit(std::mem::take(&mut lit)));
        pieces.push(Piece::Conv(Spec {
            conv,
            minus,
            plus,
            space,
            alt,
            zero,
            width,
            prec,
            keyed,
        }));
    }
    pieces.push(Piece::Lit(lit));

    // Rule 2. `TypeError: not all arguments converted during string
    // formatting` — unless the argument is mapping-like, which suppresses
    // the check entirely.
    if consumed == 0 && !suppresses_unconsumed_check(value) {
        return Err(Reject);
    }
    Ok(pieces)
}

/// A width or precision field: empty is 0, and anything past `max` is the
/// `ValueError` CPython raises rather than a saturating cast.
///
/// **The two limits differ, and were BISECTED against the interpreter rather
/// than assumed** (this is the one part of #2294's `parse_s_spec` worth
/// keeping verbatim): width is a `Py_ssize_t` and precision an `int`, so
/// `"%9223372036854775808s"` raises `width too big` while `"%.2147483648s"`
/// raises `precision too big` five orders of magnitude lower.
fn parse_c_int(digits: &[char], max: u64) -> Option<usize> {
    // Leading zeros do not count toward the limit —
    // `"%00000000000000000000010s"` is width 10, verified against CPython.
    let text: String = digits.iter().collect();
    let trimmed = text.trim_start_matches('0');
    if trimmed.is_empty() {
        return Some(0);
    }
    // #2294 had a `trimmed.len() > 19 => None` short-circuit above this
    // match. It was REDUNDANT — a 20-digit string fails `parse::<u64>` and
    // falls to the same `None` — and a gate-off mutation of it changed
    // nothing the suite could see, which is the "two mechanisms on the same
    // half" shape CLAUDE.md's v1.1.1-2 rule says to delete rather than test
    // around. The 20-digit case is covered directly instead, in
    // `TestTheWidthAndPrecisionLIMITSDifferFromEachOther`.
    match trimmed.parse::<u64>() {
        Ok(n) if n <= max => Some(n as usize),
        _ => None,
    }
}

fn format_one(value: &Value, spec: &Spec) -> Formatted {
    let value = spec.keyed.as_ref().unwrap_or(value);
    match spec.conv {
        's' => pad_text(value.to_string(), spec),
        'r' => pad_text(value.py_repr(), spec),
        'a' => pad_text(ascii_escape(&value.py_repr()), spec),
        // `%c` IGNORES the precision, alone among the text conversions:
        // `"%.0c" % 65` is `'A'` where `"%.0s" % "A"` is `''`. Measured.
        'c' => Ok(pad(&char_of(value)?, spec.width, spec.minus, false, "")),
        'd' | 'i' | 'u' => pad_number(int_body(value, 10, false, spec)?, spec),
        'o' => pad_number(int_body(value, 8, false, spec)?, spec),
        'x' => pad_number(int_body(value, 16, false, spec)?, spec),
        'X' => pad_number(int_body(value, 16, true, spec)?, spec),
        _ => pad_number(float_body(value, spec)?, spec),
    }
}

/// A formatted number, split so the padding can go between its parts.
struct Number {
    negative: bool,
    /// `0x` / `0X` / `0o`, or empty. Sits between the sign and the digits.
    prefix: &'static str,
    digits: String,
}

/// `%s` / `%r` / `%a` / `%c` padding.
///
/// The precision truncates by CODE POINT, and the `0` flag is IGNORED —
/// `"%05s" % "ab"` is `'   ab'`, not `'000ab'`. Both measured.
fn pad_text(body: String, spec: &Spec) -> Formatted {
    let body = match spec.prec {
        Some(p) => body.chars().take(p).collect(),
        None => body,
    };
    Ok(pad(&body, spec.width, spec.minus, false, ""))
}

fn pad_number(num: Number, spec: &Spec) -> Formatted {
    let sign = if num.negative {
        "-"
    } else if spec.plus {
        "+"
    } else if spec.space {
        " "
    } else {
        ""
    };
    let head = format!("{sign}{}", num.prefix);
    // Rule 4: Python zero-pads even when a precision is given, and even for
    // `inf` / `nan`. A `-` still wins over `0`, as in C.
    Ok(pad(
        &format!("{head}{}", num.digits),
        spec.width,
        spec.minus,
        spec.zero && !spec.minus,
        &head,
    ))
}

/// Pad *body* to *width*, either with spaces at one end or with zeros after
/// *head* (the sign and any `0x`-style prefix, which zeros must follow).
fn pad(body: &str, width: Option<usize>, left_align: bool, zero_fill: bool, head: &str) -> String {
    let Some(width) = width else {
        return body.to_string();
    };
    let len = body.chars().count();
    if width <= len {
        return body.to_string();
    }
    let fill = width - len;
    let mut out = String::new();
    // CPython's parse accepts a width up to `PY_SSIZE_T_MAX` and then raises
    // `MemoryError`, which the filter does not catch, so Django 500s. Rust's
    // default OOM handler ABORTS the process, which is worse than either:
    // degrade to the unpadded body. No width that can actually be rendered
    // reaches this.
    if out.try_reserve(body.len() + fill).is_err() {
        return body.to_string();
    }
    if left_align {
        out.push_str(body);
        out.extend(std::iter::repeat_n(' ', fill));
    } else if zero_fill {
        out.push_str(head);
        out.extend(std::iter::repeat_n('0', fill));
        out.push_str(&body[head.len()..]);
    } else {
        out.extend(std::iter::repeat_n(' ', fill));
        out.push_str(body);
    }
    out
}

/// `%d` / `%o` / `%x` / `%X`, as CPython's argument rule has them.
///
/// `%d` takes an `int`, a `bool`, a FINITE `float` or a FINITE `Decimal` and
/// truncates toward zero. `%o` / `%x` / `%X` take an INT only — `"%x" % 1.5`
/// is a `TypeError`, and that asymmetry is why `radix` gates the float and
/// decimal arms rather than the caller doing it.
fn int_body(value: &Value, radix: u32, upper: bool, spec: &Spec) -> Result<Number, Reject> {
    let alt = spec.alt;
    let truncating_ok = radix == 10;
    let digits = match value {
        Value::Integer(n) => n.to_string(),
        Value::Bool(b) => if *b { "1" } else { "0" }.to_string(),
        Value::BigInt(d) => d.clone(),
        Value::Float(f) if truncating_ok => {
            // `None` for a non-finite, where CPython raises `OverflowError`
            // (for `inf`) or `ValueError` (for `nan`). Only the second is
            // caught; see the bounded residue in the module docs.
            djust_core::decimal::python_float_trunc_digits(*f).ok_or(Reject)?
        }
        Value::Decimal(d) if truncating_ok => djust_core::decimal::parse_decimal_parts(d)
            .and_then(|p| p.to_int_digits_trunc(djust_core::decimal::PY_INT_MAX_STR_DIGITS))
            .ok_or(Reject)?,
        // A str, None, list, dict, view or non-finite is a `TypeError`, which
        // Django catches. `"%d" % "12"` raises too — a numeric STRING is not
        // an int to `%d`, which is what keeps `{{ "12"|stringformat:"d" }}`
        // empty rather than rendering `12`.
        _ => return Err(Reject),
    };
    let (negative, magnitude) = match digits.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, digits.as_str()),
    };
    let mut converted = to_radix(magnitude, radix).ok_or(Reject)?;
    // A precision is a MINIMUM DIGIT COUNT for the integer conversions —
    // `"%.5x" % 255` is `'000ff'` and `"%.2i" % -7` is `'-07'`. It never
    // shortens, and it never empties: `"%.0d" % 0` is `'0'`, where C's is
    // the empty string.
    if let Some(p) = spec.prec {
        if converted.len() < p {
            converted = format!("{}{converted}", "0".repeat(p - converted.len()));
        }
    }
    Ok(Number {
        negative,
        // `#` only. `"%x" % 42` is `'2a'`; `"%#x" % 42` is `'0x2a'`. `%#d`
        // has no prefix at all — the base-10 arm is unreachable for `alt`.
        prefix: match (alt, radix, upper) {
            (true, 16, false) => "0x",
            (true, 16, true) => "0X",
            (true, 8, _) => "0o",
            _ => "",
        },
        digits: if upper {
            converted.to_uppercase()
        } else {
            converted
        },
    })
}

/// An exact decimal digit string, re-based.
///
/// Long division on the DIGITS rather than a parse into `u64`, because
/// `Value::BigInt` and `Value::Decimal` carry values `u128` cannot hold and
/// `"%x" % 2**70` is exact in Python. A saturating `as` cast here is the
/// #2265 class of bug — a fabricated constant, silently, where an id was
/// meant.
fn to_radix(decimal_digits: &str, radix: u32) -> Option<String> {
    if !decimal_digits.bytes().all(|b| b.is_ascii_digit()) || decimal_digits.is_empty() {
        return None;
    }
    if radix == 10 {
        // Already base 10; strip the leading zeros a caller's `007` could
        // carry, but keep a lone `0`.
        let trimmed = decimal_digits.trim_start_matches('0');
        return Some(if trimmed.is_empty() {
            "0".to_string()
        } else {
            trimmed.to_string()
        });
    }
    let mut work: Vec<u8> = decimal_digits.bytes().map(|b| b - b'0').collect();
    let mut out: Vec<char> = Vec::new();
    while work.iter().any(|d| *d != 0) {
        let mut remainder: u32 = 0;
        let mut next: Vec<u8> = Vec::with_capacity(work.len());
        for digit in &work {
            let current = remainder * 10 + u32::from(*digit);
            next.push((current / radix) as u8);
            remainder = current % radix;
        }
        out.push(std::char::from_digit(remainder, radix)?);
        // Drop leading zeros so the loop terminates in O(digits²) rather
        // than walking a growing run of them.
        let first_nonzero = next.iter().position(|d| *d != 0).unwrap_or(next.len());
        work = next.split_off(first_nonzero);
    }
    if out.is_empty() {
        return Some("0".to_string());
    }
    Some(out.into_iter().rev().collect())
}

/// `%e` / `%E` / `%f` / `%F` / `%g` / `%G`.
fn float_body(value: &Value, spec: &Spec) -> Result<Number, Reject> {
    let x = match value {
        Value::Integer(n) => *n as f64,
        Value::Bool(b) => f64::from(u8::from(*b)),
        Value::Float(f) => *f,
        Value::BigInt(d) | Value::Decimal(d) => d.parse::<f64>().map_err(|_| Reject)?,
        _ => return Err(Reject),
    };
    let upper = spec.conv.is_ascii_uppercase();
    if !x.is_finite() {
        // `'inf'` / `'nan'`, uppercased for `%E` / `%F` / `%G`, and with the
        // sign lifted out so `%+f` and the zero pad reach it. `"%05.2f" %
        // inf` is `'00inf'` — Python zero-pads a non-finite where C does not.
        let word = if x.is_nan() { "nan" } else { "inf" };
        return Ok(Number {
            negative: x.is_sign_negative() && !x.is_nan(),
            prefix: "",
            digits: if upper {
                word.to_uppercase()
            } else {
                word.to_string()
            },
        });
    }
    let magnitude = x.abs();
    let body = match spec.conv {
        'f' | 'F' => fixed(magnitude, spec.prec.unwrap_or(6), spec.alt),
        'e' | 'E' => exponential(magnitude, spec.prec.unwrap_or(6), upper, spec.alt),
        _ => general(magnitude, spec.prec.unwrap_or(6), upper, spec.alt),
    };
    Ok(Number {
        negative: x.is_sign_negative(),
        prefix: "",
        digits: body,
    })
}

fn fixed(x: f64, prec: usize, alt: bool) -> String {
    let body = format!("{x:.prec$}");
    // `%#.0f` keeps the point: `"%#.0f" % 1.0` is `'1.'`.
    if alt && prec == 0 {
        format!("{body}.")
    } else {
        body
    }
}

/// `d.ddde±dd` — CPython always writes the exponent's sign and at least two
/// digits, and Rust's `{:e}` writes neither (`4.2e1` where C writes
/// `4.200000e+01`). That difference is #2358's group 3, and it is why this
/// rewrites the exponent rather than using `{:e}` directly.
fn exponential(x: f64, prec: usize, upper: bool, alt: bool) -> String {
    let raw = format!("{x:.prec$e}");
    let (mantissa, exponent) = match raw.split_once('e') {
        Some(parts) => parts,
        None => return raw,
    };
    let exp: i32 = exponent.parse().unwrap_or(0);
    let mut mantissa = mantissa.to_string();
    if alt && prec == 0 {
        mantissa.push('.');
    }
    format!("{mantissa}{}{exp:+03}", if upper { 'E' } else { 'e' })
}

/// `%g`, which is `%e` or `%f` chosen by the value's exponent.
///
/// CPython: with `P = max(precision, 1)` significant digits, let `X` be the
/// exponent the `%e` form would carry AFTER rounding to `P` digits. Use
/// `%f` with precision `P - 1 - X` when `-4 <= X < P`, and `%e` with
/// precision `P - 1` otherwise. Without `#`, trailing fractional zeros and
/// a trailing point are then stripped — which is why `"%g" % 42` is `'42'`
/// and `"%#g" % 42` is `'42.0000'`.
///
/// The exponent is read back from the ROUNDED `%e` form rather than computed
/// with `log10`, because rounding can carry: `9.99e2` at `P = 2` rounds to
/// `1.0e+03`, whose exponent is 3 and not 2.
fn general(x: f64, prec: usize, upper: bool, alt: bool) -> String {
    let p = prec.max(1);
    let probe = format!("{x:.*e}", p - 1);
    let exp: i32 = probe
        .split_once('e')
        .and_then(|(_, e)| e.parse().ok())
        .unwrap_or(0);
    // `alt` reaches the chosen sub-format, not just the zero-stripping:
    // `"%#.g" % 0` is `'0.'` and `"%-#.G" % 1e-300` is `'1.E-300'`, both of
    // which need the point the sub-format adds at precision 0. Passing
    // `false` here made those two rows the only VALUE-class divergences the
    // 108,000-cell sweep still reported.
    let body = if exp >= -4 && exp < p as i32 {
        let decimals = (p as i32 - 1 - exp).max(0) as usize;
        fixed(x, decimals, alt)
    } else {
        exponential(x, p - 1, upper, alt)
    };
    if alt {
        return body;
    }
    strip_trailing_zeros(&body)
}

/// Drop trailing fractional zeros, and the point if nothing follows it.
///
/// Applied to the MANTISSA only: `1.20000e+06` must become `1.2e+06`, so
/// the exponent is split off first and re-attached.
fn strip_trailing_zeros(body: &str) -> String {
    let (mantissa, exponent) = match body.find(['e', 'E']) {
        Some(at) => (&body[..at], &body[at..]),
        None => (body, ""),
    };
    if !mantissa.contains('.') {
        return body.to_string();
    }
    let trimmed = mantissa.trim_end_matches('0');
    let trimmed = trimmed.strip_suffix('.').unwrap_or(trimmed);
    format!("{trimmed}{exponent}")
}

/// `%c`: `chr(n)` for an integer, or a one-character string as itself.
///
/// A `float`, `None`, list or dict is a `TypeError`; an integer outside
/// `range(0x110000)` — or a lone surrogate, which Rust's `char` cannot
/// hold — is the `OverflowError` half of the bounded residue.
fn char_of(value: &Value) -> Result<String, Reject> {
    match value {
        Value::Integer(n) => u32::try_from(*n)
            .ok()
            .and_then(char::from_u32)
            .map(String::from)
            .ok_or(Reject),
        Value::Bool(b) => Ok(String::from(char::from(u8::from(*b)))),
        Value::String(s) | Value::SafeString(s) => {
            if s.chars().count() == 1 {
                Ok(s.clone())
            } else {
                Err(Reject)
            }
        }
        _ => Err(Reject),
    }
}

/// `ascii(x)`, given `repr(x)`.
///
/// Python's `ascii` is `repr` with every non-ASCII code point escaped, and
/// the escape widens with the code point: `\xhh`, then `\uhhhh`, then
/// `\Uhhhhhhhh`. Applying it to the finished repr is exact, because repr's
/// own escaping is per-character — `ascii(['é'])` and escaping `repr(['é'])`
/// both give `['\xe9']`.
fn ascii_escape(repr: &str) -> String {
    if repr.is_ascii() {
        return repr.to_string();
    }
    let mut out = String::with_capacity(repr.len());
    for c in repr.chars() {
        if c.is_ascii() {
            out.push(c);
            continue;
        }
        let code = c as u32;
        if code <= 0xFF {
            out.push_str(&format!("\\x{code:02x}"));
        } else if code <= 0xFFFF {
            out.push_str(&format!("\\u{code:04x}"));
        } else {
            out.push_str(&format!("\\U{code:08x}"));
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn f(value: Value, spec: &str) -> String {
        apply(&value, spec)
    }

    #[test]
    fn group_one_specs_cpython_rejects_render_empty() {
        // #2358 group 1, verbatim. Every one of these echoed `42` before.
        for spec in ["5", ".", "-", "0", ".2", "l", "%", "True"] {
            assert_eq!(f(Value::Integer(42), spec), "", "spec {spec:?}");
        }
    }

    #[test]
    fn group_two_conversions_cpython_supports() {
        assert_eq!(f(Value::Integer(42), "x"), "2a");
        assert_eq!(f(Value::Integer(255), "X"), "FF");
        assert_eq!(f(Value::Integer(42), "o"), "52");
        assert_eq!(f(Value::Integer(65), "c"), "A");
        assert_eq!(f(Value::String("a".into()), "r"), "'a'");
        assert_eq!(f(Value::Integer(42), "ss"), "42s");
    }

    #[test]
    fn group_three_exponent_carries_a_sign_and_two_digits() {
        assert_eq!(f(Value::Integer(42), "e"), "4.200000e+01");
        assert_eq!(f(Value::Integer(42), "E"), "4.200000E+01");
    }

    #[test]
    fn a_list_suppresses_the_unconsumed_argument_check() {
        assert_eq!(f(Value::List(vec![Value::Integer(1)]), "%"), "%");
        assert_eq!(f(Value::Integer(1), "%"), "");
    }

    #[test]
    fn to_radix_is_exact_past_u64() {
        // 2**70, which no `u64` holds and a saturating cast would flatten.
        assert_eq!(
            to_radix("1180591620717411303424", 16).unwrap(),
            "400000000000000000"
        );
        assert_eq!(to_radix("0", 16).unwrap(), "0");
        assert_eq!(to_radix("8", 8).unwrap(), "10");
    }

    #[test]
    fn general_strips_trailing_zeros_unless_alternate() {
        assert_eq!(f(Value::Integer(42), "g"), "42");
        assert_eq!(f(Value::Integer(42), "#g"), "42.0000");
        assert_eq!(f(Value::Integer(1000000), "g"), "1e+06");
    }
}
