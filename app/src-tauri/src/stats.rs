//! Garden dashboard numbers: git state, context-garden stores, agent token use.

use serde::Serialize;
use std::collections::{BTreeMap, HashSet};
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime};

use crate::store::{Garden, Profile};
use crate::util::{estimate_tokens, expand_tilde, git, home_dir};

#[derive(Serialize, Default)]
pub struct RepoStats {
    pub name: String,
    pub path: String,
    pub status: String,
    pub group: Option<String>,
    pub branch: Option<String>,
    pub changed: usize,
    /// Always-loaded agent files (AGENTS.md + CLAUDE.md), estimated tokens.
    pub agent_tokens: u64,
    pub seedbank_facts: usize,
    /// Tracked files and their text lines (rough: binary and huge files skipped).
    pub files: usize,
    pub lines: u64,
    /// Lines per language, keyed by file extension.
    pub languages: BTreeMap<String, u64>,
}

#[derive(Serialize, Default)]
pub struct TokenUse {
    pub sessions: usize,
    pub input: u64,
    pub output: u64,
    pub cache_read: u64,
    pub cache_write: u64,
}

#[derive(Serialize, Default)]
pub struct GardenStats {
    pub repos: Vec<RepoStats>,
    pub groups: usize,
    pub namespaces: usize,
    pub dirty: usize,
    pub agent_tokens: u64,
    pub seedbank_facts: usize,
    pub greenhouse_cards: Option<usize>,
    pub files: usize,
    pub lines: u64,
    pub languages: BTreeMap<String, u64>,
    /// Claude Code usage of the last `days` days in this garden's folders.
    pub claude: TokenUse,
    pub days: u64,
}

fn read_tokens(path: &Path) -> u64 {
    std::fs::read_to_string(path).map(|t| estimate_tokens(&t)).unwrap_or(0)
}

fn seedbank_facts(repo: &Path) -> usize {
    std::fs::read_to_string(repo.join(".seedbank").join("facts.json"))
        .ok()
        .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
        .and_then(|v| v.get("facts").and_then(|f| f.as_object()).map(|f| f.len()))
        .unwrap_or(0)
}

fn repo_stats(repo: &crate::store::Repo) -> RepoStats {
    let path = Path::new(&repo.path);
    let present = path.is_dir();
    let mut s = RepoStats {
        name: repo.name.clone(),
        path: repo.path.clone(),
        status: repo.status.clone(),
        group: repo.group.clone(),
        ..Default::default()
    };
    if !present {
        return s;
    }
    s.branch = git(&["rev-parse", "--abbrev-ref", "HEAD"], path);
    s.changed = git(&["status", "--porcelain"], path)
        .map(|o| o.lines().filter(|l| !l.is_empty()).count())
        .unwrap_or(0);
    s.agent_tokens = read_tokens(&path.join("AGENTS.md")) + read_tokens(&path.join("CLAUDE.md"));
    s.seedbank_facts = seedbank_facts(path);
    count_code(path, &mut s);
    s
}

/// Files git tracks, and a rough line count per extension. Files over 2 MB
/// and binary files (a NUL byte in the first 8 KB) count as files, not lines.
fn count_code(repo: &Path, s: &mut RepoStats) {
    let Some(listing) = git(&["ls-files", "-z"], repo) else { return };
    for rel in listing.split('\0').filter(|f| !f.is_empty()) {
        s.files += 1;
        let file = repo.join(rel);
        if std::fs::metadata(&file).map(|m| !m.is_file() || m.len() > 2_000_000).unwrap_or(true) {
            continue;
        }
        let Ok(bytes) = std::fs::read(&file) else { continue };
        if bytes[..bytes.len().min(8192)].contains(&0) {
            continue;
        }
        let lines = bytes.iter().filter(|&&b| b == b'\n').count() as u64;
        let ext = Path::new(rel)
            .extension()
            .map(|e| e.to_string_lossy().to_lowercase())
            .unwrap_or_else(|| "other".into());
        s.lines += lines;
        *s.languages.entry(ext).or_default() += lines;
    }
}

/// Claude Code names a project folder after the cwd with every
/// non-alphanumeric character replaced by '-'.
fn claude_project_name(path: &str) -> String {
    path.chars().map(|c| if c.is_ascii_alphanumeric() { c } else { '-' }).collect()
}

