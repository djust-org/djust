//! `floatformat`, as `django/template/defaultfilters.py` writes it (#2253).
//!
//! ## Why this is not `format!("{f:.n$}")`
//!
//! Django's `floatformat` does not format a float. It converts EVERY input —
//! float, int, str, `Decimal` alike — to a `Decimal` and quantizes it with
//! `ROUND_HALF_UP`. The engine's previous implementation formatted an `f64`
//! with Rust's `{:.n$}`, which differs on four independent axes, only one of
//! which is about `Decimal` at all:
//!
//! 1. **Rounding mode.** Rust rounds the binary double half-to-even;
//!    `Decimal.quantize(ROUND_HALF_UP)` rounds the decimal digits half-away
//!    -from-zero. `2.675|floatformat:2` is `2.68` in Django and was `2.67` here.
//! 2. **A negative argument** means "at most this many places" — Django's
//!    DEFAULT is `-1`, not `1`. `Decimal('0.00')|floatformat` is `0`, and
//!    `34.23234|floatformat:"-3"` is `34.232`. `"-3".parse::<usize>()` fails,
//!    so every negative argument silently fell back to one place.
//! 3. **The `g` suffix** forces thousand-separator grouping. It was stripped
//!    from the argument and then ignored.
//! 4. **Precision.** Only here does `Value::Decimal` enter: `as_f64()` on a
//!    29-digit money value loses everything past the 15th digit.
//!
//! #2214's contract — "exact rendering and transport, with `as_f64()` for
//! arithmetic" — is upheld, not overturned: `as_f64` is still what `{% if %}`
//! and `add` compare through. `floatformat` is *formatting*, which is the half
//! that contract puts on the exact side.
//!
//! ## What is NOT covered
//!
//! * **`u`/`gu` under overridden number settings.** Django's `u` re-reads
//!   `settings.DECIMAL_SEPARATOR` / `THOUSAND_SEPARATOR` / `NUMBER_GROUPING`
//!   rather than the active locale's. Only the LOCALIZED format is pushed to
//!   Rust (`render_env.apply_number_format`), so `u` here emits the Django
//!   DEFAULTS for those settings (`.`, `,`, grouping 0) — correct unless a
//!   project overrides them, in which case `u`/`gu` is unlocalized where
//!   Django would have used the override.
//! * **Give-up paths on a `Value::Float`.** Django returns `str(text)`
//!   verbatim when the argument is unparseable, the value is non-finite, or the
//!   value is past the 200-digit cut-off. That string is `repr(float)`, which
//!   [`python_float_repr`] reproduces — but `nan` reaches this function as
//!   `Value::Float(f64::NAN)` only when it came from Python; djust's own
//!   `Display` for a float still writes `NaN`, so `{{ p }}` and
//!   `{{ p|floatformat:"x" }}` disagree with each other for a NaN. That is the
//!   `Display` gap, not this one.
//! * **Arguments past [`MAX_PLACES`]** return the input unchanged rather than
//!   materialising the digits. Django accepts up to about a million places and
//!   raises `InvalidOperation` above that; djust cannot raise, and a template
//!   that asks for a megabyte of zeros gets its input back.

use djust_core::decimal;
use djust_core::locale;
use djust_core::Value;

/// The largest `abs(p)` we will materialise, in decimal places.
///
/// Django's own ceiling is `Decimal(1).scaleb(-abs(p))` raising
/// `InvalidOperation`, which happens somewhere between 10^6 and 10^9 places —
/// verified: `1000000` quantizes, `1000000000` raises. This bound sits at the
/// same order so no realistic template changes behaviour, while keeping a
/// template argument from asking for an unbounded allocation.
pub const MAX_PLACES: i64 = 1_000_000;

