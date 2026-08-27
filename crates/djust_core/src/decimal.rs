//! Exact decimal digits, shared by every consumer that must not go through
//! `f64` (#2253).
//!
//! `Value::Decimal` carries a Python `Decimal`'s exact digit string (#2214) and
//! `Value::as_f64` deliberately parses it for *arithmetic* — the contract is
//! that arithmetic behaves exactly as it did when a Decimal simply WAS a
//! `Value::Float`. **`floatformat` is not arithmetic.** Django's own
//! `floatformat` converts every input — float, int, str, Decimal alike — to a
//! `Decimal` and quantizes it with `ROUND_HALF_UP`, so matching Django there
//! means decimal digits, not a double. This module is the parse half of that;
//! `djust_templates::floatformat` is the arithmetic half.
//!
//! One parse definition, not one per consumer (#1646): `expand_decimal_exponent`
//! grew its own inline copy first, and `parse_decimal_parts` is that copy
//! lifted out so the renderer and the filter cannot drift on what counts as a
//! decimal.

/// A decimal split the way Python's `Decimal.as_tuple()` splits it, except that
/// `digits` KEEPS its leading zeros.
///
/// The value is `(-1)^neg * digits * 10^exponent`.
///
/// Keeping the leading zeros is the difference that matters: `as_tuple()` drops
/// them, and two of Django's rules (the >200-digit cut-off and the scientific
/// coefficient) are defined on the dropped form while placing the decimal point
/// needs the kept form. `significant_len()` gives the `as_tuple()` length for
/// the rules that want it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DecimalParts {
    /// A leading `-`. Note this is the SIGN, not "is negative": `-0.0` has it.
    pub neg: bool,
    /// Every digit of the coefficient, leading zeros included.
    pub digits: String,
    /// Power of ten the coefficient is scaled by. Negative means decimals.
    pub exponent: i64,
}

impl DecimalParts {
    /// `len(Decimal.as_tuple().digits)` — leading zeros dropped, floored at 1.
    ///
    /// `Decimal('0.00').as_tuple().digits` is `(0,)`, not empty, which is why
    /// the floor is there rather than being an off-by-one guard.
    pub fn significant_len(&self) -> usize {
        let trimmed = self.digits.trim_start_matches('0').len();
        if trimmed == 0 {
            1
        } else {
            trimmed
        }
    }

    /// Django's `abs(exponent) + len(digits) > 200` cut-off, which it applies
    /// *"to avoid high memory consumption and potential denial-of-service
    /// attacks"*. Both `floatformat` and `numberformat.format` use it.
    pub fn over_django_digit_cutoff(&self) -> bool {
        self.exponent.unsigned_abs() as u128 + self.significant_len() as u128 > 200
    }

    /// Python's `int(Decimal)` — truncation toward zero — as an `i128`, or
    /// `None` when the magnitude does not fit.
    ///
    /// `i128` rather than `i64` because `int(Decimal('9007199254740993'))` is
    /// the exact 2^53+1 and `as_f64().map(|f| f as i64)` is not: a double holds
    /// ~15 significant digits, so `add` was off by one from 2^53 upward and
    /// saturated to `i64::MAX` from 2^63 upward (#2253).
    ///
    /// Nothing is materialised from the exponent: the 39-digit ceiling is
    /// checked FIRST, so `Decimal('1E+400000000')` returns `None` rather than
    /// asking for a 400 MB string. Python has no such ceiling, which is the
    /// documented boundary — beyond it `add` gives up rather than guessing.
    pub fn to_i128_trunc(&self) -> Option<i128> {
        // Digits left of the point. Saturating because `exponent` may be near
        // `i64::MIN` for a tag-supplied string.
        let int_len = (self.digits.len() as i64).saturating_add(self.exponent);
        if int_len <= 0 {
            // |value| < 1. `int(Decimal('-0.5'))` is 0, unsigned.
            return Some(0);
        }
        // i128::MAX has 39 digits; anything longer cannot fit, and rejecting
        // here is what bounds the `repeat` below.
        if int_len > 39 {
            return None;
        }
        let magnitude: i128 = if self.exponent >= 0 {
            format!("{}{}", self.digits, "0".repeat(self.exponent as usize)).parse()
        } else {
            self.digits[..int_len as usize].parse()
        }
        .ok()?;
        Some(if self.neg { -magnitude } else { magnitude })
    }

