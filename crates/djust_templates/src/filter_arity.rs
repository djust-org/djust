//! Django's per-filter argument ARITY (#2400).
//!
//! # The defect this closes
//!
//! Django validates a filter's argument COUNT before it validates anything
//! else, and it does so at COMPILE time:
//!
//! ```python
//! def args_check(name, func, provided):
//!     plen = len(provided) + 1          # the input is implied
//!     args, _, _, defaults, _, _, _ = inspect.getfullargspec(inspect.unwrap(func))
//!     alen, dlen = len(args), len(defaults or [])
//!     if plen < (alen - dlen) or plen > alen:
//!         raise TemplateSyntaxError("%s requires %d arguments, %d provided" % …)
//! ```
//!
//! djust's dispatch table read `arg: Option<&str>` and silently ignored or
//! defaulted it, so **48 of Django's 57 built-ins** rendered a template Django
//! refuses. A typo in a template was silent here and loud there, which is the
//! failure shape this area keeps producing (#2325, #2334, #2377).
//!
//! # Two bounds, not one — and this is where the issue's count comes from
//!
//! `args_check` counts `autoescape` as a slot, because it reads the raw
//! argspec. For the five `needs_autoescape=True` built-ins whose only other
//! parameter IS `autoescape`, that makes `plen = 2` legal at compile time —
//! and then the CALL supplies `autoescape` as a keyword and Python raises
//! `TypeError: urlize() got multiple values for argument 'autoescape'` at
//! RENDER time. Measured against Django 5.2, not reasoned:
//!
//! ```text
//! {{ p|upper:"x" }}   TemplateSyntaxError: upper requires 1 arguments, 2 provided
//! {{ p|urlize:"x" }}  TypeError: urlize() got multiple values for argument 'autoescape'
//! ```
//!
//! So the issue's "28 raise on an EXTRA argument … TemplateSyntaxError" is 23
//! plus those 5, and its title is accurate for 43 of the 48 rather than all of
//! them. Collapsing the two bounds would make djust refuse — at parse time —
//! five templates Django COMPILES, which is a wrong answer in the other
//! direction. Hence three numbers per filter rather than two.
//!
//! # Why the table is a transcription and how it is kept honest
//!
//! Nothing in the Rust engine can introspect a Python signature, so the
//! numbers are copied. They are copied MECHANICALLY (a generator reading
//! `inspect.getfullargspec` over the live registry) and pinned MECHANICALLY:
//! `python/tests/test_filter_arity_2400.py::TestTheTableIsDjangosOwnArity`
//! re-derives all three bounds for every filter in Django's live registry and
//! compares them to this table, so a Django release that changes a signature
//! fails a test rather than drifting.

/// `(filter name, min, parse_max, call_max)` — bounds on the number of
/// TEMPLATE arguments (Django's `plen` minus the implied input).
///
/// * `min` — fewer than this is `args_check`'s "not enough".
/// * `parse_max` — more than this is `args_check`'s "too many". Derived from
///   the RAW argspec, so it counts `autoescape`.
/// * `call_max` — more than this makes the CALL raise `TypeError`, because
///   `autoescape` is passed as a keyword. Never greater than `parse_max`.
///
/// Generated from Django 5.2's live registry; see the module docs.
const ARITY: &[(&str, u8, u8, u8)] = &[
    ("add", 1, 1, 1),
    ("addslashes", 0, 0, 0),
    ("capfirst", 0, 0, 0),
    ("center", 1, 1, 1),
    ("cut", 1, 1, 1),
    ("date", 0, 1, 1),
    ("default", 1, 1, 1),
    ("default_if_none", 1, 1, 1),
    ("dictsort", 1, 1, 1),
    ("dictsortreversed", 1, 1, 1),
    ("divisibleby", 1, 1, 1),
    ("escape", 0, 0, 0),
    ("escapejs", 0, 0, 0),
    ("escapeseq", 0, 0, 0),
    ("filesizeformat", 0, 0, 0),
    ("first", 0, 0, 0),
    ("floatformat", 0, 1, 1),
    ("force_escape", 0, 0, 0),
    ("get_digit", 1, 1, 1),
    ("iriencode", 0, 0, 0),
    ("join", 1, 2, 1),
    ("json_script", 0, 1, 1),
    ("last", 0, 0, 0),
    ("length", 0, 0, 0),
    ("linebreaks", 0, 1, 0),
    ("linebreaksbr", 0, 1, 0),
    ("linenumbers", 0, 1, 0),
    ("ljust", 1, 1, 1),
    ("lower", 0, 0, 0),
    ("make_list", 0, 0, 0),
    ("phone2numeric", 0, 0, 0),
    ("pluralize", 0, 1, 1),
    ("pprint", 0, 0, 0),
    ("random", 0, 0, 0),
    ("rjust", 1, 1, 1),
    ("safe", 0, 0, 0),
    ("safeseq", 0, 0, 0),
    ("slice", 1, 1, 1),
    ("slugify", 0, 0, 0),
    ("stringformat", 1, 1, 1),
    ("striptags", 0, 0, 0),
    ("time", 0, 1, 1),
    ("timesince", 0, 1, 1),
    ("timeuntil", 0, 1, 1),
    ("title", 0, 0, 0),
    ("truncatechars", 1, 1, 1),
    ("truncatechars_html", 1, 1, 1),
    ("truncatewords", 1, 1, 1),
    ("truncatewords_html", 1, 1, 1),
    ("unordered_list", 0, 1, 0),
    ("upper", 0, 0, 0),
    ("urlencode", 0, 1, 1),
    ("urlize", 0, 1, 0),
    ("urlizetrunc", 1, 2, 1),
    ("wordcount", 0, 0, 0),
    ("wordwrap", 1, 1, 1),
    ("yesno", 0, 1, 1),
];

