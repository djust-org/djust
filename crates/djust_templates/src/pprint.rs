//! CPython's `pprint.pformat` layout, ported for Django's `pprint` filter (#2277).
//!
//! Django's `pprint` filter is `pprint.pformat(value)` and nothing else, so its
//! contract is `PrettyPrinter(indent=1, width=80, depth=None, compact=False,
//! sort_dicts=True)`. The filter this replaces built ONE line and never wrapped,
//! so every structure whose flat repr exceeded 80 characters diverged by every
//! newline and every indent space: `[1.5] * 40` is 39 newlines in Django and was
//! 0 here.
//!
//! It is a real line-breaking algorithm, not a width check. The shape ported:
//!
//! * `_format` computes the FLAT repr first and emits it whenever it fits in
//!   `width - indent - allowance`. Only when it does not does it dispatch to a
//!   per-type layout — which is why a short container never wraps no matter how
//!   deeply it is nested, and why `allowance` (the room the CLOSING brackets of
//!   every enclosing container still need) has to be threaded through.
//! * `_format_items` (list / tuple) puts one element per line at `indent + 1`,
//!   passing `allowance` to the last element and `1` to every other — the last
//!   one shares its line with the closing brackets, the others only with a comma.
//! * `_format_dict_items` indents each value past `len(repr(key)) + 2`, so a
//!   dict's values line up under the value column rather than under the key.
//! * `_pprint_str` splits a too-long string across lines at whitespace, wrapping
//!   the chunks in parentheses when the string is the whole value being printed
//!   (`level == 1`) — implicit string concatenation, so the output still evals
//!   back to the original.
//!
//! `_safe_repr`'s recursion and `maxlevels` guards have no analogue here:
//! [`Value`] is a tree built by the serializer, so it cannot be cyclic, and
//! `depth` is `None`.
//!
//! Former residual — non-ASCII non-printable code points, closed by #2292
//! ----------------------------------------------------------------------
//! Scalars are spelled by [`djust_core::py_repr_string`], the one definition the
//! `{{ list }}` path also uses (#1646). It used to escape the ASCII controls
//! and stop there, because CPython's rule is `str.isprintable()` — Unicode
//! version data that DISAGREES ACROSS THE CI MATRIX. That measurement was:
//!
//! | corpus | divergence |
//! |---|---|
//! | printable text | 0 / 4000 |
//! | + ASCII controls, quotes, backslashes | 0 / 3000 |
//! | + `U+00A0` / `U+200B` / `U+2028` / `U+FEFF` | 1488 / 4000 |
//! | the same, with an exact `repr` substituted | 0 / 4000 |
//!
//! The last row was the proof that the LAYOUT — this module — was already
//! exact and the residual was entirely the scalar spelling, i.e. not a bug
//! here. #2292 then closed the spelling half: the escaper carries a 28-range
//! table of the seven general categories that never move for an assigned code
//! point, so the third row is now 0 / 4000 too. See [`djust_core::py_repr_string`]
//! for why a fixed table is right despite the matrix spanning five Unicode
//! versions.

use djust_core::{py_repr_string, ObjectKey, Value};

/// `PrettyPrinter`'s default `width`, which is what `pprint.pformat` uses.
const WIDTH: isize = 80;

/// `PrettyPrinter`'s default `indent`.
const INDENT_PER_LEVEL: isize = 1;

/// `pprint.pformat(value)`.
pub fn pformat(value: &Value) -> String {
    let mut out = String::new();
    format_value(value, &mut out, 0, 0, 0);
    out
}