    /// The fixed-point form, as `(integer digits, fractional digits)`.
    ///
    /// Neither part is normalized: the integer part keeps its leading zeros and
    /// may be empty, and the fractional part keeps its trailing zeros — the
    /// SCALE is part of a Decimal's value and dropping it here would lose the
    /// distinction between `0` and `0.00`.
    ///
    /// Only safe to call after `over_django_digit_cutoff()` has been checked:
    /// the zero padding is bounded by `|exponent|`, which that cut-off bounds.
    pub fn to_fixed(&self) -> (String, String) {
        if self.exponent >= 0 {
            let pad = self.exponent as usize;
            (format!("{}{}", self.digits, "0".repeat(pad)), String::new())
        } else {
            let k = self.exponent.unsigned_abs() as usize;
            if k >= self.digits.len() {
                (
                    String::new(),
                    format!("{}{}", "0".repeat(k - self.digits.len()), self.digits),
                )
            } else {
                let (i, f) = self.digits.split_at(self.digits.len() - k);
                (i.to_string(), f.to_string())
            }
        }
    }
}

/// Split a decimal literal into [`DecimalParts`], or `None` if it is not one.
///
/// `None` covers exactly what Python's `Decimal(s)` raises `InvalidOperation`
/// for **plus the non-finite forms** (`NaN`, `Infinity`, `sNaN`), which Python
/// accepts and this does not. Callers need to tell those apart from garbage
/// anyway — Django's `floatformat` returns its input unchanged for a non-finite
/// value and `""` for garbage — so folding them together here would only move
/// the discrimination somewhere less obvious. [`is_non_finite`] is the other
/// half.
///
/// Surrounding whitespace is NOT accepted, though `Decimal(" 7 ")` is: this is
/// the parse `expand_decimal_exponent` has always done, and widening it would
/// change what a tag-supplied `Value::Decimal` renders as. Callers that want
/// Python's tolerance trim first.
pub fn parse_decimal_parts(raw: &str) -> Option<DecimalParts> {
    let (neg, rest) = match raw.strip_prefix('-') {
        Some(r) => (true, r),
        None => (false, raw.strip_prefix('+').unwrap_or(raw)),
    };
    let (mantissa, str_exp) = match rest.find(['e', 'E']) {
        Some(i) => (&rest[..i], rest[i + 1..].parse::<i64>().ok()?),
        None => (rest, 0),
    };
    let (int_part, frac_part) = match mantissa.split_once('.') {
        Some((i, f)) => (i, f),
        None => (mantissa, ""),
    };
    // An absent coefficient is not a zero. Without this `""` parses as `0`,
    // `-` as `-0`, and `.`, `+`, `E+5`, `e5` all as `0` — reachable, because
    // the binary Decimal tag lets a `Value::Decimal` hold any string.
    if int_part.is_empty() && frac_part.is_empty() {
        return None;
    }
    // Letters get past the check above when an exponent is present, so
    // `abcE+5` needs its own rejection or the exponent machinery is applied to
    // it and renders `abc00000`.
    if !int_part
        .bytes()
        .chain(frac_part.bytes())
        .all(|b| b.is_ascii_digit())
    {
        return None;
    }
    Some(DecimalParts {
        neg,
        digits: format!("{int_part}{frac_part}"),
        // SATURATING: `str_exp` is attacker-choosable text, so
        // `1.5E-9223372036854775808` overflows this subtraction — a panic in
        // debug, a silent wrap in release. Saturating picks the right BRANCH
        // (a magnitude that large is far past every cut-off above) without
        // claiming to reproduce unbounded arithmetic. Unreachable from a real
        // `Decimal`: CPython's `MAX_EMAX` is 999999999999999999.
        exponent: str_exp.saturating_sub(frac_part.len() as i64),
    })
}

