//! URL quoting used by Django's urlize/urlizetrunc filters.

use crate::truncate::urlencode;

const URL_SAFE: &str = ":/?#[]@!$&'()*+,;=~";

fn unquote(value: &str) -> String {
    let bytes = value.as_bytes();
    let mut decoded = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            let hi = (bytes[i + 1] as char).to_digit(16);
            let lo = (bytes[i + 2] as char).to_digit(16);
            if let (Some(hi), Some(lo)) = (hi, lo) {
                decoded.push((hi * 16 + lo) as u8);
                i += 3;
                continue;
            }
        }
        decoded.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&decoded).into_owned()
}

fn unquote_quote(value: &str) -> String {
    urlencode(&unquote(value), Some(URL_SAFE))
}

/// Django's smart_urlquote for the HTTP(S) URLs recognized by urlize.
/// Query fields are decoded separately so encoded separators never turn into
/// additional fields. parse_qsl decodes once and Django unquotes once more.
pub(crate) fn smart_urlquote(url: &str) -> String {
    // urllib.parse.urlsplit removes these ASCII controls before splitting.
    let cleaned = url.replace(['\t', '\r', '\n'], "");
    let url = cleaned.trim_start_matches(|c: char| c <= ' ');
    let (base, fragment) = url.split_once('#').map_or((url, ""), |parts| parts);
    let (base, query) = base.split_once('?').map_or((base, ""), |parts| parts);
    let (scheme, authority_path) = base.split_once("://").unwrap_or(("", base));
    let (authority, path) = authority_path
        .find('/')
        .map_or((authority_path, ""), |pos| authority_path.split_at(pos));

    // urlsplit refuses malformed bracketed hosts before quoting components.
    if authority.contains('[') || authority.contains(']') {
        let valid = authority
            .split_once('[')
            .and_then(|(_, rest)| rest.split_once(']'))
            .is_some_and(|(host, _)| {
                if let Some(version) = host.strip_prefix('v') {
                    return version.split_once('.').is_some_and(|(number, address)| {
                        !number.is_empty()
                            && number.bytes().all(|b| b.is_ascii_hexdigit())
                            && !address.is_empty()
                    });
                }
                let address = host.split_once('%').map_or(host, |(address, _)| address);
                address.parse::<std::net::Ipv6Addr>().is_ok()
            });
        if !valid {
            return unquote_quote(url);
        }
    }

    let mut result = format!(
        "{}://{}{}",
        scheme.to_lowercase(),
        unquote_quote(authority),
        unquote_quote(path)
    );
    if !query.is_empty() {
        let fields: Vec<String> = query
            .split('&')
            .filter(|part| !part.is_empty())
            .map(|field| {
                let (key, value) = field.split_once('=').unwrap_or((field, ""));
                let quote_field = |s: &str| {
                    let decoded = unquote(&unquote(&s.replace('+', " ")));
                    urlencode(&decoded, Some("")).replace("%20", "+")
                };
                format!("{}={}", quote_field(key), quote_field(value))
            })
            .collect();
        if !fields.is_empty() {
            result.push('?');
            result.push_str(&fields.join("&"));
        }
    }
    if !fragment.is_empty() {
        result.push('#');
        result.push_str(&unquote_quote(fragment));
    }
    result
}