/// `PrettyPrinter._safe_repr` — the FLAT, single-line repr.
///
/// This is also the whole of the old filter, which is why the old filter agreed
/// with Django character-for-character below the width and diverged above it.
fn flat_repr(value: &Value) -> String {
    // A serialized Django model is a map carrying a `"__str__"` string, and
    // that marker is the one `{{ obj }}` already spells it by
    // (`Value::model_str`). Printing the map here would write out the whole
    // serializer map, which Python's
    // `pformat` of the model never does: it prints the model's own text, and so
    // does this, unquoted, as a model nested in a list is spelled by `{{ rows }}`.
    // Placed before the match so the rule holds at every depth, since every
    // container arm below recurses through this function.
    if let Some(text) = value.model_str() {
        return text.to_string();
    }
    match value {
        // `pprint` renders BOTH absent and `None` as `None`, unlike `Display`,
        // which renders `Missing` as the empty string (Django's
        // `string_if_invalid`). Preserved from the filter this replaces.
        Value::Missing | Value::None => "None".to_string(),
        // `pprint.pformat(d.keys())` is `dict_keys(['a'])` — measured. The
        // elements go through this same `flat_repr`, so a nested view is
        // spelled consistently.
        Value::DictView { kind, items } => {
            let parts: Vec<String> = items.iter().map(flat_repr).collect();
            format!("{}([{}])", kind.container_name(), parts.join(", "))
        }
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::Integer(n) => n.to_string(),
        // `pprint.pformat(f)` IS `repr(f)` for a float — measured across the
        // whole spectrum, not assumed (#2258, #2270).
        Value::Float(f) => djust_core::decimal::python_float_repr(*f),
        // `pprint` shows the constructor form, as `repr` does (#2214).
        Value::Decimal(d) => format!("Decimal('{d}')"),
        // `repr(int)` is the digits, however many there are (#2260).
        Value::BigInt(d) => d.clone(),
        Value::String(s) | Value::SafeString(s) => py_repr_string(s),
        // The CONSTRUCTOR form — `datetime.datetime(2020, 1, 1, 3, 4, 5)` —
        // the way the `Decimal` arm above spells `Decimal('…')` (#2472).
        //
        // This was `py_repr_string(&e.display)` until #2472, i.e. the repr of
        // the DISPLAY STRING: `'2020-01-01 03:04:05'`, quotes and all, which is
        // exactly what it printed back when a datetime was a
        // `Value::String(str(o))` (#2448). The comment here said the real answer
        // was "out of reach because `Encoded` carries `str()` and the encoder's
        // JSON and not `repr()`" — so #2472 put `repr()` on the variant and it
        // came into reach. Not a format string: see the `Encoded::repr` doc.
        Value::Encoded(e) => e.repr.clone(),
        Value::NamedTuple { .. } => value.py_repr(),
        Value::List(items) => {
            let parts: Vec<String> = items.iter().map(flat_repr).collect();
            format!("[{}]", parts.join(", "))
        }
        Value::Tuple(items) => {
            let parts: Vec<String> = items.iter().map(flat_repr).collect();
            if parts.len() == 1 {
                format!("({},)", parts[0])
            } else {
                format!("({})", parts.join(", "))
            }
        }
        Value::Object(map) => {
            // `sort_dicts=True`, sorting by the KEY — `_safe_repr` does
            // `sorted(object.items(), key=_safe_tuple)` and `_safe_key` returns
            // an orderable key unchanged.
            //
            // NOT by the rendered pair, which is what the filter this replaces
            // did and which is a DIFFERENT order: the rendered pair begins with
            // the key's opening quote, and that quote is `"` for a key holding a
            // `'` and `'` otherwise (`0x22` vs `0x27`), so one key's quote choice
            // reorders it against every other key. Escapes shift it too — a
            // leading tab renders `\t`, sorting under `\` rather than under
            // `U+0009`. Found by the randomized differential in
            // `python/tests/test_length_pprint_parity_2279_2277.py`; a curated
            // table of well-behaved keys never reaches it.
            // `ObjectKey`'s own `Ord` mirrors CPython's `pprint._safe_key`
            // (#2339): numerics by value, then by type name. A string key
            // still sorts exactly as it did, which is what keeps the reason
            // above intact.
            let mut items: Vec<(&ObjectKey, &Value)> = map.iter().collect();
            items.sort_by(|a, b| a.0.cmp(b.0));
            let parts: Vec<String> = items
                .iter()
                // `py_repr` per key, NOT `py_repr_string` — the latter always
                // quotes, so an int key would print `'0': …`.
                .map(|(k, v)| format!("{}: {}", k.py_repr(), flat_repr(v)))
                .collect();
            format!("{{{}}}", parts.join(", "))
        }
    }
}

fn char_len(s: &str) -> isize {
    s.chars().count() as isize
}