/// Is this one of Python's non-finite `Decimal` spellings?
///
/// `Decimal` accepts these case-insensitively, with an optional sign, and — for
/// the NaNs — with a digit payload (`Decimal('NaN123')` is valid, `nanny` is
/// not). Callers need this because `Decimal('NaN')` PARSES in Python while
/// `parse_decimal_parts` rejects it, and Django's `floatformat` treats the two
/// rejections differently: a non-finite returns the input unchanged, garbage
/// returns `""`.
pub fn is_non_finite(raw: &str) -> bool {
    let body = raw
        .strip_prefix(['-', '+'])
        .unwrap_or(raw)
        .to_ascii_lowercase();
    if body == "inf" || body == "infinity" {
        return true;
    }
    let payload = body
        .strip_prefix("snan")
        .or_else(|| body.strip_prefix("nan"));
    matches!(payload, Some(p) if p.bytes().all(|b| b.is_ascii_digit()))
}

/// `repr(float)` as CPython writes it.
///
/// Not `Display`: Rust writes `100000000000000000000` where Python writes
/// `1e+20`, and Django's `floatformat` returns `str(text)` verbatim on three of
/// its give-up paths, so the two spellings are user-visible. Rust's `{:e}`
/// already gives the shortest round-tripping digits — the work here is only
/// CPython's choice of *when* to use exponent form, and its two-digit exponent.
///
/// `Value::Display` is NOT routed through this: `{{ 1e20 }}` correctly renders
/// `100000000000000000000` because Django's `{{ }}` path is
/// `numberformat.format`, which converts an exponent-form float to a `Decimal`
/// first. Django really does render the same float two ways depending on the
/// filter; so does this.
pub fn python_float_repr(f: f64) -> String {
    if f.is_nan() {
        return "nan".to_string();
    }
    if f.is_infinite() {
        return if f < 0.0 { "-inf" } else { "inf" }.to_string();
    }
    // `{:e}` is shortest-round-trip, so `digits`/`exp10` below are exactly the
    // pair CPython's `repr` works from.
    let sci = format!("{f:e}"); // e.g. "1.5e-7", "-1e20", "0e0"
    let (mantissa, exp_str) = sci.split_once('e').expect("{:e} always emits an e");
    let exp10: i32 = exp_str
        .parse()
        .expect("{:e} always emits an integer exponent");
    let (sign, magnitude) = match mantissa.strip_prefix('-') {
        Some(m) => ("-", m),
        None => ("", mantissa),
    };
    let digits: String = magnitude.chars().filter(|c| *c != '.').collect();

    // CPython's repr switches to exponent form below 1e-4 and at 1e16 and up.
    // `0.0001` is `0.0001`, `0.00001` is `1e-05`; `1e15` is `1000000000000000.0`,
    // `1e16` is `1e+16`.
    if !(-4..16).contains(&exp10) {
        let coefficient = if digits.len() == 1 {
            digits
        } else {
            format!("{}.{}", &digits[..1], &digits[1..])
        };
        // Python pads the exponent to two digits: `1e+16`, `1e-07`, `1e+300`.
        return format!("{sign}{coefficient}e{}{:02}", exp_sign(exp10), exp10.abs());
    }
    if exp10 >= 0 {
        let point = exp10 as usize + 1;
        if point >= digits.len() {
            // Integral: Python keeps a trailing `.0`.
            return format!("{sign}{}{}.0", digits, "0".repeat(point - digits.len()));
        }
        let (i, fr) = digits.split_at(point);
        format!("{sign}{i}.{fr}")
    } else {
        let zeros = (-exp10 - 1) as usize;
        format!("{sign}0.{}{}", "0".repeat(zeros), digits)
    }
}