fn claude_config_dirs(profiles: &[Profile]) -> Vec<PathBuf> {
    let mut dirs = vec![std::env::var_os("CLAUDE_CONFIG_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| home_dir().join(".claude"))];
    dirs.extend(profiles.iter().filter(|p| p.agent == "claude").map(|p| expand_tilde(&p.dir)));
    let mut seen = HashSet::new();
    dirs.retain(|d| seen.insert(d.clone()));
    dirs
}

fn claude_usage(garden: &Garden, profiles: &[Profile], days: u64) -> TokenUse {
    let mut prefixes: Vec<String> = vec![claude_project_name(&expand_tilde(&garden.root).to_string_lossy())];
    prefixes.extend(garden.repos.iter().map(|r| claude_project_name(&r.path)));
    let cutoff = SystemTime::now() - Duration::from_secs(days * 86_400);
    let mut use_ = TokenUse::default();
    // Streamed responses repeat the same message id; count each once.
    let mut seen_ids: HashSet<String> = HashSet::new();
    for config in claude_config_dirs(profiles) {
        let Ok(entries) = std::fs::read_dir(config.join("projects")) else { continue };
        for project in entries.flatten() {
            let name = project.file_name().to_string_lossy().to_string();
            let ours = prefixes
                .iter()
                .any(|p| name == *p || name.starts_with(&format!("{p}-")));
            if !ours {
                continue;
            }
            let Ok(files) = std::fs::read_dir(project.path()) else { continue };
            for file in files.flatten() {
                let path = file.path();
                if path.extension().and_then(|e| e.to_str()) != Some("jsonl") {
                    continue;
                }
                let recent = file
                    .metadata()
                    .and_then(|m| m.modified())
                    .map(|t| t >= cutoff)
                    .unwrap_or(false);
                if !recent {
                    continue;
                }
                let Ok(f) = std::fs::File::open(&path) else { continue };
                use_.sessions += 1;
                for line in BufReader::new(f).lines().map_while(Result::ok) {
                    if !line.contains("\"usage\"") {
                        continue;
                    }
                    let Ok(v) = serde_json::from_str::<serde_json::Value>(&line) else { continue };
                    let msg = &v["message"];
                    let Some(u) = msg.get("usage") else { continue };
                    if let Some(id) = msg.get("id").and_then(|i| i.as_str()) {
                        if !seen_ids.insert(id.to_string()) {
                            continue;
                        }
                    }
                    let n = |k: &str| u.get(k).and_then(|x| x.as_u64()).unwrap_or(0);
                    use_.input += n("input_tokens");
                    use_.output += n("output_tokens");
                    use_.cache_read += n("cache_read_input_tokens");
                    use_.cache_write += n("cache_creation_input_tokens");
                }
            }
        }
    }
    use_
}

pub fn collect(garden: &Garden, profiles: &[Profile]) -> GardenStats {
    let days = 30;
    // Repos are independent and mostly disk-bound: read them on 8 threads.
    let chunk = garden.repos.len().div_ceil(8).max(1);
    let repos: Vec<RepoStats> = std::thread::scope(|scope| {
        let workers: Vec<_> = garden
            .repos
            .chunks(chunk)
            .map(|part| scope.spawn(move || part.iter().map(repo_stats).collect::<Vec<_>>()))
            .collect();
        workers.into_iter().flat_map(|w| w.join().unwrap_or_default()).collect()
    });
    let mut languages = BTreeMap::new();
    for r in &repos {
        for (ext, n) in &r.languages {
            *languages.entry(ext.clone()).or_default() += n;
        }
    }
    let namespaces: HashSet<String> = garden
        .repos
        .iter()
        .filter(|r| r.group.is_some())
        .filter_map(|r| Path::new(&r.path).parent().map(|p| p.to_string_lossy().to_string()))
        .collect();
    let cards = expand_tilde(&garden.root).join(".greenhouse").join("repos");
    let greenhouse_cards = std::fs::read_dir(&cards).ok().map(|d| {
        d.flatten()
            .filter(|e| e.path().extension().and_then(|x| x.to_str()) == Some("json"))
            .count()
    });
    GardenStats {
        dirty: repos.iter().filter(|r| r.changed > 0).count(),
        agent_tokens: repos.iter().map(|r| r.agent_tokens).sum(),
        seedbank_facts: repos.iter().map(|r| r.seedbank_facts).sum(),
        groups: garden.group_inputs.len(),
        namespaces: namespaces.len(),
        greenhouse_cards,
        files: repos.iter().map(|r| r.files).sum(),
        lines: repos.iter().map(|r| r.lines).sum(),
        languages,
        claude: claude_usage(garden, profiles, days),
        days,
        repos,
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn project_name_matches_claude_code() {
        assert_eq!(
            super::claude_project_name("/Users/a/PycharmProjects/context-garden"),
            "-Users-a-PycharmProjects-context-garden"
        );
    }
}
