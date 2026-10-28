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

#[derive(Serialize)]
pub struct AccountUse {
    pub name: String,
    /// claude | codex
    pub agent: String,
    pub icon: String,
    pub color: String,
    #[serde(flatten)]
    pub tokens: TokenUse,
}

#[derive(Serialize, Default)]
pub struct GardenStats {
    pub repos: Vec<RepoStats>,
    pub groups: usize,
    pub namespaces: usize,
    pub dirty: usize,
    /// Repos the garden lists whose folder is gone from disk.
    pub missing: usize,
    pub agent_tokens: u64,
    pub seedbank_facts: usize,
    pub greenhouse_cards: Option<usize>,
    pub files: usize,
    pub lines: u64,
    pub languages: BTreeMap<String, u64>,
    /// Agent usage of the last `days` days in this garden's folders, per account
    /// (Claude Code and Codex; the default config dirs count as accounts too).
    pub accounts: Vec<AccountUse>,
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
        if repo.ready() {
            s.status = "missing".into(); // was on disk, folder deleted since
        }
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

/// Every config dir to read usage from: the default one per agent, then the accounts.
fn usage_sources(profiles: &[Profile]) -> Vec<(PathBuf, AccountUse)> {
    let blank = |name: &str, agent: &str| AccountUse {
        name: name.into(),
        agent: agent.into(),
        icon: String::new(),
        color: String::new(),
        tokens: TokenUse::default(),
    };
    let env_dir = |var: &str, fallback: &str| {
        std::env::var_os(var).map(PathBuf::from).unwrap_or_else(|| home_dir().join(fallback))
    };
    let mut out = vec![
        (env_dir("CLAUDE_CONFIG_DIR", ".claude"), blank("Claude (default)", "claude")),
        (env_dir("CODEX_HOME", ".codex"), blank("Codex (default)", "codex")),
    ];
    for p in profiles {
        let mut a = blank(&p.name, &p.agent);
        a.icon = p.icon.clone();
        a.color = p.color.clone();
        out.push((expand_tilde(&p.dir), a));
    }
    let mut seen = HashSet::new();
    out.retain(|(d, _)| seen.insert(d.clone()));
    out
}

fn recent(entry: &std::fs::DirEntry, cutoff: SystemTime) -> bool {
    entry.metadata().and_then(|m| m.modified()).map(|t| t >= cutoff).unwrap_or(false)
}

fn jsonl_files(dir: &Path) -> impl Iterator<Item = std::fs::DirEntry> {
    std::fs::read_dir(dir)
        .into_iter()
        .flatten()
        .flatten()
        .filter(|f| f.path().extension().and_then(|e| e.to_str()) == Some("jsonl"))
}

/// Claude Code: <config>/projects/<cwd as name>/<session>.jsonl, one usage per message.
fn claude_usage(config: &Path, prefixes: &[String], cutoff: SystemTime, use_: &mut TokenUse) {
    let Ok(entries) = std::fs::read_dir(config.join("projects")) else { return };
    // Streamed responses repeat the same message id; count each once.
    let mut seen_ids: HashSet<String> = HashSet::new();
    for project in entries.flatten() {
        let name = project.file_name().to_string_lossy().to_string();
        if !prefixes.iter().any(|p| name == *p || name.starts_with(&format!("{p}-"))) {
            continue;
        }
        for file in jsonl_files(&project.path()).filter(|f| recent(f, cutoff)) {
            let Ok(f) = std::fs::File::open(file.path()) else { continue };
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

/// Codex: <home>/sessions/YYYY/MM/DD/rollout-*.jsonl. The first line names the
/// cwd; token_count events carry the session's running total, so the last wins.
fn codex_usage(home: &Path, roots: &[PathBuf], cutoff: SystemTime, use_: &mut TokenUse) {
    let mut stack = vec![home.join("sessions")];
    while let Some(dir) = stack.pop() {
        let Ok(entries) = std::fs::read_dir(&dir) else { continue };
        for e in entries.flatten() {
            if e.file_type().is_ok_and(|t| t.is_dir()) {
                stack.push(e.path());
            }
        }
        for file in jsonl_files(&dir).filter(|f| recent(f, cutoff)) {
            let Ok(f) = std::fs::File::open(file.path()) else { continue };
            let mut lines = BufReader::new(f).lines().map_while(Result::ok);
            let Some(first) = lines.next() else { continue };
            let cwd = serde_json::from_str::<serde_json::Value>(&first)
                .ok()
                .and_then(|v| v["payload"]["cwd"].as_str().map(PathBuf::from));
            if !cwd.is_some_and(|c| roots.iter().any(|r| c.starts_with(r))) {
                continue;
            }
            use_.sessions += 1;
            let mut total = None;
            for line in lines.filter(|l| l.contains("\"token_count\"")) {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&line) {
                    if let Some(t) = v["payload"]["info"].get("total_token_usage") {
                        total = Some(t.clone());
                    }
                }
            }
            let Some(t) = total else { continue };
            let n = |k: &str| t.get(k).and_then(|x| x.as_u64()).unwrap_or(0);
            // Codex counts cached input inside input_tokens.
            use_.input += n("input_tokens").saturating_sub(n("cached_input_tokens"));
            use_.cache_read += n("cached_input_tokens");
            use_.output += n("output_tokens");
        }
    }
}

/// Usage per account in this garden's folders; accounts without any are left out.
fn usage(garden: &Garden, profiles: &[Profile], days: u64) -> Vec<AccountUse> {
    let root = expand_tilde(&garden.root);
    let mut roots = vec![root.clone()];
    roots.extend(garden.repos.iter().map(|r| PathBuf::from(&r.path)));
    let prefixes: Vec<String> = roots.iter().map(|r| claude_project_name(&r.to_string_lossy())).collect();
    let cutoff = SystemTime::now() - Duration::from_secs(days * 86_400);
    let mut out = Vec::new();
    for (dir, mut account) in usage_sources(profiles) {
        match account.agent.as_str() {
            "claude" => claude_usage(&dir, &prefixes, cutoff, &mut account.tokens),
            "codex" => codex_usage(&dir, &roots, cutoff, &mut account.tokens),
            _ => {}
        }
        if account.tokens.sessions > 0 {
            out.push(account);
        }
    }
    out
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
        missing: repos.iter().filter(|r| r.status == "missing").count(),
        agent_tokens: repos.iter().map(|r| r.agent_tokens).sum(),
        seedbank_facts: repos.iter().map(|r| r.seedbank_facts).sum(),
        groups: garden.group_inputs.len(),
        namespaces: namespaces.len(),
        greenhouse_cards,
        files: repos.iter().map(|r| r.files).sum(),
        lines: repos.iter().map(|r| r.lines).sum(),
        languages,
        accounts: usage(garden, profiles, days),
        days,
        repos,
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn codex_usage_takes_each_sessions_last_total() {
        let home = std::env::temp_dir().join(format!("cg-codex-{}", std::process::id()));
        let day = home.join("sessions/2026/10/27");
        std::fs::create_dir_all(&day).unwrap();
        let count = |input: u64, cached: u64, out: u64| {
            format!(
                r#"{{"type":"event_msg","payload":{{"type":"token_count","info":{{"total_token_usage":{{"input_tokens":{input},"cached_input_tokens":{cached},"output_tokens":{out}}}}}}}}}"#
            )
        };
        let session = |cwd: &str| format!(r#"{{"type":"session_meta","payload":{{"cwd":"{cwd}"}}}}"#);
        std::fs::write(
            day.join("rollout-a.jsonl"),
            [session("/g/repo"), count(100, 40, 10), count(300, 200, 30)].join("\n"),
        )
        .unwrap();
        std::fs::write(day.join("rollout-b.jsonl"), [session("/elsewhere"), count(999, 0, 999)].join("\n")).unwrap();
        let mut use_ = super::TokenUse::default();
        let cutoff = std::time::SystemTime::now() - std::time::Duration::from_secs(3600);
        super::codex_usage(&home, &[std::path::PathBuf::from("/g")], cutoff, &mut use_);
        let _ = std::fs::remove_dir_all(&home);
        assert_eq!((use_.sessions, use_.input, use_.cache_read, use_.output), (1, 100, 200, 30));
    }

    #[test]
    fn deleted_repo_is_missing_not_an_empty_repo() {
        let repo = |status: &str| crate::store::Repo {
            name: "gone".into(),
            path: "/nonexistent/context-garden-test/gone".into(),
            url: None,
            source: None,
            group: None,
            status: status.into(),
            error: None,
        };
        assert_eq!(super::repo_stats(&repo("cloned")).status, "missing");
        assert_eq!(super::repo_stats(&repo("pending")).status, "pending"); // never was on disk
    }

    #[test]
    fn project_name_matches_claude_code() {
        assert_eq!(
            super::claude_project_name("/Users/a/PycharmProjects/context-garden"),
            "-Users-a-PycharmProjects-context-garden"
        );
    }
}