/// `{{ value|floatformat:arg }}`.
///
/// `arg_was_quoted` separates Django's `int(arg)` on a *string* (`"2.5"` raises
/// `ValueError`, so the filter gives up) from `int()` on a *float literal*
/// (`2.5` truncates to 2). Same distinction `add` draws, same reason (#2203).
pub fn floatformat(value: &Value, arg: Option<&str>, arg_was_quoted: bool) -> Value {
    // `str(text)` — what Django returns verbatim from all three give-up paths,
    // and what it feeds to `Decimal(...)`.
    let input_val: String = match value {
        // `str(Decimal('1E+3'))` is `1E+3`, NOT the expanded `1000` that `{{ p }}`
        // renders. Django really does spell the same value two ways depending on
        // which path it takes; using `to_string()` here would quietly "fix" that
        // and diverge.
        Value::Decimal(d) => d.clone(),
        Value::Float(f) => decimal::python_float_repr(*f),
        Value::Integer(n) => n.to_string(),
        Value::Bool(b) => if *b { "True" } else { "False" }.to_string(),
        Value::String(s) => s.clone(),
        other => other.to_string(),
    };

    // ORDER IS LOAD-BEARING. Django parses the VALUE before the ARGUMENT, and
    // the two give-up paths return different things: an unusable value gives
    // `""`, an unusable argument gives the input back. Parsing the argument
    // first makes `{{ "abc"|floatformat:"x" }}` render `abc` where Django
    // renders nothing. Measured — the first version of this port had it
    // backwards and the differential found the four cells.
    //
    // `d = Decimal(input_val)`, with Python's tolerance for surrounding
    // whitespace (`Decimal('  7  ')` is 7).
    let trimmed = input_val.trim();
    let parts = match decimal::parse_decimal_parts(trimmed) {
        Some(p) => p,
        // `Decimal('NaN')` and `Decimal('Infinity')` PARSE in Python; it is the
        // `int(d)` below that raises, and Django catches that and returns the
        // input. Same destination, reached one step earlier.
        None if decimal::is_non_finite(trimmed) => return Value::String(input_val),
        // `except InvalidOperation: d = Decimal(str(float(text)))` — the branch
        // that makes `{{ True|floatformat }}` render `1`.
        None => match coerce_float(value, trimmed) {
            Some(f) if f.is_finite() => match decimal::parse_decimal_parts(
                decimal::python_float_repr(f).trim_start_matches('+'),
            ) {
                Some(p) => p,
                // Unreachable: `python_float_repr` of a finite float always
                // parses. Fail soft rather than unwrap on the render path.
                None => return Value::String(input_val),
            },
            // `float('nan')`/`float('inf')` -> `Decimal` fine -> `int(d)` raises.
            Some(_) => return Value::String(input_val),
            // `except (ValueError, InvalidOperation, TypeError): return ""`.
            None => return Value::String(String::new()),
        },
    };

    // `p = int(arg)`, which Django reaches only once the value has parsed.
    // `p` keeps its SIGN: `abs(p)` is the precision and `p <= 0` selects
    // Django's drop-the-fraction branch, so both come off ONE parse rather than
    // two that could disagree (#1646).
    let (p, force_grouping, use_l10n) = match parse_arg(arg, arg_was_quoted) {
        Some(spec) => spec,
        None => return Value::String(input_val),
    };
    let places = p.unsigned_abs();

    // Django's DoS cut-off, before anything allocates from the exponent.
    if parts.over_django_digit_cutoff() {
        return Value::String(input_val);
    }
    if places > MAX_PLACES as u64 {
        return Value::String(input_val);
    }
    let places = places as usize;

    let (int_str, frac_str) = parts.to_fixed();

    // `m = int(d) - d` is zero exactly when there is no fractional part.
    // `if not m and p <= 0: return number_format("%d" % int(d), 0, ...)`.
    let has_frac = frac_str.bytes().any(|b| b != b'0');
    if !has_frac && p <= 0 {
        let digits = strip_leading_zeros(&int_str);
        // `"%d" % int(Decimal('-0.0'))` is `0`, not `-0`.
        let sign = if parts.neg && digits != "0" { "-" } else { "" };
        return Value::String(finish(&format!("{sign}{digits}"), use_l10n, force_grouping));
    }

    let (rint, rfrac) = quantize_half_up(&int_str, &frac_str, places);
    // Django: `if sign and rounded_d` — the sign is dropped when the ROUNDED
    // value is zero, so `-0.04|floatformat` is `0.0` and not `-0.0`.
    let rounded_is_zero = !rint.bytes().any(|b| b != b'0') && !rfrac.bytes().any(|b| b != b'0');
    let sign = if parts.neg && !rounded_is_zero {
        "-"
    } else {
        ""
    };
    let number = if places == 0 {
        format!("{sign}{rint}")
    } else {
        format!("{sign}{rint}.{rfrac}")
    };
    Value::String(finish(&number, use_l10n, force_grouping))
}