/// `PrettyPrinter._format`.
fn format_value(value: &Value, out: &mut String, indent: isize, allowance: isize, level: usize) {
    let rep = flat_repr(value);
    // CPython lets this go negative and compares an `int` against it; `isize`
    // does the same, where `usize` would wrap.
    let max_width = WIDTH - indent - allowance;
    // A model is one atom, like an `int`: its text overruns the line rather
    // than being laid out as a wrapped dict of the fields it was serialized from.
    if char_len(&rep) > max_width && value.model_str().is_none() {
        match value {
            Value::List(items) => {
                out.push('[');
                format_items(items, out, indent, allowance + 1, level + 1);
                out.push(']');
                return;
            }
            Value::Tuple(items) => {
                out.push('(');
                let endchar = if items.len() == 1 { ",)" } else { ")" };
                format_items(
                    items,
                    out,
                    indent,
                    allowance + endchar.len() as isize,
                    level + 1,
                );
                out.push_str(endchar);
                return;
            }
            Value::Object(map) => {
                out.push('{');
                if !map.is_empty() {
                    let mut items: Vec<(&ObjectKey, &Value)> = map.iter().collect();
                    items.sort_by(|a, b| a.0.cmp(b.0));
                    format_dict_items(&items, out, indent, allowance + 1, level + 1);
                }
                out.push('}');
                return;
            }
            Value::String(s) | Value::SafeString(s) => {
                format_str(s, out, indent, allowance, level + 1);
                return;
            }
            // Every other variant's repr is atomic — CPython has no dispatch
            // entry for `int.__repr__` either, so a long number simply overruns.
            _ => {}
        }
    }
    out.push_str(&rep);
}

/// `PrettyPrinter._format_items`, with the `compact` branch omitted:
/// `pformat`'s default is `compact=False`, so that branch is never taken.
fn format_items(items: &[Value], out: &mut String, indent: isize, allowance: isize, level: usize) {
    let indent = indent + INDENT_PER_LEVEL;
    let delimnl = format!(",\n{}", " ".repeat(indent.max(0) as usize));
    let mut delim = "";
    for (i, ent) in items.iter().enumerate() {
        let last = i + 1 == items.len();
        out.push_str(delim);
        delim = &delimnl;
        // The last element shares its line with every enclosing container's
        // closing bracket; the others only with the comma that follows.
        format_value(ent, out, indent, if last { allowance } else { 1 }, level);
    }
}

/// `PrettyPrinter._format_dict_items`.
fn format_dict_items(
    items: &[(&ObjectKey, &Value)],
    out: &mut String,
    indent: isize,
    allowance: isize,
    level: usize,
) {
    let indent = indent + INDENT_PER_LEVEL;
    let delimnl = format!(",\n{}", " ".repeat(indent.max(0) as usize));
    let last_index = items.len() - 1;
    for (i, (key, ent)) in items.iter().enumerate() {
        let last = i == last_index;
        // Per-key `py_repr`, so an int key prints `0:` and not `'0':`
        // (#2339).
        let rep = key.py_repr();
        out.push_str(&rep);
        out.push_str(": ");
        // `+ 2` is the `": "` just written: a wrapped value lines up under the
        // value column, not under the key.
        format_value(
            ent,
            out,
            indent + char_len(&rep) + 2,
            if last { allowance } else { 1 },
            level,
        );
        if !last {
            out.push_str(&delimnl);
        }
    }
}

