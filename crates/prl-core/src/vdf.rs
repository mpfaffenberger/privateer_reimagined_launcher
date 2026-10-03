//! Minimal reader for Valve KeyValues text (`libraryfolders.vdf`, `appmanifest_*.acf`).
//! Enough for ownership detection: quoted keys/values, nested `{}` blocks, `//` comments,
//! and `\\` / `\"` escapes (Windows paths are written as `C:\\Program Files`).

use std::collections::BTreeMap;

#[derive(Debug, Clone, PartialEq)]
pub enum Value {
    Str(String),
    Obj(BTreeMap<String, Value>),
}

impl Value {
    pub fn get(&self, key: &str) -> Option<&Value> {
        match self {
            // Keys are case-insensitive in practice ("AppState" vs "appstate").
            Value::Obj(map) => map.iter().find(|(k, _)| k.eq_ignore_ascii_case(key)).map(|(_, v)| v),
            Value::Str(_) => None,
        }
    }

    pub fn str(&self) -> Option<&str> {
        match self {
            Value::Str(s) => Some(s),
            Value::Obj(_) => None,
        }
    }

    pub fn entries(&self) -> Vec<(&String, &Value)> {
        match self {
            Value::Obj(map) => map.iter().collect(),
            Value::Str(_) => Vec::new(),
        }
    }
}

enum Tok {
    Str(String),
    Open,
    Close,
}

fn tokenize(text: &str) -> Vec<Tok> {
    let mut out = Vec::new();
    let mut chars = text.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '{' => out.push(Tok::Open),
            '}' => out.push(Tok::Close),
            '/' if chars.peek() == Some(&'/') => {
                for c in chars.by_ref() {
                    if c == '\n' {
                        break;
                    }
                }
            }
            '"' => {
                let mut s = String::new();
                while let Some(c) = chars.next() {
                    match c {
                        '"' => break,
                        '\\' => match chars.next() {
                            Some('n') => s.push('\n'),
                            Some('t') => s.push('\t'),
                            Some(other) => s.push(other),
                            None => break,
                        },
                        _ => s.push(c),
                    }
                }
                out.push(Tok::Str(s));
            }
            _ => {}
        }
    }
    out
}

/// Parse a whole document into an object (top-level keys -> values). Tolerates junk by
/// stopping early rather than failing: callers only look for a few keys.
pub fn parse(text: &str) -> Value {
    fn block(toks: &mut std::iter::Peekable<std::vec::IntoIter<Tok>>) -> BTreeMap<String, Value> {
        let mut map = BTreeMap::new();
        while let Some(tok) = toks.next() {
            match tok {
                Tok::Close => break,
                Tok::Open => {}
                Tok::Str(key) => match toks.next() {
                    Some(Tok::Str(v)) => {
                        map.insert(key, Value::Str(v));
                    }
                    Some(Tok::Open) => {
                        map.insert(key, Value::Obj(block(toks)));
                    }
                    _ => break,
                },
            }
        }
        map
    }
    let mut toks = tokenize(text).into_iter().peekable();
    Value::Obj(block(&mut toks))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_library_folders_with_escaped_windows_paths() {
        let text = r#"
"libraryfolders"
{
	"0"
	{
		"path"		"C:\\Program Files (x86)\\Steam"
		"apps" { "228980" "1" }
	}
	// a comment
	"1" { "path" "D:\\SteamLibrary" }
}"#;
        let doc = parse(text);
        let folders = doc.get("LibraryFolders").unwrap();
        let paths: Vec<_> = folders.entries().iter().filter_map(|(_, v)| v.get("path")?.str()).collect();
        assert_eq!(paths, vec![r"C:\Program Files (x86)\Steam", r"D:\SteamLibrary"]);
    }

    #[test]
    fn parses_app_manifest() {
        let doc = parse(r#""AppState" { "appid" "12345" "name" "Wing Commander: Privateer" "installdir" "Privateer" }"#);
        let app = doc.get("appstate").unwrap();
        assert_eq!(app.get("name").unwrap().str(), Some("Wing Commander: Privateer"));
        assert_eq!(app.get("installdir").unwrap().str(), Some("Privateer"));
    }
}