/// `(p, force_grouping, use_l10n)`, or `None` for Django's
/// `except ValueError: return input_val`.
fn parse_arg(arg: Option<&str>, arg_was_quoted: bool) -> Option<(i64, bool, bool)> {
    // No argument is Django's `arg=-1` — an INT, so no suffix parsing and no
    // `int()` failure. The default being MINUS one is why
    // `{{ p|floatformat }}` on an integral value drops the `.0`.
    let Some(raw) = arg else {
        return Some((-1, false, true));
    };
    let mut force_grouping = false;
    let mut use_l10n = true;
    let mut body = raw;
    // Django gates this on `isinstance(arg, str)`. Applied unconditionally here
    // because an unquoted template literal is digits and a sign — it can never
    // end in `g` or `u` — so the two only differ for an argument that is not a
    // literal Django would accept either.
    if let Some(rest) = body.strip_suffix("gu").or_else(|| body.strip_suffix("ug")) {
        force_grouping = true;
        use_l10n = false;
        body = rest;
    } else if let Some(rest) = body.strip_suffix('g') {
        force_grouping = true;
        body = rest;
    } else if let Some(rest) = body.strip_suffix('u') {
        use_l10n = false;
        body = rest;
    }
    // `arg = arg[:-1] or -1` — a bare `"g"` means "default precision, grouped".
    if body.is_empty() {
        return Some((-1, force_grouping, use_l10n));
    }
    Some((
        parse_int_like(body, arg_was_quoted)?,
        force_grouping,
        use_l10n,
    ))
}

/// Python's `int(arg)`: exact for an integer, truncating for a float, and a
/// `ValueError` for a STRING that merely looks like a float.
fn parse_int_like(body: &str, arg_was_quoted: bool) -> Option<i64> {
    let s = body.trim();
    if let Ok(v) = s.parse::<i64>() {
        return Some(v);
    }
    if arg_was_quoted {
        // `int("2.5")` raises; Django gives up and returns the input.
        return None;
    }
    match s.parse::<f64>() {
        Ok(f) if f.is_finite() => Some(f.trunc() as i64),
        _ => None,
    }
}

/// Python's `float(text)` for the values that reach Django's second `Decimal`
/// attempt — everything numeric having already parsed as a decimal.
fn coerce_float(value: &Value, trimmed: &str) -> Option<f64> {
    match value {
        Value::Bool(b) => Some(if *b { 1.0 } else { 0.0 }),
        Value::String(_) => trimmed.parse::<f64>().ok(),
        // `float(None)`, `float([1, 2])`, `float({})` all raise TypeError.
        _ => None,
    }
}

/// Round the magnitude to `places` decimals, `ROUND_HALF_UP`.
///
/// Half-up on a magnitude with the sign held aside is "ties away from zero",
/// which is what `decimal.ROUND_HALF_UP` means — testing the first dropped
/// digit against `5` covers both the tie and every value above it.
fn quantize_half_up(int_str: &str, frac_str: &str, places: usize) -> (String, String) {
    if frac_str.len() <= places {
        return (
            strip_leading_zeros(int_str),
            format!("{frac_str}{}", "0".repeat(places - frac_str.len())),
        );
    }
    let (keep, dropped) = frac_str.split_at(places);
    let round_up = dropped.as_bytes()[0] >= b'5';
    let mut combined = format!("{int_str}{keep}");
    if round_up {
        combined = increment_digits(&combined);
    }
    let split = combined.len() - places;
    let (i, f) = combined.split_at(split);
    (strip_leading_zeros(i), f.to_string())
}