/// `PrettyPrinter._pprint_str`.
fn format_str(s: &str, out: &mut String, indent: isize, allowance: isize, level: usize) {
    if s.is_empty() {
        out.push_str(&py_repr_string(s));
        return;
    }
    let mut indent = indent;
    let mut allowance = allowance;
    let lines = py_splitlines_keepends(s);
    if level == 1 {
        indent += 1;
        allowance += 1;
    }
    let max_width = WIDTH - indent;
    let mut max_width1 = max_width;
    let mut chunks: Vec<String> = Vec::new();
    // CPython reads `rep` after the loop — Python leaks the loop variable, so
    // the single-chunk shortcut below emits the LAST line's repr, not the
    // chunk's. Faithfully reproduced: for a single-chunk result the two are the
    // same string only when there was also a single line.
    let mut rep = String::new();
    for (i, line) in lines.iter().enumerate() {
        rep = py_repr_string(line);
        let is_last_line = i + 1 == lines.len();
        if is_last_line {
            max_width1 -= allowance;
        }
        if char_len(&rep) <= max_width1 {
            chunks.push(rep.clone());
        } else {
            // `re.findall(r'\S*\s*', line)` minus its empty final match.
            let parts = split_nonspace_space(line);
            let mut max_width2 = max_width;
            let mut current = String::new();
            for (j, part) in parts.iter().enumerate() {
                let candidate = format!("{current}{part}");
                if j + 1 == parts.len() && is_last_line {
                    max_width2 -= allowance;
                }
                if char_len(&py_repr_string(&candidate)) > max_width2 {
                    if !current.is_empty() {
                        chunks.push(py_repr_string(&current));
                    }
                    current = part.to_string();
                } else {
                    current = candidate;
                }
            }
            if !current.is_empty() {
                chunks.push(py_repr_string(&current));
            }
        }
    }
    if chunks.len() == 1 {
        out.push_str(&rep);
        return;
    }
    if level == 1 {
        out.push('(');
    }
    for (i, chunk) in chunks.iter().enumerate() {
        if i > 0 {
            out.push('\n');
            out.push_str(&" ".repeat(indent.max(0) as usize));
        }
        out.push_str(chunk);
    }
    if level == 1 {
        out.push(')');
    }
}

/// Python's `str.splitlines(keepends=True)`.
///
/// NOT `str::lines()`, which splits on `\n` alone. Python splits on eight more
/// boundaries — and two of them, `U+2028` and `U+2029`, are exactly the kind of
/// character that reaches a template from user text.
///
/// The boundary SET lives in [`py_is_line_break`] so the keepends and
/// no-keepends forms cannot disagree about what a line is (#1646).
pub(crate) fn py_splitlines_keepends(s: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let mut current = String::new();
    let mut chars = s.chars().peekable();
    while let Some(c) = chars.next() {
        current.push(c);
        if py_is_line_break(c) {
            // `\r\n` is ONE boundary.
            if c == '\r' && chars.peek() == Some(&'\n') {
                current.push('\n');
                chars.next();
            }
            out.push(std::mem::take(&mut current));
        }
    }
    if !current.is_empty() {
        out.push(current);
    }
    out
}

/// The ten boundaries Python's `str.splitlines()` recognises.
///
/// This is one of THREE different whitespace sets the `wordwrap` port has to
/// keep apart — the other two are `textwrap._whitespace` (six ASCII characters)
/// and `str.isspace()` (`crate::truncate::py_is_space`). `\u{1f}` is in the
/// third and in neither of the first two; `\u{a0}` is in the third only. Every
/// known `wordwrap` defect lived in a gap between two of them (#2293), so none
/// of the three is ever spelled inline.
pub(crate) fn py_is_line_break(c: char) -> bool {
    matches!(
        c,
        '\n' | '\r'
            | '\u{0b}'
            | '\u{0c}'
            | '\u{1c}'
            | '\u{1d}'
            | '\u{1e}'
            | '\u{85}'
            | '\u{2028}'
            | '\u{2029}'
    )
}

/// `re.findall(r'\S*\s*', line)` with its always-empty final match dropped.
///
/// Each part is a run of non-space followed by the run of space that follows it,
/// so concatenating the parts reproduces the line exactly — which is what lets
/// `_pprint_str` accumulate a prefix and measure its repr.
fn split_nonspace_space(line: &str) -> Vec<String> {
    let mut parts: Vec<String> = Vec::new();
    let mut current = String::new();
    let mut in_space = false;
    for c in line.chars() {
        // `crate::truncate::py_is_space` is Python's `\s` for `str` patterns —
        // the one definition, shared with `truncatewords` (#1646).
        let space = crate::truncate::py_is_space(c);
        if space {
            in_space = true;
            current.push(c);
        } else {
            if in_space {
                parts.push(std::mem::take(&mut current));
                in_space = false;
            }
            current.push(c);
        }
    }
    if !current.is_empty() {
        parts.push(current);
    }
    parts
}

#[cfg(test)]
mod tests {
    use super::*;

