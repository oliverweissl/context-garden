//! Update checks without an HTTP client: `git ls-remote` against the public
//! repository tells the latest commit on main (what accounts install the
//! skills from) and the release tags (what the app is built from).

use serde::Serialize;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use crate::util::run;

pub const REPO: &str = "https://github.com/oliverweissl/context-garden";

/// (commit, ref name) pairs of the repository.
type Refs = Vec<(String, String)>;

/// Cached for 10 minutes: every account card asks.
static REFS: Mutex<Option<(Instant, Refs)>> = Mutex::new(None);

fn refs() -> Result<Refs, String> {
    let mut cache = REFS.lock().unwrap();
    if let Some((at, refs)) = cache.as_ref() {
        if at.elapsed() < Duration::from_secs(600) {
            return Ok(refs.clone());
        }
    }
    // Releases are tagged 0.3.0 or v0.4.0; both count.
    let out = run("git", &["ls-remote", REPO, "refs/heads/main", "refs/tags/*"], None)
        .map_err(|e| format!("could not reach GitHub: {e}"))?;
    let refs: Refs = out
        .lines()
        .filter_map(|l| l.split_once('\t'))
        .map(|(sha, name)| (sha.to_string(), name.trim_end_matches("^{}").to_string()))
        .collect();
    *cache = Some((Instant::now(), refs.clone()));
    Ok(refs)
}

/// Latest commit on main.
pub fn main_commit() -> Result<String, String> {
    refs()?
        .into_iter()
        .find(|(_, name)| name == "refs/heads/main")
        .map(|(sha, _)| sha)
        .ok_or_else(|| "main not found".into())
}

fn parse(version: &str) -> Option<Vec<u64>> {
    let core = version.trim_start_matches('v').split(['-', '+']).next()?;
    core.split('.').map(|p| p.parse().ok()).collect()
}

#[derive(Serialize, Clone)]
pub struct AppRelease {
    pub version: String,
    pub url: String,
}

/// The newest release tag above `current`, if any.
pub fn newer_release(current: &str) -> Result<Option<AppRelease>, String> {
    Ok(newest_above(current, refs()?.iter().filter_map(|(_, n)| n.strip_prefix("refs/tags/"))))
}

fn newest_above<'a>(current: &str, tags: impl Iterator<Item = &'a str>) -> Option<AppRelease> {
    let now = parse(current)?;
    // Pre-releases (v1.2.0-rc1) are not offered.
    tags.filter(|t| !t.contains('-'))
        .filter_map(|t| parse(t).map(|v| (v, t)))
        .filter(|(v, _)| *v > now)
        .max()
        .map(|(_, tag)| AppRelease {
            version: tag.trim_start_matches('v').to_string(),
            url: format!("{REPO}/releases/tag/{tag}"),
        })
}

/// Open a page of this repository in the browser; nothing else may be opened.
pub fn open_repo_page(url: &str) -> Result<(), String> {
    let ok = url.strip_prefix(REPO).is_some_and(|rest| rest.starts_with('/'))
        && url.chars().all(|c| c.is_ascii_alphanumeric() || "-_./:".contains(c));
    if !ok {
        return Err("only pages of the context-garden repository can be opened".into());
    }
    let (program, args): (&str, Vec<&str>) = if cfg!(target_os = "macos") {
        ("open", vec![url])
    } else if cfg!(windows) {
        ("cmd", vec!["/C", "start", "", url])
    } else {
        ("xdg-open", vec![url])
    };
    std::process::Command::new(program)
        .args(args)
        .spawn()
        .map(|_| ())
        .map_err(|e| format!("could not open the browser: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn picks_the_newest_higher_tag() {
        let tags = ["0.3.1", "v0.4.0", "0.4.2", "v0.4.10", "v0.5.0-rc1", "not-a-version"];
        let r = newest_above("0.4.0", tags.iter().copied()).unwrap();
        assert_eq!(r.version, "0.4.10");
        assert_eq!(r.url, format!("{REPO}/releases/tag/v0.4.10"));
        assert!(newest_above("0.4.10", tags.iter().copied()).is_none());
    }

    #[test]
    fn opens_only_repository_pages() {
        assert!(open_repo_page("https://example.com/x").is_err());
        assert!(open_repo_page(&format!("{REPO}-evil/releases")).is_err());
        assert!(open_repo_page(&format!("{REPO}/releases/tag/v1;rm")).is_err());
    }
}