/// Add one to a string of decimal digits, growing it on an all-nines carry.
fn increment_digits(digits: &str) -> String {
    let mut out: Vec<u8> = digits.as_bytes().to_vec();
    let mut i = out.len();
    while i > 0 {
        i -= 1;
        if out[i] == b'9' {
            out[i] = b'0';
        } else {
            out[i] += 1;
            return String::from_utf8(out).expect("digits stay ASCII");
        }
    }
    // Every digit was a nine (or the string was empty): 999 -> 1000.
    format!("1{}", String::from_utf8(out).expect("digits stay ASCII"))
}

fn strip_leading_zeros(s: &str) -> String {
    let t = s.trim_start_matches('0');
    if t.is_empty() {
        "0".to_string()
    } else {
        t.to_string()
    }
}

/// Django's `formats.number_format(number, abs(p), use_l10n, force_grouping)`.
///
/// The truncate-and-pad half is a no-op here — `number` already carries exactly
/// `abs(p)` decimals — so what is left is the separator and the grouping.
fn finish(number: &str, use_l10n: bool, force_grouping: bool) -> String {
    if use_l10n {
        locale::localize_number_forced(number, force_grouping)
    } else {
        // `use_l10n=False` reads the raw settings, whose defaults are `.` and a
        // grouping of 0 — i.e. exactly the digits already in hand, grouped or
        // not. See the module docstring for the overridden-settings gap.
        number.to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ff(v: Value, arg: Option<&str>) -> String {
        match floatformat(&v, arg, true) {
            Value::String(s) => s,
            other => other.to_string(),
        }
    }

    #[test]
    fn the_default_argument_is_minus_one_not_one() {
        assert_eq!(ff(Value::Decimal("0.00".into()), None), "0");
        assert_eq!(ff(Value::Decimal("34.00000".into()), None), "34");
        assert_eq!(ff(Value::Decimal("34.23234".into()), None), "34.2");
        assert_eq!(ff(Value::Integer(10000), None), "10000");
    }

    #[test]
    fn rounding_is_half_up_on_the_digits_not_half_even_on_the_double() {
        assert_eq!(ff(Value::Decimal("2.675".into()), Some("2")), "2.68");
        assert_eq!(ff(Value::Float(2.675), Some("2")), "2.68");
        assert_eq!(ff(Value::Decimal("0.5".into()), Some("0")), "1");
        assert_eq!(ff(Value::Decimal("2.5".into()), Some("0")), "3");
        assert_eq!(ff(Value::Decimal("-2.5".into()), Some("0")), "-3");
    }

    #[test]
    fn a_negative_argument_means_at_most_that_many_places() {
        assert_eq!(ff(Value::Decimal("34.23234".into()), Some("-3")), "34.232");
        assert_eq!(ff(Value::Decimal("34.00000".into()), Some("-3")), "34");
        assert_eq!(ff(Value::Decimal("34.26000".into()), Some("-3")), "34.260");
    }

    #[test]
    fn the_exact_digits_survive_past_what_a_double_holds() {
        let huge = Value::Decimal("12345678901234567890.123456789".into());
        assert_eq!(ff(huge.clone(), None), "12345678901234567890.1");
        assert_eq!(ff(huge.clone(), Some("2")), "12345678901234567890.12");
        assert_eq!(ff(huge, Some("5")), "12345678901234567890.12346");
    }

    #[test]
    fn a_rounded_to_zero_negative_loses_its_sign() {
        assert_eq!(ff(Value::Decimal("-0.04".into()), None), "0.0");
        assert_eq!(ff(Value::Decimal("-0.0".into()), Some("2")), "0.00");
        assert_eq!(ff(Value::Decimal("-0.4".into()), Some("0")), "0");
        assert_eq!(ff(Value::Decimal("-0.6".into()), Some("0")), "-1");
    }

    #[test]
    fn non_numeric_inputs_take_djangos_two_give_up_paths() {
        // `Decimal('abc')` and `float('abc')` both raise -> "".
        assert_eq!(ff(Value::String("abc".into()), None), "");
        assert_eq!(ff(Value::None, None), "");
        assert_eq!(ff(Value::Missing, None), "");
        // A string that IS a number is coerced, as Django coerces it.
        assert_eq!(ff(Value::String("34.23234".into()), Some("2")), "34.23");
        assert_eq!(ff(Value::String("  7  ".into()), None), "7");
        assert_eq!(ff(Value::String("1e5".into()), None), "100000");
        // `float(True)` is 1.0 — the second attempt, not the first.
        assert_eq!(ff(Value::Bool(true), Some("2")), "1.00");
    }

    #[test]
    fn an_unparseable_argument_returns_the_input_verbatim() {
        // `int("x")` raises ValueError -> `return input_val`, and input_val for
        // a Decimal is `str()`, NOT the expanded render.
        assert_eq!(ff(Value::Decimal("1E+3".into()), Some("x")), "1E+3");
        // A quoted float-looking argument is a string to `int()`.
        assert_eq!(ff(Value::Decimal("1.5555".into()), Some("2.5")), "1.5555");
        // ...but an UNQUOTED float literal truncates.
        match floatformat(&Value::Decimal("1.5555".into()), Some("2.5"), false) {
            Value::String(s) => assert_eq!(s, "1.56"),
            other => panic!("expected a string, got {other:?}"),
        }
    }

    /// The two give-up paths return DIFFERENT things, so which is reached first
    /// is observable. Django parses the value first.
    #[test]
    fn an_unusable_value_beats_an_unusable_argument() {
        assert_eq!(ff(Value::String("abc".into()), Some("x")), "");
        assert_eq!(ff(Value::None, Some("x")), "");
        assert_eq!(ff(Value::List(vec![Value::Integer(1)]), Some("x")), "");
    }

    #[test]
    fn past_the_200_digit_cutoff_the_input_comes_back_unchanged() {
        assert_eq!(ff(Value::Decimal("1E+400".into()), Some("2")), "1E+400");
        assert_eq!(ff(Value::Decimal("1E-400".into()), Some("2")), "1E-400");
    }

    #[test]
    fn an_argument_past_max_places_does_not_allocate_it() {
        let arg = (MAX_PLACES + 1).to_string();
        assert_eq!(ff(Value::Decimal("1.5".into()), Some(&arg)), "1.5");
    }

    #[test]
    fn a_carry_grows_the_integer_part() {
        assert_eq!(ff(Value::Decimal("999.999".into()), Some("2")), "1000.00");
        assert_eq!(ff(Value::Decimal("9.995".into()), None), "10.0");
        assert_eq!(increment_digits("999"), "1000");
        assert_eq!(increment_digits("129"), "130");
    }

    #[test]
    fn the_u_suffix_leaves_the_digits_unlocalized() {
        // No thread-local format is set in a unit test, so the localized path is
        // already a no-op; what this pins is that `u` strips off the argument
        // rather than being parsed as part of the precision.
        assert_eq!(ff(Value::Decimal("1.555".into()), Some("2u")), "1.56");
        assert_eq!(ff(Value::Decimal("1.555".into()), Some("u")), "1.6");
        assert_eq!(ff(Value::Decimal("1.555".into()), Some("2gu")), "1.56");
        assert_eq!(ff(Value::Decimal("1.555".into()), Some("2ug")), "1.56");
    }

    #[test]
    fn quantize_half_up_pads_rather_than_rounds_when_there_is_room() {
        assert_eq!(
            quantize_half_up("1", "5", 3),
            ("1".to_string(), "500".to_string())
        );
        assert_eq!(
            quantize_half_up("0", "005", 2),
            ("0".to_string(), "01".to_string())
        );
        assert_eq!(
            quantize_half_up("0", "004999", 2),
            ("0".to_string(), "00".to_string())
        );
    }

    /// `DecimalParts` is the only thing standing between a tag-supplied string
    /// and the exponent machinery; this pins that garbage still gives up rather
    /// than rendering nonsense.
    #[test]
    fn a_tag_supplied_garbage_decimal_gives_up() {
        assert_eq!(ff(Value::Decimal("abcE+5".into()), Some("2")), "");
        assert_eq!(ff(Value::Decimal("".into()), Some("2")), "");
        assert_eq!(ff(Value::Decimal("NaN".into()), Some("2")), "NaN");
        assert_eq!(ff(Value::Decimal("-Infinity".into()), None), "-Infinity");
    }
}