/// The bounds for one built-in, or `None` for a name this engine does not
/// implement as a built-in — a project's own `@register.filter`, which Django
/// arity-checks too but which djust cannot see from Rust (out of scope, #1079).
pub fn builtin_arity(name: &str) -> Option<(u8, u8, u8)> {
    ARITY
        .iter()
        .find(|(n, ..)| *n == name)
        .map(|&(_, lo, parse_max, call_max)| (lo, parse_max, call_max))
}

/// Django's own message, verbatim — including the ungrammatical
/// `requires 1 arguments` and the `+ 1` for the implied input.
fn message(name: &str, min_provided: u8, provided: u8) -> String {
    format!(
        "{name} requires {} arguments, {} provided",
        min_provided + 1,
        provided + 1
    )
}

/// The check Django runs at COMPILE time (`FilterExpression.__init__` →
/// `args_check`), for a filter given `provided` template arguments.
///
/// `None` for a name outside the built-in table, so an unknown filter is left
/// to the render-time "Unknown filter" path and a project's custom filter is
/// not refused by a table that does not describe it.
pub fn parse_time_arity_error(name: &str, provided: u8) -> Option<String> {
    let (lo, parse_max, _) = builtin_arity(name)?;
    (provided < lo || provided > parse_max).then(|| message(name, lo, provided))
}

/// The check the CALL itself performs, for a filter given `provided` template
/// arguments.
///
/// Tighter than [`parse_time_arity_error`] for exactly the `needs_autoescape`
/// built-ins whose extra argument Django accepts at compile time and refuses at
/// call time. The two are separate functions rather than one with a flag
/// because the two call sites answer different questions — the same shape
/// `python_len` has, where each caller picks its own fallback (#1646).
pub fn call_time_arity_error(name: &str, provided: u8) -> Option<String> {
    let (lo, _, call_max) = builtin_arity(name)?;
    (provided < lo || provided > call_max).then(|| message(name, lo, provided))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_name_appears_once_and_the_bounds_are_ordered() {
        // A duplicate row is invisible — `find` takes the first — so it is
        // asserted rather than trusted, and `call_max <= parse_max` is the
        // invariant the two-bound split rests on.
        let mut names: Vec<&str> = ARITY.iter().map(|(n, ..)| *n).collect();
        names.sort_unstable();
        let before = names.len();
        names.dedup();
        assert_eq!(names.len(), before, "a filter name is listed twice");
        for &(name, lo, parse_max, call_max) in ARITY {
            assert!(call_max <= parse_max, "{name}: call_max exceeds parse_max");
            assert!(lo <= call_max, "{name}: min exceeds call_max");
        }
    }

    #[test]
    fn the_two_bounds_differ_for_exactly_the_needs_autoescape_filters() {
        let split: Vec<&str> = ARITY
            .iter()
            .filter(|&&(_, _, parse_max, call_max)| call_max < parse_max)
            .map(|(n, ..)| *n)
            .collect();
        assert_eq!(
            split,
            vec![
                "join",
                "linebreaks",
                "linebreaksbr",
                "linenumbers",
                "unordered_list",
                "urlize",
                "urlizetrunc",
            ],
            "the compile-vs-call split moved; re-derive from Django"
        );
    }

    #[test]
    fn the_message_is_djangos_wording() {
        assert_eq!(
            parse_time_arity_error("upper", 1).unwrap(),
            "upper requires 1 arguments, 2 provided"
        );
        assert_eq!(
            parse_time_arity_error("default", 0).unwrap(),
            "default requires 2 arguments, 1 provided"
        );
        assert!(parse_time_arity_error("upper", 0).is_none());
        assert!(parse_time_arity_error("default", 1).is_none());
    }

    #[test]
    fn urlize_compiles_with_an_argument_and_the_call_refuses_it() {
        // The whole reason there are two bounds.
        assert!(parse_time_arity_error("urlize", 1).is_none());
        assert!(call_time_arity_error("urlize", 1).is_some());
        // …and a filter with no autoescape parameter refuses at BOTH.
        assert!(parse_time_arity_error("upper", 1).is_some());
        assert!(call_time_arity_error("upper", 1).is_some());
    }

    #[test]
    fn a_name_outside_the_table_is_not_checked() {
        // A project's own `@register.filter` reaches the parser before it is
        // registered; refusing it would break every custom filter there is.
        assert!(parse_time_arity_error("my_project_filter", 1).is_none());
        assert!(call_time_arity_error("my_project_filter", 0).is_none());
    }
}