    fn s(v: &str) -> Value {
        Value::String(v.to_string())
    }

    #[test]
    fn short_containers_do_not_wrap() {
        assert_eq!(pformat(&Value::List(vec![Value::Integer(1)])), "[1]");
        assert_eq!(pformat(&Value::List(vec![])), "[]");
        assert_eq!(pformat(&Value::Tuple(vec![Value::Integer(1)])), "(1,)");
    }

    #[test]
    fn a_long_list_is_one_element_per_line() {
        let items: Vec<Value> = (0..40).map(|_| Value::Float(1.5)).collect();
        let got = pformat(&Value::List(items));
        assert_eq!(got.matches('\n').count(), 39);
        assert!(got.starts_with("[1.5,\n 1.5,"), "{got}");
        assert!(got.ends_with("\n 1.5]"), "{got}");
    }

    #[test]
    fn splitlines_matches_python_boundaries() {
        assert_eq!(py_splitlines_keepends("a\r\nb"), vec!["a\r\n", "b"]);
        assert_eq!(py_splitlines_keepends("a\u{2028}b"), vec!["a\u{2028}", "b"]);
        assert_eq!(py_splitlines_keepends(""), Vec::<String>::new());
    }

    #[test]
    fn nonspace_space_parts_reconstruct_the_line() {
        for line in ["a b  c", "  lead", "trail  ", "", "a\u{a0}b"] {
            assert_eq!(split_nonspace_space(line).concat(), line);
        }
    }

    /// A serialized Django model: a map carrying a `"__str__"` string, which is
    /// the one marker `Value::object_str` reads. It holds more than its text on
    /// purpose, so a spelling that fell through to the map would show them.
    fn model(text: &str) -> Value {
        let mut m = indexmap::IndexMap::new();
        for (k, v) in [
            ("id", Value::Integer(1)),
            ("pk", Value::Integer(1)),
            ("__str__", s(text)),
            ("__model__", s("Account")),
            ("owner_code", s("SENTINEL-A")),
            ("get_note_text", s("SENTINEL-B")),
        ] {
            m.insert(ObjectKey::Str(k.to_string()), v);
        }
        Value::Object(m)
    }

    fn plain_dict() -> Value {
        let mut m = indexmap::IndexMap::new();
        m.insert(ObjectKey::Str("a".to_string()), Value::Integer(1));
        m.insert(ObjectKey::Str("b".to_string()), s("x"));
        Value::Object(m)
    }

    /// A model map is spelled as its text, like `{{ obj }}` spells it, and not
    /// as the map that holds the text.
    #[test]
    fn a_model_map_is_spelled_as_its_text() {
        let got = pformat(&model("Account:a1"));
        assert_eq!(got, "Account:a1");
        assert!(!got.contains("SENTINEL"), "{got}");
    }

    /// A model whose text is a SafeString (`format_html`) is spelled the same way.
    #[test]
    fn a_model_map_with_a_safe_text_is_spelled_as_its_text() {
        let mut m = indexmap::IndexMap::new();
        for (k, v) in [
            ("pk", Value::Integer(1)),
            ("__str__", Value::SafeString("<b>a1</b>".to_string())),
            ("__model__", s("Account")),
            ("owner_code", s("SENTINEL-A")),
        ] {
            m.insert(ObjectKey::Str(k.to_string()), v);
        }
        assert_eq!(pformat(&Value::List(vec![Value::Object(m)])), "[<b>a1</b>]");
    }

    /// A dict that merely carries a text `__str__` key (user data, a JSON column) is not a
    /// model: it is still the sorted map.
    #[test]
    fn a_dict_with_only_a_text_key_is_still_a_map() {
        let mut m = indexmap::IndexMap::new();
        m.insert(ObjectKey::Str("__str__".to_string()), s("lbl"));
        m.insert(ObjectKey::Str("k1".to_string()), s("v1"));
        assert_eq!(pformat(&Value::Object(m)), "{'__str__': 'lbl', 'k1': 'v1'}");
    }

    /// An ordinary dict is untouched: still the sorted map repr.
    #[test]
    fn an_ordinary_dict_is_still_a_map() {
        assert_eq!(pformat(&plain_dict()), "{'a': 1, 'b': 'x'}");
    }