fn exp_sign(e: i32) -> char {
    if e < 0 {
        '-'
    } else {
        '+'
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_the_shapes_expand_decimal_exponent_accepts() {
        let p = parse_decimal_parts("1.50").unwrap();
        assert_eq!(p.digits, "150");
        assert_eq!(p.exponent, -2);
        assert!(!p.neg);
        let p = parse_decimal_parts("-1E+3").unwrap();
        assert_eq!((p.neg, p.digits.as_str(), p.exponent), (true, "1", 3));
    }

    #[test]
    fn rejects_what_python_raises_invalid_operation_for() {
        for bad in [
            "", "-", ".", "+", "E+5", "e5", "abcE+5", "xyz", "1.2.3", " 7 ",
        ] {
            assert!(parse_decimal_parts(bad).is_none(), "accepted {bad:?}");
        }
    }

    #[test]
    fn non_finite_is_recognised_with_either_sign_and_case() {
        for s in ["NaN", "nan", "-Infinity", "inf", "sNaN", "+Inf"] {
            assert!(is_non_finite(s), "missed {s}");
        }
        assert!(!is_non_finite("1.5"));
        assert!(!is_non_finite("infinite_loop"));
    }

    #[test]
    fn significant_len_floors_at_one_for_a_zero_coefficient() {
        assert_eq!(parse_decimal_parts("0.00").unwrap().significant_len(), 1);
        assert_eq!(parse_decimal_parts("0").unwrap().significant_len(), 1);
        assert_eq!(parse_decimal_parts("100").unwrap().significant_len(), 3);
    }

    #[test]
    fn to_fixed_keeps_the_scale() {
        let f = |s: &str| parse_decimal_parts(s).unwrap().to_fixed();
        assert_eq!(f("1.50"), ("1".into(), "50".into()));
        assert_eq!(f("1E+3"), ("1000".into(), "".into()));
        assert_eq!(f("1E-3"), ("".into(), "001".into()));
        // Leading zeros are KEPT, so `0.00`'s integer part is the authored `0`,
        // not an empty string — `to_fixed` does not normalize either half.
        assert_eq!(f("0.00"), ("0".into(), "00".into()));
        assert_eq!(f("123"), ("123".into(), "".into()));
    }

    #[test]
    fn to_i128_trunc_is_exact_where_as_f64_is_not() {
        let i = |s: &str| parse_decimal_parts(s).unwrap().to_i128_trunc();
        // 2^53 + 1 — the first integer a double cannot hold.
        assert_eq!(i("9007199254740993"), Some(9007199254740993));
        assert_eq!(i("-9007199254740993"), Some(-9007199254740993));
        // Past i64 entirely.
        assert_eq!(
            i("12345678901234567890.123456789"),
            Some(12345678901234567890)
        );
        // Truncation toward zero, both signs, and no `-0`.
        assert_eq!(i("19.99"), Some(19));
        assert_eq!(i("-19.99"), Some(-19));
        assert_eq!(i("-0.5"), Some(0));
        assert_eq!(i("0.00"), Some(0));
        assert_eq!(i("1E+3"), Some(1000));
        assert_eq!(i("1E-3"), Some(0));
    }

    #[test]
    fn to_i128_trunc_refuses_rather_than_allocating_from_the_exponent() {
        // 40 digits — one past i128's ceiling.
        assert_eq!(parse_decimal_parts("1E+39").unwrap().to_i128_trunc(), None);
        // The DoS shape: twelve bytes that would expand to 400 MB.
        assert_eq!(
            parse_decimal_parts("1E+400000000").unwrap().to_i128_trunc(),
            None
        );
        // A near-`i64::MIN` exponent must not overflow the length arithmetic.
        assert_eq!(
            parse_decimal_parts("1.5E-9223372036854775807")
                .unwrap()
                .to_i128_trunc(),
            Some(0)
        );
    }

    #[test]
    fn python_float_repr_matches_cpython_on_the_switch_points() {
        // Every one of these was read off CPython, not derived.
        assert_eq!(python_float_repr(1e16), "1e+16");
        assert_eq!(python_float_repr(1e15), "1000000000000000.0");
        assert_eq!(python_float_repr(1e-4), "0.0001");
        assert_eq!(python_float_repr(1e-5), "1e-05");
        assert_eq!(python_float_repr(1e20), "1e+20");
        assert_eq!(python_float_repr(1e300), "1e+300");
        assert_eq!(python_float_repr(1.5e-7), "1.5e-07");
        assert_eq!(python_float_repr(-3.5), "-3.5");
        assert_eq!(python_float_repr(0.0), "0.0");
        assert_eq!(python_float_repr(-0.0), "-0.0");
        assert_eq!(python_float_repr(19.99), "19.99");
        assert_eq!(python_float_repr(f64::NAN), "nan");
        assert_eq!(python_float_repr(f64::INFINITY), "inf");
        assert_eq!(python_float_repr(f64::NEG_INFINITY), "-inf");
    }
}