    /// The same holds at every depth and inside every container type.
    #[test]
    fn a_model_map_nested_in_any_container_is_spelled_as_its_text() {
        let a = || model("Account:a1");
        let b = || model("Account:a2");
        assert_eq!(
            pformat(&Value::List(vec![a(), b()])),
            "[Account:a1, Account:a2]"
        );
        assert_eq!(
            pformat(&Value::Tuple(vec![a(), b()])),
            "(Account:a1, Account:a2)"
        );
        assert_eq!(pformat(&Value::Tuple(vec![a()])), "(Account:a1,)");
        let mut d = indexmap::IndexMap::new();
        d.insert(ObjectKey::Str("items".to_string()), Value::List(vec![a()]));
        d.insert(ObjectKey::Str("owner".to_string()), b());
        assert_eq!(
            pformat(&Value::Object(d)),
            "{'items': [Account:a1], 'owner': Account:a2}"
        );
        let nested = Value::List(vec![Value::List(vec![Value::Tuple(vec![a()])])]);
        assert_eq!(pformat(&nested), "[[(Account:a1,)]]");
        let named = Value::NamedTuple {
            name: "Pair".to_string(),
            fields: vec!["left".to_string(), "right".to_string()],
            items: vec![a(), b()],
        };
        let got = pformat(&named);
        assert!(!got.contains("SENTINEL"), "{got}");
        assert!(
            got.contains("Account:a1") && got.contains("Account:a2"),
            "{got}"
        );
    }

    /// A model map is ONE atom: when its text is wider than the line it
    /// overruns, as an `int` does, and is never broken into a wrapped dict.
    /// The surrounding list still wraps one element per line.
    #[test]
    fn a_wide_model_map_is_an_atom_and_never_wraps_as_a_dict() {
        let wide = "W".repeat(120);
        let got = pformat(&model(&wide));
        assert_eq!(got, wide);
        assert!(!got.contains('\n') && !got.contains("SENTINEL"), "{got}");

        let rows: Vec<Value> = (0..4)
            .map(|i| model(&format!("Account number {i:02}-{}", "x".repeat(30))))
            .collect();
        let got = pformat(&Value::List(rows));
        assert_eq!(got.matches('\n').count(), 3, "{got}");
        assert!(got.starts_with("[Account number 00-"), "{got}");
        assert!(got.ends_with("xxxxxx]"), "{got}");
        assert!(!got.contains("SENTINEL") && !got.contains('{'), "{got}");
    }

    /// The text is printed as it is: NULs, newlines and brackets in a model's
    /// `__str__` do not change the spelling and cannot reach the map.
    #[test]
    fn an_unusual_model_text_is_printed_verbatim() {
        for text in ["", "a\0b", "line1\nline2", "}{][,'\"", "\u{2028}\u{1F600}"] {
            assert_eq!(pformat(&model(text)), text, "{text:?}");
        }
    }

    /// A very deep chain of lists around a model map, and a very large list of
    /// them, neither recurse without bound nor go quadratic.
    #[test]
    fn deep_and_large_model_containers_are_bounded() {
        let mut deep = model("Account:a1");
        for _ in 0..200 {
            deep = Value::List(vec![deep]);
        }
        let got = pformat(&deep);
        assert_eq!(got.len(), 200 + "Account:a1".len() + 200);
        assert!(!got.contains("SENTINEL"), "{got}");

        let rows: Vec<Value> = (0..20_000)
            .map(|i| model(&format!("Account:{i}")))
            .collect();
        let started = std::time::Instant::now();
        let got = pformat(&Value::List(rows));
        assert!(!got.contains("SENTINEL"));
        assert_eq!(got.matches('\n').count(), 19_999);
        assert!(
            started.elapsed() < std::time::Duration::from_secs(20),
            "pformat of 20000 model maps took {:?}",
            started.elapsed()
        );
    }

    #[test]
    fn scalars_go_through_the_shared_repr() {
        assert_eq!(pformat(&s("a'b")), "\"a'b\"");
        assert_eq!(pformat(&s("a\tb")), "'a\\tb'");
        assert_eq!(pformat(&Value::Missing), "None");
    }
}
